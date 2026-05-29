from dataclasses import dataclass, field
from datetime import time
from enum import Enum


class SignalType(str, Enum):
    SELL_HIGH = "sell_high"
    BUY_LOW = "buy_low"
    HOLD = "hold"


@dataclass(frozen=True)
class Position:
    code: str
    name: str
    quantity: int
    available_quantity: int
    cost_price: float
    high_sell_ratio: float = 0.2
    low_buy_ratio: float = 0.2


@dataclass(frozen=True)
class StrategyConfig:
    high_sell_start: time = time(9, 35)
    high_sell_end: time = time(10, 30)
    high_sell_min_gain: float = 0.02
    high_sell_max_gain: float = 0.05
    min_vwap_premium: float = 0.01
    min_volume_ratio: float = 1.5
    low_buy_min_drop: float = 0.015
    low_buy_max_drop: float = 0.03
    max_market_drop: float = -0.01
    max_sector_drop: float = -0.015
    support_tolerance: float = 0.003


@dataclass(frozen=True)
class MarketSnapshot:
    code: str
    current_price: float
    previous_close: float
    open_price: float
    vwap: float
    intraday_ma: float
    day_high: float
    day_low: float
    previous_high: float
    previous_low: float
    volume_ratio: float
    market_change: float
    sector_change: float
    is_limit_up: bool = False
    failed_limit_up: bool = False
    heavy_drop: bool = False
    timestamp: time = time(9, 35)


@dataclass(frozen=True)
class TradeState:
    sold_price: float | None = None
    sold_quantity: int = 0


@dataclass(frozen=True)
class Signal:
    type: SignalType
    code: str
    name: str
    quantity: int = 0
    price: float = 0.0
    reasons: tuple[str, ...] = field(default_factory=tuple)
