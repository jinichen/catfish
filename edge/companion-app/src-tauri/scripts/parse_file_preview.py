#!/usr/bin/env python3
"""右侧成果区预览 Word / Excel / PPT (11/07 鸿波「先做成果区」)。

    python parse_file_preview.py <文件路径>

stdout 输出一份 JSON, 前端 (tabs/Chat/artifacts/OfficeView.tsx) 用 React 画出来 ——
不吐 HTML: 文字一律当纯文本走 React 渲染, 不会有脚本 / 样式混进 Companion; 颜色跟着
Companion 的亮 / 暗色走。

    Word  {"kind": "docx", "blocks": [{"t": "h", "level": 1, "text": ...},
                                      {"t": "p", "text": ..., "list": bool},
                                      {"t": "table", "rows": [[...]]},
                                      {"t": "img", "src": "data:image/png;base64,..."}],
           "truncated": bool}
    Excel {"kind": "sheet", "sheets": [{"name", "rows": [[...]], "total_rows", "total_cols"}],
           "truncated": bool}
    PPT   {"kind": "slides", "slides": [{"title", "texts": [...], "images": [...]}],
           "truncated": bool}
    出错  {"error": "..."}

上限 (够看清、不至于把界面卡死): Word 3000 段、每个表 300 行; Excel 每张表 500 行 × 60 列;
PPT 200 页; 内嵌图片合计 6MB。超了 truncated=true, 界面提示「完整内容用默认程序打开」。

只用 hermes venv 里本来就装的 python-docx / openpyxl / xlrd / python-pptx (hermes-extra-packages.txt),
跟上传文件解析 (parse_file_office.py) 是同一套依赖。
"""
from __future__ import annotations

import base64
import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Any

MAX_BLOCKS = 3000
MAX_TABLE_ROWS = 300
MAX_SHEET_ROWS = 500
MAX_SHEET_COLS = 60
MAX_SLIDES = 200
MAX_IMAGE_BYTES = 6 * 1024 * 1024
MAX_CELL_CHARS = 500


class _Images:
    """内嵌图片的总量预算 —— 超了的图不再带, 只记一笔。"""

    def __init__(self) -> None:
        self.used = 0
        self.skipped = 0

    def data_uri(self, blob: bytes, content_type: str) -> str | None:
        if not content_type.startswith("image/") or content_type in ("image/x-emf", "image/x-wmf"):
            return None  # emf / wmf 浏览器画不了
        if self.used + len(blob) > MAX_IMAGE_BYTES:
            self.skipped += 1
            return None
        self.used += len(blob)
        return f"data:{content_type};base64,{base64.b64encode(blob).decode('ascii')}"


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, _dt.datetime):
        return value.strftime("%Y-%m-%d %H:%M") if (value.hour or value.minute) else value.strftime("%Y-%m-%d")
    if isinstance(value, _dt.date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value)
    return text if len(text) <= MAX_CELL_CHARS else text[:MAX_CELL_CHARS] + "…"


# ── Word ─────────────────────────────────────────────────────────────

def _heading_level(style_name: str) -> int:
    name = (style_name or "").lower()
    if name == "title":
        return 1
    for prefix in ("heading ", "标题 "):
        if name.startswith(prefix):
            tail = name[len(prefix):].strip()
            if tail.isdigit():
                return max(1, min(int(tail), 4))
    return 0


def _docx_images(paragraph: Any, part: Any, images: _Images) -> list[str]:
    out: list[str] = []
    for blip in paragraph._element.findall(".//{http://schemas.openxmlformats.org/drawingml/2006/main}blip"):
        rid = blip.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed")
        rel = part.rels.get(rid) if rid else None
        if rel is None or getattr(rel, "is_external", False):
            continue
        target = rel.target_part
        uri = images.data_uri(target.blob, getattr(target, "content_type", ""))
        if uri:
            out.append(uri)
    return out


def preview_docx(path: Path) -> dict[str, Any]:
    from docx import Document  # noqa: PLC0415
    from docx.table import Table  # noqa: PLC0415
    from docx.text.paragraph import Paragraph  # noqa: PLC0415

    doc = Document(str(path))
    images = _Images()
    blocks: list[dict[str, Any]] = []
    truncated = False
    for child in doc.element.body.iterchildren():
        if len(blocks) >= MAX_BLOCKS:
            truncated = True
            break
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            para = Paragraph(child, doc)
            for src in _docx_images(para, doc.part, images):
                blocks.append({"t": "img", "src": src})
            text = para.text.strip()
            if not text:
                continue
            style_name = para.style.name if para.style is not None else ""
            level = _heading_level(style_name)
            if level:
                blocks.append({"t": "h", "level": level, "text": text})
            else:
                # 列表: 段落自己带编号, 或者用的是「List Bullet / 列表段落」这类样式 (编号写在样式里)
                is_list = child.find(".//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}numPr") is not None \
                    or "list" in style_name.lower() or "列表" in style_name
                blocks.append({"t": "p", "text": text, "list": is_list})
        elif tag == "tbl":
            table = Table(child, doc)
            rows: list[list[str]] = []
            for i, row in enumerate(table.rows):
                if i >= MAX_TABLE_ROWS:
                    truncated = True
                    break
                rows.append([_cell(c.text.strip()) for c in row.cells])
            if rows:
                blocks.append({"t": "table", "rows": rows})
    return {"kind": "docx", "blocks": blocks, "truncated": truncated or images.skipped > 0}


