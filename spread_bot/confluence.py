"""Retracement + 趋势线 + 支阻互换 + VWAP 共振策略。"""
from __future__ import annotations

import datetime as dt
from typing import Dict, List, Optional, Sequence, Tuple

from .config import StockConfig
from .quotes import MinuteBar, Quote
from .strategy import Action, Signal


Pivot = Tuple[int, float]


def _pct_diff(a: float, b: float) -> float:
    if b <= 0:
        return 0.0
    return (a / b - 1) * 100.0


def _near(price: float, level: float, tolerance_pct: float) -> bool:
    return abs(_pct_diff(price, level)) <= tolerance_pct


def _last_vwap(bars: Sequence[MinuteBar]) -> Optional[float]:
    for bar in reversed(bars):
        if bar.vwap and bar.vwap > 0:
            return bar.vwap
    amount = sum(b.amount for b in bars if b.amount > 0)
    volume = sum(b.volume for b in bars if b.volume > 0)
    if amount > 0 and volume > 0:
        # A股成交量常以「手」计，1 手 = 100 股。
        return amount / (volume * 100)
    return None


def _pivots(values: Sequence[float], window: int = 3) -> Tuple[List[Pivot], List[Pivot]]:
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


def _line_value(points: Sequence[Pivot], last_idx: int) -> Optional[float]:
    if len(points) < 2:
        return None
    p1, p2 = points[-2], points[-1]
    if p2[0] == p1[0]:
        return None
    slope = (p2[1] - p1[1]) / (p2[0] - p1[0])
    return p2[1] + slope * (last_idx - p2[0])


def _swing(values: Sequence[float]) -> Tuple[int, float, int, float]:
    low_idx = min(range(len(values)), key=lambda i: values[i])
    high_idx = max(range(len(values)), key=lambda i: values[i])
    return low_idx, values[low_idx], high_idx, values[high_idx]


def _find_buy_flip(price: float, highs: Sequence[Pivot], values: Sequence[float], tolerance_pct: float) -> Optional[float]:
    for idx, level in reversed(highs[:-1]):
        broke_above = any(v >= level * (1 + tolerance_pct / 100.0) for v in values[idx + 1:])
        if broke_above and price >= level * (1 - tolerance_pct / 100.0) and _near(price, level, tolerance_pct):
            return level
    return None


def _find_sell_flip(price: float, lows: Sequence[Pivot], values: Sequence[float], tolerance_pct: float) -> Optional[float]:
    for idx, level in reversed(lows[:-1]):
        broke_below = any(v <= level * (1 - tolerance_pct / 100.0) for v in values[idx + 1:])
        if broke_below and price <= level * (1 + tolerance_pct / 100.0) and _near(price, level, tolerance_pct):
            return level
    return None


