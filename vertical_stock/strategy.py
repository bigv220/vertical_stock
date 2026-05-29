from .models import MarketSnapshot, Position, Signal, SignalType, StrategyConfig, TradeState


class HighSellLowBuyStrategy:
    def __init__(self, config: StrategyConfig | None = None):
        self.config = config or StrategyConfig()

    def evaluate(self, position: Position, snapshot: MarketSnapshot, state: TradeState | None = None) -> Signal:
        state = state or TradeState()
        if state.sold_price and state.sold_quantity:
            return self._evaluate_buy_low(position, snapshot, state)
        return self._evaluate_sell_high(position, snapshot)

    def _evaluate_sell_high(self, position: Position, snapshot: MarketSnapshot) -> Signal:
        reasons = []
        gain = snapshot.current_price / snapshot.previous_close - 1
        vwap_premium = snapshot.current_price / snapshot.vwap - 1

        if not self.config.high_sell_start <= snapshot.timestamp <= self.config.high_sell_end:
            return self._hold(position, snapshot, "不在高抛观察时间")
        if not self.config.high_sell_min_gain <= gain <= self.config.high_sell_max_gain:
            return self._hold(position, snapshot, "涨幅未进入高抛区间")
        reasons.append(f"涨幅 {gain:.2%}")
        if vwap_premium < self.config.min_vwap_premium:
            return self._hold(position, snapshot, "相对 VWAP 乖离不足")
        reasons.append(f"高于 VWAP {vwap_premium:.2%}")
        if snapshot.volume_ratio < self.config.min_volume_ratio:
            return self._hold(position, snapshot, "量能未放大")
        reasons.append(f"量比 {snapshot.volume_ratio:.2f}")
        if snapshot.is_limit_up and not snapshot.failed_limit_up:
            return self._hold(position, snapshot, "强势封板不高抛")
        if snapshot.failed_limit_up:
            reasons.append("封板失败")
        if snapshot.current_price >= snapshot.previous_high * (1 - self.config.support_tolerance):
            reasons.append("接近昨日高点压力")

        quantity = self._round_lot(min(position.available_quantity, int(position.quantity * position.high_sell_ratio)))
        if quantity <= 0:
            return self._hold(position, snapshot, "无可卖仓位")
        return Signal(SignalType.SELL_HIGH, position.code, position.name, quantity, snapshot.current_price, tuple(reasons))

    def _evaluate_buy_low(self, position: Position, snapshot: MarketSnapshot, state: TradeState) -> Signal:
        blocked = self._risk_blocks(snapshot)
        if blocked:
            return self._hold(position, snapshot, "；".join(blocked))

        reasons = []
        drop_from_sell = snapshot.current_price / state.sold_price - 1
        near_support = self._near_any_support(snapshot)

        if not -self.config.low_buy_max_drop <= drop_from_sell <= -self.config.low_buy_min_drop and not near_support:
            return self._hold(position, snapshot, "未达到低吸回落或支撑条件")
        if -self.config.low_buy_max_drop <= drop_from_sell <= -self.config.low_buy_min_drop:
            reasons.append(f"较卖点回落 {-drop_from_sell:.2%}")
        if near_support:
            reasons.append("接近 VWAP/分时均线/昨收支撑")

        quantity = self._round_lot(min(state.sold_quantity, int(position.quantity * position.low_buy_ratio)))
        if quantity <= 0:
            return self._hold(position, snapshot, "无待回补仓位")
        return Signal(SignalType.BUY_LOW, position.code, position.name, quantity, snapshot.current_price, tuple(reasons))

    def _risk_blocks(self, snapshot: MarketSnapshot) -> list[str]:
        blocks = []
        if snapshot.market_change <= self.config.max_market_drop:
            blocks.append("大盘单边走弱")
        if snapshot.sector_change <= self.config.max_sector_drop:
            blocks.append("板块走弱")
        if snapshot.current_price < snapshot.previous_low:
            blocks.append("跌破昨日低点")
        if snapshot.heavy_drop:
            blocks.append("放量下跌")
        return blocks

    def _near_any_support(self, snapshot: MarketSnapshot) -> bool:
        supports = (snapshot.vwap, snapshot.intraday_ma, snapshot.previous_close)
        return any(abs(snapshot.current_price / support - 1) <= self.config.support_tolerance for support in supports)

    def _hold(self, position: Position, snapshot: MarketSnapshot, reason: str) -> Signal:
        return Signal(SignalType.HOLD, position.code, position.name, price=snapshot.current_price, reasons=(reason,))

    def _round_lot(self, quantity: int) -> int:
        return quantity // 100 * 100
