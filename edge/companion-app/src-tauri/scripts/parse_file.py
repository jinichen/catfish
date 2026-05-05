#!/usr/bin/env python3
"""文件解析 helper — preview-only mode (五一 sprint 5/5 重构).

# 设计 (5/5 鸿波报"截断了不能用" 后大改)

旧设计 (50K 字符截断后塞 prompt) 不 scalable: 任何上限对真实业务数据都不够.
12 个月 Excel 一截就只看到前 3 个月.

**新设计**: parse_file 只输出 preview (~5K 字), 原文件永远保留, LLM 调 execute_code
跑 pandas / openpyxl / pypdfium2 读完整数据.

输出 schema:
    {
        "filename": "<原文件名>",
        "ext": ".xlsx",
        "kind": "excel" | "pdf" | "word" | "csv" | "text",
        "preview_text": "<sheet 列表 / 列头 / 前 N 行 / 总行数 / ...>",
        "preview_chars": 4523,                        # 给 UI chip 显示用
        "meta": {                                     # 结构化, LLM 拼 execute_code 用
            "sheets": ["1月", "2月", ...],            # excel 才有
            "row_counts": {"1月": 31, "2月": 28},     # excel 才有
            "page_count": 12,                         # pdf 才有
            "total_rows": 365,                        # csv 才有
            ...
        },
        "kept_path_hint": "<员工的 absolute file path, Rust 端会替换>"
    }
    # 错误: {"error": "..."}

行为约定:
- Excel: 每 sheet 列头 + 前 20 行 + 总行数 (跨 sheet 统计)
- PDF: 前 5 页全文 + 总页数
- Word: 前 30 段
- CSV: 列头 + 前 30 行 + 总行数
- TXT/MD/LOG: 前 5K 字 + 总字数

支持格式 (跟原来一致):
- .pdf   pypdfium2
- .xlsx / .xls  openpyxl
- .docx  python-docx
- .csv   csv 标准库
- .txt / .md / .markdown / .log  直接读 utf-8
"""

from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path
from typing import Any

# Preview 上限 (字符数). 全格式默认 5000, 给 LLM 看个梗概就够, 不要塞数据.
PREVIEW_MAX_CHARS = int(os.environ.get("CATFISH_PREVIEW_MAX_CHARS") or 5000)
# Excel/CSV 每 sheet/file 显示前 N 行
PREVIEW_ROWS = int(os.environ.get("CATFISH_PREVIEW_ROWS") or 20)
# PDF 显示前 N 页
PREVIEW_PAGES = int(os.environ.get("CATFISH_PREVIEW_PAGES") or 5)
# Word 显示前 N 段
PREVIEW_PARAS = int(os.environ.get("CATFISH_PREVIEW_PARAS") or 30)


def _truncate(s: str, limit: int = PREVIEW_MAX_CHARS) -> str:
    """preview 防意外超出, 多裁掉. 上限是软上限, 5K 一般够."""
    if len(s) <= limit:
        return s
    return s[:limit] + f"\n[... preview 截到 {limit} 字, 完整数据用 execute_code 读]"


# ============================================================
# Excel
# ============================================================

def parse_excel_preview(path: Path) -> tuple[str, dict[str, Any]]:
    """Excel 各 sheet 列头 + 前 20 行 + 总行数."""
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


# ============================================================
# PDF
# ============================================================

