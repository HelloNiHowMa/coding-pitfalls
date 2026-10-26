---
id: CON-001
title: 先查庫存再扣，並行時會超賣，而且庫存不一定變成負數
language: sql
category: concurrency/transaction
severity: 高
affects: 所有語言與資料庫；症狀依 UPDATE 寫法、資料庫引擎、隔離等級而不同
tags: [race condition, 競態, check-then-act, TOCTOU, lost update, 遺失更新, 超賣, 庫存, 交易, isolation level, SELECT FOR UPDATE, sqlite3]
---

# 先查庫存再扣，並行時會超賣，而且庫存不一定變成負數

## 現象

```python
def place_order(conn, product_id, qty):
    with conn:                                    # 看起來有包在交易裡
        (stock,) = conn.execute(
            "SELECT stock FROM products WHERE id = ?", (product_id,)
        ).fetchone()
        if stock < qty:
            return 409
        conn.execute("UPDATE products SET stock = ? WHERE id = ?",
                     (stock - qty, product_id))
        conn.execute("INSERT INTO orders (product_id, qty) VALUES (?, ?)",
                     (product_id, qty))
    return 201
```

庫存 1，依序下單兩次：201、409，完全正確。兩個請求同時進來：

```
時間 ↓   請求 A                    請求 B
 1      讀到 stock = 1
 2                                讀到 stock = 1
 3      1 ≥ 1，寫入 stock = 0
 4      建立訂單、COMMIT → 201
 5                                1 ≥ 1，寫入 stock = 0
 6                                建立訂單、COMMIT → 201
```

庫存 1 件，賣出 2 件，兩個都回 201，而**庫存是 0，不是 -1**。

## 第二層陷阱：庫存沒變負數，不代表沒超賣

上面寫回的是程式算好的值。B 寫的 0 直接蓋掉 A 寫的 0，A 那次扣減就不見了，這叫 lost update（遺失更新）。

人越多越明顯：庫存 3、10 個人同時買 1 件，每個人都讀到 3、都寫回 2。結果 10 個 201、10 筆訂單，**庫存還剩 2**。

同樣是先查再扣，只要 UPDATE 寫法或資料庫約束不同，症狀就不同（庫存 1、兩人同時買 1 件）：

| 寫法 | 結果 | 只檢查「庫存 ≥ 0」抓得到嗎 |
|---|---|---|
| 寫回算好的值 `SET stock = ?` | stock 0，2 筆訂單 | 抓不到 |
| 相對扣減 `SET stock = stock - ?` | stock -1，2 筆訂單 | 抓得到 |
| 寫回算好的值，加 `CHECK (stock >= 0)` | stock 0，2 筆訂單，**沒有任何錯誤** | 約束也擋不住 |
| 相對扣減，加 `CHECK (stock >= 0)` | stock 0，1 筆訂單；另一個請求拿到 IntegrityError | 資料庫擋下來了，但程式要把錯誤轉成 409，不能變 500 |

所以判定超賣要用**守恆式**：`初始庫存 = 目前庫存 + 成立訂單的數量總和`，不要用「庫存 ≥ 0」。

## 第三層陷阱：包在交易裡不一定有用

**Python `sqlite3` 的 `with conn:`**：預設（`LEGACY_TRANSACTION_CONTROL`）只在 INSERT / UPDATE / DELETE / REPLACE 之前自動 BEGIN，**SELECT 不會開交易**。上面的 SELECT 其實在交易外面。Python 文件說這個預設值未來會改成 `autocommit=False`，改了以後 `test_python_sqlite3_does_not_begin_before_select` 會失敗，提醒回來更新這一段。

**交易從 SELECT 之前就開始**，結果還是要看資料庫和隔離等級：

| 資料庫／隔離等級 | 寫回算好的值 | 相對扣減 |
|---|---|---|
| SQLite，`BEGIN` 在 SELECT 之前 | 一個成功，另一個 `database is locked` | 同左 |
| PostgreSQL READ COMMITTED（預設）* | 兩個都成功，stock 0（lost update） | 兩個都成功，stock -1 |
| PostgreSQL REPEATABLE READ 以上 * | 後到的那個 `could not serialize access`，要整個重試 | 同左 |
| MySQL InnoDB REPEATABLE READ（預設）* | 兩個都成功，stock 0（lost update） | 兩個都成功，stock -1 |

\* SQLite 列已在本 repo 實際執行確認；PostgreSQL、MySQL 列依官方文件描述的行為，尚未在本 repo 用測試驗證。

PostgreSQL READ COMMITTED 下，第二個 UPDATE 會等第一個 COMMIT，然後套用到更新後的那一列。`SET stock = 0` 套上去還是 0，`SET stock = stock - 1` 套上去變 -1。

這也說明了為什麼單元測試、整合測試可能全過：測試是依序跑的，或測試用的資料庫（SQLite、in-memory）跟正式環境不同，同一段程式的症狀不一樣。

## 原因

「檢查」和「動作」是兩個步驟，中間有空檔。兩個請求同時擠進空檔，兩個都通過檢查。空檔通常只有幾毫秒，所以：

- 依序執行的測試永遠測不到
- 低負載時很少發生，搶購、開賣等尖峰才出現
- 重現不穩定，容易被當成偶發