# ── Excel ────────────────────────────────────────────────────────────

def _trim_rows(rows: list[list[str]]) -> list[list[str]]:
    """去掉尾部全空的行和全空的列 (openpyxl 的 max_row 常把格式化过的空行算进去)。"""
    while rows and not any(rows[-1]):
        rows.pop()
    width = max((max((i + 1 for i, v in enumerate(r) if v), default=0) for r in rows), default=0)
    return [r[:width] for r in rows]


def preview_xlsx(path: Path) -> dict[str, Any]:
    from openpyxl import load_workbook  # noqa: PLC0415

    wb = load_workbook(str(path), read_only=True, data_only=True)
    sheets: list[dict[str, Any]] = []
    truncated = False
    try:
        for ws in wb.worksheets:
            total_rows = ws.max_row or 0
            total_cols = ws.max_column or 0
            rows: list[list[str]] = []
            for i, row in enumerate(ws.iter_rows(max_col=min(total_cols, MAX_SHEET_COLS) or None, values_only=True)):
                if i >= MAX_SHEET_ROWS:
                    truncated = True
                    break
                rows.append([_cell(v) for v in row])
            if total_cols > MAX_SHEET_COLS:
                truncated = True
            sheets.append({"name": ws.title, "rows": _trim_rows(rows), "total_rows": total_rows, "total_cols": total_cols})
    finally:
        wb.close()
    return {"kind": "sheet", "sheets": sheets, "truncated": truncated}


def preview_xls(path: Path) -> dict[str, Any]:
    import xlrd  # noqa: PLC0415

    book = xlrd.open_workbook(str(path))
    sheets: list[dict[str, Any]] = []
    truncated = False
    for sh in book.sheets():
        rows: list[list[str]] = []
        for r in range(min(sh.nrows, MAX_SHEET_ROWS)):
            values = sh.row_values(r)[:MAX_SHEET_COLS]
            rows.append([_cell(v) for v in values])
        if sh.nrows > MAX_SHEET_ROWS or sh.ncols > MAX_SHEET_COLS:
            truncated = True
        sheets.append({"name": sh.name, "rows": _trim_rows(rows), "total_rows": sh.nrows, "total_cols": sh.ncols})
    return {"kind": "sheet", "sheets": sheets, "truncated": truncated}


# ── PPT ──────────────────────────────────────────────────────────────

def _shape_texts(shape: Any, images: _Images, texts: list[str], pics: list[str]) -> None:
    if shape.shape_type == 6 and hasattr(shape, "shapes"):  # GROUP
        for sub in shape.shapes:
            _shape_texts(sub, images, texts, pics)
        return
    if getattr(shape, "has_text_frame", False) and shape.has_text_frame:
        for para in shape.text_frame.paragraphs:
            line = "".join(run.text for run in para.runs).strip()
            if line:
                texts.append(line)
    if getattr(shape, "has_table", False) and shape.has_table:
        for row in shape.table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                texts.append(" | ".join(cells))
    image = getattr(shape, "image", None) if shape.shape_type == 13 else None  # PICTURE
    if image is not None:
        uri = images.data_uri(image.blob, image.content_type)
        if uri:
            pics.append(uri)


def preview_pptx(path: Path) -> dict[str, Any]:
    from pptx import Presentation  # noqa: PLC0415

    prs = Presentation(str(path))
    images = _Images()
    slides: list[dict[str, Any]] = []
    truncated = False
    for i, slide in enumerate(prs.slides):
        if i >= MAX_SLIDES:
            truncated = True
            break
        title = ""
        if slide.shapes.title is not None and slide.shapes.title.has_text_frame:
            title = slide.shapes.title.text_frame.text.strip()
        texts: list[str] = []
        pics: list[str] = []
        for shape in slide.shapes:
            if slide.shapes.title is not None and shape.shape_id == slide.shapes.title.shape_id:
                continue
            _shape_texts(shape, images, texts, pics)
        slides.append({"title": title, "texts": texts, "images": pics})
    return {"kind": "slides", "slides": slides, "truncated": truncated or images.skipped > 0}


HANDLERS = {".docx": preview_docx, ".xlsx": preview_xlsx, ".xlsm": preview_xlsx, ".xls": preview_xls, ".pptx": preview_pptx}


def preview(path: Path) -> dict[str, Any]:
    handler = HANDLERS.get(path.suffix.lower())
    if handler is None:
        return {"error": f"这种文件 ({path.suffix or '无扩展名'}) 预览不了, 用默认程序打开看"}
    if not path.is_file():
        return {"error": "文件不在了 (可能被移走或删了)"}
    try:
        return handler(path)
    except ImportError as e:
        return {"error": f"鲶鱼自带的 Python 环境缺组件 ({e.name}), 修复安装后再试"}
    except Exception as e:  # noqa: BLE001 —— 坏文件 / 加密文件, 原因给到界面
        return {"error": f"文件打不开: {type(e).__name__}: {e}"}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(json.dumps({"error": "用法: parse_file_preview.py <文件路径>"}, ensure_ascii=False))
        return 2
    print(json.dumps(preview(Path(argv[1])), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
