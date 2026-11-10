"""11/10: 富文档不靠 markitdown 也能进索引; 大文档不按文件大小拦; 超长按段平均分; 跳过的记下来。

背景: Companion 拉起的 watcher 跑在 hermes 环境里, 没有 markitdown, 员工机上新增的
Word / PPT / Excel / PDF 一个都进不了索引; 70~120MB 的 PPT 又被 20MB 上限拦掉。都是静默的。
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

docx = pytest.importorskip("docx")
pptx = pytest.importorskip("pptx")
openpyxl = pytest.importorskip("openpyxl")

from catfish_search import extractor, indexer  # noqa: E402
from catfish_search.config import SearchConfig  # noqa: E402


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(indexer, "DB_FILE", tmp_path / "search.db")
    return tmp_path / "search.db"


def _cfg(root: Path, max_mb: int = 20) -> SearchConfig:
    return SearchConfig(include=[root], exclude=[], file_types={".docx", ".pptx", ".xlsx", ".xls", ".pdf", ".md"},
                        max_file_size_mb=max_mb)


def _make_docx(p: Path, text: str) -> Path:
    d = docx.Document()
    d.add_paragraph(text)
    t = d.add_table(rows=1, cols=2)
    t.rows[0].cells[0].text, t.rows[0].cells[1].text = "表格甲", "表格乙"
    d.save(str(p))
    return p


def _make_pptx_with_group(p: Path, text: str) -> Path:
    from pptx.util import Inches
    prs = pptx.Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # 空白页
    group = slide.shapes.add_group_shape()
    box = group.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1))
    box.text_frame.text = text
    prs.save(str(p))
    return p


def _make_xlsx(p: Path, sheets: dict[str, list[list[str]]]) -> Path:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
    wb.save(str(p))
    return p


def _rows(db: Path, sql: str, *args):
    c = sqlite3.connect(db)
    try:
        return c.execute(sql, args).fetchall()
    finally:
        c.close()


def test_没有markitdown也能读_docx_表格也读(tmp_path, monkeypatch):
    monkeypatch.setattr(extractor, "_MARKITDOWN", None)
    text = extractor.extract_text(_make_docx(tmp_path / "a.docx", "任命书正文 QWERTY"))
    assert "任命书正文 QWERTY" in text and "表格甲 | 表格乙" in text


def test_pptx_组合里的文字也读(tmp_path, monkeypatch):
    monkeypatch.setattr(extractor, "_MARKITDOWN", None)
    secs = extractor.extract_sections(_make_pptx_with_group(tmp_path / "b.pptx", "组合里的标题 ITSS"))
    assert secs and "组合里的标题 ITSS" in secs[0] and secs[0].startswith("第 1 页")


def test_xlsx_每个工作表一段(tmp_path, monkeypatch):
    monkeypatch.setattr(extractor, "_MARKITDOWN", None)
    secs = extractor.extract_sections(_make_xlsx(tmp_path / "c.xlsx", {"表一": [["a", "b"]], "表二": [["c", "d"]]}))
    assert [s.split("\n")[0] for s in secs] == ["表一", "表二"]


def test_xls_老格式也读(tmp_path, monkeypatch):
    xlwt = pytest.importorskip("xlwt")
    pytest.importorskip("xlrd")
    monkeypatch.setattr(extractor, "_MARKITDOWN", None)
    wb = xlwt.Workbook()
    for name in ("台账一", "台账二"):
        ws = wb.add_sheet(name)
        ws.write(0, 0, f"{name}项目")
    p = tmp_path / "d.xls"
    wb.save(str(p))
    secs = extractor.extract_sections(p)
    assert len(secs) == 2 and "台账二项目" in secs[1]


def test_pdf_每页一段(tmp_path, monkeypatch):
    pytest.importorskip("pypdfium2")
    monkeypatch.setattr(extractor, "_MARKITDOWN", None)
    body = b"BT /F1 24 Tf 72 720 Td (HelloPDF ZQX) Tj ET"
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(body) + body + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offs)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    p = tmp_path / "e.pdf"
    p.write_bytes(out)
    assert "HelloPDF ZQX" in extractor.extract_text(p)


def test_fit_sections_不超不动_超了每段都留一份():
    assert extractor.fit_sections(["ab", "cd"], 100) == "ab\n\ncd"
    long = ["A" * 1000, "B" * 1000, "C" * 10, "D" * 1000]
    out = extractor.fit_sections(long, 400)
    parts = out.split("\n\n")
    assert len(out) <= 400 and len(parts) == 4
    assert parts[2] == "C" * 10                       # 短段原样保留
    assert all(p and p[0] == ch for p, ch in zip(parts, "ABCD"))  # 每段都有份
    assert abs(len(parts[0]) - len(parts[3])) <= 1    # 长段平分


def test_大文档不按文件大小拦_纯文本还是拦_跳过的记下来(db, tmp_path, monkeypatch):
    monkeypatch.setattr(extractor, "_MARKITDOWN", None)
    root = tmp_path / "root"
    root.mkdir()
    big_doc = _make_docx(root / "大汇报.docx", "大文档正文 BIGDOC")
    big_md = root / "大笔记.md"
    big_md.write_text("笔记正文", encoding="utf-8")
    # 上限设成 0 MB: 两个文件都"超了"。文档照收 (只受 RICH_MAX_FILE_BYTES 管), 纯文本被拦
    stats = indexer.run_index(_cfg(root, max_mb=0))
    assert stats["indexed"] == 1 and stats["too_large"] == 1
    assert _rows(db, "SELECT path FROM file_meta") == [(str(big_doc),)]
    assert _rows(db, "SELECT path, reason FROM skipped_files") == [(str(big_md), "too_large")]


def test_抽不出字的记下来_后来能收了就从跳过里删(db, tmp_path, monkeypatch):
    monkeypatch.setattr(extractor, "_MARKITDOWN", None)
    root = tmp_path / "root"
    root.mkdir()
    p = _make_docx(root / "空.docx", " ")
    d = docx.Document()
    d.save(str(p))  # 真没字
    indexer.run_index(_cfg(root))
    assert _rows(db, "SELECT reason FROM skipped_files") == [("no_text",)]
    time.sleep(0.01)
    _make_docx(p, "补上了正文")
    import os
    os.utime(p, (time.time() + 5, time.time() + 5))
    indexer.run_index(_cfg(root))
    assert _rows(db, "SELECT count(*) FROM skipped_files") == [(0,)]
    assert len(_rows(db, "SELECT path FROM file_meta")) == 1


def test_文件删了_跳过记录也清(db, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    big_md = root / "大.md"
    big_md.write_text("x" * (1024 * 1024 + 10), encoding="utf-8")
    cfg = _cfg(root, max_mb=1)
    indexer.run_index(cfg)
    assert len(_rows(db, "SELECT * FROM skipped_files")) == 1
    big_md.unlink()
    indexer.cleanup_missing(cfg)
    assert _rows(db, "SELECT count(*) FROM skipped_files") == [(0,)]


def test_超长按段分额度_每个工作表都搜得到(db, tmp_path, monkeypatch):
    monkeypatch.setattr(extractor, "_MARKITDOWN", None)
    monkeypatch.setattr(indexer, "MAX_CONTENT_CHARS", 3000)
    root = tmp_path / "root"
    root.mkdir()
    sheets = {f"表{i}": [[f"项目{i}标记"] + ["填充" * 50]] * 20 for i in range(10)}
    _make_xlsx(root / "台账.xlsx", sheets)
    indexer.run_index(_cfg(root))
    content = _rows(db, "SELECT content FROM documents")[0][0]
    assert len(content) <= 3000
    assert all(f"项目{i}标记" in content for i in range(10))  # 最后一个表也在


def test_升级迁移_老数据补扫一遍_卡在50万字的重抽(db, tmp_path):
    c = sqlite3.connect(db)
    c.executescript(indexer.SCHEMA.split("-- 11/10")[0])  # 老库: 没有 skipped_files / index_meta
    c.execute("INSERT INTO indexed_roots VALUES('/r', 1, 3)")
    c.execute("INSERT INTO documents(path,title,content,file_type) VALUES('/r/big.xls','big',?,'.xls')", ("x" * 500_000,))
    c.execute("INSERT INTO documents(path,title,content,file_type) VALUES('/r/s.md','s','short','.md')")
    c.executemany("INSERT INTO file_meta VALUES(?,1,1,1,'h')", [("/r/big.xls",), ("/r/s.md",)])
    c.commit()
    c.close()
    indexer.open_db().close()
    assert _rows(db, "SELECT path FROM file_meta") == [("/r/s.md",)]
    assert _rows(db, "SELECT count(*) FROM indexed_roots") == [(0,)]
    assert _rows(db, "SELECT value FROM index_meta WHERE key='extract_version'") == [(str(indexer.EXTRACT_VERSION),)]
    # 只做一次
    c = sqlite3.connect(db)
    c.execute("INSERT INTO indexed_roots VALUES('/r', 2, 1)")
    c.commit()
    c.close()
    indexer.open_db().close()
    assert _rows(db, "SELECT count(*) FROM indexed_roots") == [(1,)]
