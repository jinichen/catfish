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



# ─────────────────────────────────────────────────────────────
# 检索式回退 —— 给没有可用 sources 的条目用
# ─────────────────────────────────────────────────────────────
#
# 8/4 实测鸿波机器 255 条的 sources 分布:
#
#     75 (29%)  employee_journal   ← 老常量格式 (P3.5.205 之前), 指不到具体日期
#     73 (28%)  journal:YYYY-MM-DD ← 可溯源
#     66 (25%)  wiki:raw/sources/  ← 指向原始材料
#     39 (15%)  没写
#
# 我一度说这 114 条 (44%) "无法溯源, 不动 —— 改写历史 sources 等于伪造溯源"。
# 鸿波反问"那就应该做完, 为什么做一半"。他是对的, 我把两件事混为一谈了:
#
#   · 给条目编一个它没有的来源            → 伪造, 不能做
#   · 用现有证据把它的来源查出来          → 正当, 而且证据都在
#
# 溯源的目的是**员工看到一条可疑的断言能查证**, 不是"每条都有个 sources 字段"。
# 实测证据充足: employee_journal.md 里搜标题有命中, state.db 的 messages_fts
# (50794 条消息全文索引) 也能按标题定位到具体会话。
#
# 所以做法是: sources 指不到日期时, 按标题 + aliases 去 journal 和会话全文里
# 检索, 结果**明确标注成 inferred (检索推断)**, 跟 recorded (记录的) 分开。
# 检索命中不等于"这条就是从那儿来的" —— 那是员工自己判断的事, 工具只负责把
# 证据摆出来, 并且如实说这是推断。

def _fts_quote(term: str) -> str:
    """FTS5 MATCH 的入参要转义, 否则标题里的标点会被当查询语法。

    直接把整个词用双引号包成短语查询, 内部的双引号翻倍转义。带 `-` `:` `*`
    的标题 (比如 CMMI-5 / ISO/IEC 17021-1:2015) 不转义会直接语法错。
    """
    return '"' + term.replace('"', '""') + '"'


def _search_journal(catfish_home: Path, terms: list[str], limit: int = 8) -> list[dict[str, str]]:
    """在 journal 里按词检索, 返回**命中的条目**(含日期和 session 提示)。"""
    jp = catfish_home / "employee_journal.md"
    if not jp.is_file() or not terms:
        return []
    try:
        text = jp.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    heads = list(_JOURNAL_HEAD.finditer(text))
    out = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        body = text[m.end():end]
        hit = [t for t in terms if t and t in body]
        if not hit:
            continue
        out.append({
            "date": m.group(1),
            "sid_hint": m.group(3),
            "matched_terms": ", ".join(hit),
            "summary": body.strip()[:400],
        })
        if len(out) >= limit:
            break
    return out


