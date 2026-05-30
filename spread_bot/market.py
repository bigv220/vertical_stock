"""市场相关工具：交易所推断、涨跌幅限制、交易时段判断。"""
from __future__ import annotations

import datetime as dt
from typing import Optional


def market_prefix(code: str) -> str:
    """根据 6 位股票代码推断交易所前缀：sh / sz / bj。"""
    c = code.strip()
    if c[:3] in ("600", "601", "603", "605", "688", "689", "900"):
        return "sh"
    if c[:3] in ("000", "001", "002", "003", "300", "301", "200"):
        return "sz"
    if c[:2] in ("43", "83", "87", "88", "92"):
        return "bj"
    # 兜底：ETF / 其它，按首位粗判
    if c[:1] in ("5", "6", "9"):
        return "sh"
    return "sz"


def secu_id(code: str) -> str:
    """返回带交易所前缀的证券标识，如 sz300763。"""
    return f"{market_prefix(code)}{code.strip()}"


def price_limit_pct(code: str) -> float:
    """该标的的涨跌幅限制（百分比）。ST 股 5% 无法仅凭代码判断，按板块默认值返回。"""
    c = code.strip()
    if c[:2] in ("30", "68"):  # 创业板 / 科创板
        return 20.0
    if c[:2] in ("43", "83", "87", "88", "92"):  # 北交所
        return 30.0
    return 10.0  # 沪深主板


# A股连续竞价交易时段（北京时间）
_MORNING = (dt.time(9, 30), dt.time(11, 30))
_AFTERNOON = (dt.time(13, 0), dt.time(15, 0))


def is_trading_time(now: Optional[dt.datetime] = None) -> bool:
    """是否处于 A股交易时段（不含法定节假日，部署时建议结合交易日历）。"""
    now = now or dt.datetime.now()
    if now.weekday() >= 5:  # 周六、周日
        return False
    t = now.time()
    return (_MORNING[0] <= t <= _MORNING[1]) or (_AFTERNOON[0] <= t <= _AFTERNOON[1])
