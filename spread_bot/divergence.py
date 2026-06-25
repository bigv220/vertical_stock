"""背离分析（纯函数，stdlib only）。

RSI 背离（价 HH/LL 但 RSI LH/HL）+ 量价背离（价 HH/LL 但峰值量缩）。
本模块带 _pivots 的私有副本，不依赖 confluence，保持可独立单测。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from .quotes import MinuteKline

Pivot = Tuple[int, float]


@dataclass
class DivergenceResult:
    rsi_bearish: bool = False   # 价 HH、RSI LH → 偏空 / 压制低吸
    rsi_bullish: bool = False   # 价 LL、RSI HL → 偏多 / 压制高抛
    pv_bearish: bool = False    # 价 HH、峰值量缩
    pv_bullish: bool = False    # 价 LL、峰值量缩
    rsi: float = 50.0
    has_data: bool = False
    note: str = ""

    @property
    def bearish(self) -> bool:
        return self.rsi_bearish or self.pv_bearish

    @property
    def bullish(self) -> bool:
        return self.rsi_bullish or self.pv_bullish

    @property
    def ambiguous(self) -> bool:
        return self.bearish and self.bullish


def compute_rsi(closes: Sequence[float], period: int = 14) -> Optional[List[float]]:
    """Wilder RSI 序列（对齐 closes，首 period-1 个为 None）。数据不足返回 None。"""
    n = len(closes)
    if n < period + 1 or period <= 0:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        ch = closes[i] - closes[i - 1]
        if ch >= 0:
            gains += ch
        else:
            losses -= ch
    avg_gain = gains / period
    avg_loss = losses / period
    out: List[Optional[float]] = [None] * (period + 1)
    out[period] = 100.0 - 100.0 / (1.0 + (avg_gain / avg_loss if avg_loss > 0 else float("inf")))
    for i in range(period + 1, n):
        ch = closes[i] - closes[i - 1]
        gain = ch if ch >= 0 else 0.0
        loss = -ch if ch < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        if avg_loss == 0:
            out.append(100.0)
        else:
            rs = avg_gain / avg_loss
            out.append(100.0 - 100.0 / (1.0 + rs))
    return out


def _pivots(values: Sequence[float], window: int = 3) -> Tuple[List[Pivot], List[Pivot]]:
    """局部极大/极小点。返回 (highs, lows)，每个为 (idx, value)。"""
    highs: List[Pivot] = []
    lows: List[Pivot] = []
    if len(values) < window * 2 + 1:
        return highs, lows
    for idx in range(window, len(values) - window):
        left = values[idx - window:idx]
        right = values[idx + 1:idx + window + 1]
        cur = values[idx]
        if cur >= max(left) and cur >= max(right):
            highs.append((idx, cur))
        if cur <= min(left) and cur <= min(right):
            lows.append((idx, cur))
    return highs, lows


def _meaningful(new: float, old: float, min_swing_pct: float) -> bool:
    """新极值相对旧极值是否有意义（幅差 ≥ min_swing_pct%）。"""
    if old <= 0:
        return True
    return abs(new / old - 1.0) * 100.0 >= min_swing_pct


def detect_rsi_divergence(
    klines: Sequence[MinuteKline],
    period: int,
    window: int,
    lookback: int,
    min_swing_pct: float,
) -> Tuple[bool, bool, float]:
    """返回 (rsi_bearish, rsi_bullish, last_rsi)。"""
    closes = [k.close for k in klines]
    rsi = compute_rsi(closes, period)
    if not rsi:
        return False, False, 50.0
    last_rsi = next((v for v in reversed(rsi) if v is not None), 50.0)

    series = closes[-lookback:] if len(closes) > lookback else closes[:]
    rsi_tail = rsi[-len(series):]
    highs, lows = _pivots(series, window)
    bearish = bullish = False

    if len(highs) >= 2:
        i1, v1 = highs[-2]
        i2, v2 = highs[-1]
        if v2 > v1 and _meaningful(v2, v1, min_swing_pct):
            r1 = rsi_tail[i1] if i1 < len(rsi_tail) else None
            r2 = rsi_tail[i2] if i2 < len(rsi_tail) else None
            if r1 is not None and r2 is not None and r2 < r1:
                bearish = True
    if len(lows) >= 2:
        i1, v1 = lows[-2]
        i2, v2 = lows[-1]
        if v2 < v1 and _meaningful(v2, v1, min_swing_pct):
            r1 = rsi_tail[i1] if i1 < len(rsi_tail) else None
            r2 = rsi_tail[i2] if i2 < len(rsi_tail) else None
            if r1 is not None and r2 is not None and r2 > r1:
                bullish = True
    return bearish, bullish, last_rsi


def detect_pv_divergence(
    klines: Sequence[MinuteKline],
    window: int,
    lookback: int,
    min_swing_pct: float,
) -> Tuple[bool, bool]:
    """返回 (pv_bearish, pv_bullish)。比较峰值量，单位无关。"""
    series = list(klines[-lookback:]) if len(klines) > lookback else list(klines)
    prices = [k.close for k in series]
    vols = [k.volume for k in series]
    highs, lows = _pivots(prices, window)
    bearish = bullish = False

    if len(highs) >= 2:
        i1, v1 = highs[-2]
        i2, v2 = highs[-1]
        if v2 > v1 and _meaningful(v2, v1, min_swing_pct):
            if vols[i2] < vols[i1]:
                bearish = True
    if len(lows) >= 2:
        i1, v1 = lows[-2]
        i2, v2 = lows[-1]
        if v2 < v1 and _meaningful(v2, v1, min_swing_pct):
            if vols[i2] < vols[i1]:
                bullish = True
    return bearish, bullish


def detect(klines: Sequence[MinuteKline], cfg) -> DivergenceResult:
    """串起 RSI 与量价背离。数据不足 → has_data=False 全 false；双向 → 视为无信号。"""
    res = DivergenceResult()
    min_needed = cfg.divergence_rsi_period + 1 + cfg.divergence_pivot_window * 2
    if not klines or len(klines) < min_needed:
        return res

    rsi_bear = rsi_bull = pv_bear = pv_bull = False
    last_rsi = 50.0
    if cfg.enable_rsi_divergence:
        rsi_bear, rsi_bull, last_rsi = detect_rsi_divergence(
            klines,
            cfg.divergence_rsi_period,
            cfg.divergence_pivot_window,
            cfg.divergence_lookback,
            cfg.divergence_min_swing_pct,
        )
    if cfg.enable_pv_divergence:
        pv_bear, pv_bull = detect_pv_divergence(
            klines,
            cfg.divergence_pivot_window,
            cfg.divergence_lookback,
            cfg.divergence_min_swing_pct,
        )

    res.rsi = last_rsi
    res.rsi_bearish = rsi_bear
    res.rsi_bullish = rsi_bull
    res.pv_bearish = pv_bear
    res.pv_bullish = pv_bull
    res.has_data = True

    # 同时双向（pivot 冲突）→ 视为无可靠信号，全置 false
    if res.ambiguous:
        res.rsi_bearish = res.rsi_bullish = res.pv_bearish = res.pv_bullish = False
        res.note = "多空背离同时出现，信号冲突，忽略"
        return res

    parts = []
    if res.bearish:
        parts.append("顶背离" + ("（RSI）" if rsi_bear else "") + ("（量价）" if pv_bear else ""))
    if res.bullish:
        parts.append("底背离" + ("（RSI）" if rsi_bull else "") + ("（量价）" if pv_bull else ""))
    res.note = "、".join(parts) if parts else ""
    return res