def parse_pdf_preview(path: Path) -> tuple[str, dict[str, Any]]:
    """PDF 前 N 页 + 总页数."""
    try:
        import pypdfium2 as pdfium  # noqa: PLC0415
    except ImportError:
        raise RuntimeError("pypdfium2 未装. 跑: pip install pypdfium2")

    pdf = pdfium.PdfDocument(str(path))
    total_pages = len(pdf)
    parts: list[str] = [f"## PDF · 共 {total_pages} 页"]

    pages_to_show = min(total_pages, PREVIEW_PAGES)
    for i in range(pages_to_show):
        page = pdf[i]
        textpage = page.get_textpage()
        chunk = textpage.get_text_range() or ""
        if chunk.strip():
            parts.append(f"\n--- Page {i + 1} ---\n{chunk.strip()}")
        else:
            parts.append(f"\n--- Page {i + 1} ---\n(空 / 扫描版无文字)")

    if total_pages > pages_to_show:
        parts.append(
            f"\n[... 还有 {total_pages - pages_to_show} 页未显示, "
            "用 execute_code + pypdfium2 读完整]"
        )

    text = _truncate("\n".join(parts))
    meta: dict[str, Any] = {
        "page_count": total_pages,
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


# ============================================================
# CSV
# ============================================================

def parse_csv_preview(path: Path) -> tuple[str, dict[str, Any]]:
    """CSV 列头 + 前 N 行 + 总行数."""
    # 先 count rows (一次过, 不读 cell)
    with path.open(encoding="utf-8-sig", newline="") as f:
        total_rows = sum(1 for _ in f)

    parts: list[str] = [f"## CSV · 共 {total_rows} 行"]
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        for i, row in enumerate(reader):
            if i >= PREVIEW_ROWS + 1:
                break
            parts.append("\t".join(row))

    if total_rows > PREVIEW_ROWS + 1:
        parts.append(
            f"\n[... 还有 {total_rows - PREVIEW_ROWS - 1} 行未显示, "
            "用 execute_code + pandas 读完整]"
        )

    text = _truncate("\n".join(parts))
    meta: dict[str, Any] = {
        "total_rows": total_rows,
    }
    return text, meta


# ============================================================
# Text (txt/md/log)
# ============================================================

def parse_text_preview(path: Path) -> tuple[str, dict[str, Any]]:
    """纯文本前 N 字 + 总字数."""
    full = path.read_text(encoding="utf-8", errors="replace")
    total = len(full)
    if total <= PREVIEW_MAX_CHARS:
        return full, {"total_chars": total}

    head = full[:PREVIEW_MAX_CHARS]
    text = (
        f"## 文本 · 共 {total} 字 (显示前 {PREVIEW_MAX_CHARS} 字)\n\n"
        f"{head}\n"
        f"\n[... 还有 {total - PREVIEW_MAX_CHARS} 字未显示, "
        "用 execute_code 读完整 (open + read)]"
    )
    return text, {"total_chars": total}


# ============================================================
# 主 dispatcher
# ============================================================

PARSERS = {
    ".pdf": ("pdf", parse_pdf_preview),
    ".xlsx": ("excel", parse_excel_preview),
    ".xls": ("excel", parse_excel_preview),
    ".docx": ("word", parse_docx_preview),
    ".csv": ("csv", parse_csv_preview),
    ".txt": ("text", parse_text_preview),
    ".md": ("text", parse_text_preview),
    ".markdown": ("text", parse_text_preview),
    ".log": ("text", parse_text_preview),
}


def main() -> int:
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: parse_file.py <file>"}, ensure_ascii=False))
        return 2

    path = Path(sys.argv[1])
    if not path.exists():
        print(json.dumps({"error": f"文件不存在: {path}"}, ensure_ascii=False))
        return 3
    if not path.is_file():
        print(json.dumps({"error": f"不是文件: {path}"}, ensure_ascii=False))
        return 3

    ext = path.suffix.lower()
    entry = PARSERS.get(ext)
    if entry is None:
        print(json.dumps({
            "error": f"不支持 {ext}. 支持: {', '.join(sorted(PARSERS.keys()))}"
        }, ensure_ascii=False))
        return 4

    kind, parser = entry
    try:
        preview_text, meta = parser(path)
    except Exception as e:
        print(json.dumps(
            {"error": f"{type(e).__name__}: {e}"},
            ensure_ascii=False,
        ))
        return 5

    result = {
        "filename": path.name,
        "ext": ext,
        "kind": kind,
        "preview_text": preview_text,
        "preview_chars": len(preview_text),
        "meta": meta,
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
