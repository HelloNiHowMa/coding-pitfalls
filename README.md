# Coding Pitfalls

寫程式容易踩的坑。每一筆都附**可執行的測試**當作證據。

用途：設計測試案例時來這裡查「這類功能該挑哪些輸入值、該問規格什麼問題」。

## 索引

| ID | 陷阱 | 語言 | 分類 | 嚴重度 |
|----|------|------|------|--------|
| [NUM-001](pitfalls/numeric/python-round-half-even/) | `round()` 是四捨六入五成雙，不是四捨五入 | Python | 數值／捨入 | 高 |
| [CON-001](pitfalls/concurrency/sql-check-then-act/) | 先查庫存再扣，並行時會超賣，而且庫存不一定變成負數 | SQL（Python sqlite3 示範） | 並行／交易 | 高 |

ID 是為了在測試案例裡引用，例如測試案例備註寫「參考 NUM-001」。

## 結構

```
pitfalls/
  <分類>/
    <語言>-<簡短名稱>/
      README.md     現象、原因、正確寫法、測試設計提示
      test_*.py     可執行的證據
```

依「分類」而不是「語言」分資料夾，因為設計測試時是從「我在測什麼功能」去找，而且同一類坑在不同語言常常都有。

分類與 ID 字首（需要時再加）：

| 資料夾 | ID 字首 | 例子 |
|---|---|---|
| `numeric` | NUM | 浮點數、捨入、整數溢位、除法 |
| `datetime` | DT | 時區、夏令時間、閏年、epoch |
| `string` | STR | 編碼、Unicode 正規化、大小寫、空白 |
| `collections` | COL | 可變預設參數、淺拷貝、排序穩定性 |
| `concurrency` | CON | 競態、鎖、非同步 |

## 測試的寫法

測試斷言的是**令人意外的實際行為**，不是你以為的行為。

所以平常測試全部通過；哪天升級語言版本後某個測試失敗，就代表行為改了，該回頭更新那一筆的 README。CI 會用多個 Python 版本跑，順便檢查不同版本的差異。

## 執行

```bash
pip install -r requirements.txt
pytest
```

## 新增一筆

1. 複製 `TEMPLATE.md` 到 `pitfalls/<分類>/<語言>-<名稱>/README.md`
2. 在同一個資料夾寫 `test_<名稱>.py`，跑 `pytest` 確認通過
3. 在上面的索引表加一行
