"""从文件抽取纯文本。

# 11/10 改: 富文档不再只靠 markitdown

Companion 拉起的 watcher 跑在 hermes 的 Python 环境里, 那里**没有 markitdown** ——
于是员工机上新增的 Word / PPT / Excel / PDF 一个都进不了索引 (抽不出字 → 静默跳过)。
鸿波开发机上旧的 launchd watcher 碰巧带 markitdown, 掩盖了这件事; 11/10 卸掉它后,
往 ~/.catfish/outputs 放一份「任命书.docx」, 索引里查无此文件。

现在用 hermes 环境里本来就装着的库 (src-tauri/hermes-extra-packages.txt, 跟 tool-bridge
doc_text.py 同一套): pdf → pypdfium2, docx → python-docx, pptx → python-pptx,
xlsx → openpyxl, xls → xlrd。它们缺了、或者是 .doc / .ppt / .epub 这种它们读不了的,
再退回 markitdown (装了的话)。

按「段」返回 (PDF 每页、PPT 每页、Excel 每个工作表、Word 整篇一段), 给 indexer 在文字
超长时按段平均分额度用 —— 不然截断只留前面, 一本 54 个工作表的台账只搜得到前 10 个表。

PPT 的文字常在「组合」形状里 (ITSS 汇报 57 页, 不进组合读是 0 字, 进去读 2.8 万字), 要递归。

图片和扫描版 PDF (没有文字层) 走本机识字 (ocr.py): 图片整张识别; PDF 只识别没字的那些页。
这台机器没法识别时抛 OcrUnavailable, indexer 记成 needs_ocr, 面板上说明原因。
"""
from __future__ import annotations

import logging
from pathlib import Path

from .ocr import IMAGE_EXTS, OcrUnavailable, meaningful, ocr_image, ocr_pdf_pages

logger = logging.getLogger("catfish.search.extractor")

# markitdown 底层会调 pdfminer，对非标准 PDF 会刷一堆 WARNING（FontBBox/CMap 之类）。
# 这些跟我们要做的事没关系，全部压到 ERROR 级别，免得把索引进度刷没了。
for _noisy in (
    "pdfminer",
    "pdfminer.pdffont",
    "pdfminer.pdfinterp",
    "pdfminer.pdfpage",
    "pdfminer.cmapdb",
    "pdfminer.converter",
    "pdfminer.layout",
    "pdfminer.pdfparser",
):
    logging.getLogger(_noisy).setLevel(logging.ERROR)

# 不需要 markitdown 就能读的类型（纯文本、代码、配置）
PLAIN_TEXT_EXTS = {
    # 笔记 / 标记
    ".md", ".txt", ".rst", ".tex",
    # 代码
    ".py", ".js", ".ts", ".tsx", ".jsx", ".vue",
    ".go", ".rs", ".java", ".kt", ".swift",
    ".c", ".cpp", ".cc", ".h", ".hpp",
    ".cs", ".rb", ".php",
    ".sh", ".bash", ".zsh",
    # 前端
    ".html", ".htm", ".css", ".scss", ".less",
    # 配置 / 数据
    ".json", ".yaml", ".yml", ".toml", ".ini", ".conf",
    ".xml", ".csv", ".tsv",
    # 其他
    ".sql", ".log",
}

# 需要 markitdown 的富文档类型
RICH_DOC_EXTS = {
    # PDF
    ".pdf",
    # Office 新格式
    ".docx", ".xlsx", ".pptx",
    # Office 旧格式（markitdown 支持）
    ".doc", ".xls", ".ppt",
    # 其他
    ".epub",
}


def _try_import_markitdown():
    try:
        from markitdown import MarkItDown  # noqa: PLC0415
        return MarkItDown()
    except ImportError:
        return None


_MARKITDOWN = _try_import_markitdown()


def extract_sections(path: Path) -> list[str] | None:
    """抽文字, 按段返回 (见模块说明)。抽不出 / 失败返回 None; 需要识字但本机没法识别抛 OcrUnavailable。"""
    ext = path.suffix.lower()
    try:
        if ext in PLAIN_TEXT_EXTS:
            return [_read_plain(path)]
        if ext in RICH_DOC_EXTS:
            return _read_rich(path)
        if ext in IMAGE_EXTS:
            text = ocr_image(path)
            return [text] if meaningful(text) else None
    except OcrUnavailable:
        raise
    except Exception as e:
        logger.debug("extract failed for %s: %s", path, e)
        return None
    return None


def extract_text(path: Path) -> str | None:
    """从文件抽纯文本 (各段拼起来)，失败返回 None。"""
    sections = extract_sections(path)
    return None if sections is None else "\n\n".join(sections)


def _read_plain(path: Path) -> str:
    """直接读文本文件，容错编码。"""
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return path.read_text(encoding=enc, errors="strict")
        except UnicodeDecodeError:
            continue
    # 最后一道：替换错误字符
    return path.read_text(encoding="utf-8", errors="replace")


#: 一页抽出来不到这么多字, 当它是扫描页 (页码 / 页眉那点字不算)
_SCANNED_PAGE_CHARS = 10


