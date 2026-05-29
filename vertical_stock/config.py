import tomllib
from pathlib import Path

from .models import Position, StrategyConfig


def load_positions(path: str | Path) -> list[Position]:
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    return [Position(**item) for item in data.get("positions", [])]


def load_strategy_config(path: str | Path) -> StrategyConfig:
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    values = data.get("strategy", {})
    return StrategyConfig(**values)
