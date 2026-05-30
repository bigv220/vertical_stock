"""运行状态持久化（JSON）。

按股票代码保存网格档位、方向连续计数、累计信号统计等，使脚本可被 cron
反复单次调用而不丢失上下文，也避免重复推送同一格信号。
"""
from __future__ import annotations

import json
import os
import tempfile
from typing import Dict


def load_state(path: str) -> Dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(path: str, state: Dict) -> None:
    """原子写入，避免进程中断导致状态文件损坏。"""
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
