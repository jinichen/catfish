"""溯源: 记录的来源 vs 检索推断的, 必须分得清清楚楚。

# 这组测试守的是什么 (8/4)

知识库 66% 是 journal 蒸馏来的 (摘要的摘要, 四次 LLM 转写), 失真是压缩的物理
必然。真正决定它能不能用的是**看到一条错的能不能查证**。

第一版只走"记录的 sources", 覆盖 141/255 = 55%; 我当时说剩下 44% "无法溯源,
不动 —— 改写历史 sources 等于伪造溯源", 被鸿波反问"那就该做完, 为什么做一半"。

他是对的, 我把两件事混了:
  · 给条目编一个它没有的来源   → 伪造, 不能做
  · 用现有证据把来源查出来     → 正当, 而且证据都在 (journal 全文 + state.db
                                 的 messages_fts 5 万条消息索引)

加了检索式回退之后 141 → 236 (55% → 92%)。

但**推断必须标成推断**。检索命中只说明那些材料提到了同一个词, 不能证明条目是
从它们蒸馏来的。把推断混进"记录的来源"里, 等于制造一个看起来可信的假溯源 ——
比没有溯源更坏。这组测试钉的就是这条边界。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from catfish_tool_bridge import wiki_trace as T


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / ".catfish"
    (home / "wiki" / "entities").mkdir(parents=True)
    monkeypatch.setenv("CATFISH_HOME", str(home))

    db = tmp_path / "state.db"
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE sessions (id TEXT PRIMARY KEY, started_at REAL,
                               message_count INT, title TEXT, model TEXT);
        CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT,
                               role TEXT, content TEXT, timestamp REAL);
        CREATE VIRTUAL TABLE messages_fts USING fts5(content);
        """
    )
    con.execute("INSERT INTO sessions VALUES ('20260717_101010_8d4bfe',1,20,'高新','m')")
    con.execute("INSERT INTO sessions VALUES ('20260601_060000_6b6e81',1,4,'集成','m')")
    for i, (sid, txt) in enumerate(
        [("20260717_101010_8d4bfe", "今天聊了高新资质申报的进度"),
         ("20260601_060000_6b6e81", "集成部那边的排期确认了")], start=1
    ):
        con.execute("INSERT INTO messages VALUES (?,?,?,?,?)", (i, sid, "user", txt, 1.0))
        con.execute("INSERT INTO messages_fts(rowid, content) VALUES (?,?)", (i, txt))
    con.commit(); con.close()
    monkeypatch.setenv("HERMES_STATE_DB", str(db))

    (home / "employee_journal.md").write_text(
        "## [2026-07-17 10:10] session | 20260717_101010_8d4bfe\n\n"
        "推进了高新资质申报。\n\n"
        "## [2026-06-01 06:00] session | 6b6e81\n\n"
        "集成部排期确认。\n\n",
        encoding="utf-8",
    )
    return home


def _entry(home: Path, name: str, sources: str, title: str, aliases: str = "[]") -> str:
    rel = f"wiki/entities/{name}.md"
    (home / rel).write_text(
        f"---\ntype: entity\ntitle: {title}\nsources: {sources}\naliases: {aliases}\n---\n\n"
        f"# {title}\n\n正文。\n",
        encoding="utf-8",
    )
    return rel


def test_recorded_journal_chain(env):
    """sources 是 journal:日期 → 记录的来源, 能接到真实会话。"""
    rel = _entry(env, "gaoxin", '["journal:2026-07-17"]', "高新资质申报")
    r = T.trace_wiki_entry(rel)
    assert r["provenance"] == "recorded", r["verdict"]
    assert r["journal_entries"] and r["sessions"]
    assert r["sessions"][0]["session_id"] == "20260717_101010_8d4bfe"
    assert r["sessions"][0]["matched_by"] == "exact"   # 8/4 起写完整 id
    assert "记录的来源" in r["verdict"]


def test_suffix_match_is_labelled(env):
    """存量 journal 只记后 6 位 → 靠后缀匹配, 必须在 verdict 里说出来。"""
    rel = _entry(env, "jicheng", '["journal:2026-06-01"]', "集成部")
    r = T.trace_wiki_entry(rel)
    assert r["provenance"] == "recorded"
    assert r["sessions"][0]["matched_by"] == "suffix"
    assert "后缀匹配" in r["verdict"] and "不保证" in r["verdict"]


def test_legacy_constant_falls_back_to_search(env):
    """老常量 employee_journal 指不到日期 → 检索推断, 且必须标成推断。"""
    rel = _entry(env, "jicheng2", '["employee_journal"]', "集成部")
    r = T.trace_wiki_entry(rel)
    assert r["provenance"] == "inferred", r["verdict"]
    assert r["inferred_journal_entries"] or r["inferred_sessions"]
    assert "检索推断" in r["verdict"]
    assert "不能证明" in r["verdict"], "必须说清命中≠来源"
    assert not r["journal_entries"], "推断的结果不能混进 journal_entries"


def test_no_sources_falls_back_to_search(env):
    rel = _entry(env, "gaoxin2", "[]", "高新资质申报")
    r = T.trace_wiki_entry(rel)
    assert r["provenance"] == "inferred"
    assert "没写 sources" in r["verdict"]


def test_material_ref_is_recorded_not_inferred(env):
    """指向 raw/sources 的是**真实来源**, 不是"老常量"。

    8/4 第一版把它误归成 inferred, verdict 还说它"是老常量格式" —— 错的。
    """
    rel = _entry(env, "juzhen", '["wiki:raw/sources/1785759315-矩阵.md"]', "对齐矩阵")
    r = T.trace_wiki_entry(rel)
    assert r["provenance"] == "recorded-material", r["verdict"]
    assert r["material_refs"] == ["wiki:raw/sources/1785759315-矩阵.md"]
    assert "记录的来源" in r["verdict"] and "老常量" not in r["verdict"]


def test_nothing_found_says_so(env):
    """查不到就说查不到 —— 不能编一个来源出来。"""
    rel = _entry(env, "kongbai", "[]", "一个谁都没提过的词ZZZQQQ")
    r = T.trace_wiki_entry(rel)
    assert r["provenance"] == "none"
    assert "只能人工判断" in r["verdict"]
    assert not r["inferred_sessions"]


def test_fts_quote_handles_punctuation(env):
    """标题带 - : / 的 (CMMI-5 / ISO/IEC 17021-1:2015) 不转义会让 FTS 语法错。"""
    rel = _entry(env, "iso", "[]", "ISO/IEC 17021-1:2015")
    r = T.trace_wiki_entry(rel)      # 不抛异常就算过
    assert r["ok"]


def test_missing_file(env):
    assert T.trace_wiki_entry("wiki/entities/不存在.md")["ok"] is False
