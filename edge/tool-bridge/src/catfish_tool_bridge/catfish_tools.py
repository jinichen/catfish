"""Catfish 原生 tools —— 不来自 hermes registry, 是 catfish 自己加的。

为什么需要:
    Hermes 的 session_search / memory_recall 是面向 LLM 自己"翻历史"用的, 但
    员工聊天里问"今天小鲶学了啥"时, LLM 调 session_search 要么搜不到要么
    答非所问 (今晚截图里就是这样)。我们暴露一个明确的 catfish_today_summary
    工具, 让 LLM 一眼知道该调它。

数据源:
    1. ~/.hermes/USER.md + memories/*.md  → 今天有更新的 memory
    2. ~/.hermes/skills/<ns>/<name>/SKILL.md  → 今天 mtime 落在今天的
    3. ~/.hermes/state.db sessions  → 今天启动的会话 + token 总量
    4. ~/.hermes/state.db messages  → 今天的 tool 调用次数

跟 companion-app/src-tauri/src/commands/learning.rs 是同一份逻辑的 Python
镜像 —— 故意不走 IPC 调 Companion, 因为 tool-bridge 起来时 Companion 不一
定开着 (CLI 也在用 tool-bridge)。两边各自直读 ~/.hermes 是最 robust 的。
"""
from __future__ import annotations

import os
import sqlite3
import time
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any, Dict, List


# ============================================================
# 工具 schema —— 给 LLM 看的描述
# ============================================================

CATFISH_NATIVE_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "catfish_today_summary",
        "description": (
            "看小鲶今天学到了什么:今天的对话数、工具调用次数、新增/更新的 "
            "memory 条目、新增的 skill、token 消耗总量。当员工问"
            "「今天学了什么」「今天做了啥」「今日活动」「今天有什么新进展」"
            "「小鲶今天怎么样」之类的问题时调用这个 tool, 而不是 "
            "session_search 或 memory_recall —— 那两个是给你自己翻历史的, "
            "回答员工的「今日」相关问题就用 catfish_today_summary。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
        "emoji": "📊",
        "toolset": "catfish_native",
        "available": True,
    },
]


# ============================================================
# 实现
# ============================================================

def _home() -> Path:
    return Path(os.environ.get("HOME") or os.environ.get("USERPROFILE") or ".")


def _hermes_dir() -> Path:
    return _home() / ".hermes"


def _today_start_unix() -> float:
    """本地时区今天 00:00:00 的 unix 秒。"""
    today = datetime.now().date()
    return datetime.combine(today, dtime.min).timestamp()


def _is_today(mtime: float) -> bool:
    return mtime >= _today_start_unix()


def _unix_to_iso(secs: float) -> str:
    try:
        return datetime.fromtimestamp(secs).isoformat()
    except Exception:
        return ""


def _collect_memories() -> List[Dict[str, Any]]:
    """读 ~/.hermes/USER.md + memories/*.md, 按 mtime 降序返回。"""
    out: List[Dict[str, Any]] = []
    hermes = _hermes_dir()

    user_md = hermes / "USER.md"
    if user_md.exists():
        try:
            st = user_md.stat()
            out.append({
                "name": "USER",
                "size": st.st_size,
                "modified_at": _unix_to_iso(st.st_mtime),
                "modified_today": _is_today(st.st_mtime),
            })
        except OSError:
            pass

    mem_dir = hermes / "memories"
    if mem_dir.is_dir():
        for entry in mem_dir.iterdir():
            if entry.suffix != ".md" or not entry.is_file():
                continue
            try:
                st = entry.stat()
            except OSError:
                continue
            out.append({
                "name": f"memories/{entry.stem}",
                "size": st.st_size,
                "modified_at": _unix_to_iso(st.st_mtime),
                "modified_today": _is_today(st.st_mtime),
            })

    out.sort(key=lambda m: m["modified_at"], reverse=True)
    return out


def _extract_description(text: str) -> str:
    """从 SKILL.md frontmatter 抽 description 字段。"""
    stripped = text.lstrip()
    if not stripped.startswith("---"):
        return ""
    after = stripped[3:].lstrip("\n")
    end = after.find("\n---")
    if end < 0:
        return ""
    for line in after[:end].splitlines():
        if line.startswith("description:"):
            value = line[len("description:"):].strip().strip('"').strip("'")
            if value:
                return value
    return ""


