#!/usr/bin/env python3
"""文件解析 helper — 五一 sprint Day 1 多模态文件上传.

# 用法

    python parse_file.py <file-path>
    # 输出 JSON 到 stdout: {filename, ext, char_count, truncated, text} 或 {error}

# 支持格式

- .pdf   pypdfium2  (gateway venv 已装)
- .xlsx / .xls  openpyxl  (skill 也用)
- .docx  python-docx  (skill 也用)
- .csv   csv 标准库
- .txt / .md  直接读 utf-8

# 不支持

- .doc (老 Word) — 提示用户另存为 .docx
- 加密 PDF — 抛 error
- 扫描版 PDF (无 OCR) — 返空文本, 提示用户

# 设计

- 50KB 截断 (~12K token), 防注入到 system prompt 后炸 token
- 返 JSON 而不是文本, Rust 端能区分 error/truncated/text
- 出错也返 JSON {error: "..."}, Rust 不靠 exit code 判断
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

#: 文本截断上限. ~12K token (1 中文字 ~ 1.3 token), 给 LLM context 留余地.
MAX_CHARS = 50000


def parse_pdf(path: Path) -> str:
    """PDF 文本提取. 用 pypdfium2 (gateway venv 已装)."""
    try:
        import pypdfium2 as pdfium  # noqa: PLC0415
    except ImportError:
        raise RuntimeError("pypdfium2 未装. 跑: pip install pypdfium2")

    pdf = pdfium.PdfDocument(str(path))
    parts = []
    for i in range(len(pdf)):
        page = pdf[i]
        textpage = page.get_textpage()
        chunk = textpage.get_text_range() or ""
        if chunk.strip():
            parts.append(f"--- Page {i + 1} ---\n{chunk.strip()}")
    text = "\n\n".join(parts)
    if not text.strip():
        text = "[PDF 无可提取文本, 可能是扫描版. 后续做 OCR.]"
    return text


def parse_xlsx(path: Path) -> str:
    """Excel 各 sheet 转 tsv 风格文本."""
    try:
        from openpyxl import load_workbook  # noqa: PLC0415
    except ImportError:
        raise RuntimeError("openpyxl 未装. 跑: pip install openpyxl")

    wb = load_workbook(path, read_only=True, data_only=True)
    parts = []
    for sheet in wb.worksheets:
        parts.append(f"## Sheet: {sheet.title}")
        for row in sheet.iter_rows(values_only=True):
            cells = [str(c) if c is not None else "" for c in row]
            if any(cells):
                parts.append("\t".join(cells))
        parts.append("")  # sheet 间空行
    return "\n".join(parts)


def parse_docx(path: Path) -> str:
    """Word 文档提取段落 + 表格."""
    try:
        from docx import Document  # noqa: PLC0415
    except ImportError:
        raise RuntimeError("python-docx 未装. 跑: pip install python-docx")

    doc = Document(str(path))
    parts = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            if any(cells):
                parts.append(" | ".join(cells))
    return "\n\n".join(parts)


def parse_csv(path: Path) -> str:
    import csv  # noqa: PLC0415
    # utf-8-sig 自动剥 BOM
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        return "\n".join("\t".join(row) for row in reader)


def parse_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


PARSERS = {
    ".pdf": parse_pdf,
    ".xlsx": parse_xlsx,
    ".xls": parse_xlsx,
    ".docx": parse_docx,
    ".csv": parse_csv,
    ".txt": parse_text,
    ".md": parse_text,
    ".markdown": parse_text,
    ".log": parse_text,
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
    parser = PARSERS.get(ext)
    if parser is None:
        print(json.dumps({
            "error": f"不支持 {ext}. 支持: {', '.join(sorted(PARSERS.keys()))}"
        }, ensure_ascii=False))
        return 4

    try:
        full_text = parser(path)
    except Exception as e:
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False))
        return 5

    truncated = len(full_text) > MAX_CHARS
    text = full_text[:MAX_CHARS] if truncated else full_text

    result = {
        "filename": path.name,
        "ext": ext,
        "char_count": len(full_text),
        "truncated": truncated,
        "text": text,
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
