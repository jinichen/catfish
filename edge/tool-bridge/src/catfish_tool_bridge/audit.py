"""tool-bridge dispatch 审计事件流 — 写 JSONL 到 ~/.hermes/.catfish_audit.jsonl.

# 为啥需要
=========
配套 docs/SKILL-LIFECYCLE.md 阶段 4 (Use 监控) — 没有数据就没法做:
  - skill 健康面板 (BL-C15: today's invocations / failures / unused_30d)
  - 30 天未用 skill 主动建议删 (BL-C16)
  - 失败率告警 (BL-C17: skill 连续 1 周失败率 > 30% 主动诊断)
  - 未来: 安全审计 (谁调了 sensitive tool) / billing 用量 (token 之外的 tool 调用次数)

设计为通用 dispatch audit (不只 skill, 所有 hermes + catfish native tool 都记),
让上层按需 filter (skill 维度从 tool name pattern 提取, 比如 skill_run/skill_view).

# 事件格式 (JSONL, 一行一事件)
=============================
{
  "ts": "2026-04-28T14:30:00.123Z",     # ISO-8601 UTC
  "tool": "read_file",                    # tool name
  "ok": true,                             # 成功 / 失败
  "error": null,                          # 失败时短错误描述, 成功时 null
  "latency_ms": 23.4,                     # 调用耗时
  "args_preview": "{\"path\":\"~/...\"}"  # 截短入参 (前 200 字, 防敏感数据)
}

# 隐私设计
========
args_preview 截短 200 字, 避免:
  - 长邮件正文 (catfish-email tools)
  - base64 PNG (catfish_screenshot)
  - 整个 SKILL.md 内容 (skill_manage update)
泄漏到 audit log 反成新的隐私风险源.

# 文件位置 ~/.hermes/.catfish_audit.jsonl
====================================
- 在 ~/.hermes/ 下 (跟 hermes 配套, 员工 rm -rf ~/.hermes 一起干净)
- 文件名 .开头 (员工 ls 默认看不到, 不打扰)
- 不进 skills/ 子目录 (跟 skill manage 解耦, audit 是 cross-cutting)

# rotation
==========
**不做** rotation. JSONL 一行一事件, 员工感觉文件大就自己 truncate / archive.
未来加 rotation 再说.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("catfish.tool_bridge.audit")

#: 入参 / 错误消息截断长度. 太长进 audit 既浪费磁盘也可能泄敏感数据.
_MAX_PREVIEW_CHARS = 200

#: 写文件用的锁. 单进程多线程并发调 dispatch_tool 时防 partial line.
#: (asyncio + thread, append-only 写一般原子但不保证, 加锁稳)
_write_lock = threading.Lock()

#: 默认 audit 文件路径. 测试时可以 override.
_audit_path: Path | None = None


def audit_path() -> Path:
    """audit 文件路径, lazy resolve. 默认 ~/.hermes/.catfish_audit.jsonl."""
    global _audit_path
    if _audit_path is None:
        home = os.environ.get("HOME") or os.environ.get("USERPROFILE") or "."
        _audit_path = Path(home) / ".hermes" / ".catfish_audit.jsonl"
    return _audit_path


def _set_audit_path(path: Path) -> None:
    """测试用 — 改 audit 文件路径. 生产代码不该用."""
    global _audit_path
    _audit_path = path


def _truncate(s: str, max_len: int = _MAX_PREVIEW_CHARS) -> str:
    """截断字符串到 max_len, 截了加 "...{剩 N 字}" 后缀."""
    if len(s) <= max_len:
        return s
    return s[:max_len] + f"...({len(s) - max_len} more chars)"


def _serialize_args(args: Any) -> str:
    """args dict → 短预览字符串. 失败兜底 repr."""
    try:
        s = json.dumps(args, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        s = repr(args)
    return _truncate(s)


def write_event(
    tool: str,
    *,
    ok: bool,
    args: Any = None,
    error: str | None = None,
    latency_ms: float = 0.0,
) -> None:
    """写一行事件到 audit jsonl.

    **永远不抛**. 写失败 (磁盘满 / 权限错 / 路径不存在) 只 log warning,
    不能影响 dispatch_tool 主流程.

    Args:
        tool: tool 名 (read_file / catfish_screenshot / 等)
        ok: 调用是否成功
        args: 入参 dict (会被截短到 200 字)
        error: 失败时的错误描述 (成功时传 None)
        latency_ms: 耗时毫秒
    """
    event = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "tool": tool,
        "ok": ok,
        "error": _truncate(str(error), _MAX_PREVIEW_CHARS) if error else None,
        "latency_ms": round(latency_ms, 1),
        "args_preview": _serialize_args(args) if args is not None else "",
    }

    try:
        line = json.dumps(event, ensure_ascii=False) + "\n"
    except Exception:  # noqa: BLE001
        logger.warning("audit: 序列化失败, tool=%s", tool)
        return

    path = audit_path()
    try:
        # mkdir parents — ~/.hermes 应该存在 (hermes 装了就有), 但兜底建一下
        path.parent.mkdir(parents=True, exist_ok=True)
        with _write_lock:
            with path.open("a", encoding="utf-8") as f:
                f.write(line)
    except OSError as e:
        # 磁盘满 / 权限错都不该影响主流程
        logger.warning("audit: 写 %s 失败: %s", path, e)


def read_events(
    *,
    since_iso: str | None = None,
    tool_filter: str | None = None,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    """读 audit log, 给上层 (健康面板 / 分析) 用.

    Args:
        since_iso: 只要 ts >= 这个时间的事件 (ISO-8601). None = 所有
        tool_filter: 只要 tool 等于这个名字的事件. None = 所有
        limit: 最多返回多少条 (从最新算起). 防内存爆.

    Returns:
        事件 dict 列表, 按时间倒序 (最新在前).

    永远不抛. 读失败返空列表.
    """
    path = audit_path()
    if not path.is_file():
        return []

    try:
        with path.open("r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError as e:
        logger.warning("audit: 读 %s 失败: %s", path, e)
        return []

    out: list[dict[str, Any]] = []
    # 倒序遍历 (最新在前), limit 后立刻 break
    for line in reversed(lines):
        if len(out) >= limit:
            break
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if since_iso and event.get("ts", "") < since_iso:
            continue
        if tool_filter and event.get("tool") != tool_filter:
            continue
        out.append(event)
    return out
