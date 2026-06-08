"""实时行情抓取：腾讯财经（主）+ 新浪财经（备）。

均为 A股免费行情接口，返回最新价、昨收、涨跌幅、最高/最低等。
非交易时段返回最近一个交易日的收盘数据，可用于联调与状态查看。
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from typing import Dict, List, Optional

from .market import price_limit_pct, secu_id

_TIMEOUT = 8
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; spread-bot/1.0)"}


@dataclass
class Quote:
    code: str          # 6 位代码，如 300763
    name: str          # 股票名称
    price: float       # 最新价（非交易时段为最近收盘价）
    prev_close: float  # 昨收价
    open: float        # 今开
    high: float        # 当日最高
    low: float         # 当日最低
    limit_up: float    # 涨停价
    limit_down: float  # 跌停价
    time: str          # 行情时间
    source: str        # 数据来源

    @property
    def change(self) -> float:
        return self.price - self.prev_close

    @property
    def change_pct(self) -> float:
        if self.prev_close <= 0:
            return 0.0
        return (self.price - self.prev_close) / self.prev_close * 100.0


@dataclass
class MinuteBar:
    """分时数据点（腾讯财经三文件格式）。"""
    time: str          # HHMM 或 HH:MM
    price: float       # 该分钟收盘价
    avg_price: float   # 该分钟均价
    volume: float      # 该分钟成交量（手）
    amount: float      # 该分钟成交额（元）

    @property
    def vwap(self) -> Optional[float]:
        """当日累计 VWAP 近似值。"""
        if self.avg_price > 0:
            return self.avg_price
        if self.volume > 0 and self.amount > 0:
            return self.amount / (self.volume * 100)
        return None


def _http_get(url: str, headers: Optional[dict] = None) -> str:
    req = urllib.request.Request(url, headers={**_HEADERS, **(headers or {})})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        # 两家接口均为 GBK 编码
        return resp.read().decode("gbk", errors="ignore")


def fetch_tencent(codes: List[str]) -> Dict[str, Quote]:
    """腾讯财经：一次请求可拉取多只股票，字段最全（含涨跌停价）。"""
    if not codes:
        return {}
    ids = ",".join(secu_id(c) for c in codes)
    raw = _http_get(f"https://qt.gtimg.cn/q={ids}")
    out: Dict[str, Quote] = {}
    for line in raw.split(";"):
        line = line.strip()
        if "=" not in line:
            continue
        payload = line.split("=", 1)[1].strip().strip('"')
        f = payload.split("~")
        if len(f) < 35 or not f[3]:
            continue
        try:
            q = Quote(
                code=f[2],
                name=f[1],
                price=float(f[3]),
                prev_close=float(f[4]),
                open=float(f[5]),
                high=float(f[33]),
                low=float(f[34]),
                limit_up=float(f[47]) if len(f) > 47 and f[47] else 0.0,
                limit_down=float(f[48]) if len(f) > 48 and f[48] else 0.0,
                time=f[30],
                source="tencent",
            )
        except (ValueError, IndexError):
            continue
        out[q.code] = q
    return out


def fetch_sina(codes: List[str]) -> Dict[str, Quote]:
    """新浪财经备用源；需带 Referer，且不直接提供涨跌停价（按板块推算）。"""
    if not codes:
        return {}
    ids = ",".join(secu_id(c) for c in codes)
    raw = _http_get(
        f"https://hq.sinajs.cn/list={ids}",
        headers={"Referer": "https://finance.sina.com.cn"},
    )
    out: Dict[str, Quote] = {}
    for line in raw.split(";"):
        line = line.strip()
        if 'hq_str_' not in line or '="' not in line:
            continue
        head, payload = line.split('="', 1)
        code = head.split("hq_str_")[1][2:].strip()  # 去掉 sh/sz 前缀
        f = payload.strip().strip('"').split(",")
        if len(f) < 32 or not f[3] or float(f[3]) == 0:
            continue
        try:
            prev_close = float(f[2])
            lim = price_limit_pct(code) / 100.0
            q = Quote(
                code=code,
                name=f[0],
                price=float(f[3]),
                prev_close=prev_close,
                open=float(f[1]),
                high=float(f[4]),
                low=float(f[5]),
                limit_up=round(prev_close * (1 + lim), 2),
                limit_down=round(prev_close * (1 - lim), 2),
                time=f"{f[30]} {f[31]}",
                source="sina",
            )
        except (ValueError, IndexError):
            continue
        out[q.code] = q
    return out


def fetch_minute_bars(code: str) -> List[MinuteBar]:
    """拉取腾讯当日分时线，用于趋势线、支阻与 VWAP 共振判断。"""
    try:
        raw = _http_get(f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={secu_id(code)}")
        payload = json.loads(raw)
        node = payload.get("data", {}).get(secu_id(code), {}).get("data", {})
        rows = node.get("data") or []
    except Exception:
        return []

    bars: List[MinuteBar] = []
    for row in rows:
        if isinstance(row, str):
            f = row.split()
        elif isinstance(row, list):
            f = [str(x) for x in row]
        else:
            continue
        if len(f) < 2:
            continue
        try:
            bars.append(MinuteBar(
                time=f[0],
                price=float(f[1]),
                avg_price=float(f[2]) if len(f) > 2 and f[2] else 0.0,
                volume=float(f[3]) if len(f) > 3 and f[3] else 0.0,
                amount=float(f[4]) if len(f) > 4 and f[4] else 0.0,
            ))
        except ValueError:
            continue
    return bars


def fetch_quotes(codes: List[str], source: str = "tencent") -> Dict[str, Quote]:
    """按配置选择数据源，主源失败时自动回退到另一个源。"""
    primary, backup = (fetch_tencent, fetch_sina)
    if source == "sina":
        primary, backup = backup, primary
    try:
        quotes = primary(codes)
        if quotes:
            return quotes
    except Exception:
        pass
    return backup(codes)
