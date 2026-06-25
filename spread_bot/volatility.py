"""波动率分析（纯函数，stdlib only）。

ATR / 布林带 / 历史波动率。所有周期以「分钟根数」计；跨日序列中，隔夜/午休跳空
在 TR 与 HV 里显式中和，避免污染指标。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence

from .quotes import MinuteKline


@dataclass
class VolatilityProfile:
    atr: float              # ATR（价格单位）
    atr_pct: float          # ATR / 末根 close * 100
    bb_upper: float
    bb_middle: float
    bb_lower: float
    bb_width_pct: float     # (upper-lower)/middle * 100
    bb_position: float      # (price-lower)/(upper-lower)，0~1；除零时 0.5
    hv_pct: float           # 最近 N 根分钟收益率标准差（%）
    regime: str             # 'high' | 'normal' | 'low'
    has_data: bool = True


def _is_new_session(prev_ts: str, ts: str) -> bool:
    """是否跨到新交易时段：日期不同，或午休跨越（11:3x → 13:0x）。"""
    if not prev_ts:
        return True
    if prev_ts[:10] != ts[:10]:
        return True
    # 同日午休跨越：prev<=11:3x 且 ts>=13:0x
    ph, pm = prev_ts[11:13], prev_ts[14:16]
    th, tm = ts[11:13], ts[14:16]
    if ph < "13" <= th:
        return True
    _ = pm, tm
    return False


def true_range(prev_close: Optional[float], high: float, low: float) -> float:
    """真实波幅。无 prev_close（新时段第一根）时退化为 high-low，避免跳空污染。"""
    if prev_close is None or prev_close <= 0:
        return high - low
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def compute_atr(klines: Sequence[MinuteKline], period: int = 14) -> Optional[float]:
    """ATR（TR 的简单移动平均；日内够用，注明非 Wilder 平滑）。"""
    if len(klines) < 2 or period <= 0:
        return None
    trs: List[float] = []
    for i, k in enumerate(klines):
        prev_close = klines[i - 1].close if i > 0 and not _is_new_session(klines[i - 1].ts, k.ts) else None
        trs.append(true_range(prev_close, k.high, k.low))
    window = trs[-period:] if len(trs) >= period else trs
    return sum(window) / len(window) if window else None


def compute_bollinger(
    closes: Sequence[float], period: int = 20, num_std: float = 2.0
) -> Optional[tuple]:
    """布林带 (upper, middle, lower)。middle=末段 SMA，bands=middle±num_std·σ（总体标准差）。"""
    n = len(closes)
    if n < period or period <= 0:
        return None
    window = closes[-period:]
    middle = sum(window) / period
    var = sum((v - middle) ** 2 for v in window) / period
    sd = math.sqrt(var)
    return middle + num_std * sd, middle, middle - num_std * sd


def compute_hv(klines: Sequence[MinuteKline], period: int = 60) -> Optional[float]:
    """历史波动率：最近 period 根分钟「百分比收益率」标准差（%）。

    剔除每个新交易日的第一个收益率（隔夜跳空），避免其污染标准差。
    """
    if len(klines) < 2 or period <= 0:
        return None
    rets: List[float] = []
    for i in range(1, len(klines)):
        if _is_new_session(klines[i - 1].ts, klines[i].ts):
            continue  # 跨日/跨午休的第一个收益率跳过
        prev = klines[i - 1].close
        cur = klines[i].close
        if prev <= 0:
            continue
        rets.append((cur / prev - 1.0) * 100.0)
    if len(rets) < 2:
        return None
    window = rets[-period:] if len(rets) >= period else rets
    mean = sum(window) / len(window)
    var = sum((r - mean) ** 2 for r in window) / len(window)
    return math.sqrt(var)


def build_profile(klines: Sequence[MinuteKline], cfg) -> Optional[VolatilityProfile]:
    """串起 ATR/布林/HV，判定 regime。数据不足（冷启动）返回 None。"""
    if not klines or len(klines) < 2:
        return None

    atr = compute_atr(klines, cfg.vol_atr_period)
    closes = [k.close for k in klines]
    bb = compute_bollinger(closes, cfg.vol_bb_period, cfg.vol_bb_std)
    hv = compute_hv(klines, cfg.vol_hv_period)
    if atr is None or bb is None or hv is None:
        return None

    upper, middle, lower = bb
    price = closes[-1]
    bb_width = (upper - lower) / middle * 100.0 if middle > 0 else 0.0
    bb_pos = (price - lower) / (upper - lower) if (upper - lower) > 1e-9 else 0.5
    bb_pos = max(0.0, min(1.0, bb_pos))

    if bb_width >= cfg.vol_high_bb_width_pct:
        regime = "high"
    elif bb_width <= cfg.vol_low_bb_width_pct:
        regime = "low"
    else:
        regime = "normal"

    return VolatilityProfile(
        atr=atr,
        atr_pct=atr / price * 100.0 if price > 0 else 0.0,
        bb_upper=upper,
        bb_middle=middle,
        bb_lower=lower,
        bb_width_pct=bb_width,
        bb_position=bb_pos,
        hv_pct=hv,
        regime=regime,
    )
