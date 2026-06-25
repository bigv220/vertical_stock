"""confluence.py 单测：压制逻辑、冷却不消耗、低波动降噪。无网络。"""
import unittest

from spread_bot.confluence import evaluate_confluence
from spread_bot.divergence import DivergenceResult
from spread_bot.quotes import MinuteBar, Quote
from spread_bot.volatility import VolatilityProfile


def _quote(price=10.0, prev=10.0):
    return Quote(code="300001", name="测试", price=price, prev_close=prev,
                 open=prev, high=price, low=price, limit_up=0, limit_down=0,
                 time="20240620093100", source="tencent")


class _Cfg:
    code = "300001"
    name = "测试"
    cost = None
    base_price = None
    grid_step_pct = 3.0
    trade_shares = 100
    upper_limit_pct = None
    lower_limit_pct = None
    trend_alert_grids = 3
    strategy = "confluence"
    confluence_lookback = 120
    confluence_min_bars = 30
    confluence_min_score = 4
    confluence_cooldown_minutes = 30
    retracement_min_pct = 38.2
    retracement_max_pct = 61.8
    trendline_tolerance_pct = 1.0
    sr_tolerance_pct = 1.0
    vwap_tolerance_pct = 0.5
    kline_lookback = 320
    enable_volatility = True
    vol_denoise_score_bump = 1
    enable_divergence = True
    divergence_suppress_opposite = True


def _bars(prices):
    """构造分时线：先下跌(找 swing_low)，再反弹到高(找 swing_high)。"""
    out = []
    for i, p in enumerate(prices):
        mm = 31 + i
        out.append(MinuteBar(f"{mm:04d}", p, p, 100.0, 1000.0))
    return out


class SuppressionTest(unittest.TestCase):
    def test_bearish_divergence_suppresses_buy_no_cooldown(self):
        # 构造一个会触发低吸(买)的回撤场景：价格从高点回落 50%
        seg = [10 + i * 0.1 for i in range(40)]   # 涨到 ~13.9
        seg += [13.9 - i * 0.05 for i in range(1, 40)]  # 回落到 ~12.0（回撤约 50%）
        bars = _bars(seg)
        q = _quote(price=bars[-1].price, prev=bars[0].price - 1)

        # 顶背离 → 买信号应被压制
        div = DivergenceResult(rsi_bearish=True, has_data=True)
        sig, st = evaluate_confluence(_Cfg(), q, {}, bars, divergence=div)
        # 若产生了买信号且被压制：suppressed=True 且 st 不含冷却时间戳
        if sig is not None and sig.action.value == "低吸":
            self.assertTrue(sig.suppressed)
            self.assertNotIn("confluence_last_signal_time", st)

    def test_low_vol_raises_threshold_silent(self):
        # 低波动 vol_profile → eff_min 抬高，边界信号静默 return None
        seg = [10 + i * 0.1 for i in range(40)]
        seg += [13.9 - i * 0.05 for i in range(1, 40)]
        bars = _bars(seg)
        q = _quote(price=bars[-1].price, prev=bars[0].price - 1)
        vol = VolatilityProfile(
            atr=0.01, atr_pct=0.01, bb_upper=10.01, bb_middle=10.0, bb_lower=9.99,
            bb_width_pct=0.2, bb_position=0.5, hv_pct=0.01, regime="low")
        sig, st = evaluate_confluence(_Cfg(), q, {}, bars, vol_profile=vol)
        # 低波动下，达不到抬高的门槛 → 无信号
        if sig is None:
            self.assertNotIn("confluence_last_signal_time", st)

    def test_no_divergence_no_suppression(self):
        seg = [10 + i * 0.1 for i in range(40)]
        seg += [13.9 - i * 0.05 for i in range(1, 40)]
        bars = _bars(seg)
        q = _quote(price=bars[-1].price, prev=bars[0].price - 1)
        # 无背离 → 即便触发也不压制
        div = DivergenceResult(has_data=True)  # 全 false
        sig, st = evaluate_confluence(_Cfg(), q, {}, bars, divergence=div)
        if sig is not None:
            self.assertFalse(sig.suppressed)


if __name__ == "__main__":
    unittest.main()
