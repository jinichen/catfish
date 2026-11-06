"""parse_file_preview.py (右侧成果区预览 Word / Excel / PPT) 单测。

跑法: pytest src-tauri/scripts/test_parse_file_preview.py
依赖: python-docx, openpyxl, python-pptx (hermes venv 里本来就有)
"""
from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

import pytest

THIS = Path(__file__).resolve()
SCRIPT = THIS.parent / "parse_file_preview.py"

# 1×1 PNG
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def run(path: Path) -> dict:
    res = subprocess.run([sys.executable, str(SCRIPT), str(path)], capture_output=True, text=True, check=True)
    return json.loads(res.stdout)


def test_docx_keeps_order_headings_lists_tables_and_images(tmp_path: Path) -> None:
    docx = pytest.importorskip("docx")
    img = tmp_path / "dot.png"
    img.write_bytes(PNG)
    doc = docx.Document()
    doc.add_heading("季度报告", level=1)
    doc.add_paragraph("正文第一段 <script>alert(1)</script>")
    doc.add_paragraph("要点一", style="List Bullet")
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "项目"
    table.cell(0, 1).text = "金额"
    table.cell(1, 0).text = "A"
    table.cell(1, 1).text = "100"
    doc.add_picture(str(img))
    doc.add_heading("小结", level=2)
    f = tmp_path / "r.docx"
    doc.save(str(f))

    r = run(f)
    assert r["kind"] == "docx"
    kinds = [b["t"] for b in r["blocks"]]
    assert kinds == ["h", "p", "p", "table", "img", "h"]
    assert r["blocks"][0] == {"t": "h", "level": 1, "text": "季度报告"}
    # 原文原样当文本给出去, 由 React 渲染 (不会被当 HTML)
    assert r["blocks"][1]["text"] == "正文第一段 <script>alert(1)</script>"
    assert r["blocks"][1]["list"] is False
    assert r["blocks"][2]["list"] is True
    assert r["blocks"][3]["rows"] == [["项目", "金额"], ["A", "100"]]
    assert r["blocks"][4]["src"].startswith("data:image/png;base64,")
    assert r["blocks"][5]["level"] == 2
    assert r["truncated"] is False


def test_xlsx_sheets_values_and_trailing_blank_trim(tmp_path: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    import datetime as dt

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "1月"
    ws.append(["日期", "金额", None])
    ws.append([dt.date(2026, 1, 5), 12.0, None])
    ws.append([dt.datetime(2026, 1, 6, 9, 30), 3.5, None])
    ws["A10"].number_format = "0.00"  # 只有格式的空行, 不该出现在预览里
    ws2 = wb.create_sheet("汇总")
    ws2["A1"] = "合计"
    ws2["B1"] = "=SUM('1月'!B2:B3)"
    f = tmp_path / "t.xlsx"
    wb.save(str(f))

    r = run(f)
    assert r["kind"] == "sheet"
    assert [s["name"] for s in r["sheets"]] == ["1月", "汇总"]
    assert r["sheets"][0]["rows"] == [["日期", "金额"], ["2026-01-05", "12"], ["2026-01-06 09:30", "3.5"]]
    # 公式没缓存值 (openpyxl 存的文件没算过) → 空, 不报错
    assert r["sheets"][1]["rows"][0][0] == "合计"
    assert r["truncated"] is False


def test_xlsx_row_limit_marks_truncated(tmp_path: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    for i in range(520):
        ws.append([i])
    f = tmp_path / "big.xlsx"
    wb.save(str(f))
    r = run(f)
    assert len(r["sheets"][0]["rows"]) == 500
    assert r["sheets"][0]["total_rows"] == 520
    assert r["truncated"] is True


def test_pptx_titles_texts_tables_and_pictures(tmp_path: Path) -> None:
    pptx = pytest.importorskip("pptx")
    from pptx.util import Inches

    img = tmp_path / "dot.png"
    img.write_bytes(PNG)
    prs = pptx.Presentation()
    s1 = prs.slides.add_slide(prs.slide_layouts[1])  # 标题 + 正文
    s1.shapes.title.text = "方案概述"
    s1.placeholders[1].text_frame.text = "第一点"
    s1.placeholders[1].text_frame.add_paragraph().text = "第二点"
    s2 = prs.slides.add_slide(prs.slide_layouts[6])  # 空白
    s2.shapes.add_picture(str(img), Inches(1), Inches(1))
    tbl = s2.shapes.add_table(2, 2, Inches(1), Inches(3), Inches(4), Inches(1)).table
    tbl.cell(0, 0).text = "指标"
    tbl.cell(0, 1).text = "值"
    f = tmp_path / "d.pptx"
    prs.save(str(f))

    r = run(f)
    assert r["kind"] == "slides"
    assert r["slides"][0]["title"] == "方案概述"
    assert r["slides"][0]["texts"] == ["第一点", "第二点"]
    assert r["slides"][1]["title"] == ""
    assert "指标 | 值" in r["slides"][1]["texts"]
    assert len(r["slides"][1]["images"]) == 1


def test_errors_are_json_not_crash(tmp_path: Path) -> None:
    bad = tmp_path / "broken.docx"
    bad.write_bytes(b"not a zip")
    assert "打不开" in run(bad)["error"]
    assert "预览不了" in run(tmp_path / "x.zip")["error"]
    assert "不在了" in run(tmp_path / "missing.xlsx")["error"]
