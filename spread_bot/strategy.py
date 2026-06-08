"""网格做价差策略引擎。

核心思想：以基准价为中心，按固定百分比划分网格。
  - 价格上穿一格 → 高抛（卖出一份），在高位兑现筹码；
  - 价格下穿一格 → 低吸（买回一份），在低位补回筹码。
被套个股通过反复高抛低吸赚取格差，从而摊低持仓成本。

为避免在格线附近因微小波动反复触发，采用「整格回滞」：必须完整越过相邻一格
才触发，且支持一次跨多格。同时统计同向连续格数，识别单边行情并预警
（单边市场会使做价差失效——踏空或越买越套）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

from .config import StockConfig
from .quotes import Quote


class Action(str, Enum):
    SELL = "高抛"
    BUY = "低吸"


@dataclass
class Signal:
    code: str
    name: str
    action: Action
    price: float
    grids: int            # 本次触发跨越的格数
    shares: int           # 建议交易股数
    level_from: int       # 原档位
    level_to: int         # 新档位
    base_price: float
    step: float           # 每格价格
    grid_step_pct: float
    next_sell: float      # 触发后，下一档高抛价
    next_buy: float       # 触发后，下一档低吸价
    streak: int           # 同向连续格数（正=上行，负=下行）
    reversal: bool = False  # 是否方向反转（意味着完成一轮价差）
    out_of_range: bool = False
    note: str = ""
    strategy: str = "grid"
    confluence_score: int = 0
    confluence_reasons: List[str] = field(default_factory=list)
    vwap: Optional[float] = None
    reference_level: Optional[float] = None


@dataclass
class GridStatus:
    """非交易动作的当前网格态势，用于状态卡展示。"""
    base_price: float
    step: float
    level: int
    next_sell: float
    next_buy: float
    to_sell_pct: float
    to_buy_pct: float


def _init_state(cfg: StockConfig, q: Quote, st: Dict) -> bool:
    """初始化基准价与档位。返回是否「显式锚定」（首次即可触发）。"""
    if cfg.base_price:
        st["base_price"] = float(cfg.base_price)
        st["last_level"] = 0
        st.setdefault("streak", 0)
        return True
    base = q.prev_close or q.price
    step = base * cfg.grid_step_pct / 100.0
    st["base_price"] = base
    st["last_level"] = round((q.price - base) / step) if step else 0
    st.setdefault("streak", 0)
    return False


def evaluate(cfg: StockConfig, q: Quote, st: Dict) -> Tuple[Optional[Signal], Dict]:
    """评估单只股票，返回（信号或 None，更新后的状态）。"""
    explicit_anchor = False
    if "last_level" not in st:
        explicit_anchor = _init_state(cfg, q, st)
        if not explicit_anchor:
            # 自动锚定：首次仅记录基准，不立即触发
            return None, st
    elif cfg.base_price and abs(st.get("base_price", 0.0) - float(cfg.base_price)) > 1e-9:
        # 配置中的显式基准价已变更 → 以新中枢重新锚定网格
        st["base_price"] = float(cfg.base_price)
        st["last_level"] = 0
        st["streak"] = 0

    base = st["base_price"]
    step = base * cfg.grid_step_pct / 100.0
    if step <= 0:
        return None, st

    last = st["last_level"]
    level = (q.price - base) / step

    if q.price >= base + (last + 1) * step:           # 上穿至少一整格 → 高抛
        new_level = math.floor(level + 1e-9)
        action, grids = Action.SELL, new_level - last
    elif q.price <= base + (last - 1) * step:         # 下穿至少一整格 → 低吸
        new_level = math.ceil(level - 1e-9)
        action, grids = Action.BUY, last - new_level
    else:
        return None, st                               # 仍在当前格内，不动作

    if grids <= 0:
        st["last_level"] = new_level
        return None, st

    # —— 同向连续格数统计 & 单边预警 ——
    prev_streak = st.get("streak", 0)
    if action == Action.SELL:
        streak = prev_streak + grids if prev_streak >= 0 else grids
        reversal = prev_streak < 0
    else:
        streak = prev_streak - grids if prev_streak <= 0 else -grids
        reversal = prev_streak > 0
    st["streak"] = streak

    notes = []
    if reversal:
        notes.append("✅ 方向反转，完成一轮高抛低吸，价差落袋")
    if action == Action.SELL and streak >= cfg.trend_alert_grids:
        notes.append(
            f"⚠️ 已连续 {streak} 格上行，疑似单边上涨，谨防踏空——可保留底仓、放缓高抛"
        )
    if action == Action.BUY and -streak >= cfg.trend_alert_grids:
        notes.append(
            f"⚠️ 已连续 {-streak} 格下行，疑似单边下跌，谨防越买越套——建议控制仓位、放缓低吸"
        )

    # —— 网格上下限保护 ——
    out_of_range = False
    if cfg.upper_limit_pct is not None:
        upper_level = cfg.upper_limit_pct / cfg.grid_step_pct
        if action == Action.SELL and new_level > upper_level:
            out_of_range = True
            notes.append("📈 已超网格上限，建议停止高抛、保留剩余仓位")
    if cfg.lower_limit_pct is not None:
        lower_level = cfg.lower_limit_pct / cfg.grid_step_pct
        if action == Action.BUY and new_level < lower_level:
            out_of_range = True
            notes.append("📉 已触网格下限，建议停止低吸、严控风险")

    st["last_level"] = new_level
    st["last_signal_time"] = q.time
    st["signal_count"] = st.get("signal_count", 0) + 1
    if reversal:
        st["round_trips"] = st.get("round_trips", 0) + 1

    signal = Signal(
        code=q.code,
        name=cfg.name or q.name,
        action=action,
        price=q.price,
        grids=grids,
        shares=grids * cfg.trade_shares,
        level_from=last,
        level_to=new_level,
        base_price=base,
        step=step,
        grid_step_pct=cfg.grid_step_pct,
        next_sell=base + (new_level + 1) * step,
        next_buy=base + (new_level - 1) * step,
        streak=streak,
        reversal=reversal,
        out_of_range=out_of_range,
        note="；".join(notes),
    )
    return signal, st


def grid_status(cfg: StockConfig, q: Quote, st: Dict) -> GridStatus:
    """计算当前网格态势（不改变状态），用于状态查看。"""
    base = st.get("base_price") or (cfg.base_price or q.prev_close or q.price)
    step = base * cfg.grid_step_pct / 100.0
    if "last_level" in st:
        level = st["last_level"]
    elif cfg.base_price:
        level = 0
    else:
        level = round((q.price - base) / step) if step else 0
    next_sell = base + (level + 1) * step
    next_buy = base + (level - 1) * step
    return GridStatus(
        base_price=base,
        step=step,
        level=level,
        next_sell=next_sell,
        next_buy=next_buy,
        to_sell_pct=(next_sell / q.price - 1) * 100 if q.price else 0.0,
        to_buy_pct=(next_buy / q.price - 1) * 100 if q.price else 0.0,
    )
