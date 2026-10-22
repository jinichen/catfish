"""P2 (10/23): catfish_wiki_read 带关系 (出边 + 入边), 可按名字读。"""
from __future__ import annotations

import json
import sqlite3

from catfish_tool_bridge import wiki_files


def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    (tmp_path / "wiki" / "entities").mkdir(parents=True)
    for slug, title in (("co", "中电福富信息科技有限公司"), ("mkt", "市场部")):
        (tmp_path / "wiki" / "entities" / f"{slug}.md").write_text(
            f"---\ntype: entity\ntitle: {title}\n---\n\n# {title}\n", encoding="utf-8")
    con = sqlite3.connect(tmp_path / "wiki_graph.db")
    con.execute("CREATE TABLE wiki_graph_node (rel_path TEXT PRIMARY KEY, title TEXT, slug TEXT, kind TEXT, aliases TEXT, mtime REAL)")
    con.execute("CREATE TABLE wiki_graph_edge (source_path TEXT, target_path TEXT, relation_type TEXT, source_name TEXT)")
    con.execute("INSERT INTO wiki_graph_node VALUES ('wiki/entities/co.md','中电福富信息科技有限公司','co','entity',?,0)",
                (json.dumps(["中电福富"], ensure_ascii=False),))
    con.execute("INSERT INTO wiki_graph_node VALUES ('wiki/entities/mkt.md','市场部','mkt','entity','[]',0)")
    con.execute("INSERT INTO wiki_graph_edge VALUES ('wiki/entities/mkt.md','wiki/entities/co.md','隶属','')")
    con.commit()
    con.close()


def test_读条目带入边(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    r = wiki_files.tool_wiki_read({"rel_path": "wiki/entities/co.md"})
    assert r["ok"] and r["relations"]["in"] == [{"rel": "隶属", "source": "市场部", "rel_path": "wiki/entities/mkt.md"}]
    assert r["relations"]["out"] == []


def test_按别名读(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    r = wiki_files.tool_wiki_read({"name": "中电福富"})
    assert r["ok"] and r["rel_path"] == "wiki/entities/co.md"
    miss = wiki_files.tool_wiki_read({"name": "不存在"})
    assert not miss["ok"] and miss["candidates"] == []


def test_没有图库时照旧只读文件(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    (tmp_path / "wiki_graph.db").unlink()
    r = wiki_files.tool_wiki_read({"rel_path": "wiki/entities/co.md"})
    assert r["ok"] and "relations" not in r
