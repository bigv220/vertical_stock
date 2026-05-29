from datetime import time

from .models import MarketSnapshot


class MockDataProvider:
    def get_snapshot(self, code: str) -> MarketSnapshot:
        return MarketSnapshot(
            code=code,
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
