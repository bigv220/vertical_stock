"""配置加载与校验（YAML）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import yaml


@dataclass
class StockConfig:
    code: str                              # 6 位股票代码
    name: str = ""                         # 名称（留空则用行情返回的名称）
    cost: Optional[float] = None           # 持仓成本，仅用于展示与摊薄提示
    base_price: Optional[float] = None     # 网格基准价；显式设置则首次即按其评估
    grid_step_pct: float = 3.0             # 每格价差百分比
    trade_shares: int = 100                # 每格建议交易股数
    upper_limit_pct: Optional[float] = None  # 网格上限（相对基准价 %），超出停止高抛
    lower_limit_pct: Optional[float] = None  # 网格下限（相对基准价 %），触及停止低吸
    trend_alert_grids: int = 3             # 连续同向 N 格触发单边市场预警
    strategy: str = "grid"                  # grid | confluence
    confluence_lookback: int = 120          # 共振策略使用的分时点数
    confluence_min_bars: int = 30           # 最少分时点数，不足则不触发
    confluence_min_score: int = 3           # 四要素中至少满足几项才触发
    confluence_cooldown_minutes: int = 30   # 同股信号冷却时间
    retracement_min_pct: float = 38.2       # 回撤/反抽下限
    retracement_max_pct: float = 61.8       # 回撤/反抽上限
    trendline_tolerance_pct: float = 1.0    # 趋势线附近容忍偏差
    sr_tolerance_pct: float = 1.0           # 支阻互换附近容忍偏差
    vwap_tolerance_pct: float = 0.5         # VWAP 附近容忍偏差


@dataclass
class AppConfig:
    webhook: str
    stocks: List[StockConfig] = field(default_factory=list)
    data_source: str = "tencent"           # tencent | sina
    poll_interval_seconds: int = 60        # loop 模式轮询间隔
    only_trading_hours: bool = True        # 仅在交易时段运行
    state_file: str = "state.json"
    feishu_secret: Optional[str] = None    # 飞书机器人「加签」密钥（可选）


def _opt_float(v) -> Optional[float]:
    return float(v) if v is not None and v != "" else None


def load_config(path: str) -> AppConfig:
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    if not raw.get("webhook"):
        raise ValueError("配置缺少 webhook（飞书自定义机器人地址）")

    stocks: List[StockConfig] = []
    for item in raw.get("stocks", []):
        if not item.get("code"):
            raise ValueError(f"股票配置缺少 code: {item}")
        stocks.append(
            StockConfig(
                code=str(item["code"]).strip(),
                name=item.get("name", ""),
                cost=_opt_float(item.get("cost")),
                base_price=_opt_float(item.get("base_price")),
                grid_step_pct=float(item.get("grid_step_pct", 3.0)),
                trade_shares=int(item.get("trade_shares", 100)),
                upper_limit_pct=_opt_float(item.get("upper_limit_pct")),
                lower_limit_pct=_opt_float(item.get("lower_limit_pct")),
                trend_alert_grids=int(item.get("trend_alert_grids", 3)),
                strategy=str(item.get("strategy", "grid")).strip().lower(),
                confluence_lookback=int(item.get("confluence_lookback", 120)),
                confluence_min_bars=int(item.get("confluence_min_bars", 30)),
                confluence_min_score=int(item.get("confluence_min_score", 3)),
                confluence_cooldown_minutes=int(item.get("confluence_cooldown_minutes", 30)),
                retracement_min_pct=float(item.get("retracement_min_pct", 38.2)),
                retracement_max_pct=float(item.get("retracement_max_pct", 61.8)),
                trendline_tolerance_pct=float(item.get("trendline_tolerance_pct", 1.0)),
                sr_tolerance_pct=float(item.get("sr_tolerance_pct", 1.0)),
                vwap_tolerance_pct=float(item.get("vwap_tolerance_pct", 0.5)),
            )
        )
    if not stocks:
        raise ValueError("配置中没有任何股票（stocks 为空）")

    return AppConfig(
        webhook=raw["webhook"],
        stocks=stocks,
        data_source=raw.get("data_source", "tencent"),
        poll_interval_seconds=int(raw.get("poll_interval_seconds", 60)),
        only_trading_hours=bool(raw.get("only_trading_hours", True)),
        state_file=raw.get("state_file", "state.json"),
        feishu_secret=raw.get("feishu_secret") or None,
    )