def _cooling_down(cfg: StockConfig, q: Quote, st: Dict) -> bool:
    last_time = st.get("confluence_last_signal_time")
    if not last_time or cfg.confluence_cooldown_minutes <= 0:
        return False
    try:
        prev = dt.datetime.strptime(last_time[:19], "%Y-%m-%d %H:%M:%S")
        cur = dt.datetime.strptime(q.time[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return False
    return (cur - prev).total_seconds() < cfg.confluence_cooldown_minutes * 60


def _build_signal(
    cfg: StockConfig,
    q: Quote,
    action: Action,
    score: int,
    reasons: List[str],
    vwap: Optional[float],
    reference_level: Optional[float],
    st: Dict,
) -> Signal:
    direction = 1 if action == Action.SELL else -1
    st["confluence_last_action"] = action.value
    st["confluence_last_signal_time"] = q.time
    st["confluence_signal_count"] = st.get("confluence_signal_count", 0) + 1
    return Signal(
        code=q.code,
        name=cfg.name or q.name,
        action=action,
        price=q.price,
        grids=1,
        shares=cfg.trade_shares,
        level_from=0,
        level_to=direction,
        base_price=reference_level or vwap or q.prev_close or q.price,
        step=abs(q.price - (reference_level or vwap or q.price)),
        grid_step_pct=0.0,
        next_sell=q.price,
        next_buy=q.price,
        streak=0,
        note="；".join(reasons),
        strategy="confluence",
        confluence_score=score,
        confluence_reasons=reasons,
        vwap=vwap,
        reference_level=reference_level,
    )


def evaluate_confluence(
    cfg: StockConfig,
    q: Quote,
    st: Dict,
    bars: Optional[Sequence[MinuteBar]],
) -> Tuple[Optional[Signal], Dict]:
    """评估共振策略，至少满足 min_score 个条件才触发。"""
    if not bars or len(bars) < max(20, cfg.confluence_min_bars):
        return None, st
    if _cooling_down(cfg, q, st):
        return None, st

    recent = list(bars)[-cfg.confluence_lookback:]
    values = [b.price for b in recent if b.price > 0]
    if len(values) < max(20, cfg.confluence_min_bars):
        return None, st

    price = q.price or values[-1]
    vwap = _last_vwap(recent)
    highs, lows = _pivots(values)
    low_idx, swing_low, high_idx, swing_high = _swing(values)
    move = swing_high - swing_low
    if move <= 0:
        return None, st

    retr_min = cfg.retracement_min_pct / 100.0
    retr_max = cfg.retracement_max_pct / 100.0
    last_idx = len(values) - 1

    buy_reasons: List[str] = []
    buy_ref: Optional[float] = None
    if low_idx < high_idx:
        retraced = (swing_high - price) / move
        if retr_min <= retraced <= retr_max:
            buy_reasons.append(f"回撤 {retraced * 100:.0f}% 落在设定区间")
    trend_support = _line_value(lows, last_idx)
    if trend_support and price >= trend_support * (1 - cfg.trendline_tolerance_pct / 100.0) and _near(price, trend_support, cfg.trendline_tolerance_pct):
        buy_ref = trend_support
        buy_reasons.append(f"回踩上升趋势线 {trend_support:.2f} 附近")
    buy_flip = _find_buy_flip(price, highs, values, cfg.sr_tolerance_pct)
    if buy_flip:
        buy_ref = buy_flip
        buy_reasons.append(f"前压力 {buy_flip:.2f} 转支撑并回踩")
    if vwap and price >= vwap * (1 - cfg.vwap_tolerance_pct / 100.0):
        buy_reasons.append(f"价格守住 VWAP {vwap:.2f} 附近")

    sell_reasons: List[str] = []
    sell_ref: Optional[float] = None
    if high_idx < low_idx:
        rebound = (price - swing_low) / move
        if retr_min <= rebound <= retr_max:
            sell_reasons.append(f"反抽 {rebound * 100:.0f}% 落在设定区间")
    trend_resistance = _line_value(highs, last_idx)
    if trend_resistance and price <= trend_resistance * (1 + cfg.trendline_tolerance_pct / 100.0) and _near(price, trend_resistance, cfg.trendline_tolerance_pct):
        sell_ref = trend_resistance
        sell_reasons.append(f"反抽下降趋势线 {trend_resistance:.2f} 附近")
    sell_flip = _find_sell_flip(price, lows, values, cfg.sr_tolerance_pct)
    if sell_flip:
        sell_ref = sell_flip
        sell_reasons.append(f"前支撑 {sell_flip:.2f} 转压力并反抽")
    if vwap and price <= vwap * (1 + cfg.vwap_tolerance_pct / 100.0):
        sell_reasons.append(f"价格未有效站上 VWAP {vwap:.2f}")

    buy_score = len(buy_reasons)
    sell_score = len(sell_reasons)
    min_score = max(1, min(cfg.confluence_min_score, 4))
    if buy_score < min_score and sell_score < min_score:
        return None, st

    if buy_score >= sell_score:
        return _build_signal(cfg, q, Action.BUY, buy_score, buy_reasons, vwap, buy_ref, st), st
    return _build_signal(cfg, q, Action.SELL, sell_score, sell_reasons, vwap, sell_ref, st), st
