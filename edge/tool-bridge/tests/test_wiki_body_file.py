"""10/23: 长正文从本机文件读 (catfish_wiki_create body_file / catfish_wiki_update content_file)。"""
from __future__ import annotations

from catfish_tool_bridge import wiki_files
from catfish_tool_bridge.wiki_body_file import read_body_file


def test_body_file_作正文_body_当导语(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path / "home"))
    src = tmp_path / "全文转录.md"
    src.write_text("## 一、总体要求\n\n「369」产业体系 ……", encoding="utf-8")
    r = wiki_files.tool_wiki_create({
        "kind": "concept", "title": "福建省十五五规划", "subtype": "政策",
        "body": "闽政〔2026〕12号。", "body_file": str(src),
    })
    assert r["ok"], r
    text = (tmp_path / "home" / r["rel_path"]).read_text(encoding="utf-8")
    assert text.index("闽政〔2026〕12号") < text.index("「369」产业体系")


def test_body_和_body_file_都没有就拒(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    r = wiki_files.tool_wiki_create({"kind": "concept", "title": "空", "subtype": "政策"})
    assert not r["ok"] and "body_file" in r["error"]


def test_content_file_整篇覆写(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path / "home"))
    r = wiki_files.tool_wiki_create({"kind": "concept", "title": "规划", "subtype": "政策", "body": "旧"})
    new = tmp_path / "new.md"
    new.write_text("---\ntype: concept\ntitle: 规划\n---\n\n# 规划\n\n新的全文内容足够长" * 3, encoding="utf-8")
    u = wiki_files.tool_wiki_update({"rel_path": r["rel_path"], "content_file": str(new)})
    assert u["ok"], u
    assert "新的全文内容" in (tmp_path / "home" / r["rel_path"]).read_text(encoding="utf-8")


def test_只读文本_只读用户目录(tmp_path):
    pdf = tmp_path / "原件.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    assert "只收文本" in read_body_file(str(pdf))[1]
    assert "不存在" in read_body_file(str(tmp_path / "没有.md"))[1]
    assert "用户目录" in (read_body_file("/etc/hosts")[1] or "")