def _pdf(path: Path) -> list[str]:
    import pypdfium2 as pdfium  # noqa: PLC0415

    pdf = pdfium.PdfDocument(str(path))
    try:
        pages = []
        for i in range(len(pdf)):
            page = pdf[i]
            try:
                pages.append(page.get_textpage().get_text_range().strip())
            finally:
                page.close()
    finally:
        pdf.close()
    empty = [i for i, t in enumerate(pages) if len(t) < _SCANNED_PAGE_CHARS]
    if not empty:
        return pages
    whole_scan = sum(len(t) for t in pages) < 20
    try:
        recognized = ocr_pdf_pages(path, empty)
    except OcrUnavailable:
        if whole_scan:
            raise  # 整份都是扫描件, 又没法识别 → 让 indexer 记 needs_ocr
        return pages  # 只有几页是图, 有字的照收
    for i, text in recognized.items():
        if meaningful(text):
            pages[i] = text
    return pages


def _docx(path: Path) -> list[str]:
    from docx import Document  # noqa: PLC0415

    doc = Document(str(path))
    parts = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            parts.append(" | ".join(c.text.strip() for c in row.cells))
    return ["\n".join(parts)]


def _pptx_shape_texts(shapes, out: list[str]) -> None:
    from pptx.enum.shapes import MSO_SHAPE_TYPE  # noqa: PLC0415

    for shape in shapes:
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            _pptx_shape_texts(shape.shapes, out)
            continue
        if getattr(shape, "has_text_frame", False) and shape.text_frame.text.strip():
            out.append(shape.text_frame.text.strip())
        if getattr(shape, "has_table", False) and shape.has_table:
            for row in shape.table.rows:
                out.append(" | ".join(c.text.strip() for c in row.cells))


def _pptx(path: Path) -> list[str]:
    from pptx import Presentation  # noqa: PLC0415

    slides = []
    for i, slide in enumerate(Presentation(str(path)).slides, 1):
        texts: list[str] = []
        _pptx_shape_texts(slide.shapes, texts)
        if slide.has_notes_slide:
            note = slide.notes_slide.notes_text_frame.text.strip() if slide.notes_slide.notes_text_frame else ""
            if note:
                texts.append(note)
        if texts:
            slides.append(f"第 {i} 页\n" + "\n".join(texts))
    return slides


def _xlsx(path: Path) -> list[str]:
    from openpyxl import load_workbook  # noqa: PLC0415

    wb = load_workbook(str(path), read_only=True, data_only=True)
    sheets = []
    try:
        for ws in wb.worksheets:
            rows = []
            for row in ws.iter_rows(values_only=True):
                cells = ["" if v is None else str(v) for v in row]
                if any(c.strip() for c in cells):
                    rows.append(" | ".join(cells))
            if rows:
                sheets.append(f"{ws.title}\n" + "\n".join(rows))
    finally:
        wb.close()
    return sheets


def _xls(path: Path) -> list[str]:
    import xlrd  # noqa: PLC0415

    book = xlrd.open_workbook(str(path), on_demand=True)
    sheets = []
    try:
        for sh in book.sheets():
            rows = []
            for r in range(sh.nrows):
                cells = ["" if v is None else str(v) for v in sh.row_values(r)]
                if any(c.strip() for c in cells):
                    rows.append(" | ".join(cells))
            if rows:
                sheets.append(f"{sh.name}\n" + "\n".join(rows))
    finally:
        book.release_resources()
    return sheets


_NATIVE = {".pdf": _pdf, ".docx": _docx, ".pptx": _pptx, ".xlsx": _xlsx, ".xls": _xls}


def _read_rich(path: Path) -> list[str] | None:
    """富文档: 先用 hermes 环境里的库, 缺库 / 读不了的类型再退回 markitdown。"""
    fn = _NATIVE.get(path.suffix.lower())
    if fn is not None:
        try:
            return [s for s in fn(path) if s.strip()]
        except ImportError:
            pass  # 这台机器没装对应的库, 试 markitdown
    if _MARKITDOWN is None:
        logger.debug("no extractor for %s (markitdown not installed)", path)
        return None
    result = _MARKITDOWN.convert(str(path))
    text = getattr(result, "text_content", None) or getattr(result, "markdown", None)
    return [text] if text else None


def fit_sections(sections: list[str], cap: int) -> str:
    """把各段拼成不超过 cap 字的正文。

    不超 → 原样拼。超了 → 每段平均分额度 (短的段用不完的份额让给长的), 每段从头截。
    这样一本 54 个工作表的台账, 每个表的表名和前面若干行都搜得到, 而不是只剩前 10 个表。
    """
    sep = "\n\n"
    total = sum(len(s) for s in sections) + len(sep) * max(0, len(sections) - 1)
    if total <= cap:
        return sep.join(sections)
    budget = max(0, cap - len(sep) * max(0, len(sections) - 1))
    alloc = [0] * len(sections)
    remaining = budget
    order = sorted(range(len(sections)), key=lambda i: len(sections[i]))
    for k, i in enumerate(order):
        share = remaining // (len(order) - k)
        take = min(len(sections[i]), share)
        alloc[i] = take
        remaining -= take
    return sep.join(s[:alloc[i]] for i, s in enumerate(sections))
