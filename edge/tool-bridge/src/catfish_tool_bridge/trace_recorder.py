"""trace_recorder — BL-MM9-FREEZE-v2 (5/12 鸿波拍板, "彻底解决").

# v1 的问题 (5/12 上午一上午翻车)

v1 是 append-only 单文件 + freeze 时按"最近 N 小时"窗口取. 时间窗口里混了:

  1. 教学时员工 explicit 指挥的步骤 (这是要凝固的)
  2. LLM 教学过程中的探索 (snapshot 看 DOM / retry / 截图等)
  3. **复用阶段 LLM 降级到手工时的所有 ad-hoc 操作** (绝对不能凝固)

5/12 12:20 重新凝固 v2 把 11:41 之后 LLM 手工降级试错的 11 步当成"教学"凝
进去, 产出垃圾 skill (4 次重复 goto + 错 selector). 鸿波: "不能小打小闹,
要彻底解决".

# v2 — 显式教学 session 边界

教学跟其它所有操作**物理隔离**:

  start_session(name) → 关闭老 session (rotate to archive) → 设 _active
                       → 新 trace 文件 active.jsonl
  record()           → 只在 _active is not None 时写, 否则跳过
  end_session()      → rotate active.jsonl → session_<name>_<ts>.jsonl
                       → 记 _last_completed, _active = None
  get_last_completed() → freeze 引擎拿这个 session 的 trace 凝固

复用阶段 (catfish_run_skill) / 其它对话乱试 / LLM 自主探索 — _active=None,
**永远不录**. trace 文件干净, freeze 出来的 script.py 不再混探索.

# 持久化

跨进程 (tool-bridge 重启): _active 状态写 ~/.catfish/traces/_state.json,
重启后能恢复. _last_completed 同样落文件.

# 哪些 tool 进 trace

只录"业务流程类":
  catfish_browser_goto / click / fill / snapshot / screenshot / find_by_text
  catfish_recognize_captcha
  catfish_browser_locate
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
# 拦截 → 把"复用一次跑的步骤"写回 trace.
#
# 修法: thread-local depth counter. 只在最外层 dispatch (depth == 1) 才考虑
# 录制. 嵌套调用 (depth>=2) 一律跳过.

_local = threading.local()


class _DepthGuard:
    """记录嵌套层级的 contextmanager. 最外层 depth=1, 嵌套 depth>=2."""
    def __enter__(self) -> int:
        d = getattr(_local, "depth", 0) + 1
        _local.depth = d
        return d

    def __exit__(self, *args) -> None:
        d = getattr(_local, "depth", 1)
        _local.depth = max(d - 1, 0)


def record_depth_guard() -> _DepthGuard:
    return _DepthGuard()


def is_outermost() -> bool:
    return getattr(_local, "depth", 0) <= 1


# ─── 路径常量 ──────────────────────────────────────────────────────────

TRACE_DIR = Path.home() / ".catfish" / "traces"
TRACE_PATH = TRACE_DIR / "active.jsonl"
STATE_PATH = TRACE_DIR / "_state.json"  #  当前 session metadata
LAST_COMPLETED_PATH = TRACE_DIR / "_last_completed.json"  #  最近完成 session
ARCHIVE_DIR = TRACE_DIR  #  归档文件直接放 TRACE_DIR (前缀 session_ 区分)


# ─── 录制白名单 ────────────────────────────────────────────────────────

RECORDED_TOOLS = frozenset({
    "catfish_browser_goto",
    "catfish_browser_click",
    "catfish_browser_fill",
    "catfish_browser_snapshot",
    "catfish_browser_screenshot",
    "catfish_browser_find_by_text",
    "catfish_recognize_captcha",
    "catfish_browser_locate",
    # v2.2 (5/12): 嵌套调 skill (eis-checkin 教学时直接 catfish_run_skill('eis-login'),
    # 不重复教 7 步登录). 嵌套 depth_guard 保证 script.py 内部 dispatch 不再录,
    # 不会循环污染.
    "catfish_run_skill",
})

_MAX_FIELD_BYTES = 8000
_SEQ = 0


def _next_seq() -> int:
    global _SEQ
    _SEQ += 1
    return _SEQ


def is_recorded(tool_name: str) -> bool:
    return tool_name in RECORDED_TOOLS


def _truncate_for_trace(obj: Any) -> Any:
    if isinstance(obj, str):
        if len(obj) > _MAX_FIELD_BYTES:
            return obj[:_MAX_FIELD_BYTES] + f"…[truncated, full={len(obj)} chars]"
        return obj
    if isinstance(obj, dict):
        return {k: _truncate_for_trace(v) for k, v in obj.items()}
    if isinstance(obj, list):
        if len(obj) > 50:
            return [_truncate_for_trace(x) for x in obj[:50]] + [
                f"…[truncated, full={len(obj)} items]"
            ]
        return [_truncate_for_trace(x) for x in obj]
    return obj


# ─── Session 状态 (持久化) ─────────────────────────────────────────────


def _read_state() -> dict[str, Any] | None:
    """读当前 active session metadata. None = 没在教学."""
    if not STATE_PATH.exists():
        return None
    try:
        d = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(d, dict) and d.get("active"):
            return d
        return None
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("读 _state.json 失败: %s", e)
        return None


def _write_state(state: dict[str, Any] | None) -> None:
    """写当前 session 状态. state=None → 清空 (写 {active: false})."""
    try:
        TRACE_DIR.mkdir(parents=True, exist_ok=True)
        if state is None:
            STATE_PATH.write_text(
                json.dumps({"active": False}, ensure_ascii=False),
                encoding="utf-8",
            )
        else:
            STATE_PATH.write_text(
                json.dumps({"active": True, **state}, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
    except OSError as e:
        logger.warning("写 _state.json 失败: %s", e)


def _write_last_completed(info: dict[str, Any]) -> None:
    try:
        LAST_COMPLETED_PATH.write_text(
            json.dumps(info, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except OSError as e:
        logger.warning("写 _last_completed.json 失败: %s", e)


def is_session_active() -> bool:
    """当前是否有 active 教学 session."""
    return _read_state() is not None


def get_active_session() -> dict[str, Any] | None:
    """返当前 active session metadata (name / started_at / ...)."""
    return _read_state()


def get_last_completed_session() -> dict[str, Any] | None:
    """返最近完成的 teach session 元信息 (含 path 给 freeze 用). None = 没."""
    if not LAST_COMPLETED_PATH.exists():
        return None
    try:
        return json.loads(LAST_COMPLETED_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("读 _last_completed.json 失败: %s", e)
        return None


# ─── Session 生命周期 ─────────────────────────────────────────────────


def start_session(
    name: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """开始一个教学 session.

    - 关闭老 active session (如果有) → archive
    - 清空 active.jsonl
    - 新 session 元信息写到 _state.json
    - 返 session metadata
    """
    name = (name or "").strip() or "unnamed"
    description = (description or "").strip()[:500]

    TRACE_DIR.mkdir(parents=True, exist_ok=True)

    # 关闭老 session (如有)
    old = _read_state()
    if old:
        logger.warning("start_session: 老 session %r 未 end, 自动关掉", old.get("name"))
        try:
            end_session(reason="auto-closed-by-new-start")
        except Exception as e:
            logger.warning("auto-close 老 session 失败: %s", e)

    # 即使没 active state, active.jsonl 也可能有"前科残留" (复用阶段误录之类),
    # rotate 一下保险
    if TRACE_PATH.exists():
        try:
            ts = time.strftime("%Y%m%d_%H%M%S")
            stale = TRACE_DIR / f"stale_{ts}_{uuid.uuid4().hex[:6]}.jsonl"
            TRACE_PATH.rename(stale)
            logger.info("start_session: 残留 active.jsonl 搬到 %s", stale)
        except OSError as e:
            logger.warning("rotate stale active.jsonl 失败: %s", e)

    session_id = uuid.uuid4().hex[:12]
    started_at = time.time()
    state = {
        "session_id": session_id,
        "name": name,
        "description": description,
        "started_at": started_at,
        "started_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(started_at)),
        "pid": os.getpid(),
    }
    _write_state(state)
    logger.info("teach session 开始: %s (id=%s)", name, session_id)
    return dict(state)


def end_session(reason: str = "manual") -> dict[str, Any]:
    """结束 active session, 把 active.jsonl 归档.

    Returns: {ok, session_id, name, archive_path, step_count, ...}
    """
    state = _read_state()
    if not state:
        return {
            "ok": False,
            "error": "没有 active session 可结束 (是不是没调 catfish_teach_start?)",
        }

    name = state.get("name", "unnamed")
    session_id = state.get("session_id", "")
    started_at = state.get("started_at", time.time())
    ended_at = time.time()

    # 归档 active.jsonl
    archive_path: Path | None = None
    step_count = 0
    if TRACE_PATH.exists():
        ts = time.strftime("%Y%m%d_%H%M%S", time.localtime(ended_at))
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:40]
        archive_path = TRACE_DIR / f"session_{safe_name}_{ts}_{session_id[:6]}.jsonl"
        try:
            # 数一下 step
            with open(TRACE_PATH, encoding="utf-8") as f:
                step_count = sum(1 for line in f if line.strip())
            TRACE_PATH.rename(archive_path)
        except OSError as e:
            logger.warning("end_session 归档失败: %s", e)
            archive_path = None

    info = {
        "ok": True,
        "session_id": session_id,
        "name": name,
        "description": state.get("description", ""),
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_s": int(ended_at - started_at),
        "step_count": step_count,
        "archive_path": str(archive_path) if archive_path else "",
        "end_reason": reason,
    }
    _write_state(None)
    _write_last_completed(info)
    logger.info(
        "teach session 结束: name=%s steps=%d archive=%s",
        name, step_count, archive_path,
    )
    return info


# ─── 录制 ─────────────────────────────────────────────────────────────


def record(
    tool_name: str,
    args: dict[str, Any],
    result: Any,
    ok: bool,
    duration_ms: int,
    session_hint: str | None = None,
) -> None:
    """记一条 trace. **只在 active session 内才写**. 否则静默跳过.

    永不抛 — 失败只 log warn.
    """
    if tool_name not in RECORDED_TOOLS:
        return

    # **核心新逻辑**: 没 active session → 不录.
    # 这是 v2 跟 v1 最大区别 — 把教学跟复用/探索物理隔离.
    state = _read_state()
    if state is None:
        return

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
            "session_id": state.get("session_id"),
            "session_name": state.get("name"),
            "pid": os.getpid(),
        }
        line = json.dumps(entry, ensure_ascii=False, default=str)
        with open(TRACE_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        logger.warning("trace 写失败 (无关键路径): %s", e)


# ─── 读 trace (给 freeze 引擎) ────────────────────────────────────────


def read_session_traces(
    session_archive_path: str | Path,
    only_ok: bool = True,
) -> list[dict[str, Any]]:
    """读指定 session archive 的 trace.

    给 freeze 引擎用 — 凝固时拿 last_completed.archive_path → 读它的步骤.
    """
    p = Path(session_archive_path)
    if not p.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if only_ok and not entry.get("ok"):
                    continue
                out.append(entry)
    except OSError as e:
        logger.warning("读 session trace 失败 (%s): %s", p, e)
    return out


def read_traces(
    since_unix: float | None = None,
    until_unix: float | None = None,
    only_ok: bool = True,
    tools: list[str] | None = None,
) -> list[dict[str, Any]]:
    """legacy: 读 active.jsonl 按时间筛.

    v2 下 active.jsonl 只在 active session 内有内容. 主要给 inspect / debug 用.
    freeze 引擎应该走 read_session_traces.
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
    """给 catfish_freeze_inspect 用: 当前状态 + active 文件 + last_completed."""
    out: dict[str, Any] = {
        "active_session": _read_state(),
        "last_completed": get_last_completed_session(),
    }
    if TRACE_PATH.exists():
        try:
            n = 0
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
            out["active_file"] = {
                "exists": True,
                "path": str(TRACE_PATH),
                "lines": n,
                "ok_count": ok_count,
                "tools": tool_counts,
            }
        except OSError as e:
            out["active_file"] = {"error": str(e), "path": str(TRACE_PATH)}
    else:
        out["active_file"] = {"exists": False, "path": str(TRACE_PATH)}
    return out


def rotate(reason: str = "manual") -> str:
    """legacy: rotate active.jsonl. v2 下一般用不到 (end_session 包了)."""
    if not TRACE_PATH.exists():
        return ""
    ts = time.strftime("%Y%m%d_%H%M%S")
    archive = TRACE_DIR / f"manual_{ts}_{reason}_{uuid.uuid4().hex[:6]}.jsonl"
    try:
        TRACE_PATH.rename(archive)
        return str(archive)
    except OSError as e:
        logger.warning("rotate 失败: %s", e)
        return ""


__all__ = [
    # 路径
    "TRACE_DIR",
    "TRACE_PATH",
    "STATE_PATH",
    "LAST_COMPLETED_PATH",
    "RECORDED_TOOLS",
    # 嵌套保护
    "record_depth_guard",
    "is_outermost",
    # 工具白名单
    "is_recorded",
    # session 生命周期
    "start_session",
    "end_session",
    "is_session_active",
    "get_active_session",
    "get_last_completed_session",
    # 录 / 读
    "record",
    "read_session_traces",
    "read_traces",
    "session_summary",
    "rotate",
]
