#!/usr/bin/env python3
"""A股高抛低吸做价差通知机器人 —— 命令行入口。

用法：
  python main.py run      [--config config.yaml] [--dry-run] [--force]
  python main.py loop     [--config config.yaml]
  python main.py status   [--config config.yaml] [--notify]
  python main.py test     [--config config.yaml]

子命令：
  run     单次评估并推送信号（适合 cron 定时调用）
  loop    常驻循环，按 poll_interval_seconds 轮询（适合 systemd 守护）
  status  查看各标的当前网格态势；加 --notify 推送状态卡到飞书
  test    向飞书发送一条测试卡片，校验 webhook 配置
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time

from spread_bot.config import load_config
from spread_bot.confluence import evaluate_confluence
from spread_bot.divergence import detect as detect_divergence
from spread_bot.market import is_trading_time
from spread_bot.notifier import (
    FeishuNotifier,
    build_signal_card,
    build_status_card,
    is_ok,
)
from spread_bot.quotes import fetch_minute_bars, fetch_minute_klines, fetch_quotes
from spread_bot.state import load_state, save_state
from spread_bot.strategy import evaluate, grid_status
from spread_bot import store
from spread_bot.volatility import build_profile


def log(msg: str) -> None:
    print(f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def _resolve_paths(app, config_path: str) -> None:
    """状态文件/DB 若为相对路径，则相对配置文件所在目录解析，便于 cron。"""
    base = os.path.dirname(os.path.abspath(config_path))
    if not os.path.isabs(app.state_file):
        app.state_file = os.path.join(base, app.state_file)
    if not os.path.isabs(app.db_path):
        app.db_path = os.path.join(base, app.db_path)


def _date_from_quote(q) -> str:
    """从行情时间戳取交易日 'YYYY-MM-DD'；无法解析时用今天。"""
    raw = getattr(q, "time", "") or ""
    raw = raw.strip()
    if raw and raw[:4].isdigit():
        if len(raw) >= 8:
            return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
        if len(raw) >= 10 and raw[4] == "-":
            return raw[:10]
    return dt.datetime.now().strftime("%Y-%m-%d")


def cmd_run(app, args) -> int:
    if app.only_trading_hours and not args.force and not is_trading_time():
        log("当前非 A股交易时段，跳过本次评估（--force 可强制运行）。")
        return 0

    state = load_state(app.state_file)
    codes = [s.code for s in app.stocks]
    quotes = fetch_quotes(codes, app.data_source)
    if not quotes:
        log("未获取到任何行情，跳过。请检查网络或数据源。")
        return 1

    today = dt.datetime.now().strftime("%Y-%m-%d")
    conn = store.open_db(app.db_path) if app.store_history else None
    # 日切清理历史（每天最多一次）
    if conn and app.db_retention_days > 0 and state.get("last_prune_date") != today:
        for s in app.stocks:
            store.prune_old(conn, s.code, app.db_retention_days)
        state["last_prune_date"] = today

    items = []
    try:
        for cfg in app.stocks:
            q = quotes.get(cfg.code)
            if not q:
                log(f"未获取到行情：{cfg.code}（{cfg.name}）")
                continue
            if not cfg.name:
                cfg.name = q.name
            st = state.setdefault(cfg.code, {})

            # —— 拉取 K 线并落盘 ——
            klines = fetch_minute_klines(cfg.code, cfg.kline_lookback)
            if conn and klines:
                store.upsert_klines(conn, cfg.code, klines)
            if conn and klines:
                kline_series = store.get_recent_klines(conn, cfg.code, cfg.kline_lookback)
            else:
                kline_series = klines

            # —— 新鲜度守卫：K 线最后日期非今日且非 --force → 跳过（挡节假日漏判）——
            if (app.only_trading_hours and not args.force and kline_series
                    and kline_series[-1].ts[:10] != today):
                log(f"{cfg.name or cfg.code} 数据非本交易日（{kline_series[-1].ts[:10]}），跳过。")
                continue

            # —— 拉取分时线并落盘（日期取自 K 线，权威）——
            bars = fetch_minute_bars(cfg.code)
            if conn and bars:
                trading_date = klines[-1].ts[:10] if klines else _date_from_quote(q)
                store.upsert_bars(conn, cfg.code, bars, trading_date)
                bar_series = store.get_recent_bars(conn, cfg.code, cfg.confluence_lookback)
            else:
                bar_series = bars

            # —— 波动率 / 背离（每标的算一次，两种策略共用）——
            vol = build_profile(kline_series, cfg) if cfg.enable_volatility and kline_series else None
            div = detect_divergence(kline_series, cfg) if cfg.enable_divergence and kline_series else None

            if cfg.strategy == "confluence":
                if not bar_series:
                    log(f"未获取到分时线：{cfg.code}（{cfg.name}），跳过共振策略。")
                    continue
                signal, st = evaluate_confluence(cfg, q, st, bar_series,
                                                 klines=kline_series, vol_profile=vol, divergence=div)
            else:
                signal, st = evaluate(cfg, q, st, vol_profile=vol, divergence=div)
            state[cfg.code] = st

            if signal:
                if not signal.suppressed or app.notify_suppressed:
                    items.append((signal, q, cfg))
                label = "共振" if signal.strategy == "confluence" else "网格"
                tag = "（已压制）" if signal.suppressed else ""
                log(f"信号 ▶ [{label}]{tag} {signal.action.value} {cfg.name} 现价{q.price} 建议{signal.shares}股"
                    + (f"｜{signal.note}" if signal.note else ""))
            else:
                vol_note = f"｜波动{vol.regime}" if vol else ""
                div_note = f"｜{div.note}" if (div and div.note) else ""
                log(f"无信号　 {cfg.name or cfg.code} 现价{q.price}（{q.change_pct:+.2f}%）{vol_note}{div_note}")
    finally:
        if conn:
            conn.close()

    if items and not args.dry_run:
        notifier = FeishuNotifier(app.webhook, app.feishu_secret)
        title, template, elements = build_signal_card(items)
        resp = notifier.send_card(title, template, elements)
        log(("飞书推送成功" if is_ok(resp) else f"飞书推送异常: {resp}"))

    if args.dry_run:
        log(f"dry-run：命中 {len(items)} 条信号，未推送；历史已写库、状态未写。")
    else:
        save_state(app.state_file, state)
    return 0


def cmd_loop(app, args) -> int:
    log(f"循环模式启动：标的 {len(app.stocks)} 只，间隔 {app.poll_interval_seconds}s，"
        f"数据源 {app.data_source}，仅交易时段={app.only_trading_hours}")
    while True:
        try:
            cmd_run(app, args)
        except KeyboardInterrupt:
            log("收到中断，退出。")
            return 0
        except Exception as e:  # 守护进程不因单次异常退出
            log(f"运行异常：{e!r}")
        time.sleep(max(5, app.poll_interval_seconds))


def cmd_status(app, args) -> int:
    state = load_state(app.state_file)
    quotes = fetch_quotes([s.code for s in app.stocks], app.data_source)
    if not quotes:
        log("未获取到任何行情。")
        return 1

    rows = []
    log("=== 网格态势总览 ===")
    for cfg in app.stocks:
        q = quotes.get(cfg.code)
        if not q:
            log(f"未获取到行情：{cfg.code}")
            continue
        if not cfg.name:
            cfg.name = q.name
        gs = grid_status(cfg, q, state.get(cfg.code, {}))
        rows.append((cfg, q, gs))
        log(f"{cfg.name}({cfg.code}) 现价{q.price:.2f}({q.change_pct:+.2f}%) "
            f"基准{gs.base_price:.2f} 第{gs.level:+d}格 | "
            f"高抛↑{gs.next_sell:.2f}({gs.to_sell_pct:+.2f}%) "
            f"低吸↓{gs.next_buy:.2f}({gs.to_buy_pct:+.2f}%)")

    if args.notify and rows:
        notifier = FeishuNotifier(app.webhook, app.feishu_secret)
        title, template, elements = build_status_card(rows)
        resp = notifier.send_card(title, template, elements)
        log(("状态卡推送成功" if is_ok(resp) else f"状态卡推送异常: {resp}"))
    return 0


def cmd_test(app, args) -> int:
    notifier = FeishuNotifier(app.webhook, app.feishu_secret)
    names = "、".join((s.name or s.code) for s in app.stocks)
    resp = notifier.send_card(
        "✅ 做价差通知机器人已就绪",
        "green",
        [
            {"tag": "div", "text": {"tag": "lark_md", "content":
                f"机器人已连接成功，正在监控：**{names}**\n"
                f"震荡行情中将自动推送高抛/低吸信号，助你做价差、摊薄成本。"}},
            {"tag": "note", "elements": [{"tag": "plain_text", "content":
                f"测试时间 {dt.datetime.now():%Y-%m-%d %H:%M:%S}"}]},
        ],
    )
    log(("测试卡片推送成功" if is_ok(resp) else f"推送异常: {resp}"))
    return 0 if is_ok(resp) else 1


def cmd_debug(app, args) -> int:
    """调试：拉取单只标的的分钟 K 线原始/解析结果，并打印波动率与背离。
    用于上线前验证腾讯 mkline 字段顺序与解析是否正确。"""
    code = args.code
    cfg = next((s for s in app.stocks if s.code == code), None)
    if cfg is None:
        cfg = app.stocks[0]
        log(f"--code 未匹配配置标的，回退用 {cfg.code}（{cfg.name}）。")

    klines = fetch_minute_klines(cfg.code, cfg.kline_lookback)
    if not klines:
        log(f"未获取到分钟 K 线：{cfg.code}")
        return 1
    log(f"=== {cfg.name or cfg.code} 分钟 K 线 · 共 {len(klines)} 根 ===")
    for k in klines[:5]:
        log(f"  {k.ts}  O{k.open} H{k.high} L{k.low} C{k.close}  V{k.volume} A{k.amount}")
    log("  ...")
    for k in klines[-5:]:
        log(f"  {k.ts}  O{k.open} H{k.high} L{k.low} C{k.close}  V{k.volume} A{k.amount}")

    vol = build_profile(klines, cfg) if cfg.enable_volatility else None
    if vol:
        log(f"波动率: ATR {vol.atr:.2f}({vol.atr_pct:.2f}%) 布林[{vol.bb_lower:.2f}/{vol.bb_middle:.2f}/{vol.bb_upper:.2f}] "
            f"宽{vol.bb_width_pct:.2f}% 位置{vol.bb_position:.2f} HV{vol.hv_pct:.3f}% → {vol.regime}")
    else:
        log("波动率: 数据不足（冷启动）")

    div = detect_divergence(klines, cfg) if cfg.enable_divergence else None
    if div:
        log(f"背离: RSI {div.rsi:.1f} bearish={div.rsi_bearish or div.pv_bearish} "
            f"bullish={div.rsi_bullish or div.pv_bullish} note={div.note or '无'}")

    # 与分时线交叉校验
    bars = fetch_minute_bars(cfg.code)
    if bars and klines:
        last_bar = bars[-1]
        last_kl = klines[-1]
        log(f"交叉校验: 分时末价 {last_bar.price} vs K线末close {last_kl.close}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="A股高抛低吸做价差通知机器人")
    parser.add_argument("command", choices=["run", "loop", "status", "test", "debug"],
                        help="run=单次评估 loop=常驻循环 status=态势查看 test=测试推送 debug=K线/指标诊断")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    parser.add_argument("--dry-run", action="store_true", help="只评估、不推送、不写状态")
    parser.add_argument("--force", action="store_true", help="忽略交易时段限制，强制运行")
    parser.add_argument("--notify", action="store_true", help="status 模式下同时推送状态卡")
    parser.add_argument("--code", help="debug 模式指定标的代码")
    args = parser.parse_args()

    try:
        app = load_config(args.config)
    except (OSError, ValueError) as e:
        log(f"配置加载失败：{e}")
        return 2
    _resolve_paths(app, args.config)

    return {
        "run": cmd_run,
        "loop": cmd_loop,
        "status": cmd_status,
        "test": cmd_test,
        "debug": cmd_debug,
    }[args.command](app, args)


if __name__ == "__main__":
    sys.exit(main())
