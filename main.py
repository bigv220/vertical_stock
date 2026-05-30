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
from spread_bot.market import is_trading_time
from spread_bot.notifier import (
    FeishuNotifier,
    build_signal_card,
    build_status_card,
    is_ok,
)
from spread_bot.quotes import fetch_quotes
from spread_bot.state import load_state, save_state
from spread_bot.strategy import evaluate, grid_status


def log(msg: str) -> None:
    print(f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def _resolve_paths(app, config_path: str) -> None:
    """状态文件若为相对路径，则相对配置文件所在目录解析，便于 cron。"""
    if not os.path.isabs(app.state_file):
        base = os.path.dirname(os.path.abspath(config_path))
        app.state_file = os.path.join(base, app.state_file)


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

    items = []
    for cfg in app.stocks:
        q = quotes.get(cfg.code)
        if not q:
            log(f"未获取到行情：{cfg.code}（{cfg.name}）")
            continue
        if not cfg.name:
            cfg.name = q.name
        st = state.setdefault(cfg.code, {})
        signal, st = evaluate(cfg, q, st)
        state[cfg.code] = st
        if signal:
            items.append((signal, q, cfg))
            log(f"信号 ▶ {signal.action.value} {cfg.name} 现价{q.price} 建议{signal.shares}股"
                + (f"｜{signal.note}" if signal.note else ""))
        else:
            log(f"无信号　 {cfg.name or cfg.code} 现价{q.price}（{q.change_pct:+.2f}%）")

    if items and not args.dry_run:
        notifier = FeishuNotifier(app.webhook, app.feishu_secret)
        title, template, elements = build_signal_card(items)
        resp = notifier.send_card(title, template, elements)
        log(("飞书推送成功" if is_ok(resp) else f"飞书推送异常: {resp}"))

    if args.dry_run:
        log(f"dry-run：命中 {len(items)} 条信号，未推送、未写状态。")
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


def main() -> int:
    parser = argparse.ArgumentParser(description="A股高抛低吸做价差通知机器人")
    parser.add_argument("command", choices=["run", "loop", "status", "test"],
                        help="run=单次评估 loop=常驻循环 status=态势查看 test=测试推送")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    parser.add_argument("--dry-run", action="store_true", help="只评估、不推送、不写状态")
    parser.add_argument("--force", action="store_true", help="忽略交易时段限制，强制运行")
    parser.add_argument("--notify", action="store_true", help="status 模式下同时推送状态卡")
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
    }[args.command](app, args)


if __name__ == "__main__":
    sys.exit(main())
