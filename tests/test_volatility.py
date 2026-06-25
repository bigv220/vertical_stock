"""volatility.py 单测：纯函数，无网络。"""
import math
import unittest

from spread_bot.quotes import MinuteKline
from spread_bot.volatility import (
    VolatilityProfile,
    build_profile,
    compute_atr,
    compute_bollinger,
    compute_hv,
    true_range,
)


def _kl(ts, o, c, h=None, l=None, v=100.0):
    return MinuteKline(ts=ts, open=o, close=c, high=h if h is not None else c,
                       low=l if l is not None else c, volume=v, amount=0.0)


class _Cfg:
    vol_atr_period = 14
    vol_bb_period = 20
    vol_bb_std = 2.0
    vol_hv_period = 60
    vol_high_bb_width_pct = 1.5
    vol_low_bb_width_pct = 0.5
    vol_denoise_score_bump = 1


class TrueRangeTest(unittest.TestCase):
    def test_no_prev_close(self):
        self.assertEqual(true_range(None, 11.0, 9.0), 2.0)

    def test_with_prev_close(self):
        self.assertEqual(true_range(10.0, 12.0, 9.0), 3.0)  # max(3,2,1)


class ATRTest(unittest.TestCase):
    def test_simple_atr(self):
        # 两根，period=2：TR1=h-l（无prev），TR2=max(h-l,|h-pc|,|l-pc|)
        klines = [
            _kl("2024-06-20 09:31:00", 10, 10, 11, 9),
            _kl("2024-06-20 09:32:00", 10, 10, 12, 8),
        ]
        atr = compute_atr(klines, 2)
        # TR1=2, TR2=max(4,2,2)=4 → mean=3
        self.assertAlmostEqual(atr, 3.0)

    def test_cross_day_no_gap_inflation(self):
        # 跨日第一根无 prev_close → TR=high-low，不算隔夜跳空
        klines = [
            _kl("2024-06-19 15:00:00", 10, 10, 10, 10),
            _kl("2024-06-20 09:31:00", 20, 20, 21, 19),  # 隔夜跳空，但跨日 → TR=2
        ]
        atr = compute_atr(klines, 2)
        # TR1=0, TR2=2 → mean=1
        self.assertAlmostEqual(atr, 1.0)


class BollingerTest(unittest.TestCase):
    def test_constant_series(self):
        closes = [10.0] * 20
        u, m, l = compute_bollinger(closes, 20, 2.0)
        self.assertAlmostEqual(u, 10.0)
        self.assertAlmostEqual(m, 10.0)
        self.assertAlmostEqual(l, 10.0)

    def test_insufficient(self):
        self.assertIsNone(compute_bollinger([1, 2, 3], 20, 2.0))

    def test_known_std(self):
        # period=3，值 1,2,3 → mean=2，var=2/3，sd=sqrt(2/3)
        u, m, l = compute_bollinger([1.0, 2.0, 3.0], 3, 2.0)
        sd = math.sqrt(2.0 / 3.0)
        self.assertAlmostEqual(m, 2.0)
        self.assertAlmostEqual(u, 2.0 + 2 * sd)
        self.assertAlmostEqual(l, 2.0 - 2 * sd)


class HVTest(unittest.TestCase):
    def test_excludes_overnight_return(self):
        # 同日两根收益率 + 跨日第一根被剔除
        klines = [
            _kl("2024-06-19 15:00:00", 10, 10),
            _kl("2024-06-20 09:31:00", 20, 20),  # 隔夜跳空 → 收益率被剔除
            _kl("2024-06-20 09:32:00", 20, 20),  # 收益率 0
        ]
        hv = compute_hv(klines, 60)
        # 仅一个有效收益率(0)，len<2 → None
        self.assertIsNone(hv)

    def test_variance_computed(self):
        klines = [
            _kl(f"2024-06-20 09:{31 + i:02d}:00", 10, 10 + i) for i in range(5)
        ]
        hv = compute_hv(klines, 60)
        self.assertIsNotNone(hv)
        self.assertGreater(hv, 0.0)


class BuildProfileTest(unittest.TestCase):
    def test_cold_start_none(self):
        self.assertIsNone(build_profile([_kl("2024-06-20 09:31:00", 10, 10, 11, 9)], _Cfg()))

    def test_low_and_high_regime(self):
        cfg = _Cfg()
        # 低波动：价格几乎不动
        low = [_kl(f"2024-06-20 09:{31 + i:02d}:00", 10.00, 10.00 + 0.001 * i, 10.01, 9.99) for i in range(25)]
        p = build_profile(low, cfg)
        self.assertIsNotNone(p)
        self.assertEqual(p.regime, "low")
        self.assertIsInstance(p, VolatilityProfile)

        # 高波动：大幅摆动
        high = [_kl(f"2024-06-20 09:{31 + i:02d}:00", 10 + (1 if i % 2 else -1) * i * 0.2,
                    10 + (1 if i % 2 else -1) * i * 0.2,
                    10 + (1 if i % 2 else -1) * i * 0.2 + 0.2,
                    10 + (1 if i % 2 else -1) * i * 0.2 - 0.2) for i in range(25)]
        ph = build_profile(high, cfg)
        self.assertIsNotNone(ph)
        self.assertEqual(ph.regime, "high")

    def test_bb_position_no_divide_by_zero(self):
        low = [_kl(f"2024-06-20 09:{31 + i:02d}:00", 10.00, 10.00, 10.00, 10.00) for i in range(25)]
        p = build_profile(low, _Cfg())
        self.assertIsNotNone(p)
        self.assertAlmostEqual(p.bb_position, 0.5)


if __name__ == "__main__":
    unittest.main()
