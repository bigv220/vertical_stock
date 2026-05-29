from .models import Signal, SignalType


class ConsoleNotifier:
    def send(self, signal: Signal) -> None:
        if signal.type == SignalType.HOLD:
            print(f"HOLD {signal.code} {signal.name} @{signal.price:.2f}: {'；'.join(signal.reasons)}")
            return
        action = "高抛" if signal.type == SignalType.SELL_HIGH else "低吸"
        print(f"{action}提醒 {signal.code} {signal.name} {signal.quantity}股 @{signal.price:.2f}: {'；'.join(signal.reasons)}")
