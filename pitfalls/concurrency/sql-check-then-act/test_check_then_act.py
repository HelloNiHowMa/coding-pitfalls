"""
CON-001: 先查庫存再扣，並行時會超賣，而且庫存不一定變成負數

用 Python 內建的 sqlite3 示範，不需要資料庫伺服器。

並行的 bug 平常很難穩定重現：兩個請求要剛好擠進「讀庫存」和「寫庫存」
之間的小空檔。這裡在讀完庫存之後放一個 after_read 掛鉤，用
threading.Barrier 讓所有請求都讀完庫存、才放任何一個去寫，
所以每次執行結果都一樣。

黑箱測試沒辦法在產品程式裡加這種掛鉤，做法見 README.md 的「測試設計提示」。

這些測試斷言的是「實際上」的行為。平常應該全部通過；如果哪天升級
Python 或 SQLite 後失敗，代表行為變了，要回頭更新 README.md。
"""
import sqlite3
import threading
from typing import NamedTuple

import pytest

PRODUCT_ID = 1001


# ---------------------------------------------------------------------------
# 被測的「產品程式」：幾種下單寫法
# 回傳 201（成功保留庫存）或 409（庫存不足）
# ---------------------------------------------------------------------------

def place_order_check_then_set(conn, product_id, qty, after_read=None):
    """有問題的寫法：先查，再寫回程式算好的值。"""
    with conn:  # 看起來有包在交易裡（見 TestTransactionIsNotEnough）
        (stock,) = conn.execute(
            "SELECT stock FROM products WHERE id = ?", (product_id,)
        ).fetchone()
        if after_read:
            after_read()
        if stock < qty:
            return 409
        conn.execute(
            "UPDATE products SET stock = ? WHERE id = ?", (stock - qty, product_id)
        )
        conn.execute(
            "INSERT INTO orders (product_id, qty) VALUES (?, ?)", (product_id, qty)
        )
    return 201


def place_order_check_then_decrement(conn, product_id, qty, after_read=None):
    """有問題的寫法：先查，再相對扣減。只差在 UPDATE 那一行。"""
    with conn:
        (stock,) = conn.execute(
            "SELECT stock FROM products WHERE id = ?", (product_id,)
        ).fetchone()
        if after_read:
            after_read()
        if stock < qty:
            return 409
        conn.execute(
            "UPDATE products SET stock = stock - ? WHERE id = ?", (qty, product_id)
        )
        conn.execute(
            "INSERT INTO orders (product_id, qty) VALUES (?, ?)", (product_id, qty)
        )
    return 201


def explicit_begin(order_fn):
    """同一段程式，但在 SELECT 之前就明確 BEGIN。"""

    def wrapped(conn, product_id, qty, after_read=None):
        conn.execute("BEGIN")
        try:
            return order_fn(conn, product_id, qty, after_read)
        except Exception:
            conn.rollback()
            raise

    wrapped.__name__ = f"explicit_begin({order_fn.__name__})"
    return wrapped


def place_order_atomic(conn, product_id, qty, after_read=None):
    """正確寫法一：檢查放進 UPDATE 的 WHERE，一個敘述完成，再看影響幾列。

    沒有「讀完再寫」的空檔，所以 after_read 用不到。
    """
    with conn:
        cur = conn.execute(
            "UPDATE products SET stock = stock - ? WHERE id = ? AND stock >= ?",
            (qty, product_id, qty),
        )
        if cur.rowcount == 0:
            return 409
        conn.execute(
            "INSERT INTO orders (product_id, qty) VALUES (?, ?)", (product_id, qty)
        )
    return 201


