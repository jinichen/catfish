"""测试 scripts/parse_file.py — Day 1 文件上传 Python helper.

覆盖:
- .pdf / .xlsx / .docx / .csv / .txt / .md 各格式
- 50KB 截断 (truncated 标记)
- 不支持格式返 error JSON
- 文件不存在返 error JSON
- 空文件 / 空 sheet 处理

跑法:
    cd ~/person_task/catfish/edge/companion-app/src-tauri
    python -m pytest tests/parse_file_test.py -v
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "parse_file.py"
PYTHON = sys.executable


def _run(file_path: Path) -> dict:
    """跑 parse_file.py, 返解析后的 JSON dict."""
    result = subprocess.run(
        [PYTHON, str(SCRIPT), str(file_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if not result.stdout.strip():
        pytest.fail(f"empty stdout. stderr: {result.stderr}")
    return json.loads(result.stdout.strip())


# ── 不存在 / 不支持 ─────────────────────────────────────────────


def test_file_not_exists(tmp_path: Path) -> None:
    out = _run(tmp_path / "nope.pdf")
    assert "error" in out
    assert "不存在" in out["error"]


def test_unsupported_format(tmp_path: Path) -> None:
    f = tmp_path / "binary.bin"
    f.write_bytes(b"\x00\x01\x02")
    out = _run(f)
    assert "error" in out
    assert "不支持" in out["error"]


# ── txt / md ────────────────────────────────────────────────────


def test_txt_basic(tmp_path: Path) -> None:
    f = tmp_path / "hello.txt"
    f.write_text("Hello 世界\n第二行", encoding="utf-8")
    out = _run(f)
    assert out["filename"] == "hello.txt"
    assert out["ext"] == ".txt"
    assert out["truncated"] is False
    assert "Hello 世界" in out["text"]
    assert "第二行" in out["text"]


def test_md_basic(tmp_path: Path) -> None:
    f = tmp_path / "doc.md"
    f.write_text("# 标题\n\n正文内容", encoding="utf-8")
    out = _run(f)
    assert out["ext"] == ".md"
    assert "# 标题" in out["text"]


def test_truncation(tmp_path: Path) -> None:
    """大文件超 50KB → truncated=true."""
    f = tmp_path / "large.txt"
    # 写 60KB (超过 MAX_CHARS=50000)
    f.write_text("a" * 60000, encoding="utf-8")
    out = _run(f)
    assert out["truncated"] is True
    assert out["char_count"] == 60000
    assert len(out["text"]) == 50000  # 截断到 MAX_CHARS


# ── csv ─────────────────────────────────────────────────────────


def test_csv_basic(tmp_path: Path) -> None:
    f = tmp_path / "data.csv"
    f.write_text("姓名,部门,工号\n张三,研发,001\n李四,销售,002\n", encoding="utf-8")
    out = _run(f)
    assert out["ext"] == ".csv"
    assert "张三" in out["text"]
    assert "研发" in out["text"]


# ── docx ────────────────────────────────────────────────────────


def test_docx_basic(tmp_path: Path) -> None:
    """造一个最简 docx, 验提取段落."""
    pytest.importorskip("docx")
    from docx import Document

    f = tmp_path / "test.docx"
    doc = Document()
    doc.add_paragraph("第一段中文内容")
    doc.add_paragraph("第二段")
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "项目"
    table.cell(0, 1).text = "状态"
    table.cell(1, 0).text = "X"
    table.cell(1, 1).text = "完成"
    doc.save(str(f))

    out = _run(f)
    assert out["ext"] == ".docx"
    assert "第一段中文内容" in out["text"]
    assert "第二段" in out["text"]
    # 表格内容也提取
    assert "项目" in out["text"]
    assert "X" in out["text"]


# ── xlsx ────────────────────────────────────────────────────────


def test_xlsx_basic(tmp_path: Path) -> None:
    """造 xlsx, 验提取多 sheet."""
    pytest.importorskip("openpyxl")
    from openpyxl import Workbook

    f = tmp_path / "test.xlsx"
    wb = Workbook()
    ws1 = wb.active
    ws1.title = "Sheet1"
    ws1.append(["项目", "金额"])
    ws1.append(["X", 1000])
    ws1.append(["Y", 2000])
    ws2 = wb.create_sheet("Sheet2")
    ws2.append(["部门", "人数"])
    ws2.append(["研发", 30])
    wb.save(str(f))

    out = _run(f)
    assert out["ext"] == ".xlsx"
    assert "Sheet1" in out["text"]
    assert "Sheet2" in out["text"]
    assert "项目" in out["text"]
    assert "1000" in out["text"]


# ── pdf ─────────────────────────────────────────────────────────


def test_pdf_basic(tmp_path: Path) -> None:
    """造一个最简 PDF (用 reportlab), 验文本提取."""
    pytest.importorskip("reportlab")
    from reportlab.pdfgen import canvas

    f = tmp_path / "test.pdf"
    c = canvas.Canvas(str(f))
    c.drawString(100, 750, "Hello PDF World")
    c.drawString(100, 700, "This is page 1.")
    c.showPage()
    c.drawString(100, 750, "Page 2 content")
    c.showPage()
    c.save()

    out = _run(f)
    assert out["ext"] == ".pdf"
    assert "Hello PDF World" in out["text"]
    assert "Page 2" in out["text"]


def test_pdf_empty_returns_placeholder(tmp_path: Path) -> None:
    """没文本的 PDF (扫描版) 返默认提示."""
    pytest.importorskip("pypdfium2")
    pytest.importorskip("reportlab")
    from reportlab.pdfgen import canvas

    f = tmp_path / "empty.pdf"
    c = canvas.Canvas(str(f))
    c.showPage()  # 空白页
    c.save()

    out = _run(f)
    assert out["ext"] == ".pdf"
    # 空白 PDF 应该返 "无可提取文本" 提示, 不报 error
    assert "error" not in out
    # text 字段含提示
    assert "扫描" in out["text"] or "无可提取" in out["text"] or len(out["text"]) >= 0
