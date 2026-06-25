"""每分钟价格/K线的历史存储（SQLite，stdlib）。

分时线（MinuteBar）与分钟K线（MinuteKline）各一张表，按 (code, ts) 复合主键去重。
跨日历史供波动率/背离等跨日指标使用。腾讯接口每日重置，新一天即新 ts 日期，
天然成为新行，无需特殊处理。
"""
from __future__ import annotations

import datetime as dt
import os
import sqlite3
from typing import List, Optional, Sequence

from .quotes import MinuteBar, MinuteKline

_SCHEMA = """
CREATE TABLE IF NOT EXISTS minute_bar (
  code      TEXT NOT NULL,
  ts        TEXT NOT NULL,
  price     REAL NOT NULL,
  avg_price REAL,
  volume    REAL,
  amount    REAL,
  source    TEXT,
  PRIMARY KEY (code, ts)
);
CREATE TABLE IF NOT EXISTS minute_kline (
  code   TEXT NOT NULL,
  ts     TEXT NOT NULL,
  open   REAL NOT NULL,
  close  REAL NOT NULL,
  high   REAL NOT NULL,
  low    REAL NOT NULL,
  volume REAL,
  amount REAL,
  source TEXT,
  PRIMARY KEY (code, ts)
);
CREATE INDEX IF NOT EXISTS idx_bar_code_ts   ON minute_bar(code, ts);
CREATE INDEX IF NOT EXISTS idx_kline_code_ts ON minute_kline(code, ts);
"""


def open_db(path: str) -> sqlite3.Connection:
    """打开/创建库：建表、WAL、忙等。路径目录自动创建（同 state.save_state 风格）。"""
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_SCHEMA)
    return conn


def _max_ts(conn: sqlite3.Connection, table: str, code: str) -> Optional[str]:
    row = conn.execute(f"SELECT MAX(ts) FROM {table} WHERE code=?", (code,)).fetchone()
    return row[0] if row else None


def upsert_klines(conn: sqlite3.Connection, code: str, klines: Sequence[MinuteKline]) -> int:
    """写入新 K 线（仅 ts > 已存最大 ts 的行），已存在则更新。返回新增/更新行数。"""
    if not klines:
        return 0
    last = _max_ts(conn, "minute_kline", code)
    rows = [k for k in klines if not last or k.ts > last]
    if not rows:
        return 0
    conn.executemany(
        "INSERT INTO minute_kline(code,ts,open,close,high,low,volume,amount,source) "
        "VALUES(?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(code,ts) DO UPDATE SET "
        "open=excluded.open,close=excluded.close,high=excluded.high,low=excluded.low,"
        "volume=excluded.volume,amount=excluded.amount",
        [(code, k.ts, k.open, k.close, k.high, k.low, k.volume, k.amount, k.source) for k in rows],
    )
    conn.commit()
    return len(rows)


def upsert_bars(
    conn: sqlite3.Connection,
    code: str,
    bars: Sequence[MinuteBar],
    date: str,
) -> int:
    """写入分时线。分时线 time 只有 HHMM，需传入 date('YYYY-MM-DD') 补全 ts。

    date 应取自当日 K 线时间戳（权威，节假日安全），而非本机时钟。
    """
    if not bars or not date:
        return 0
    last = _max_ts(conn, "minute_bar", code)
    rows = []
    for b in bars:
        hhmm = b.time.replace(":", "")
        if len(hhmm) >= 4:
            ts = f"{date} {hhmm[-4:-2]}:{hhmm[-2:]}:00"
        else:
            continue
        if last and ts <= last:
            continue
        rows.append((code, ts, b.price, b.avg_price, b.volume, b.amount, "tencent"))
    if not rows:
        return 0
    conn.executemany(
        "INSERT INTO minute_bar(code,ts,price,avg_price,volume,amount,source) "
        "VALUES(?,?,?,?,?,?,?) "
        "ON CONFLICT(code,ts) DO UPDATE SET "
        "price=excluded.price,avg_price=excluded.avg_price,volume=excluded.volume,"
        "amount=excluded.amount",
        rows,
    )
    conn.commit()
    return len(rows)


def get_recent_klines(conn: sqlite3.Connection, code: str, n: int) -> List[MinuteKline]:
    """最近 n 根 K 线，按时间正序返回（旧→新）。"""
    if n <= 0:
        return []
    cur = conn.execute(
        "SELECT ts,open,close,high,low,volume,amount FROM minute_kline "
        "WHERE code=? ORDER BY ts DESC LIMIT ?",
        (code, n),
    )
    rows = [MinuteKline(r[0], r[1], r[2], r[3], r[4], r[5], r[6]) for r in cur.fetchall()]
    rows.reverse()
    return rows


def get_recent_bars(conn: sqlite3.Connection, code: str, n: int) -> List[MinuteBar]:
    """最近 n 个分时点，按时间正序返回（旧→新）。"""
    if n <= 0:
        return []
    cur = conn.execute(
        "SELECT ts,price,avg_price,volume,amount FROM minute_bar "
        "WHERE code=? ORDER BY ts DESC LIMIT ?",
        (code, n),
    )
    out: List[MinuteBar] = []
    for r in cur.fetchall():
        ts = r[0]
        # ts 形如 'YYYY-MM-DD HH:MM:00'，回填 time 为 'HHMM'
        hhmm = ts[11:13] + ts[14:16] if len(ts) >= 16 else ts
        out.append(MinuteBar(hhmm, r[1], r[2], r[3], r[4]))
    out.reverse()
    return out


def get_klines_since(conn: sqlite3.Connection, code: str, days: int) -> List[MinuteKline]:
    """取最近 days 个自然日（含今日）的 K 线，正序。cutoff 在 Python 用北京日期算，
    不用 SQLite date('now')（UTC 会偏移）。"""
    cutoff = (dt.date.today() - dt.timedelta(days=max(0, days - 1))).strftime("%Y-%m-%d")
    cur = conn.execute(
        "SELECT ts,open,close,high,low,volume,amount FROM minute_kline "
        "WHERE code=? AND ts>=? ORDER BY ts ASC",
        (code, cutoff),
    )
    return [MinuteKline(r[0], r[1], r[2], r[3], r[4], r[5], r[6]) for r in cur.fetchall()]


def prune_old(conn: sqlite3.Connection, code: str, days: int) -> int:
    """删除早于最近 days 自然日的数据，返回删除行数。days<=0 不删（永久保留）。"""
    if days <= 0:
        return 0
    cutoff = (dt.date.today() - dt.timedelta(days=days)).strftime("%Y-%m-%d")
    nb = conn.execute("DELETE FROM minute_bar WHERE code=? AND ts<?", (code, cutoff)).rowcount
    nk = conn.execute("DELETE FROM minute_kline WHERE code=? AND ts<?", (code, cutoff)).rowcount
    conn.commit()
    return nb + nk


def last_date(conn: sqlite3.Connection, code: str) -> Optional[str]:
    """该标的最近一条 K 线的日期（'YYYY-MM-DD'），用于新鲜度守卫与日切检测。"""
    ts = _max_ts(conn, "minute_kline", code)
    return ts[:10] if ts else None