def place_order_lock_first(conn, product_id, qty, after_read=None):
    """正確寫法二：先鎖再查。

    SQLite 沒有 SELECT ... FOR UPDATE，用 BEGIN IMMEDIATE 一開始就拿寫入鎖。
    PostgreSQL / MySQL 的寫法見 README.md。
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        (stock,) = conn.execute(
            "SELECT stock FROM products WHERE id = ?", (product_id,)
        ).fetchone()
        if after_read:
            after_read()
        if stock < qty:
            conn.rollback()
            return 409
        conn.execute(
            "UPDATE products SET stock = stock - ? WHERE id = ?", (qty, product_id)
        )
        conn.execute(
            "INSERT INTO orders (product_id, qty) VALUES (?, ?)", (product_id, qty)
        )
        conn.commit()
        return 201
    except Exception:
        conn.rollback()
        raise


# ---------------------------------------------------------------------------
# 測試環境
# ---------------------------------------------------------------------------

class State(NamedTuple):
    stock: int       # 目前庫存
    orders: int      # 成立的訂單筆數
    sold_qty: int    # 訂單數量總和


def create_db(path, stock, check_constraint=False):
    check = " CHECK (stock >= 0)" if check_constraint else ""
    conn = sqlite3.connect(path)
    with conn:
        conn.execute(f"CREATE TABLE products (id INTEGER PRIMARY KEY, stock INTEGER{check})")
        conn.execute(
            "CREATE TABLE orders (id INTEGER PRIMARY KEY, product_id INTEGER, qty INTEGER)"
        )
        conn.execute("INSERT INTO products VALUES (?, ?)", (PRODUCT_ID, stock))
    conn.close()


def read_state(path):
    conn = sqlite3.connect(path)
    (stock,) = conn.execute(
        "SELECT stock FROM products WHERE id = ?", (PRODUCT_ID,)
    ).fetchone()
    orders, sold_qty = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(qty), 0) FROM orders WHERE product_id = ?",
        (PRODUCT_ID,),
    ).fetchone()
    conn.close()
    return State(stock, orders, sold_qty)


def conserved(initial_stock, state):
    """守恆式：初始庫存 = 目前庫存 + 賣出數量。比「庫存 >= 0」可靠。"""
    return initial_stock == state.stock + state.sold_qty


def run_concurrently(path, order_fn, quantities, wait_timeout=10.0):
    """每個請求一條執行緒、各自一條連線，一起放行下單。

    after_read 掛鉤：讀完庫存後，等其他請求也都讀完才往下寫。
    等不到（逾時）就直接往下走。

    回傳 (每個請求的結果, 所有請求的「讀→寫」區間是否真的重疊)。
    結果是 201、409，或是丟出的 sqlite3 例外。
    """
    n = len(quantities)
    start_gate = threading.Barrier(n)
    all_read = threading.Barrier(n, timeout=wait_timeout)
    met_others = []
    results = [None] * n

    def after_read():
        try:
            all_read.wait()
            met_others.append(True)
        except threading.BrokenBarrierError:
            met_others.append(False)

    def worker(i, qty):
        conn = sqlite3.connect(path, timeout=30)
        try:
            start_gate.wait()
            results[i] = order_fn(conn, PRODUCT_ID, qty, after_read=after_read)
        except sqlite3.Error as e:
            results[i] = e
        finally:
            conn.close()

    threads = [threading.Thread(target=worker, args=(i, q)) for i, q in enumerate(quantities)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    overlapped = len(met_others) == n and all(met_others)
    return results, overlapped


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "shop.db")


# ---------------------------------------------------------------------------
# 測試
# ---------------------------------------------------------------------------

class TestSequentialLooksFine:
    """對照組：依序下單時完全正確，所以依序的測試抓不到。"""

    def test_second_order_gets_409(self, db):
        create_db(db, stock=1)
        conn = sqlite3.connect(db)
        assert place_order_check_then_set(conn, PRODUCT_ID, 1) == 201
        assert place_order_check_then_set(conn, PRODUCT_ID, 1) == 409
        conn.close()
        assert read_state(db) == State(stock=0, orders=1, sold_qty=1)


class TestOversell:
    """同時下單時，每個請求都通過檢查，全部回 201。"""

    def test_two_requests_both_get_201(self, db):
        create_db(db, stock=1)
        results, overlapped = run_concurrently(db, place_order_check_then_set, [1, 1])
        assert overlapped
        assert results == [201, 201]
        assert read_state(db) == State(stock=0, orders=2, sold_qty=2)

    @pytest.mark.parametrize(
        "stock, quantities, expected",
        [
            (1, [1, 1], State(stock=0, orders=2, sold_qty=2)),
            (3, [1] * 10, State(stock=2, orders=10, sold_qty=10)),  # 還剩 2 件，卻賣了 10 件
            (3, [2, 2], State(stock=1, orders=2, sold_qty=4)),      # 各自都夠，加起來不夠
        ],
    )
    def test_stock_never_goes_negative_but_oversold(self, db, stock, quantities, expected):
        """寫回算好的值時，後寫的蓋掉先寫的（lost update），庫存看起來很正常。"""
        create_db(db, stock=stock)
        results, overlapped = run_concurrently(db, place_order_check_then_set, quantities)
        assert overlapped
        assert all(r == 201 for r in results)

        state = read_state(db)
        assert state == expected
        assert state.stock >= 0            # 只檢查這個：PASS，抓不到
        assert not conserved(stock, state)  # 檢查守恆：抓得到
        assert state.sold_qty > stock


class TestSymptomDependsOnUpdate:
    """一樣是先查再扣，UPDATE 寫法和資料庫約束不同，症狀就不同。"""

    def test_relative_decrement_goes_negative(self, db):
        create_db(db, stock=1)
        results, _ = run_concurrently(db, place_order_check_then_decrement, [1, 1])
        assert results == [201, 201]
        assert read_state(db) == State(stock=-1, orders=2, sold_qty=2)

    def test_check_constraint_cannot_stop_lost_update(self, db):
        """CHECK (stock >= 0) 擋不住寫回算好的值：兩次都寫 0，0 合法。"""
        create_db(db, stock=1, check_constraint=True)
        results, _ = run_concurrently(db, place_order_check_then_set, [1, 1])
        assert results == [201, 201]
        assert read_state(db) == State(stock=0, orders=2, sold_qty=2)

    def test_check_constraint_stops_relative_decrement(self, db):
        """相對扣減會算出 -1，約束擋下來；程式要把這個錯誤轉成 409，不能變 500。"""
        create_db(db, stock=1, check_constraint=True)
        results, _ = run_concurrently(db, place_order_check_then_decrement, [1, 1])
        assert results.count(201) == 1
        errors = [r for r in results if r != 201]
        assert len(errors) == 1
        assert isinstance(errors[0], sqlite3.IntegrityError)
        assert read_state(db) == State(stock=0, orders=1, sold_qty=1)


class TestTransactionIsNotEnough:
    """「包在交易裡」不等於「檢查和扣減是原子的」。"""

    def test_python_sqlite3_does_not_begin_before_select(self, db):
        """Python sqlite3 預設只在 INSERT/UPDATE/DELETE/REPLACE 前自動 BEGIN。

        所以 `with conn:` 裡的 SELECT 其實在交易外面。
        文件說這個預設值未來會改；改了以後這個測試會失敗，要回頭更新 README。
        """
        create_db(db, stock=1)
        conn = sqlite3.connect(db)
        with conn:
            conn.execute("SELECT stock FROM products WHERE id = ?", (PRODUCT_ID,)).fetchone()
            assert not conn.in_transaction
            conn.execute("UPDATE products SET stock = 0 WHERE id = ?", (PRODUCT_ID,))
            assert conn.in_transaction
        conn.close()

    @pytest.mark.parametrize(
        "order_fn", [place_order_check_then_set, place_order_check_then_decrement]
    )
    def test_sqlite_explicit_begin_turns_into_lock_error(self, db, order_fn):
        """SQLite：交易從 SELECT 之前開始時，後寫的那個會拿到 database is locked。

        沒有超賣，但變成錯誤，程式要處理（重試或回 409），不然使用者看到 500。
        PostgreSQL READ COMMITTED、MySQL REPEATABLE READ 不是這樣，見 README.md。
        """
        create_db(db, stock=1)
        results, overlapped = run_concurrently(db, explicit_begin(order_fn), [1, 1])
        assert overlapped
        assert results.count(201) == 1
        errors = [r for r in results if r != 201]
        assert len(errors) == 1
        assert isinstance(errors[0], sqlite3.OperationalError)
        assert "locked" in str(errors[0])
        assert read_state(db) == State(stock=0, orders=1, sold_qty=1)


class TestCorrectWay:

    @pytest.mark.parametrize(
        "stock, quantities, ok, final_stock",
        [
            (1, [1, 1], 1, 0),
            (3, [1] * 10, 3, 0),
            (3, [2, 2], 1, 1),
        ],
    )
    def test_atomic_conditional_update(self, db, stock, quantities, ok, final_stock):
        create_db(db, stock=stock)
        results, _ = run_concurrently(db, place_order_atomic, quantities)
        assert results.count(201) == ok
        assert results.count(409) == len(quantities) - ok

        state = read_state(db)
        assert state.stock == final_stock
        assert state.orders == ok  # 每個 201 都有一筆訂單
        assert conserved(stock, state)

    def test_lock_first(self, db):
        """先鎖再查：第二個請求連庫存都讀不到，要等第一個 COMMIT。

        所以兩個請求的「讀→寫」區間不可能重疊：
        after_read 的 Barrier 等不到對方，逾時後各自往下走。
        """
        create_db(db, stock=1)
        results, overlapped = run_concurrently(
            db, place_order_lock_first, [1, 1], wait_timeout=1.0
        )
        assert not overlapped
        assert results.count(201) == 1
        assert results.count(409) == 1
        state = read_state(db)
        assert state == State(stock=0, orders=1, sold_qty=1)
        assert conserved(1, state)
