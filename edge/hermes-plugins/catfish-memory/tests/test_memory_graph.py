"""P2 (10/23): 问题里点名的实体 → 它在知识库里的关系 (catfish_memory_graph)。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from catfish_memory_graph import render_graph


def _db(home: Path, nodes, edges):
    con = sqlite3.connect(home / "wiki_graph.db")
    con.execute("CREATE TABLE wiki_graph_node (rel_path TEXT PRIMARY KEY, title TEXT, slug TEXT, kind TEXT, aliases TEXT, mtime REAL)")
    con.execute("CREATE TABLE wiki_graph_edge (source_path TEXT, target_path TEXT, relation_type TEXT, source_name TEXT)")
    con.executemany("INSERT INTO wiki_graph_node VALUES (?,?,?,?,?,0)",
                    [(p, t, p, "entity", json.dumps(a, ensure_ascii=False)) for p, t, a in nodes])
    con.executemany("INSERT INTO wiki_graph_edge VALUES (?,?,?,'')", edges)
    con.commit()
    con.close()


CO, MKT, IND, ZD, CMMI = (f"wiki/entities/{n}.md" for n in ("co", "mkt", "ind", "zd", "cmmi"))


def _setup(tmp_path):
    _db(tmp_path,
        [(CO, "中电福富信息科技有限公司", ["中电福富"]), (MKT, "市场部", []), (IND, "工业互联网事业部", []),
         (ZD, "金智达", ["北京金智达"]), (CMMI, "CMMI-5", [])],
        [(MKT, CO, "隶属"), (IND, CO, "隶属"), (CO, ZD, "协作"), (ZD, CO, "协作"), (CO, CMMI, "持有"), (CO, MKT, "关联")])


def test_入边按关系分组_对称关系只列一次(tmp_path):
    _setup(tmp_path)
    out = render_graph(tmp_path, "中电福富有哪些部门")
    assert "隶属 中电福富信息科技有限公司 的: 市场部、工业互联网事业部" in out
    assert "中电福富信息科技有限公司 协作: 金智达" in out and out.count("金智达") == 1
    assert "中电福富信息科技有限公司 持有: CMMI-5" in out
    assert "相关 (未标关系类型): 市场部" in out


def test_长名字优先_不重复算短的(tmp_path):
    _setup(tmp_path)
    out = render_graph(tmp_path, "北京金智达和中电福富信息科技有限公司")
    assert out.count("(`wiki/entities/co.md`)") == 1 and "(`wiki/entities/zd.md`)" in out


def test_没点名_没图库_都不出(tmp_path):
    assert render_graph(tmp_path, "中电福富") == ""
    _setup(tmp_path)
    assert render_graph(tmp_path, "今天先做什么") == ""
    assert render_graph(tmp_path, "") == ""


def test_正文提及单独一行_不算关系(tmp_path):
    _db(tmp_path, [(CO, "中电福富信息科技有限公司", ["中电福富"]), (CMMI, "CMMI-5", []), (ZD, "金智达", [])],
        [(CO, CMMI, "持有"), (CMMI, CO, "提及"), (ZD, CO, "提及")])
    out = render_graph(tmp_path, "中电福富")
    assert "中电福富信息科技有限公司 持有: CMMI-5" in out
    assert "正文里提到 (不是关系): CMMI-5、金智达" in out
    assert "未标关系类型" not in out
