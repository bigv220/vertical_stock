"""divergence.py 单测：纯函数，无网络。"""
import unittest

from spread_bot.divergence import compute_rsi, detect
from spread_bot.quotes import MinuteKline


def _kl(ts, c, v=100.0):
    return MinuteKline(ts=ts, open=c, close=c, high=c, low=c, volume=v, amount=0.0)


class _Cfg:
    enable_rsi_divergence = True
    enable_pv_divergence = True
    divergence_rsi_period = 14
    divergence_lookback = 120
    divergence_pivot_window = 3
    divergence_min_swing_pct = 0.3


class RSITest(unittest.TestCase):
    def test_all_up(self):
        closes = [100 + i for i in range(20)]
        rsi = compute_rsi(closes, 14)
        self.assertIsNotNone(rsi)
        self.assertAlmostEqual(rsi[-1], 100.0)

    def test_all_down(self):
        closes = [100 - i for i in range(20)]
        rsi = compute_rsi(closes, 14)
        self.assertIsNotNone(rsi)
        self.assertLess(rsi[-1], 5.0)

    def test_insufficient(self):
        self.assertIsNone(compute_rsi([1, 2, 3], 14))


class DetectTest(unittest.TestCase):
    def _build(self, pv_pairs):
        """pv_pairs: [(price, volume), ...] → 每分钟一根 K 线。"""
        out = []
        for i, (p, v) in enumerate(pv_pairs):
            mm = 31 + i
            hh = "09" if mm < 60 else "10"
            out.append(_kl(f"2024-06-20 {hh}:{mm % 60:02d}:00", p, v))
        return out

    def test_bearish_divergence(self):
        # 峰A：陡涨到 20（高 RSI、放量），回落；峰B：缓涨到 21（RSI 更低、缩量），回落。
        # 两个峰都有后续回落，故能被 _pivots 注册为 pivot high。
        pv = []
        # seg1 陡涨 10→20（放量）
        pv += [(10 + i * 0.66, 500.0) for i in range(16)]      # 末值 ~19.9
        # 回落 20→13（让峰A成为 pivot）
        pv += [(19.9 - i * 0.8, 200.0) for i in range(1, 9)]   # → ~13.5
        # seg3 缓涨 13.5→21（缩量）
        pv += [(13.5 + i * 0.27, 150.0) for i in range(1, 29)] # → ~21
        # 回落（让峰B成为 pivot）
        pv += [(21 - i * 0.5, 100.0) for i in range(1, 6)]     # → ~19
        klines = self._build(pv)
        res = detect(klines, _Cfg())
        self.assertTrue(res.has_data)
        # 峰B(21) > 峰A(20)：价格 HH；缓涨 → RSI LH、缩量 → 顶背离
        self.assertTrue(res.bearish)

    def test_ambiguous_returns_no_signal(self):
        from spread_bot.divergence import DivergenceResult
        r = DivergenceResult(rsi_bearish=True, rsi_bullish=True, has_data=True)
        self.assertTrue(r.ambiguous)

    def test_insufficient_data(self):
        res = detect([_kl("2024-06-20 09:31:00", 10)], _Cfg())
        self.assertFalse(res.has_data)
        self.assertFalse(res.bearish)
        self.assertFalse(res.bullish)


if __name__ == "__main__":
    unittest.main()