這類問題叫 check-then-act，或 TOCTOU（time-of-check to time-of-use）。不只庫存：餘額扣款、座位劃位、限量優惠券、帳號名稱不可重複，都是同一個形狀。

## 正確寫法

### 一：把檢查放進 UPDATE，再看影響了幾列

```python
def place_order(conn, product_id, qty):
    with conn:
        cur = conn.execute(
            "UPDATE products SET stock = stock - ? WHERE id = ? AND stock >= ?",
            (qty, product_id, qty),
        )
        if cur.rowcount == 0:          # 庫存不夠，沒有扣
            return 409
        conn.execute("INSERT INTO orders (product_id, qty) VALUES (?, ?)",
                     (product_id, qty))
    return 201
```

- 檢查和扣減是同一個敘述，資料庫保證不會被插隊。
- 扣庫存和建訂單要在同一個交易裡，建訂單失敗時扣掉的庫存才會一起還原。
- 商品不存在時 `rowcount` 也是 0。如果規格要分開回 404，要另外查。

### 二：先鎖再查

PostgreSQL／MySQL：

```sql
BEGIN;
SELECT stock FROM products WHERE id = 1001 FOR UPDATE;  -- 鎖住這一列，其他人要等
-- 檢查、扣減、建立訂單
COMMIT;
```

SQLite 沒有 `FOR UPDATE`，用 `BEGIN IMMEDIATE` 一開始就拿寫入鎖（測試裡的 `place_order_lock_first`）。

鎖住的時候其他請求要排隊。鎖的範圍越小、時間越短越好；交易裡不要呼叫外部 API。

### 三：資料庫約束只當最後防線

`CHECK (stock >= 0)` 只擋得住相對扣減，擋不住寫回算好的值（見上面第一個表格），不能當主要防護。

## 測試設計提示

### 要挑的情境

| 目的 | 情境 |
|---|---|
| 最小、最有鑑別度 | 庫存 1，2 個請求同時買 1 件 |
| 放大 lost update | 庫存 3，10 個請求同時買 1 件（有問題時庫存可能還剩 2） |
| 數量大於 1 | 庫存 3，2 個請求各買 2 件（各自都夠，加起來不夠） |
| 對照組 | 同樣的請求依序送：201、409。依序通過不代表並行正確 |
| 不該互相等待 | 2 個請求買不同商品，都應該成功、不應該互相拖慢 |

### 判定：不要只看狀態碼和「庫存 ≥ 0」

測試結束後**查主資料庫**（不是報表庫或唯讀副本，那裡可能有複寫延遲），檢查：

- 守恆：`初始庫存 = 目前庫存 + 成立訂單的數量總和`
- 回 201 的請求，每一個都找得到對應的訂單；反過來也是
- 成功的數量總和 ≤ 初始庫存

### 讓衝突真的發生

只有兩個請求的「讀庫存 → 寫庫存」區間重疊時，bug 才會出現。

- **一起放行**：請求先準備好，用 `threading.Barrier` 一起放行，不要在迴圈裡一個個送出。
- **每輪重設**：每一輪都重設庫存、清掉上一輪的訂單，每輪獨立判定。
- **確認真的重疊**：「同時送出」不等於「關鍵區間重疊」。從應用程式 log 或資料庫取得每個請求讀庫存與 COMMIT 的時間；B 讀庫存在 A COMMIT 之前，這一輪才算有效的並行測試。
- 本 repo 能在程式裡插 `after_read` 掛鉤，所以每次都會重疊、結果穩定。黑箱測試通常不能改產品程式，只能靠上面三點提高機率，並記錄重疊的證據。

### N 次都通過代表什麼

重複 100 次都通過，只能說「在這個環境、這個負載下沒有觀察到」，不能證明沒有競態。能附上「關鍵區間確實重疊過」的證據，這個結論才比較有力。找出「先查再寫」的程式碼審查，要和這類測試一起用。

### 要問規格的問題

- 201 代表什麼？扣庫存的交易已經提交，還是只是收到請求、之後才處理？
- 庫存不足時回 409？排隊？候補？
- 下單後多久沒付款要釋放庫存？釋放時有沒有同樣的並行問題？
- 庫存以哪個資料庫為準？查詢頁面、報表是不是讀副本？允許落後多久？

### 容易一起出錯的地方

- **回應早於 COMMIT**：API 先回 201 再提交交易（或丟到佇列非同步寫入），提交失敗時使用者已經看到成功。測試要比對「回 201 的請求」和主資料庫的訂單。
- **用副本驗證**：測試後去報表庫、唯讀副本查結果，看到的可能是舊資料，不能當最終證據。
- **重送與連點**：客戶端逾時重送、使用者連點兩下，同一個人也會產生並行請求。要搭配冪等鍵（idempotency key）測。

## 參考

- [PostgreSQL 文件：Transaction Isolation](https://www.postgresql.org/docs/current/transaction-iso.html)
- [MySQL 文件：Locking Reads](https://dev.mysql.com/doc/refman/8.4/en/innodb-locking-reads.html)
- [Python 文件：sqlite3](https://docs.python.org/3/library/sqlite3.html)（`autocommit`、`isolation_level`、交易控制）
