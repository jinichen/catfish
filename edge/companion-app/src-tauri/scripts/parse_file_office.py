"""Office 文档 preview —— Excel (.xlsx/.xls) / PowerPoint (.pptx) / Word (.docx)。

8/15 从 parse_file.py 搬出来。

# 为什么这四个一起

它们的共同点是**都靠一个第三方库, 而且那个库经常不在**:

    parse_excel_preview   openpyxl
    _parse_xls_preview    xlrd (必须 1.x, 见下)
    parse_pptx_preview    python-pptx
    parse_docx_preview    python-docx

所以每个函数都是同一个形状: 函数体内 import (装没装都不影响脚本能不能起来)
+ try/except 把 ImportError 变成一句人能看懂的"装这个包"。这一组放一起,
下次加个新格式照着抄就行。

# ⚠ xlrd 必须锁 1.x (P3.3.21, 6/11)

老 .xls 是 97-2003 的二进制格式, 只有 xlrd 1.x 能读。xlrd 2.0+ **主动砍掉了
.xls 支持**, 只剩 .xlsx —— 而 .xlsx 用 openpyxl 更全。也就是说升到 xlrd 2.0
的唯一效果是: 员工传老 .xls 进来解析不了, 而且报的是"格式不支持"这种看不出
根因的错。

# 依赖方向

只依赖 parse_file_common (常量 + _truncate) 和标准库, 不反向 import
parse_file —— 那会成环, 而且 parse_file.py 是当脚本直接跑的 (Rust 侧
file_parse.rs 用 subprocess 调), 反向 import 会拿到第二个 module 对象。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from parse_file_common import PREVIEW_PARAS, PREVIEW_ROWS, _truncate

# ============================================================
# Excel
# ============================================================

def parse_excel_preview(path: Path) -> tuple[str, dict[str, Any]]:
    """Excel 各 sheet 列头 + 前 20 行 + 总行数.

    P3.3.21 (6/11): 老 .xls (97-2003 binary) openpyxl 不吃, 走 xlrd 分支.
    .xlsx / .xlsm (xlsx + macro) 走 openpyxl. .xlsb 用户极少, 暂不支持.
    """
    ext = path.suffix.lower()
    if ext == ".xls":
        return _parse_xls_preview(path)

    try:
        from openpyxl import load_workbook  # noqa: PLC0415
    except ImportError:
        raise RuntimeError("openpyxl 未装. 跑: pip install openpyxl")

    wb = load_workbook(path, read_only=True, data_only=True)
    parts: list[str] = []
    sheets: list[str] = []
    row_counts: dict[str, int] = {}

    for sheet in wb.worksheets:
        sheets.append(sheet.title)
        # 用 max_row 拿总行数 (read_only=True 时是估值, 但够 LLM 决策用)
        total_rows = sheet.max_row or 0
        row_counts[sheet.title] = total_rows
        parts.append(f"## Sheet: {sheet.title}  (共 {total_rows} 行)")

        # 拿前 PREVIEW_ROWS+1 行 (含 header)
        rows_iter = sheet.iter_rows(values_only=True)
        sample: list[tuple] = []
        for i, row in enumerate(rows_iter):
            if i >= PREVIEW_ROWS + 1:
                break
            sample.append(row)

        if not sample:
            parts.append("  (空 sheet)")
            parts.append("")
            continue

        # 第一行当 header (大多数 Excel 是这样, 不绝对)
        for row in sample:
            cells = [str(c) if c is not None else "" for c in row]
            if any(cells):
                parts.append("\t".join(cells))
        if total_rows > len(sample):
            parts.append(f"  (... 还有 {total_rows - len(sample)} 行未显示, 用 execute_code 读完整)")
        parts.append("")  # sheet 间空行

    text = _truncate("\n".join(parts))
    meta: dict[str, Any] = {
        "sheets": sheets,
        "row_counts": row_counts,
        "total_sheets": len(sheets),
    }
    return text, meta


# P3.3.21 (6/11): 老 .xls (97-2003 binary) 走 xlrd<2.0.
# xlrd 2.0+ 砍掉 .xls 支持 (只剩 .xlsx 没意义, openpyxl 更全), 必须 1.x.
def _parse_xls_preview(path: Path) -> tuple[str, dict[str, Any]]:
    """老 .xls (Excel 97-2003 binary) 用 xlrd 1.2.0 解."""
    try:
        import xlrd  # noqa: PLC0415
    except ImportError:
        raise RuntimeError(
            "老 .xls (Excel 97-2003 格式) 需 xlrd 1.2.0. "
            "解法: (1) pip install 'xlrd<2.0' 或 "
            "(2) Excel 打开 → 另存为 .xlsx (openpyxl 兼容更好)"
        )

    wb = xlrd.open_workbook(str(path), on_demand=True)
    parts: list[str] = []
    sheets: list[str] = []
    row_counts: dict[str, int] = {}

    for sheet_name in wb.sheet_names():
        sheet = wb.sheet_by_name(sheet_name)
        sheets.append(sheet_name)
        total_rows = sheet.nrows
        row_counts[sheet_name] = total_rows
        parts.append(f"## Sheet: {sheet_name}  (共 {total_rows} 行)")

        if total_rows == 0:
            parts.append("  (空 sheet)")
            parts.append("")
            continue

        max_rows = min(PREVIEW_ROWS + 1, total_rows)
        for r in range(max_rows):
            cells = [str(sheet.cell_value(r, c)) for c in range(sheet.ncols)]
            if any(cells):
                parts.append("\t".join(cells))
        if total_rows > max_rows:
            parts.append(f"  (... 还有 {total_rows - max_rows} 行未显示, 用 execute_code 读完整)")
        parts.append("")

    text = _truncate("\n".join(parts))
    meta: dict[str, Any] = {
        "sheets": sheets,
        "row_counts": row_counts,
        "total_sheets": len(sheets),
        "format": "xls",  # 给 LLM 看 — 老格式 execute_code 时要 pandas + engine='xlrd'
    }
    return text, meta


# ============================================================
# PowerPoint (.pptx) — P3.3.21 (6/11)
# ============================================================

def parse_pptx_preview(path: Path) -> tuple[str, dict[str, Any]]:
    """.pptx 每页 title + 正文 text. .ppt 老格式不支持 — 装 python-pptx 即可."""
    try:
        from pptx import Presentation  # noqa: PLC0415
    except ImportError:
        raise RuntimeError(
            "python-pptx 未装. 跑: pip install python-pptx  "
            "(注: 老 .ppt 97-2003 格式不支持, 请另存为 .pptx)"
        )

    prs = Presentation(str(path))
    parts: list[str] = []
    slide_titles: list[str] = []
    total_slides = len(prs.slides)
    parts.append(f"## PPTX  (共 {total_slides} 页)")

    # PREVIEW_ROWS 复用 — pptx 前 N 页 preview, 跟 PDF 一致
    max_slides = min(PREVIEW_ROWS, total_slides)
    for i, slide in enumerate(prs.slides):
        if i >= max_slides:
            break
        title = ""
        body_lines: list[str] = []
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            tf = shape.text_frame
            txt = (tf.text or "").strip()
            if not txt:
                continue
            # 第一个有文字的 placeholder 当 title (粗略, 大多 ppt 是这样)
            if not title:
                title = txt.split("\n", 1)[0][:80]
            body_lines.append(txt)
        slide_titles.append(title or f"(第 {i + 1} 页, 无标题)")
        parts.append(f"### 第 {i + 1} 页: {title or '(无标题)'}")
        for line in body_lines:
            parts.append(line)
        parts.append("")

    if total_slides > max_slides:
        parts.append(f"  (... 还有 {total_slides - max_slides} 页未显示, 用 execute_code 读完整)")

    text = _truncate("\n".join(parts))
    meta: dict[str, Any] = {
        "total_slides": total_slides,
        "slide_titles": slide_titles,
    }
    return text, meta

# ============================================================
# Word
# ============================================================

def parse_docx_preview(path: Path) -> tuple[str, dict[str, Any]]:
    """Word 前 N 段."""
    try:
        from docx import Document  # noqa: PLC0415
    except ImportError:
        raise RuntimeError("python-docx 未装. 跑: pip install python-docx")

    doc = Document(str(path))
    paras = [p.text for p in doc.paragraphs if p.text.strip()]
    total_paras = len(paras)
    parts: list[str] = [f"## Word · 共 {total_paras} 段"]

    paras_to_show = min(total_paras, PREVIEW_PARAS)
    for p in paras[:paras_to_show]:
        parts.append(p)

    if total_paras > paras_to_show:
        parts.append(
            f"\n[... 还有 {total_paras - paras_to_show} 段未显示, "
            "用 execute_code + python-docx 读完整]"
        )

    # 表格也 preview 一下
    if doc.tables:
        parts.append(f"\n## 含 {len(doc.tables)} 个表格 (preview 第一个前 5 行)")
        first_t = doc.tables[0]
        for row in first_t.rows[:5]:
            cells = [c.text for c in row.cells]
            parts.append(" | ".join(cells))
        if len(first_t.rows) > 5:
            parts.append(f"  (...还有 {len(first_t.rows) - 5} 行)")

    text = _truncate("\n".join(parts))
    meta: dict[str, Any] = {
        "paragraph_count": total_paras,
        "table_count": len(doc.tables),
    }
    return text, meta