def _collect_new_skills() -> List[Dict[str, Any]]:
    """~/.hermes/skills/<ns>/<name>/SKILL.md mtime 落在今天的算"今天新增"。"""
    out: List[Dict[str, Any]] = []
    skills_dir = _hermes_dir() / "skills"
    if not skills_dir.is_dir():
        return out

    for ns_dir in skills_dir.iterdir():
        if not ns_dir.is_dir() or ns_dir.name.startswith("."):
            continue
        for skill_dir in ns_dir.iterdir():
            if not skill_dir.is_dir():
                continue
            manifest = skill_dir / "SKILL.md"
            if not manifest.exists():
                continue
            try:
                st = manifest.stat()
            except OSError:
                continue
            if not _is_today(st.st_mtime):
                continue
            try:
                desc = _extract_description(manifest.read_text(encoding="utf-8"))
            except OSError:
                desc = ""
            out.append({
                "full_name": f"{ns_dir.name}/{skill_dir.name}",
                "description": desc or "(无 description)",
                "modified_at": _unix_to_iso(st.st_mtime),
            })

    out.sort(key=lambda s: s["modified_at"], reverse=True)
    return out


def _collect_db_stats() -> Dict[str, int]:
    """state.db: 今天的 sessions / token / tool_calls 数。"""
    db_path = _hermes_dir() / "state.db"
    fallback = {"sessions_today": 0, "tool_calls_today": 0, "total_tokens_today": 0}
    if not db_path.exists():
        return fallback

    today_start = _today_start_unix()
    try:
        # read-only 打开,免得污染 hermes 自己的 WAL
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error:
        return fallback

    try:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS n,
                COALESCE(SUM(
                    COALESCE(input_tokens, 0) +
                    COALESCE(output_tokens, 0) +
                    COALESCE(cache_read_tokens, 0) +
                    COALESCE(cache_write_tokens, 0) +
                    COALESCE(reasoning_tokens, 0)
                ), 0) AS tok
            FROM sessions
            WHERE started_at >= ?
            """,
            (today_start,),
        ).fetchone()
        sessions = int(row[0] or 0)
        tokens = int(row[1] or 0)

        tool_row = conn.execute(
            """
            SELECT COUNT(*) FROM messages
            WHERE timestamp >= ?
              AND tool_calls IS NOT NULL
              AND tool_calls != ''
            """,
            (today_start,),
        ).fetchone()
        tool_calls = int(tool_row[0] or 0)
    except sqlite3.Error:
        return fallback
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return {
        "sessions_today": sessions,
        "tool_calls_today": tool_calls,
        "total_tokens_today": tokens,
    }


def _build_summary(
    memories_today: int,
    skills_today: int,
    sessions: int,
    tool_calls: int,
) -> str:
    if memories_today == 0 and skills_today == 0 and sessions == 0:
        return "今天还没动静——跟小鲶聊聊它就开始学了。"
    parts: List[str] = []
    if sessions > 0:
        parts.append(f"{sessions} 次对话")
    if tool_calls > 0:
        parts.append(f"调用 {tool_calls} 次工具")
    if memories_today > 0:
        parts.append(f"更新 {memories_today} 条 memory")
    if skills_today > 0:
        parts.append(f"新增 {skills_today} 个 skill")
    return f"今天小鲶 {'、'.join(parts)}。"


def collect_today_summary() -> Dict[str, Any]:
    """返回与 Tauri learning_today_stats 对齐的字段(camelCase 风格), 给 LLM 看。"""
    memories = _collect_memories()
    memories_today = sum(1 for m in memories if m["modified_today"])

    new_skills = _collect_new_skills()
    skills_today = len(new_skills)

    db_stats = _collect_db_stats()
    sessions = db_stats["sessions_today"]
    tool_calls = db_stats["tool_calls_today"]
    tokens = db_stats["total_tokens_today"]

    summary = _build_summary(memories_today, skills_today, sessions, tool_calls)

    return {
        "summary": summary,
        "date": datetime.now().date().isoformat(),
        "sessions_today": sessions,
        "tool_calls_today": tool_calls,
        "total_tokens_today": tokens,
        "memories_updated_today": memories_today,
        "memories": [m for m in memories if m["modified_today"]],
        "new_skills_count": skills_today,
        "new_skills": new_skills,
        "generated_at": _unix_to_iso(time.time()),
    }


# ============================================================
# dispatch 入口
# ============================================================

NATIVE_TOOL_NAMES = {t["name"] for t in CATFISH_NATIVE_TOOLS}


def is_native(name: str) -> bool:
    return name in NATIVE_TOOL_NAMES


def dispatch_native(name: str, args: Dict[str, Any]) -> Any:  # noqa: ARG001
    """目前只有一个 tool, 入参也都是空 dict。"""
    if name == "catfish_today_summary":
        return collect_today_summary()
    raise ValueError(f"unknown native tool: {name}")
