---
id: NUM-001
title: round() 是四捨六入五成雙，不是四捨五入
language: python
category: numeric/rounding
severity: 高
affects: Python 3.0 以後（Python 2 是遠離零的四捨五入）
tags: [round, banker's rounding, 銀行家捨入, decimal, float, 金額, 稅額]
---

# `round()` 是四捨六入五成雙，不是四捨五入

## 現象

```python
>>> round(0.5), round(1.5), round(2.5), round(3.5)
(0, 2, 2, 4)
>>> round(-2.5)
-2
```

剛好在 .5 的時候，Python 捨入到**最近的偶數**，不是一律進位。這叫 round half to even，中文常稱「銀行家捨入」或「四捨六入五成雙」。

順便一提：`round(2.5)` 回傳 `int`，`round(2.5, 0)` 回傳 `float`。

## 第二層陷阱：保留小數位數

```python
>>> round(2.675, 2)
2.67      # 預期 2.68
>>> round(0.125, 2)
0.12      # 預期 0.13
```

這兩個「錯」的原因不一樣：

| 輸入 | 結果 | 原因 |
|---|---|---|
| `round(0.125, 2)` | 0.12 | 0.125 能用二進位精確表示，真的剛好一半 → 五成雙 |
| `round(0.375, 2)` | 0.38 | 同上，只是 8 剛好是偶數，看起來像四捨五入 |
| `round(2.675, 2)` | 2.67 | 2.675 存進 float 其實是 2.67499999999999982…，根本沒到一半 |

第三種是浮點數表示誤差：**就算自己寫一個「正確的四捨五入」，只要輸入是 float 一樣會錯。**

## 同樣是五成雙的地方

- 格式化輸出：`f"{2.5:.0f}"` → `'2'`、`"%.2f" % 0.125` → `'0.12'`
- `decimal` 沒指定 rounding 時：`Decimal("2.5").quantize(Decimal("1"))` → `2`
- `numpy.round(2.5)` → `2.0`

也就是說，畫面上顯示的數字（f-string 格式化）也是五成雙。

## 原因

五成雙是 IEEE 754 的預設捨入模式。大量資料加總時，四捨五入在 .5 一律往上，偏差會一直累積；五成雙一半往上、一半往下，偏差互相抵銷。

```python
values = [0.5, 1.5, 2.5, 3.5]       # 總和 8
sum(round(v) for v in values)       # 0+2+2+4 = 8
# 四捨五入：1+2+3+4 = 10，多了 2
```

## 正確寫法

規格要傳統四捨五入（金額、稅額、成績常見）時，用 `Decimal`，而且**從字串建立**：

```python
from decimal import Decimal, ROUND_HALF_UP

def round_half_up(value, exp="0.01"):
    return Decimal(str(value)).quantize(Decimal(exp), rounding=ROUND_HALF_UP)

round_half_up(2.675)       # Decimal('2.68')
round_half_up(2.5, "1")    # Decimal('3')
round_half_up(-2.5, "1")   # Decimal('-3')，負數遠離零
```

注意：

- `Decimal(2.675)` 會把 float 的誤差一起帶進來，結果還是 2.67。要用 `Decimal("2.675")` 或 `Decimal(str(x))`。
- 最好金額從頭到尾都用 `Decimal`（或用整數存「分」），不要中途經過 float。
- `ROUND_HALF_UP` 對負數是遠離零（-2.5 → -3）。如果規格要 -2.5 → -2，是另一種規則，要先問清楚。

## 測試設計提示

### 要挑的輸入值

| 目的 | 值 |
|---|---|
| 抓五成雙 | 0.5、1.5、2.5、3.5（奇偶都要有；只測 1.5 會以為是四捨五入） |
| 負數 | -0.5、-1.5、-2.5 |
| 浮點表示誤差 | 2.675、1.005、0.285（保留兩位） |
| 可精確表示的 .xx5 | 0.125、0.375（保留兩位） |
| 對照組 | 1.24、1.26（保留一位） |

常見盲點：測試資料只用 1.24、1.26 這種值，兩種捨入結果一樣，測不出差別。**最關鍵的單一測試值是 2.5**：四捨五入得 3，五成雙得 2。

### 要問規格的問題

- 規格寫的「四捨五入」是哪一種？負數怎麼處理？
- 先捨入再加總，還是加總完再捨入？（例：每個品項各自算稅額，還是整張單據算一次）
- 畫面顯示的捨入和存進資料庫的捨入，是不是同一套規則？

### 容易一起出錯的地方：跨系統不一致

同一個數字，後端、前端、報表可能算出不同結果：

| | 2.5 → 整數 | -2.5 → 整數 | 2.675 → 兩位 |
|---|---|---|---|
| Python `round()` | 2 | -2 | 2.67 |
| JavaScript `Math.round()` / `toFixed(2)` | 3 | -2 | 2.67 |
| Excel `ROUND()`* | 3 | -3 | 2.68 |

\* Python 和 JavaScript 欄位已實際執行確認；Excel 欄位依 Microsoft 文件描述的行為，尚未在本 repo 用測試驗證。

測試案例可以設計成：同一筆資料走完後端計算、前端顯示、匯出報表，三邊數字要一致。

## 參考

- [Python 文件：round()](https://docs.python.org/3/library/functions.html#round)
- [Python 文件：Floating Point Arithmetic: Issues and Limitations](https://docs.python.org/3/tutorial/floatingpoint.html)
- [Python 文件：decimal 模組](https://docs.python.org/3/library/decimal.html)
