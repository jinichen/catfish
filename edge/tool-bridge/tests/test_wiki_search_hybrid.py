"""11/08 知识库检索整改 (鸿波机器实测暴露的): 编号搜不到 (分词) / 对话里只有弱版搜索 / 过期待确认照列 /
按属性查排序错 / 部门共享页读不了 / 装机根目录不一致。全部虚构数据。"""
from __future__ import annotations

import json
import socket
import sqlite3
import struct
import threading
from pathlib import Path

import pytest

from catfish_tool_bridge import search_skills as ss
from catfish_tool_bridge import wiki_files, wiki_install, wiki_types
from catfish_tool_bridge import wiki_search as ws


# ── 分词 ────────────────────────────────────────────────────────

def test_编号后面紧跟中文标点_不再粘进词里():
    toks = ss._tokenize("证书编号ZTY2026011513590212，由中霆誉（北京）。文号〔2026〕48号；CCRC-2019-ISV-SD-210")
    assert "zty2026011513590212" in toks and "zty2026011513590212，" not in toks
    assert "2026" in toks and "48" in toks and "ccrc" in toks and "北" in toks
    assert ss._tokenize("leadership_briefing PPT文档") == ["leadership_briefing", "ppt", "文", "档"]


# ── 混合搜索 ─────────────────────────────────────────────────────

@pytest.fixture()
def home(tmp_path, monkeypatch):
    h = tmp_path / ".catfish"
    for sub in ("wiki/entities", "wiki/concepts", "wiki/queries", "wiki-shared/dept/研发部"):
        (h / sub).mkdir(parents=True)
    monkeypatch.setenv("CATFISH_HOME", str(h))
    ws._cache.update({"fingerprint": "", "index": None, "meta": []})
    ws._vec_cache.update({"mtime": None, "rows": []})
    ws._embed_down_until = 0.0
    return h


def _page(h: Path, rel: str, title: str, body: str, **fm):
    lines = [f"title: {title}"]
    for k, v in fm.items():
        lines.append(f"{k}: {v}")
    (h / rel).write_text("---\n" + "\n".join(lines) + "\n---\n\n" + body + "\n", encoding="utf-8")


def test_属性值_别名_关系对象名都能搜到_结果带类型和属性(home):
    _page(home, "wiki/entities/cert-a.md", "无人机反制证书", "正文只说这是一张证书。", type="entity", entity_type="cert",
          cert_no='"ZTY2026011513590212"', valid_until="2030-01-01", aliases='["反制证"]',
          related='[{name: "中霆誉", rel: "持有"}]')
    _page(home, "wiki/entities/org-b.md", "中霆誉", "一家发证机构。", type="entity", entity_type="org")
    r = ws.search_wiki("ZTY2026011513590212", 3)
    assert r["matches"][0]["title"] == "无人机反制证书" and r["semantic"] is False
    m = r["matches"][0]
    assert m["type"] == "cert" and m["props"]["cert_no"] == "ZTY2026011513590212" and m["status"] == "有效"
    assert m["aliases"] == ["反制证"] and m["relations"] == 1
    assert ws.search_wiki("反制证", 3)["matches"][0]["title"] == "无人机反制证书"
    assert "只按字面匹配" in r["summary"]


def test_正文两千字以后的内容也搜得到(home):
    _page(home, "wiki/concepts/long.md", "长文", "前面是废话。" * 400 + "深处藏着一句: 福富知识管理平台的口令规则。", type="concept",
          concept_type="rule")
    assert ws.search_wiki("福富知识管理平台 口令规则", 3)["matches"][0]["title"] == "长文"


def test_已过期和待确认的默认不列_summary说明_要看可以传参(home):
    _page(home, "wiki/entities/old.md", "旧证书", "已经过期的证书。", type="entity", entity_type="cert", valid_until="2020-01-01")
    _page(home, "wiki/entities/new.md", "新证书", "还有效的证书。", type="entity", entity_type="cert", valid_until="2099-01-01")
    _page(home, "wiki/entities/pend.md", "待确认证书", "关系没对上的证书。", type="entity", entity_type="cert",
          ontology_status="pending", valid_until="2099-01-01")
    r = ws.search_wiki("证书", 10)
    assert [m["title"] for m in r["matches"]] == ["新证书"]
    assert r["hidden"] == {"expired": 1, "pending": 1} and "1 条已过期" in r["summary"] and "1 条待确认" in r["summary"]
    r2 = ws.search_wiki("证书", 10, include_expired=True, include_pending=True)
    assert {m["title"] for m in r2["matches"]} == {"旧证书", "新证书", "待确认证书"} and r2["hidden"] == {}


def test_废弃的_历史状态的不进索引(home):
    _page(home, "wiki/entities/dep.md", "废弃页", "证书。", type="entity", deprecated="true")
    _page(home, "wiki/entities/hist.md", "历史页", "证书。", type="entity", status="historical")
    _page(home, "wiki/entities/ok.md", "现用页", "证书。", type="entity")
    assert ws.search_wiki("证书", 10)["total_indexed"] == 1


def _fake_companion(home: Path, vec_for: dict[str, list[float]]):
    """假 Companion 向量口: 认得的文本给固定向量, 别的给零向量。"""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    (home / "companion-embed.port").write_text(str(srv.getsockname()[1]))

    def run():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            with conn:
                data = conn.makefile("rb").readline()
                texts = json.loads(data)["texts"]
                vecs = [vec_for.get(t, [0.0] * 4) for t in texts]
                conn.sendall((json.dumps({"model": "fake", "dim": 4, "vectors": vecs}) + "\n").encode())
    threading.Thread(target=run, daemon=True).start()
    return srv


