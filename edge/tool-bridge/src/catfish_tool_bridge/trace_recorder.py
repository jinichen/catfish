"""trace_recorder — BL-MM9-FREEZE (5/12 鸿波拍板).

# 为啥

catfish 的核心卖点: 员工教鲶鱼一次, 鲶鱼凝固成 skill, 下次秒开. 之前缺
"教学→凝固"管道里的**录制**这一步, 所有 SKILL.md 都得手写.

trace_recorder 拦截 LLM agent 调的每个 browser-related tool (goto / fill /
click / snapshot / screenshot / find_by_text / recognize_captcha /
browser_locate), 顺序记到 jsonl, 后续 catfish_freeze_skill 读 trace
模板化生成 script.py + SKILL.md.

# 设计

- 路径: `~/.catfish/traces/active.jsonl` (单文件 append, 跨多个对话累加)
- 一行一个 tool 调用, JSON: {seq, ts, tool, args, result, ok, duration_ms, session_hint}
- 敏感字段脱敏: args 里看到 `secret_ref="keychain://..."` 原样保留 (引用,
  不是明文), 但 result 里如果有 base64 大图 → 只记元信息不记原文 (省盘)
- 失败也记 (ok=false), freeze 引擎过滤只取成功 trace
- 不阻塞: 写失败静默 (logging.warning), 不破坏 tool 调用本身

# 哪些 tool 进 trace

只录"可凝固为 script.py 业务流程的 tool" — 浏览器自动化 + 视觉工具:

  catfish_browser_goto / click / fill / snapshot / screenshot / find_by_text
  catfish_recognize_captcha
  catfish_browser_locate

**不**录: catfish_remember / catfish_user_profile_* / catfish_run_skill /
catfish_task_* / 等 (这些是"系统操作"不是"业务流程").

# trace 隔离

短期: 单进程一个文件, append 所有 session. freeze 时按时间段筛选.
长期 (BL-MM9-FREEZE-v1 之后): 每次 LLM agent 一个 session_id, 分文件
存. 现在 MVP 不做.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger("catfish.tool_bridge.trace_recorder")

# ─── 嵌套深度跟踪 ──────────────────────────────────────────────────────
#
# BL-MM9-FREEZE-bugfix (5/12): 复用阶段, LLM 调 catfish_run_skill →
# script.py 内部用 dispatch_native 调 catfish_browser_* → 又被 trace_recorder
# 拦截 → 把"复用一次跑的步骤"写回 trace → 下次 freeze 撞混.
#
# 修法: thread-local depth counter. 只在最外层 dispatch (depth == 1, 即
# 真 LLM 教学调用) 时 record. script.py 内部嵌套 dispatch (depth >= 2) 跳过.
#
# `record_depth_guard()` 上下文管理器, dispatch_native wrapper 用它包.
# ─── ─────────────────────────────────────────────────────────────────

_local = threading.local()


class _DepthGuard:
    """记录嵌套层级的 contextmanager. 最外层 depth=1, 嵌套 depth>=2."""
    def __enter__(self) -> int:
        d = getattr(_local, "depth", 0) + 1
        _local.depth = d
        return d  # 当前层级

    def __exit__(self, *args) -> None:
        d = getattr(_local, "depth", 1)
        _local.depth = max(d - 1, 0)


def record_depth_guard() -> _DepthGuard:
    """给 catfish_tools.dispatch_native wrapper 用. 每次进 wrapper 包一下."""
    return _DepthGuard()


def is_outermost() -> bool:
    """当前是否在最外层 (depth == 1). dispatch wrapper 调它决定要不要 record."""
    return getattr(_local, "depth", 0) <= 1

#: trace 文件路径
TRACE_DIR = Path.home() / ".catfish" / "traces"
TRACE_PATH = TRACE_DIR / "active.jsonl"

#: 要录的 tool 白名单 (业务流程类). 系统操作类不录.
RECORDED_TOOLS = frozenset({
    "catfish_browser_goto",
    "catfish_browser_click",
    "catfish_browser_fill",
    "catfish_browser_snapshot",
    "catfish_browser_screenshot",
    "catfish_browser_find_by_text",
    "catfish_recognize_captcha",
    "catfish_browser_locate",
})

#: 单字段最大字节 (大 result 截断防盘爆)
_MAX_FIELD_BYTES = 8000

#: process-level seq (单进程内严格递增)
_SEQ = 0


def _next_seq() -> int:
    global _SEQ
    _SEQ += 1
    return _SEQ


def is_recorded(tool_name: str) -> bool:
    """tool 是不是在白名单里 — 给 dispatch 调."""
    return tool_name in RECORDED_TOOLS


def _truncate_for_trace(obj: Any) -> Any:
    """大字段截断 (主要是 result 里的 png base64 / snapshot 长 DOM 等)."""
    if isinstance(obj, str):
        if len(obj) > _MAX_FIELD_BYTES:
            return obj[:_MAX_FIELD_BYTES] + f"…[truncated, full={len(obj)} chars]"
        return obj
    if isinstance(obj, dict):
        return {k: _truncate_for_trace(v) for k, v in obj.items()}
    if isinstance(obj, list):
        # 大数组只留前 50 项
        if len(obj) > 50:
            return [_truncate_for_trace(x) for x in obj[:50]] + [
                f"…[truncated, full={len(obj)} items]"
            ]
        return [_truncate_for_trace(x) for x in obj]
    return obj


def record(
    tool_name: str,
    args: dict[str, Any],
    result: Any,
    ok: bool,
    duration_ms: int,
    session_hint: str | None = None,
) -> None:
    """记一条 trace. 永不抛 — 失败只 log warn.

    Args:
        tool_name: 工具名 (catfish_browser_goto 等)
        args: 原 args (会浅拷贝 + 截断)
        result: dispatch 返回 (会截断)
        ok: 工具有没有成功 (按 result.ok 或异常判断)
        duration_ms: 耗时
        session_hint: 可选, LLM 端传过来的 session_id (现在 MVP 没传, 默认 None)
    """
    if tool_name not in RECORDED_TOOLS:
        return  # 不录

    try:
        TRACE_DIR.mkdir(parents=True, exist_ok=True)
        entry = {
            "seq": _next_seq(),
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()),
            "ts_unix": time.time(),
            "tool": tool_name,
            "args": _truncate_for_trace(args),
            "result": _truncate_for_trace(result),
            "ok": bool(ok),
            "duration_ms": int(duration_ms),
            "session_hint": session_hint,
            "pid": os.getpid(),
        }
        line = json.dumps(entry, ensure_ascii=False, default=str)
        with open(TRACE_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        # 永不 break 主流程
        logger.warning("trace 写失败 (无关键路径): %s", e)


def read_traces(
    since_unix: float | None = None,
    until_unix: float | None = None,
    only_ok: bool = True,
    tools: list[str] | None = None,
) -> list[dict[str, Any]]:
    """读 trace, 按时间 / ok / 工具过滤. 给 freeze 引擎调.

    Args:
        since_unix: 起始时间 (unix ts), None=不限
        until_unix: 截止时间, None=不限
        only_ok: 只返 ok=true 的 (默认)
        tools: 只返这些工具的 trace, None=所有 recorded
    """
    if not TRACE_PATH.exists():
        return []
    out = []
    try:
        with open(TRACE_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if since_unix is not None and entry.get("ts_unix", 0) < since_unix:
                    continue
                if until_unix is not None and entry.get("ts_unix", 0) > until_unix:
                    continue
                if only_ok and not entry.get("ok"):
                    continue
                if tools and entry.get("tool") not in tools:
                    continue
                out.append(entry)
    except OSError as e:
        logger.warning("读 trace 失败: %s", e)
    return out


def session_summary() -> dict[str, Any]:
    """快速查 trace 文件状态. 给 catfish_freeze_inspect 用."""
    if not TRACE_PATH.exists():
        return {"exists": False, "path": str(TRACE_PATH), "lines": 0}
    try:
        n = 0
        first_ts = None
        last_ts = None
        ok_count = 0
        tool_counts: dict[str, int] = {}
        with open(TRACE_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                n += 1
                if entry.get("ok"):
                    ok_count += 1
                tool = entry.get("tool", "?")
                tool_counts[tool] = tool_counts.get(tool, 0) + 1
                ts = entry.get("ts_unix")
                if ts:
                    if first_ts is None or ts < first_ts:
                        first_ts = ts
                    if last_ts is None or ts > last_ts:
                        last_ts = ts
        return {
            "exists": True,
            "path": str(TRACE_PATH),
            "lines": n,
            "ok_count": ok_count,
            "first_ts_unix": first_ts,
            "last_ts_unix": last_ts,
            "tools": tool_counts,
        }
    except OSError as e:
        return {"exists": True, "error": str(e), "path": str(TRACE_PATH)}


def rotate(reason: str = "manual") -> str:
    """把当前 trace 文件搬到 archive (用时间戳命名), 给 freeze 完后清空用.

    Returns: 新 archive 路径 str (或空串 = trace 不存在).
    """
    if not TRACE_PATH.exists():
        return ""
    ts = time.strftime("%Y%m%d_%H%M%S")
    archive = TRACE_DIR / f"archive_{ts}_{reason}_{uuid.uuid4().hex[:6]}.jsonl"
    try:
        TRACE_PATH.rename(archive)
        logger.info("trace rotate: %s → %s", TRACE_PATH, archive)
        return str(archive)
    except OSError as e:
        logger.warning("trace rotate 失败: %s", e)
        return ""


__all__ = [
    "TRACE_DIR",
    "TRACE_PATH",
    "RECORDED_TOOLS",
    "is_recorded",
    "record",
    "read_traces",
    "session_summary",
    "rotate",
]
