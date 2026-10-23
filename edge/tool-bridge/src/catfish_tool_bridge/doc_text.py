"""入知识库前把文档变成文字 (10/23)。

鸿波 10/23: 让鲶鱼把「材料库/2026」整个目录入库。那些 pdf / pptx 不是聊天附件, 没有
Companion 解析好的文字 (parsed_text_path), catfish_wiki_ingest 就退回「把原文件当文本读」
(errors="replace") —— 扫描版 pdf 的 JPEG 字节原样写进 raw/sources, 一份 2700 万字的乱码。
后台蒸馏把乱码喂给模型, 当然什么都抽不出来。

这里用 hermes 环境里本来就装着的库 (跟 Companion 的 parse_file.py 同一套, 见
src-tauri/hermes-extra-packages.txt) 抽正文: pdf → pypdfium2, docx → python-docx,
pptx → python-pptx, xlsx → openpyxl, 文本类直接读。抽不出文字 (扫描件 / 图片) 返回空,
由调用方告诉模型先识别成文字再入库, **绝不**把二进制当文字写进去。
"""
from __future__ import annotations

from pathlib import Path

TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".csv", ".json", ".yaml", ".yml", ".html", ".htm", ".xml", ".log"}
#: 这么多比例的字符是乱码 / 控制字符, 就不是文字 (正常中文文档是 0%, 二进制当文字读是 50% 上下)
_JUNK_RATIO = 0.02


def looks_binary(text: str) -> bool:
    sample = text[:200000]
    if not sample:
        return False
    junk = sum(1 for c in sample if c == "�" or (ord(c) < 32 and c not in "\n\r\t\f"))
    return junk / len(sample) > _JUNK_RATIO


def _pdf(path: Path) -> str:
    import pypdfium2 as pdfium  # noqa: PLC0415

    pdf = pdfium.PdfDocument(str(path))
    try:
        pages = []
        for i in range(len(pdf)):
            page = pdf[i]
            pages.append(page.get_textpage().get_text_range())
        return "\n\n".join(p.strip() for p in pages if p.strip())
    finally:
        pdf.close()


def _docx(path: Path) -> str:
    from docx import Document  # noqa: PLC0415

    doc = Document(str(path))
    parts = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            parts.append(" | ".join(c.text.strip() for c in row.cells))
    return "\n".join(parts)


def _pptx(path: Path) -> str:
    from pptx import Presentation  # noqa: PLC0415

    out = []
    for i, slide in enumerate(Presentation(str(path)).slides, 1):
        texts = []
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False) and shape.text_frame.text.strip():
                texts.append(shape.text_frame.text.strip())
            if getattr(shape, "has_table", False):
                for row in shape.table.rows:
                    texts.append(" | ".join(c.text.strip() for c in row.cells))
        if texts:
            out.append(f"## 第 {i} 页\n" + "\n".join(texts))
    return "\n\n".join(out)


def _xlsx(path: Path) -> str:
    from openpyxl import load_workbook  # noqa: PLC0415

    wb = load_workbook(str(path), read_only=True, data_only=True)
    out = []
    try:
        for ws in wb.worksheets:
            rows = []
            for row in ws.iter_rows(values_only=True):
                cells = ["" if v is None else str(v) for v in row]
                if any(c.strip() for c in cells):
                    rows.append(" | ".join(cells))
            if rows:
                out.append(f"## {ws.title}\n" + "\n".join(rows))
    finally:
        wb.close()
    return "\n\n".join(out)


_EXTRACTORS = {".pdf": _pdf, ".docx": _docx, ".pptx": _pptx, ".xlsx": _xlsx, ".xlsm": _xlsx}


def extract_text(path: Path) -> tuple[str, str | None]:
    """返回 (正文, 抽不出来的原因)。正文为空时原因必有。"""
    suffix = path.suffix.lower()
    if suffix in TEXT_SUFFIXES:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                text = path.read_text(encoding="gbk")
            except (UnicodeDecodeError, OSError) as e:
                return "", f"读不出文字 ({e})"
        except OSError as e:
            return "", f"读不了 ({e})"
        return ("", "内容不是文字") if looks_binary(text) else (text, None)
    fn = _EXTRACTORS.get(suffix)
    if fn is None:
        return "", f"{suffix or '无扩展名'} 这种文件抽不出文字 (图片要先识别成文字; 老 .doc/.ppt 请另存为 .docx/.pptx)"
    try:
        text = fn(path)
    except Exception as e:  # noqa: BLE001
        return "", f"解析失败 ({type(e).__name__}: {e})"
    # pdf 抽出来只有零星几个字 = 扫描件 (页码之类); 别的格式有字就算
    if suffix == ".pdf" and len(text.strip()) < 20:
        return "", "这是扫描件 / 图片型 pdf, 没有文字层"
    if not text.strip():
        return "", "里面没有文字 (可能都是图片)"
    return text, None
