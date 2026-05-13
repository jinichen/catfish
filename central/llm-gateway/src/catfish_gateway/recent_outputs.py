"""BL-FIX-TIMEOUT-OUTPUTS (5/13 鸿波"做不出文档") — 列 ~/.catfish/output/ 最近文件.

# 真用途

LLM 调 execute_code 写文件后, 上游 LLM 卡 / timeout / 鸿波等不及刷, 副手
**实际**已经写过文件了 (磁盘上有), 但鸿波看不到, 以为"做不出来" 反复 retry.

这个模块给 gateway timeout 友好错误用 — 列出最近 24h 写过的文件, 让员工
知道"她已经做了, 直接打开就行".

# 路径

`~/.catfish/output/` (跟 BL-LEAN-SESSION CATFISH_HOME 联动, 多 agent 不撞).

# 调用方

- gateway: `_friendly_upstream_error` 后追加 outputs 清单 (timeout/503/broken pipe 时)
- 后续可暴露 catfish_list_my_outputs native tool 让 LLM 主动调
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger("catfish.gateway.recent_outputs")


def _output_dir() -> Path:
    """跟 employee_journal / a2a_notifications 同 CATFISH_HOME 联动."""
    catfish_home = os.environ.get("CATFISH_HOME", "").strip()
    if catfish_home:
        return Path(catfish_home).expanduser() / "output"
    return Path.home() / ".catfish" / "output"


def _human_size(size: int) -> str:
    """1234 → '1.2 KB', 1234567 → '1.2 MB'."""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def list_recent(
    *,
    hours_back: int = 24,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """列 ~/.catfish/output/ 最近 N 小时写的文件, 按 mtime 倒序.

    返每条: {path, name, size, size_human, mtime, mtime_iso}.
    永不抛, 失败返 [].
    """
    d = _output_dir()
    if not d.exists() or not d.is_dir():
        return []
    cutoff = (datetime.now() - timedelta(hours=max(0, hours_back))).timestamp()
    out: list[dict[str, Any]] = []
    try:
        for entry in d.iterdir():
            if not entry.is_file():
                continue
            try:
                st = entry.stat()
            except OSError:
                continue
            if st.st_mtime < cutoff:
                continue
            out.append({
                "path": str(entry),
                "name": entry.name,
                "size": st.st_size,
                "size_human": _human_size(st.st_size),
                "mtime": st.st_mtime,
                "mtime_iso": datetime.fromtimestamp(st.st_mtime).isoformat(),
            })
    except OSError as e:
        logger.warning("BL-FIX-TIMEOUT-OUTPUTS: 列 %s 失败: %s", d, e)
        return []
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out[: max(1, int(limit))]


__all__ = ["list_recent"]