def _vector_db(home: Path, rows: list[tuple[str, str, list[float]]]):
    con = sqlite3.connect(home / "wiki_embeddings.db")
    con.execute("CREATE TABLE wiki_embed_chunk (chunk_id TEXT PRIMARY KEY, rel_path TEXT, title TEXT, kind TEXT, heading TEXT,"
                " chunk_index INTEGER, snippet TEXT, text TEXT, vector BLOB, mtime INTEGER, content_hash TEXT)")
    for i, (rel, snippet, v) in enumerate(rows):
        con.execute("INSERT INTO wiki_embed_chunk VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (f"{rel}#{i}", rel, "", "entity", "", i, snippet, snippet, struct.pack("4f", *v), 0, ""))
    con.commit()
    con.close()


def test_借Companion的向量_按意思命中_关系扩一跳_已命中的只加分(home):
    _page(home, "wiki/entities/cmmi.md", "CMMI-5 软件成熟度评估", "五级。", type="entity", entity_type="cert",
          related='[{name: "赛宝", rel: "发证"}]')
    _page(home, "wiki/entities/saibao.md", "赛宝", "认证机构。", type="entity", entity_type="org")
    _page(home, "wiki/entities/other.md", "无关条目", "跟软件成熟度一个字都不沾。", type="entity", entity_type="org")
    _page(home, "wiki/entities/hub.md", "枢纽", "关系很多。", type="entity", entity_type="org",
          related="[" + ", ".join(f'{{name: "n{i}", rel: "包含"}}' for i in range(70)) + "]")
    for i in range(70):
        _page(home, f"wiki/entities/n{i}.md", f"n{i}", "小条目。", type="entity", entity_type="org")
    _vector_db(home, [("wiki/entities/cmmi.md", "五级那一段", [1, 0, 0, 0]), ("wiki/entities/other.md", "无关", [0, 1, 0, 0]),
                      ("wiki/entities/hub.md", "枢纽", [0, 0, 1, 0])])
    srv = _fake_companion(home, {"公司软件成熟度几级": [1, 0, 0, 0]})
    try:
        r = ws.search_wiki("公司软件成熟度几级", 5)
    finally:
        srv.close()
    assert r["semantic"] is True
    titles = [m["title"] for m in r["matches"]]
    assert titles[0] == "CMMI-5 软件成熟度评估" and "semantic" in r["matches"][0]["matched_in"]
    assert r["matches"][0]["head"] == "五级那一段", "head 是最像的那段, 不是正文开头"
    sb = next(m for m in r["matches"] if m["title"] == "赛宝")
    assert sb["matched_in"] == ["graph-1hop"] and sb["via"] == "CMMI-5 软件成熟度评估"
    other = next((m for m in r["matches"] if m["title"] == "无关条目"), None)
    assert other is None or "semantic" not in other["matched_in"], "相似度不过门槛的不算按意思命中"
    assert not any(t.startswith("n") for t in titles), "枢纽的邻居不往外扩"


def test_Companion没开_退回词法_一分钟内不再试(home, monkeypatch):
    _page(home, "wiki/entities/a.md", "条目", "证书。", type="entity")
    _vector_db(home, [("wiki/entities/a.md", "x", [1, 0, 0, 0])])
    (home / "companion-embed.port").write_text("1")   # 没人听
    calls = []
    real = socket.create_connection

    def counting(*a, **k):
        calls.append(1)
        return real(*a, **k)
    monkeypatch.setattr(socket, "create_connection", counting)
    assert ws.search_wiki("证书", 3)["semantic"] is False
    assert ws.search_wiki("证书", 3)["semantic"] is False
    assert len(calls) == 1


def test_部门共享页_搜得到也读得了(home):
    _page(home, "wiki-shared/dept/研发部/abc123.md", "部门规范", "代码评审规范全文。", type="concept")
    r = ws.search_wiki("代码评审", 3)
    assert r["matches"][0]["source"] == "dept/研发部"
    got = wiki_files.read_wiki_file(r["matches"][0]["rel_path"])
    assert got["ok"] and "代码评审规范全文" in got["body"]
    assert not wiki_files.read_wiki_file("wiki-shared/../x.md")["ok"]


# ── 按属性查 ─────────────────────────────────────────────────────

def test_按属性查_先排序再截断_没填的排最后_部门共享页也算_废弃的不算(home):
    for i, (slug, until) in enumerate([("z", "2027-01-01"), ("a", "2029-01-01"), ("m", ""), ("b", "2026-06-01")]):
        _page(home, f"wiki/entities/{slug}.md", f"证书{slug}", "x", type="entity", entity_type="cert",
              **({"valid_until": until} if until else {}))
    _page(home, "wiki/entities/dep.md", "废弃证书", "x", type="entity", entity_type="cert", valid_until="2025-01-01", deprecated="true")
    _page(home, "wiki-shared/dept/研发部/f1.md", "部门证书", "x", type="entity", entity_type="cert", valid_until="2028-01-01")
    r = wiki_types.query({"type": "cert", "sort": "valid_until", "limit": 2})
    assert r["total"] == 5 and [x["title"] for x in r["items"]] == ["证书b", "证书z"]
    r = wiki_types.query({"type": "cert", "sort": "valid_until", "limit": 10})
    assert [x["title"] for x in r["items"]] == ["证书b", "证书z", "部门证书", "证书a", "证书m"]
    assert r["items"][2]["rel_path"].startswith("wiki-shared/dept/研发部/")


# ── 装机根目录 ───────────────────────────────────────────────────

def test_装机部门wiki_跟搜索用同一个根目录(home):
    assert wiki_install._shared_root() == home / "wiki-shared"
    assert wiki_install._audit_path().parent == home
