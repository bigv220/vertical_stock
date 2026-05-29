from .data_provider import MockDataProvider
from .models import Position
from .notifier import ConsoleNotifier
from .strategy import HighSellLowBuyStrategy


def main() -> None:
    positions = [Position(code="000001", name="示例股票", quantity=10000, available_quantity=6000, cost_price=9.5)]
    provider = MockDataProvider()
    strategy = HighSellLowBuyStrategy()
    notifier = ConsoleNotifier()

    for position in positions:
        snapshot = provider.get_snapshot(position.code)
        signal = strategy.evaluate(position, snapshot)
        notifier.send(signal)


if __name__ == "__main__":
    main()