def _search_sessions_fts(terms: list[str], limit: int = 5) -> list[dict[str, Any]]:
    """在 state.db 的 messages_fts 里按词检索, 返回提到该词最多的会话。"""
    db = _state_db()
    if db is None or not terms:
        return []
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=3.0)
    except sqlite3.Error:
        return []
    seen: dict[str, dict[str, Any]] = {}
    try:
        for t in terms:
            if not t or len(t) < 2:
                continue
            try:
                rows = con.execute(
                    "SELECT m.session_id, COUNT(*) AS n FROM messages_fts f "
                    "JOIN messages m ON m.id = f.rowid "
                    "WHERE messages_fts MATCH ? "
                    "GROUP BY m.session_id ORDER BY n DESC LIMIT ?",
                    (_fts_quote(t), limit),
                ).fetchall()
            except sqlite3.Error as e:
                logger.warning("FTS 查 %r 失败: %s", t, e)
                continue
            for sid, n in rows:
                cur = seen.setdefault(
                    sid, {"session_id": sid, "mentions": 0, "matched_terms": []}
                )
                cur["mentions"] += n
                cur["matched_terms"].append(t)
        # 补会话元信息
        for sid, rec in seen.items():
            r = con.execute(
                "SELECT started_at, message_count, title, model FROM sessions WHERE id = ?",
                (sid,),
            ).fetchone()
            if r:
                rec.update(started_at=r[0], message_count=r[1], title=r[2], model=r[3])
    finally:
        con.close()
    return sorted(seen.values(), key=lambda x: -x["mentions"])[:limit]


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

    # ── 记录的 sources 指不到日期 → 走检索式回退 ─────────────────
    #
    # 8/4: 一度把这 44% (75 条老常量 employee_journal + 39 条没写 sources) 当成
    # "无法溯源, 不能动"。那是把两件事混了 —— 给条目编来源是伪造, 用现有证据
    # 查出来是正当的。溯源的目的是员工能查证, 不是每条都有个 sources 字段。
    #
    # 检索命中**不等于**"这条就是从那儿来的", 所以 provenance 明确标 inferred,
    # 跟 recorded 分开。判断是员工的事, 工具只负责把证据摆出来并说清它是推断。
    # other_sources 里哪些是**真实材料指针** (raw/sources / file:), 哪些只是
    # 老常量 employee_journal (P3.5.205 之前的写法, 指不到任何具体东西)。
    # 8/4 第一版把两者混成一类, 于是指向 raw/sources 的条目被说成"老常量格式" ——
    # 那是错的, 它有明确来源, 只是不走 journal 这条链。
    material_refs = [
        x for x in other_sources
        if x.startswith(("wiki:raw", "raw/", "file:", "wiki:"))
    ]
    legacy_const = [x for x in other_sources if x == "employee_journal"]

    provenance = "recorded" if journal_hits else ("recorded-material" if material_refs else None)
    inferred_journal: list[dict[str, str]] = []
    inferred_sessions: list[dict[str, Any]] = []
    terms: list[str] = []
    if not journal_hits:      # 有真实材料指针时也查, 当补充证据
        title = W._parse_field(fm, "title") or ""
        aliases = W._parse_list(fm, "aliases")
        slug = rel_path.rsplit("/", 1)[-1][:-3]
        terms = [t for t in ([title] + aliases) if t and len(t) >= 2]
        if not terms and slug:
            terms = [slug]
        inferred_journal = _search_journal(home, terms)
        inferred_sessions = _search_sessions_fts(terms)
        if (inferred_journal or inferred_sessions) and provenance is None:
            provenance = "inferred"

    if with_messages:
        for s_ in sessions + inferred_sessions:
            s_["messages"] = _messages(s_["session_id"])

    # 如实说清楚这条链断在哪 / 是记录的还是查出来的
    if journal_hits and sessions:
        n_multi = sum(1 for x in sessions if x["matched_by"] == "suffix")
        verdict = (
            f"【记录的来源】{len(journal_hits)} 条 journal → {len(sessions)} 个会话。"
            + (f" 其中 {n_multi} 个靠 session id 后缀匹配 (存量 journal 只记后 6 位), "
               "同一天内基本唯一但不保证。" if n_multi else "")
        )
    elif journal_hits:
        verdict = ("【记录的来源】journal 找到了, 但里面的 session 提示在 state.db "
                   "匹配不到 (会话被删/归档)。只能看 journal 这层摘要。")
    elif material_refs:
        extra = (f" 另外按标题检索到 {len(inferred_sessions)} 个会话提到它, 可作旁证。"
                 if inferred_sessions else "")
        verdict = (f"【记录的来源】sources 指向原始材料 ({', '.join(material_refs)}) —— "
                   f"不走 journal 链, 直接看那份材料。{extra}")
    elif provenance == "inferred":
        why = ("sources 是老常量 employee_journal (P3.5.205 之前的写法), 指不到具体日期"
               if legacy_const else "这条没写 sources")
        verdict = (
            f"【检索推断, 不是记录的来源】{why}。按标题/别名 ({', '.join(terms[:3])}) "
            f"检索到 {len(inferred_journal)} 条 journal、{len(inferred_sessions)} 个会话。\n"
            "⚠ 命中只说明这些材料提到了同一个词, **不能证明条目是从它们蒸馏来的**。"
            "请员工自己对照判断。"
        )
    else:
        verdict = ("既没有可用 sources, 按标题检索也没命中 —— 这条可能是手工建的, "
                   "或者标题跟原文用词不一致。只能人工判断。")

    return {
        "ok": True,
        "rel_path": rel_path,
        "title": W._parse_field(fm, "title") or "",
        "provenance": provenance or "none",
        "sources": sources,
        "other_sources": other_sources,
        "material_refs": material_refs,
        "legacy_const_sources": legacy_const,
        "journal_entries": journal_hits,
        "sessions": sessions,
        "inferred_journal_entries": inferred_journal,
        "inferred_sessions": inferred_sessions,
        "searched_terms": terms,
        "verdict": verdict,
    }


def tool_wiki_trace(args: dict) -> dict[str, Any]:
    rel = str(args.get("rel_path", "")).strip()
    if not rel:
        return {"ok": False, "error": "rel_path 不能空 (先用 catfish_wiki_list 拿路径)"}
    return trace_wiki_entry(rel, with_messages=bool(args.get("with_messages")))
