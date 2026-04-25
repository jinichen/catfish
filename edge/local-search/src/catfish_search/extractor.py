"""从文件抽取纯文本。

优先用 markitdown（支持 pdf/docx/xlsx/pptx 等）。
没装 markitdown 时，md/txt 类纯文本可以直接读。
"""
from __future__ import annotations

import logging
from pathlib import Path

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


def extract_text(path: Path) -> str | None:
    """从文件抽纯文本，失败返回 None。"""
    ext = path.suffix.lower()
    try:
        if ext in PLAIN_TEXT_EXTS:
            return _read_plain(path)
        if ext in RICH_DOC_EXTS:
            return _read_rich(path)
    except Exception as e:
        logger.debug("extract failed for %s: %s", path, e)
        return None
    return None


def _read_plain(path: Path) -> str:
    """直接读文本文件，容错编码。"""
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return path.read_text(encoding=enc, errors="strict")
        except UnicodeDecodeError:
            continue
    # 最后一道：替换错误字符
    return path.read_text(encoding="utf-8", errors="replace")


def _read_rich(path: Path) -> str | None:
    """用 markitdown 读富文档。没装 markitdown 则跳过。"""
    if _MARKITDOWN is None:
        logger.debug("markitdown not installed, skipping %s", path)
        return None
    result = _MARKITDOWN.convert(str(path))
    return getattr(result, "text_content", None) or getattr(result, "markdown", None)
