"""10/23: 入知识库前抽文字; 抽不出就拒, 不再把二进制当文字写进去 (doc_text.py)。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from catfish_tool_bridge import catfish_tools_wiki as tw
from catfish_tool_bridge.doc_text import extract_text, looks_binary


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path / ".catfish"


def _sources(home: Path) -> list[Path]:
    return sorted((home / "wiki" / "raw" / "sources").glob("*.md"))


def test_docx_抽正文入库(tmp_path, home):
    docx = pytest.importorskip("docx")
    d = docx.Document()
    d.add_paragraph("十五五规划 三大新兴支柱产业")
    f = tmp_path / "规划.docx"
    d.save(f)
    r = tw.wiki_ingest({"kept_path": str(f), "filename": f.name})
    assert r["type"] == "success", r
    text = _sources(home)[0].read_text(encoding="utf-8")
    assert "三大新兴支柱产业" in text and not looks_binary(text)


def test_扫描件pdf_拒绝_不写乱码(tmp_path, home):
    pdfium = pytest.importorskip("pypdfium2")
    pdf = pdfium.PdfDocument.new()
    pdf.new_page(595, 842)
    f = tmp_path / "扫描件.pdf"
    pdf.save(str(f))
    r = tw.wiki_ingest({"kept_path": str(f), "filename": f.name})
    assert r["type"] == "error" and "扫描件" in r["error"] and "parsed_text_path" in r["error"]
    assert _sources(home) == []


def test_图片_拒绝(tmp_path, home):
    f = tmp_path / "图.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 50)
    r = tw.wiki_ingest({"kept_path": str(f), "filename": f.name})
    assert r["type"] == "error"
    assert _sources(home) == []


def test_识别好的文字通过_parsed_text_path入库(tmp_path, home):
    scan = tmp_path / "扫描件.pdf"
    scan.write_bytes(b"%PDF-1.4 binary")
    ocr = tmp_path / "识别.md"
    ocr.write_text("闽政〔2026〕12号 「369」产业体系", encoding="utf-8")
    r = tw.wiki_ingest({"kept_path": str(scan), "parsed_text_path": str(ocr), "filename": scan.name})
    assert r["type"] == "success", r
    assert "369" in _sources(home)[0].read_text(encoding="utf-8")


def test_同一原文件再入库_替换旧副本和已读标记(tmp_path, home):
    f = tmp_path / "说明.txt"
    f.write_text("第一版", encoding="utf-8")
    r1 = tw.wiki_ingest({"kept_path": str(f), "filename": f.name})
    old = r1["rel_path"].split("/")[-1]
    (home / "wiki_ingested_state.json").write_text(json.dumps({f"source:{old}": 1.0, "别的": 2.0}), encoding="utf-8")
    f.write_text("第二版", encoding="utf-8")
    import time
    time.sleep(1.1)  # 文件名带秒级时间戳
    r2 = tw.wiki_ingest({"kept_path": str(f), "filename": f.name})
    assert r2["replaced"] == [old]
    files = _sources(home)
    assert len(files) == 1 and "第二版" in files[0].read_text(encoding="utf-8")
    state = json.loads((home / "wiki_ingested_state.json").read_text(encoding="utf-8"))
    assert state == {"别的": 2.0}


def test_文本文件_gbk_也读得出(tmp_path):
    f = tmp_path / "旧.txt"
    f.write_bytes("国标编码的中文".encode("gbk"))
    assert extract_text(f)[0] == "国标编码的中文"
