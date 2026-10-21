"""
NUM-001: round() 是四捨六入五成雙，不是四捨五入

這些測試斷言的是 Python「實際上」的行為，不是一般人以為的行為。
平常應該全部通過；如果哪天升級 Python 後失敗，代表語言行為變了，
要回頭更新 README.md。
"""
from decimal import Decimal, ROUND_HALF_UP

import pytest


def round_half_up(value, exp="0.01"):
    """傳統四捨五入（負數遠離零）。

    value 可以是 float、int 或字串；先轉成字串再建 Decimal，
    避免把 float 的二進位誤差帶進來。
    exp 是要保留的精度，例如 "1"、"0.1"、"0.01"。
    """
    return Decimal(str(value)).quantize(Decimal(exp), rounding=ROUND_HALF_UP)


class TestRoundHalfToEven:
    """剛好在 .5 時，round() 捨入到最近的偶數。"""

    @pytest.mark.parametrize(
        "value, expected",
        [
            (0.5, 0),    # 不是 1
            (1.5, 2),
            (2.5, 2),    # 不是 3
            (3.5, 4),
            (-0.5, 0),   # 不是 -1
            (-1.5, -2),
            (-2.5, -2),  # 不是 -3
        ],
    )
    def test_half_goes_to_even(self, value, expected):
        assert round(value) == expected

    def test_non_half_values_look_normal(self):
        """對照組：不在 .5 上的值，結果和四捨五入一樣，所以測不出差別。"""
        assert round(1.4) == 1
        assert round(1.6) == 2
        assert round(1.24, 1) == 1.2
        assert round(1.26, 1) == 1.3

    def test_return_type(self):
        """順便：沒給 ndigits 回傳 int，給了（即使是 0）回傳 float。"""
        assert isinstance(round(2.5), int)
        assert isinstance(round(2.5, 0), float)


class TestFloatRepresentation:
    """保留小數位數時，.xx5 的「錯誤」有兩種完全不同的原因。"""

    @pytest.mark.parametrize(
        "value, expected",
        [
            (2.675, 2.67),  # 實際存成 2.67499999999999982...
            (1.005, 1.0),   # 實際存成 1.00499999999999989...
            (0.285, 0.28),  # 實際存成 0.28499999999999997...
        ],
    )
    def test_stored_value_is_below_half(self, value, expected):
        """原因一：float 存不到剛好 .xx5，根本沒到一半，跟五成雙無關。"""
        assert Decimal(value) < Decimal(str(value))
        assert round(value, 2) == expected

    @pytest.mark.parametrize(
        "value, expected",
        [
            (0.125, 0.12),  # 0.125 = 1/8，可精確表示
            (0.375, 0.38),  # 0.375 = 3/8，8 是偶數，看起來像四捨五入
        ],
    )
    def test_exact_half_goes_to_even(self, value, expected):
        """原因二：值可以精確表示、真的剛好一半，這時才是五成雙。"""
        assert Decimal(value) == Decimal(str(value))
        assert round(value, 2) == expected


class TestOtherPlacesThatRoundHalfEven:
    """不只 round()，格式化輸出、decimal 預設值、numpy 也是五成雙。"""

    def test_fstring(self):
        assert f"{0.5:.0f}" == "0"
        assert f"{2.5:.0f}" == "2"
        assert f"{0.125:.2f}" == "0.12"

    def test_percent_format(self):
        assert "%.0f" % 2.5 == "2"
        assert "%.2f" % 0.125 == "0.12"

    def test_decimal_default_context(self):
        """沒指定 rounding 時，Decimal.quantize 用的是 ROUND_HALF_EVEN。"""
        assert Decimal("2.5").quantize(Decimal("1")) == Decimal("2")
        assert Decimal("0.125").quantize(Decimal("0.01")) == Decimal("0.12")

    def test_numpy_round(self):
        np = pytest.importorskip("numpy")
        assert np.round(2.5) == 2.0
        assert np.round(0.5) == 0.0
        assert np.round(-2.5) == -2.0


class TestWhyHalfEven:
    """設計理由：大量加總時，五成雙的偏差會互相抵銷。"""

    def test_sum_bias(self):
        values = [0.5, 1.5, 2.5, 3.5]
        exact = sum(values)                                    # 8.0
        half_even = sum(round(v) for v in values)              # 0+2+2+4 = 8
        half_up = sum(round_half_up(v, "1") for v in values)   # 1+2+3+4 = 10
        assert half_even == exact
        assert half_up == exact + 2


class TestCorrectWay:
    """規格要傳統四捨五入時的正確寫法。"""

    @pytest.mark.parametrize(
        "value, exp, expected",
        [
            (0.5, "1", "1"),
            (2.5, "1", "3"),
            (-2.5, "1", "-3"),       # ROUND_HALF_UP 對負數是遠離零
            (2.675, "0.01", "2.68"),
            (1.005, "0.01", "1.01"),
            (0.125, "0.01", "0.13"),
            ("2.675", "0.01", "2.68"),
        ],
    )
    def test_round_half_up(self, value, exp, expected):
        assert round_half_up(value, exp) == Decimal(expected)

    def test_decimal_from_float_still_wrong(self):
        """用了 Decimal 也不保證對：Decimal(float) 會把誤差一起帶進來。"""
        wrong = Decimal(2.675).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        assert wrong == Decimal("2.67")
