"""把一条 wiki 断言接回说出它的那次对话。

# 为什么需要 (8/4 鸿波 "知识库的内容都是LLM自动生成？要怎么提高准确性")

先量了鸿波机器上的真实数据 (255 条):

    169 条 (66%)  ← employee_journal 蒸馏
     64 条 (25%)  ← 一份上传的 Excel
     39 条 (15%)  ← 连 sources 都没写
      5 条        ← manual / chat / file

也就是说知识库几乎全是 LLM 生成的, 而且最大来源是**摘要的摘要**。链条上
LLM 被调了四次:

    对话原文
      ↓ _call_summarize_llm    每 5 轮压成 ≤200 字
    employee_journal.md
      ↓ _call_analysis_llm
      ↓ _call_generation_llm
      ↓ _call_merge_llm        ← 每次更新都再过一遍, 反复
    wiki/entities/*.md

原文在第 1 步之后就退出了, 后面三次都是在改写已经失真的文本。

# 真正致命的不是失真, 是不可验证

失真是压缩的物理必然, 不可能消除。但**看到一条错的能不能查证**, 决定了这个
知识库是能不能用。而在 8/4 之前, 这条路是断的:

    wiki 条目   sources: [journal:2026-07-17]           只到日期
    journal     ## [2026-07-17 17:53] session | …8d4bfe  只留 session id 后 6 位
    state.db    5329 sessions / 50794 messages           原文完整保留

_format_journal_entry 拿到的是**完整 session_id**, 自己截成了后 6 位
(catfish_memory_helpers.py:415)。信息没丢, 只是指针被截断了。

不可验证 = 不可修正。员工看到一条可疑的断言, 只能选择"信"或"不信", 没有第三
条路 —— 这比断言本身错了更糟。

# 这个模块做什么

给一条 wiki 条目, 顺着 sources → journal → state.db 把原始对话拉出来。

session id 形如 `20260803_195928_f39669`, 后 6 位是随机后缀, 同一天内基本唯一,
所以**存量条目也能溯源**, 不用等新数据。匹配不到就如实说匹配不到, 不猜。
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger("catfish.tool_bridge.wiki_trace")

# journal 条目头: `## [2026-07-17 17:53] session | …8d4bfe`
_JOURNAL_HEAD = re.compile(
    r"^##\s*\[(\d{4}-\d{2}-\d{2})[^\]]*\]\s*(\S+)\s*\|\s*…?(\S+)\s*$",
    re.MULTILINE,
)


def _catfish_home() -> Path:
    if env := os.environ.get("CATFISH_HOME"):
        return Path(env)
    return Path.home() / ".catfish"


def _state_db() -> Path | None:
    """跟 sessions_search._state_db_path 同一份路径口径。"""
    if env := os.environ.get("HERMES_STATE_DB"):
        p = Path(env)
        return p if p.exists() else None
    p = Path.home() / ".hermes" / "state.db"
    return p if p.exists() else None


def _journal_entries_on(catfish_home: Path, date: str) -> list[dict[str, str]]:
    """拉某一天 journal 里的全部条目 (头 + 正文)。"""
    jp = catfish_home / "employee_journal.md"
    if not jp.is_file():
        return []
    try:
        text = jp.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    heads = list(_JOURNAL_HEAD.finditer(text))
    out = []
    for i, m in enumerate(heads):
        if m.group(1) != date:
            continue
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        out.append(
            {
                "date": m.group(1),
                "kind": m.group(2),
                "sid_hint": m.group(3),      # 可能是完整 id, 也可能只有后 6 位
                "summary": text[m.end():end].strip(),
            }
        )
    return out


def _resolve_sessions(sid_hints: list[str], date: str) -> list[dict[str, Any]]:
    """把 journal 里的 session 提示解析成真实 session。

    hint 可能是完整 id (8/4 之后写的), 也可能只有后 6 位 (存量)。后者用
    `LIKE '%hint'` 匹配, 并且**如实报告匹配到几条** —— 匹配到多条就说多条,
    不挑一条当答案。
    """
    db = _state_db()
    if db is None:
        return []
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error as e:
        logger.warning("连 state.db 失败: %s", e)
        return []
    out = []
    try:
        for hint in sid_hints:
            if not hint:
                continue
            rows = con.execute(
                "SELECT id, started_at, message_count, title, model "
                "FROM sessions WHERE id = ? OR id LIKE ? ORDER BY started_at",
                (hint, f"%{hint}"),
            ).fetchall()
            for r in rows:
                out.append(
                    {
                        "session_id": r[0],
                        "started_at": r[1],
                        "message_count": r[2],
                        "title": r[3],
                        "model": r[4],
                        "matched_by": "exact" if r[0] == hint else "suffix",
                        "hint": hint,
                    }
                )
    finally:
        con.close()
    return out


def _messages(session_id: str, limit: int = 40) -> list[dict[str, Any]]:
    db = _state_db()
    if db is None:
        return []
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error:
        return []
    try:
        rows = con.execute(
            "SELECT role, content, timestamp FROM messages "
            "WHERE session_id = ? AND role IN ('user','assistant') "
            "ORDER BY id LIMIT ?",
            (session_id, limit),
        ).fetchall()
    finally:
        con.close()
    return [
        {"role": r[0], "content": (r[1] or "")[:1500], "ts": r[2]} for r in rows
    ]


def trace_wiki_entry(rel_path: str, with_messages: bool = False) -> dict[str, Any]:
    """给一条 wiki 条目, 顺着 sources 把原始材料翻出来。

    返回结构分三段, 每段都如实标注"接上了没有":
      · entry     条目自己的 sources
      · journal   sources 指向那些日期的 journal 原文 (第一层摘要)
      · sessions  journal 指回的真实会话 (原始对话)
    """
    from . import wiki_files as W  # noqa: PLC0415 - 复用同一套解析, 避免两份口径

    got = W.read_wiki_file(rel_path)
    if not got.get("ok"):
        return {"ok": False, "error": got.get("error", "读不到这个条目")}

    fm = got["frontmatter"]
    sources = W._parse_list(fm, "sources")
    home = _catfish_home()

    journal_dates, other_sources = [], []
    for s in sources:
        s = s.strip().strip('"').strip("'")
        m = re.match(r"^journal:(\d{4}-\d{2}-\d{2})", s)
        if m:
            journal_dates.append(m.group(1))
        else:
            other_sources.append(s)

    journal_hits: list[dict[str, str]] = []
    for d in sorted(set(journal_dates)):
        journal_hits.extend(_journal_entries_on(home, d))

    sessions = _resolve_sessions([h["sid_hint"] for h in journal_hits],
                                 journal_dates[0] if journal_dates else "")
    if with_messages:
        for s in sessions:
            s["messages"] = _messages(s["session_id"])

    # 如实说清楚这条链断在哪 —— 这个工具的价值就在于不糊弄
    if not sources:
        verdict = ("这条目连 sources 都没写 (255 条里有 39 条是这样), "
                   "无法溯源。只能靠人工判断内容对不对。")
    elif not journal_dates:
        verdict = (f"sources 不是 journal 来源 ({', '.join(other_sources)}), "
                   "不走这条链 —— 直接看那个原始材料。")
    elif not journal_hits:
        verdict = (f"sources 指向 {journal_dates}, 但 employee_journal.md 里"
                   "找不到那几天的条目 (journal 被清过 / 日期对不上)。")
    elif not sessions:
        verdict = ("journal 找到了, 但里面的 session 提示在 state.db 里匹配不到 "
                   "(会话被删 / 归档)。只能看 journal 这层摘要。")
    else:
        n_multi = sum(1 for s in sessions if s["matched_by"] == "suffix")
        verdict = (
            f"接通了: {len(journal_hits)} 条 journal → {len(sessions)} 个会话。"
            + (f" 其中 {n_multi} 个是靠 session id 后缀匹配的 (存量数据 journal "
               "只记了后 6 位), 同一天内基本唯一但不保证。"
               if n_multi else "")
        )

    return {
        "ok": True,
        "rel_path": rel_path,
        "title": W._parse_field(fm, "title") or "",
        "sources": sources,
        "other_sources": other_sources,
        "journal_entries": journal_hits,
        "sessions": sessions,
        "verdict": verdict,
    }


def tool_wiki_trace(args: dict) -> dict[str, Any]:
    rel = str(args.get("rel_path", "")).strip()
    if not rel:
        return {"ok": False, "error": "rel_path 不能空 (先用 catfish_wiki_list 拿路径)"}
    return trace_wiki_entry(rel, with_messages=bool(args.get("with_messages")))
