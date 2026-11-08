"""11/08 第三批: 建页按类型定义直接填属性 / 原始资料也进搜索 (默认不列) / 整理属性时连原件段落一起看 /
词表外的细分类型归一 (资料要点 → doc) / 新加的「文件资料」类型。全部虚构。"""
from __future__ import annotations

from pathlib import Path

import pytest

from catfish_tool_bridge import wiki_files, wiki_sources, wiki_types
from catfish_tool_bridge import wiki_search as ws
from catfish_tool_bridge import wiki_write_tools as wt


@pytest.fixture()
def home(tmp_path, monkeypatch):
    h = tmp_path / ".catfish"
    for sub in ("wiki/entities", "wiki/concepts", "wiki/queries", "wiki/raw/sources"):
        (h / sub).mkdir(parents=True)
    monkeypatch.setenv("CATFISH_HOME", str(h))
    ws._cache.update({"fingerprint": "", "index": None, "meta": []})
    wiki_sources._cache.update({"stamp": None, "docs": []})
    return h


def _page(h: Path, rel: str, title: str, body: str = "正文。", **fm):
    lines = [f"title: {title}"] + [f"{k}: {v}" for k, v in fm.items()]
    (h / rel).write_text("---\n" + "\n".join(lines) + "\n---\n\n" + body + "\n", encoding="utf-8")


def test_建证书页_编号和有效期按固定句式自动填_给的props也填(home):
    r = wt.tool_wiki_create({"kind": "entity", "title": "ISO 9001 质量管理体系认证", "subtype": "cert",
                             "body": "证书编号：CN-2026-QMS-0001，有效期至 2029-03-01，由方圆认证发证。", "props": {"issuer": "方圆认证"}})
    assert r["ok"] and r["type"] == "cert"
    assert r["props"]["cert_no"] == "CN-2026-QMS-0001" and r["props"]["valid_until"] == "2029-03-01" and r["props"]["issuer"] == "方圆认证"
    txt = (home / r["rel_path"]).read_text()
    assert 'cert_no: "CN-2026-QMS-0001"' in txt and 'valid_until: "2029-03-01"' in txt
    assert wiki_types.query({"type": "cert", "sort": "valid_until"})["items"][0]["props"]["valid_until"] == "2029-03-01"


def test_没类型定义的页_不动(home):
    r = wt.tool_wiki_create({"kind": "entity", "title": "某活动", "subtype": "event", "body": "编号 X-1。"})
    assert r["ok"] and "props" not in r


def test_原始资料进索引_默认不列_要看可以传(home):
    (home / "wiki/raw/sources/1-花名册.md").write_text("---\ntype: source\nfilename: 花名册.xlsx\n---\n\n张三 研发部 13900000000 zhangsan@x.com\n")
    _page(home, "wiki/entities/zhang.md", "张三", "研发的同事。", type="entity", entity_type="person")
    r = ws.search_wiki("张三 13900000000", 5)
    assert [m["title"] for m in r["matches"]] == ["张三"] and r["hidden"] == {"sources": 1} and "1 份原始资料" in r["summary"]
    r = ws.search_wiki("张三 13900000000", 5, include_sources=True)
    assert {m["kind"] for m in r["matches"]} == {"entity", "source"}


def test_原件里提到它的段落(home):
    (home / "wiki/raw/sources/1-花名册.md").write_text("---\ntype: source\nfilename: 花名册.xlsx\n---\n\n前面一堆。张三，研发部，高级工程师，邮箱 zhangsan@x.com。后面一堆。\n")
    (home / "wiki/raw/sources/2-周报.md").write_text("---\ntype: source\nfilename: 周报.docx\n---\n\n本周老张完成了评审。\n")
    s = wiki_sources.snippets_about("张三", ["老张"])
    assert "《花名册.xlsx》" in s and "zhangsan@x.com" in s and "《周报.docx》" in s and "老张完成了评审" in s
    assert wiki_sources.snippets_about("没人提") == "" and wiki_sources.snippets_about("") == ""


def test_整理属性_连原件段落一起给模型看_值在原件里也算数(home, monkeypatch):
    _page(home, "wiki/entities/zhang.md", "张三", "研发的同事。", type="entity", entity_type="person")
    (home / "wiki/raw/sources/1-花名册.md").write_text("---\ntype: source\nfilename: 花名册.xlsx\n---\n\n张三，研发部，高级工程师，邮箱 zhangsan@x.com。\n")
    seen = {}

    def fake_model(prompt):
        seen["prompt"] = prompt
        return '{"0": {"email": "zhangsan@x.com", "department": "研发部", "phone": "13800000000"}}'
    monkeypatch.setattr(wiki_types, "_call_model", fake_model)
    r = wiki_types.propose({"type": "person"})
    assert "《花名册.xlsx》" in seen["prompt"] and "zhangsan@x.com" in seen["prompt"]
    item = r["items"][0]
    assert item["proposed"]["email"] == "zhangsan@x.com" and item["proposed"].get("department") == "研发部"
    assert "phone" not in item["proposed"], "模型编的 (原件和正文里都没有) 不要"
    r2 = wiki_types.propose({"type": "person", "use_model": False})
    assert r2["items"] == [] and r2["used_model"] is False


def test_词表外的细分类型归一_资料要点是文件资料(home):
    schema = wiki_types.load_schema()
    assert "doc" in schema and schema["doc"]["label"] == "文件资料"
    for raw, want in [("资料要点", "doc"), ("政策文件", "doc"), ("软件平台", "software"), ("事业部", "department"), ("政府机构", "org"), ("攻关项目", "project")]:
        assert wiki_types.type_key_of_subtype(raw, schema) == want, raw
    r = wt.tool_wiki_create({"kind": "entity", "title": "关于加强审计工作的通知", "subtype": "资料要点",
                             "body": "文号：中电福富司〔2026〕59号，发布日期 2026-05-01。"})
    assert r["ok"] and r["type"] == "doc" and r["props"]["doc_no"] == "中电福富司〔2026〕59号" and r["props"]["issued_on"] == "2026-05-01"
    assert wiki_types.query({"type": "doc", "where": [{"field": "doc_no", "contains": "59号"}]})["total"] == 1
