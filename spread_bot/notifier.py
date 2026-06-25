"""飞书自定义机器人通知（交互式卡片）。

支持「加签」校验（可选）。卡片按信号类型着色：高抛=红，低吸=绿，混合=橙。
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import time
import urllib.request
from typing import Dict, List, Optional, Tuple

from .config import StockConfig
from .quotes import Quote
from .strategy import Action, GridStatus, Signal

_TIMEOUT = 8


class FeishuNotifier:
    def __init__(self, webhook: str, secret: Optional[str] = None):
        self.webhook = webhook
        self.secret = secret

    def _sign(self, ts: int) -> str:
        string_to_sign = f"{ts}\n{self.secret}"
        digest = hmac.new(string_to_sign.encode("utf-8"), b"", hashlib.sha256).digest()
        return base64.b64encode(digest).decode("utf-8")

    def send(self, payload: dict) -> dict:
        if self.secret:
            ts = int(time.time())
            payload = {**payload, "timestamp": str(ts), "sign": self._sign(ts)}
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.webhook, data=data,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            body = resp.read().decode("utf-8")
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"raw": body}

    def send_card(self, title: str, template: str, elements: list) -> dict:
        return self.send({
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {
                    "title": {"tag": "plain_text", "content": title},
                    "template": template,
                },
                "elements": elements,
            },
        })


def is_ok(resp: dict) -> bool:
    return resp.get("code") == 0 or resp.get("StatusCode") == 0 or resp.get("msg") == "success"


def _now() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _md(content: str) -> dict:
    return {"tag": "div", "text": {"tag": "lark_md", "content": content}}


def _hr() -> dict:
    return {"tag": "hr"}


def _note(text: str) -> dict:
    return {"tag": "note", "elements": [{"tag": "plain_text", "content": text}]}


def _cost_line(cfg: StockConfig, price: float) -> str:
    if cfg.cost is None or cfg.cost <= 0:
        return ""
    diff = (price / cfg.cost - 1) * 100
    tag = "盈" if diff >= 0 else "亏"
    return f"　持仓成本 **{cfg.cost:.2f}**（现价较成本 {diff:+.1f}%，浮{tag}）\n"


def build_signal_card(items: List[Tuple[Signal, Quote, StockConfig]]) -> Tuple[str, str, list]:
    """items: [(signal, quote, stock_cfg), ...] → (title, template, elements)。"""
    actions = {sig.action for sig, _, _ in items}
    if actions == {Action.SELL}:
        template = "red"
    elif actions == {Action.BUY}:
        template = "green"
    else:
        template = "orange"
    has_confluence = any(sig.strategy == "confluence" for sig, _, _ in items)
    title = f"📊 {'共振策略' if has_confluence else '高抛低吸'}提醒 · {len(items)} 条信号"

    elements: list = [_note(f"做价差信号 · {_now()}")]
    for sig, q, cfg in items:
        icon = "🔴 高抛" if sig.action == Action.SELL else "🟢 低吸"
        if sig.suppressed:
            icon = f"🚫（已压制）{icon}"
        verb = "卖出" if sig.action == Action.SELL else "买入"
        if sig.strategy == "confluence":
            reason_lines = "\n".join(f"　- {r}" for r in sig.confluence_reasons)
            extra = []
            if sig.vwap:
                extra.append(f"VWAP {sig.vwap:.2f}")
            if sig.reference_level:
                extra.append(f"参考位 {sig.reference_level:.2f}")
            if sig.vol_regime:
                extra.append(f"波动 {sig.vol_regime}")
            if sig.divergence_tag:
                extra.append(sig.divergence_tag)
            block = (
                f"**{icon} | {sig.name}（{sig.code}）**\n"
                f"　现价 **{sig.price:.2f}**（较昨收 {q.change_pct:+.2f}%）\n"
                f"　建议{verb} **{sig.shares}** 股 ≈ **{sig.price * sig.shares:,.0f}** 元\n"
                f"　共振评分 **{sig.confluence_score}**"
                + (f"｜{'｜'.join(extra)}" if extra else "") + "\n"
                f"{_cost_line(cfg, sig.price)}"
                f"{reason_lines}"
            )
        else:
            block = (
                f"**{icon} | {sig.name}（{sig.code}）**\n"
                f"　现价 **{sig.price:.2f}**（较昨收 {q.change_pct:+.2f}%）\n"
                f"　建议{verb} **{sig.shares}** 股 ≈ **{sig.price * sig.shares:,.0f}** 元"
                f"（{sig.grids} 格 × {cfg.trade_shares} 股）\n"
                f"　网格：基准 {sig.base_price:.2f}｜每格 {sig.grid_step_pct:.1f}%"
                f"（{sig.step:.2f} 元）｜第 {sig.level_from:+d} → {sig.level_to:+d} 格\n"
                f"{_cost_line(cfg, sig.price)}"
                f"　下一高抛 **{sig.next_sell:.2f}**　下一低吸 **{sig.next_buy:.2f}**"
            )
        elements.append(_md(block))
        if sig.note:
            elements.append(_note(sig.note))
        elements.append(_hr())
    elements.append(_note("⚠️ 本提醒为量化策略信号，非投资建议；共振只提高胜率，不保证方向，注意仓位与止损。"))
    return title, template, elements


def build_status_card(rows: List[Tuple[StockConfig, Quote, GridStatus]]) -> Tuple[str, str, list]:
    """当前网格态势总览（不含交易动作）。"""
    title = f"🧭 网格态势总览 · {len(rows)} 只"
    elements: list = [_note(f"状态快照 · {_now()}")]
    for cfg, q, gs in rows:
        cost = _cost_line(cfg, q.price)
        block = (
            f"**{cfg.name or q.name}（{cfg.code}）　现价 {q.price:.2f}（{q.change_pct:+.2f}%）**\n"
            f"　基准 {gs.base_price:.2f}｜每格 {cfg.grid_step_pct:.1f}%｜当前第 {gs.level:+d} 格\n"
            f"{cost}"
            f"　↑ 高抛触发价 **{gs.next_sell:.2f}**（还需 {gs.to_sell_pct:+.2f}%）\n"
            f"　↓ 低吸触发价 **{gs.next_buy:.2f}**（还需 {gs.to_buy_pct:+.2f}%）"
        )
        elements.append(_md(block))
        elements.append(_hr())
    elements.append(_note("数据来源：腾讯/新浪财经｜仅供参考，不构成投资建议。"))
    return title, "blue", elements
