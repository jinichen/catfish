"""测试 scripts/parse_file.py.

5/6 BL-D17: schema 已从旧版 {text, truncated, char_count} 切到新版
{preview_text, preview_chars, kind, meta}, 测试同步对齐.

PDF 多了一个结构化表格识别 path (anchor + sub-records), 也加了 case.

覆盖:
- .pdf / .xlsx / .docx / .csv / .txt / .md 各格式 (新 schema)
- 不支持格式返 error JSON
- 文件不存在返 error JSON
- 空文件 / 空 sheet 处理
- PDF 结构化模式 (≥5 个 18 位 ID → 自动出 JSON 抽取文件)

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
    assert out["kind"] == "text"
    assert "Hello 世界" in out["preview_text"]
    assert "第二行" in out["preview_text"]
    assert out["meta"]["total_chars"] == len("Hello 世界\n第二行")


def test_md_basic(tmp_path: Path) -> None:
    f = tmp_path / "doc.md"
    f.write_text("# 标题\n\n正文内容", encoding="utf-8")
    out = _run(f)
    assert out["ext"] == ".md"
    assert out["kind"] == "text"
    assert "# 标题" in out["preview_text"]


def test_text_truncation_marker(tmp_path: Path) -> None:
    """文本超 PREVIEW_MAX_CHARS (5000) → preview 含截断提示, meta.total_chars 是原长."""
    f = tmp_path / "large.txt"
    f.write_text("a" * 8000, encoding="utf-8")
    out = _run(f)
    assert out["meta"]["total_chars"] == 8000
    assert "用 execute_code" in out["preview_text"]


# ── csv ─────────────────────────────────────────────────────────


def test_csv_basic(tmp_path: Path) -> None:
    f = tmp_path / "data.csv"
    f.write_text("姓名,部门,工号\n张三,研发,001\n李四,销售,002\n", encoding="utf-8")
    out = _run(f)
    assert out["ext"] == ".csv"
    assert out["kind"] == "csv"
    assert "张三" in out["preview_text"]
    assert "研发" in out["preview_text"]
    assert out["meta"]["total_rows"] == 3  # header + 2 数据


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
    assert out["kind"] == "word"
    assert "第一段中文内容" in out["preview_text"]
    assert "第二段" in out["preview_text"]
    assert "项目" in out["preview_text"]
    assert "X" in out["preview_text"]


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
    assert out["kind"] == "excel"
    assert "Sheet1" in out["preview_text"]
    assert "Sheet2" in out["preview_text"]
    assert "项目" in out["preview_text"]
    assert "1000" in out["preview_text"]
    assert out["meta"]["sheets"] == ["Sheet1", "Sheet2"]


# ── pdf (普通模式) ──────────────────────────────────────────────


def test_pdf_basic(tmp_path: Path) -> None:
    """简 PDF (无主键 ID 锚点) → 走原 preview 5 页路径, 不进结构化模式."""
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
    assert out["kind"] == "pdf"
    assert "Hello PDF World" in out["preview_text"]
    assert "Page 2" in out["preview_text"]
    # 没主键 ID → 不应触发结构化模式
    assert "structured_path" not in out["meta"]
    assert out["meta"]["page_count"] == 2


def test_pdf_empty(tmp_path: Path) -> None:
    """没文本的 PDF (扫描版) — 不报 error, preview 含'空' 标记."""
    pytest.importorskip("pypdfium2")
    pytest.importorskip("reportlab")
    from reportlab.pdfgen import canvas

    f = tmp_path / "empty.pdf"
    c = canvas.Canvas(str(f))
    c.showPage()
    c.save()

    out = _run(f)
    assert out["ext"] == ".pdf"
    assert "error" not in out
    # 走 preview 路径, "空" / "扫描" 标记
    assert "空" in out["preview_text"] or "扫描" in out["preview_text"]


# ── pdf (结构化模式) ────────────────────────────────────────────


def test_pdf_structured_mode_anchors(tmp_path: Path) -> None:
    """≥5 个 18 位身份证 ID 的 PDF → 走 anchor 模式, 出 JSON 文件."""
    pytest.importorskip("pypdfium2")
    pytest.importorskip("reportlab")
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    # 中文要 TTF, 不一定有, 用纯英文测 ID 锚点逻辑
    f = tmp_path / "social.pdf"
    c = canvas.Canvas(str(f))
    # 5 个假身份证号 (前 17 位数字 + 结尾 X 或数字), 每个前面加假名
    fake_records = [
        ("Zhang San",  "11010119900101001X"),
        ("Li Si",      "11010119910202002X"),
        ("Wang Wu",    "11010119920303003X"),
        ("Zhao Liu",   "11010119930404004X"),
        ("Sun Qi",     "11010119940505005X"),
        ("Zhou Ba",    "11010119950606006X"),
    ]
    y = 800
    for name, sid in fake_records:
        c.drawString(50, y, f"1 {name} {sid}")
        y -= 20
    c.save()

    out = _run(f)
    assert out["ext"] == ".pdf"
    assert "error" not in out
    # 应该走 anchor 模式
    assert "structured_path" in out["meta"]
    assert out["meta"]["structured_count"] >= 5
    # JSON 文件应该真存在
    sp = Path(out["meta"]["structured_path"])
    assert sp.exists()
    data = json.loads(sp.read_text(encoding="utf-8"))
    assert len(data) >= 5
    # 每条至少有 序号/姓名/身份证号 三字段
    assert "身份证号" in data[0]
    sp.unlink(missing_ok=True)
