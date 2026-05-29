import unittest
from datetime import time

from vertical_stock.models import MarketSnapshot, Position, SignalType, TradeState
from vertical_stock.strategy import HighSellLowBuyStrategy


def make_position() -> Position:
    return Position(code="000001", name="示例股票", quantity=10000, available_quantity=6000, cost_price=9.5)


def make_snapshot(**overrides) -> MarketSnapshot:
    data = dict(
        code="000001",
        current_price=10.35,
        previous_close=10.0,
        open_price=10.05,
        vwap=10.15,
        intraday_ma=10.18,
        day_high=10.48,
        day_low=10.02,
        previous_high=10.4,
        previous_low=9.8,
        volume_ratio=1.8,
        market_change=0.001,
        sector_change=0.003,
        failed_limit_up=True,
        timestamp=time(9, 50),
    )
    data.update(overrides)
    return MarketSnapshot(**data)


class HighSellLowBuyStrategyTest(unittest.TestCase):
    def test_sell_high_signal_when_morning_spike_with_volume(self) -> None:
        signal = HighSellLowBuyStrategy().evaluate(make_position(), make_snapshot())

        self.assertEqual(signal.type, SignalType.SELL_HIGH)
        self.assertEqual(signal.quantity, 2000)

    def test_buy_low_signal_after_sell_and_pullback(self) -> None:
        snapshot = make_snapshot(current_price=10.12, vwap=10.15, intraday_ma=10.12)
        state = TradeState(sold_price=10.35, sold_quantity=2000)

        signal = HighSellLowBuyStrategy().evaluate(make_position(), snapshot, state)

        self.assertEqual(signal.type, SignalType.BUY_LOW)
        self.assertEqual(signal.quantity, 2000)

    def test_hold_when_market_risk_blocks_buy_low(self) -> None:
        snapshot = make_snapshot(current_price=10.12, market_change=-0.02)
        state = TradeState(sold_price=10.35, sold_quantity=2000)

        signal = HighSellLowBuyStrategy().evaluate(make_position(), snapshot, state)

        self.assertEqual(signal.type, SignalType.HOLD)
        self.assertEqual(signal.reasons, ("大盘单边走弱",))


if __name__ == "__main__":
    unittest.main()
