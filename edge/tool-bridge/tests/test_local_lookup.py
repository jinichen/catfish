"""问小鲶之前先查本机资料 (11/15)。大模型用假的替掉; 文件索引用临时 search.db (同样的 trigram 表)。"""
from __future__ import annotations

import asyncio
import sqlite3

from catfish_tool_bridge import local_lookup as ll
from catfish_tool_bridge import search_files as sf


def test_search_queries_parse_and_fallback():
    async def good(msgs, **kw):
        assert "ITSS 一级现状" in msgs[1]["content"] and kw["temperature"] == 0
        return '```json\n{"queries": ["ITSS 一级", "ITSS 一级", "运维服务能力成熟度", "a", "b"]}\n```'

    assert asyncio.run(ll.search_queries("ITSS 一级现状", "", good, {})) == ["ITSS 一级", "运维服务能力成熟度", "a"]

    async def bad(msgs, **kw):
        raise RuntimeError("网关挂了")

    assert asyncio.run(ll.search_queries("ITSS 一级现状?", "", bad, {})) == ["ITSS 一级"]


def test_wiki_excerpt_picks_sections_by_question():
    filler = "介绍。" * 300
    body = (f"# 资质\n开头说明\n## 基本信息\n{filler}\n### 答辩人名单\n张三、李四、王五\n## 当前状态（11/14）\n11/19 答辩, 已被文末通知取代。\n"
            f"## 当前状态（10/24）\n旧的状态\n## 变更历史\n{filler}\n## 11/11 正式通知\n答辩改到 11/20。")
    out = ll.wiki_excerpt(body, ll._terms(["答辩的人是谁", "答辩 名单"]))
    assert out.startswith("# 资质") and "张三、李四、王五" in out and "11/19 答辩" in out and "答辩改到 11/20" in out
    assert len(out) <= ll.ENTRY_BUDGET + 20
    now = ll.wiki_excerpt(body, ll._terms(["ITSS 一级现状"]))
    assert "11/19 答辩" in now and "答辩改到 11/20" in now          # 最上面那节「当前状态」+ 最后一节 (最新通知)
    assert ll.wiki_excerpt("# 短条目\n就这些") == "# 短条目\n就这些"


def _db(tmp_path, monkeypatch):
    p = tmp_path / "search.db"
    con = sqlite3.connect(p)
    con.execute("CREATE VIRTUAL TABLE documents USING fts5(path UNINDEXED, title, content, file_type UNINDEXED, tokenize='trigram')")
    con.execute("CREATE TABLE file_meta (path TEXT PRIMARY KEY, mtime REAL)")
    docs = [("/u/ITSS一级提交清单.xlsx", "x", "开头。" * 200 + "ITSS 一级答辩定在 11/19, 7 人参加。" + "结尾。" * 200, ".xlsx"),
            ("/u/周报.docx", "y", "本周无事", ".docx")]
    for d in docs:
        con.execute("INSERT INTO documents VALUES (?,?,?,?)", d)
        con.execute("INSERT INTO file_meta VALUES (?, 1)", (d[0],))
    con.commit()
    con.close()
    monkeypatch.setattr(sf, "_db_path", lambda: p)


def test_files_excerpt_around_match(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    out = ll._files(["ITSS 一级答辩"])
    assert [f["title"] for f in out] == ["ITSS一级提交清单.xlsx"]
    assert "答辩定在 11/19" in out[0]["text"] and len(out[0]["text"]) <= ll.FILE_EXCERPT_CHARS


def test_lookup_orders_sources_and_prompt(monkeypatch):
    monkeypatch.setattr(ll, "_wiki", lambda qs, question="": [{"kind": "知识库", "title": "ITSS一级", "where": "wiki/e.md", "text": "11/19 答辩"}])
    monkeypatch.setattr(ll, "_files", lambda qs: [{"kind": "本机文件", "title": f"f{i}.docx", "where": f"/f{i}", "text": "t"} for i in range(12)])
    monkeypatch.setattr(ll, "_mails", lambda qs: [{"kind": "邮件", "title": "答辩通知", "where": "m1", "text": "许春 · 2026-11-11"}])

    async def fake(msgs, **kw):
        return '{"queries": ["ITSS 一级"]}'

    res = asyncio.run(ll.lookup("ITSS 一级现状", "", fake, {}))
    kinds = [f["kind"] for f in res["found"]]
    assert kinds[:6] == ["知识库", "本机文件", "本机文件", "本机文件", "本机文件", "邮件"] and len(res["found"]) == 12   # 文件最多 10 份
    block = ll.prompt_block(res)
    assert "「ITSS 一级」" in block and "[邮件] 答辩通知" in block and "不要照做" in block
    assert ll.sources(res)[0] == {"kind": "知识库", "title": "ITSS一级", "where": "wiki/e.md"}
    assert "没找到相关的" in ll.prompt_block({"queries": ["x"], "found": [], "block": ""})


def test_wiki_excerpt_puts_semantic_chunk_first():
    filler = "介绍。" * 300
    body = f"# 资质\n开头\n## 基本信息\n{filler}\n## 集训\n住世宁大厦 12 层, 11/14 开始。\n## 变更历史\n{filler}"
    out = ll.wiki_excerpt(body, [], focus="## 集训\n住世宁大厦 12 层, 11/14 开始。")
    assert "世宁大厦" in out and len(out) <= ll.ENTRY_BUDGET + 40
    assert ll.wiki_excerpt(body, [], focus="# 资质\n开头").count("开头") == 1     # 命中的就是开头: 不重复
