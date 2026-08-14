"""leadership-briefing skill — blocks 模式 + CSV 附件 + 公文样式渲染.

# 设计

  - 5 段固定 (一二三四五), 每段是 blocks 数组
  - 4 种 block: paragraph / kv_table / table / ordered_list
  - 附件 (CSV) 跟主 .docx 同目录
  - 输出路径: 员工指定 honor, 没指定 → ~/.catfish/outputs/YYYY-MM-DD/HHMMSS_<title>/

# 字体

  - 标题: 方正小标宋简体 三号 (16pt) 加粗
  - 一级标题: 黑体 四号 (14pt) 加粗
  - 表头 + 正文: 仿宋_GB2312 四号 (14pt)
  - 红字: 同上 + RGB(255,0,0)
  - 页码: 仿宋_GB2312 小五 (9pt)

调用入口: render_briefing(...) — 见 SKILL.md
"""

from __future__ import annotations

import csv
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.table import WD_ALIGN_VERTICAL
from docx.enum.text import WD_LINE_SPACING, WD_PARAGRAPH_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

# ── 字体常量 ────────────────────────────────────────────────────
FONT_TITLE = "方正小标宋简体"
FONT_HEADING = "黑体"
FONT_BODY = "仿宋_GB2312"

SIZE_TITLE = Pt(16)
SIZE_HEADING = Pt(14)
SIZE_BODY = Pt(14)
SIZE_PAGE_NUMBER = Pt(9)

LINE_SPACING_PT = Pt(28)
RED = RGBColor(0xFF, 0x00, 0x00)
TABLE_BORDER_SZ = "4"  # 1/8 pt — 4 = 0.5pt


# ── 字体 + 段落 helpers ─────────────────────────────────────────


def set_run_font(
    run, font_name: str, size: Pt, bold: bool = False, color: RGBColor | None = None
) -> None:
    run.font.name = font_name
    run.font.size = size
    run.bold = bold
    if color is not None:
        run.font.color.rgb = color
    rPr = run._element.get_or_add_rPr()
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rPr.insert(0, rFonts)
    rFonts.set(qn("w:eastAsia"), font_name)
    rFonts.set(qn("w:ascii"), font_name)
    rFonts.set(qn("w:hAnsi"), font_name)
    rFonts.set(qn("w:cs"), font_name)


def set_paragraph_format(
    paragraph,
    *,
    first_line_indent: bool = True,
    alignment: WD_PARAGRAPH_ALIGNMENT | None = None,
    space_before: Pt | None = None,
    space_after: Pt | None = None,
    line_spacing: Pt | None = None,
    left_indent: Pt | None = None,
) -> None:
    pf = paragraph.paragraph_format
    pf.line_spacing_rule = WD_LINE_SPACING.EXACTLY
    pf.line_spacing = line_spacing if line_spacing is not None else LINE_SPACING_PT
    if first_line_indent:
        pf.first_line_indent = Pt(28)
    if alignment is not None:
        paragraph.alignment = alignment
    if space_before is not None:
        pf.space_before = space_before
    if space_after is not None:
        pf.space_after = space_after
    if left_indent is not None:
        pf.left_indent = left_indent


def set_page_margins(doc) -> None:
    for section in doc.sections:
        section.top_margin = Cm(3.7)
        section.bottom_margin = Cm(3.5)
        section.left_margin = Cm(2.8)
        section.right_margin = Cm(2.6)
        section.page_width = Cm(21.0)
        section.page_height = Cm(29.7)


# ── 红字高亮 ────────────────────────────────────────────────────


def _add_text_with_redzones(
    paragraph, text: str, important: list[str], *, bold: bool = False
) -> None:
    """把 text 按 important_phrases 切片, 命中红字, 其他正常字."""
    if not text:
        return
    if not important:
        run = paragraph.add_run(text)
        set_run_font(run, FONT_BODY, SIZE_BODY, bold=bold)
        return

    phrases_sorted = sorted(set(important), key=len, reverse=True)
    matches: list[tuple[int, int]] = []
    for ph in phrases_sorted:
        if not ph:
            continue
        start = 0
        while True:
            i = text.find(ph, start)
            if i < 0:
                break
            end = i + len(ph)
            overlap = any(not (end <= s or i >= e) for s, e in matches)
            if not overlap:
                matches.append((i, end))
            start = end
    matches.sort()

    cur = 0
    for s, e in matches:
        if cur < s:
            run = paragraph.add_run(text[cur:s])
            set_run_font(run, FONT_BODY, SIZE_BODY, bold=bold)
        run = paragraph.add_run(text[s:e])
        set_run_font(run, FONT_BODY, SIZE_BODY, bold=bold, color=RED)
        cur = e
    if cur < len(text):
        run = paragraph.add_run(text[cur:])
        set_run_font(run, FONT_BODY, SIZE_BODY, bold=bold)


# ── 标题 + 段标题 ───────────────────────────────────────────────


def add_title_lines(doc, lines: list[str]) -> None:
    for i, line in enumerate(lines):
        p = doc.add_paragraph()
        is_last = i == len(lines) - 1
        set_paragraph_format(
            p,
            first_line_indent=False,
            alignment=WD_PARAGRAPH_ALIGNMENT.CENTER,
            space_after=Pt(18) if is_last else Pt(2),
        )
        run = p.add_run(line)
        set_run_font(run, FONT_TITLE, SIZE_TITLE, bold=True)


def add_section_heading(doc, text: str) -> None:
    p = doc.add_paragraph()
    set_paragraph_format(
        p, first_line_indent=False, space_before=Pt(8), space_after=Pt(2)
    )
    run = p.add_run(text)
    set_run_font(run, FONT_HEADING, SIZE_HEADING, bold=True)


# ── block 渲染器 ────────────────────────────────────────────────


def _set_cell_borders(cell) -> None:
    tcPr = cell._tc.get_or_add_tcPr()
    tcBorders = tcPr.find(qn("w:tcBorders"))
    if tcBorders is None:
        tcBorders = OxmlElement("w:tcBorders")
        tcPr.append(tcBorders)
    for side in ("top", "left", "bottom", "right"):
        b = tcBorders.find(qn(f"w:{side}"))
        if b is None:
            b = OxmlElement(f"w:{side}")
            tcBorders.append(b)
        b.set(qn("w:val"), "single")
        b.set(qn("w:sz"), TABLE_BORDER_SZ)
        b.set(qn("w:color"), "000000")


def _write_cell_text(
    cell,
    text: str,
    important: list[str],
    *,
    bold: bool = False,
    align: WD_PARAGRAPH_ALIGNMENT = WD_PARAGRAPH_ALIGNMENT.LEFT,
) -> None:
    cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
    p = cell.paragraphs[0]
    p.clear()
    set_paragraph_format(
        p, first_line_indent=False, alignment=align, line_spacing=Pt(20)
    )
    _add_text_with_redzones(p, text, important, bold=bold)
    _set_cell_borders(cell)


def _write_cell_multiline(cell, lines: list[str], important: list[str]) -> None:
    cell.vertical_alignment = WD_ALIGN_VERTICAL.TOP
    cell.paragraphs[0].clear()
    for i, line in enumerate(lines):
        p = cell.paragraphs[0] if i == 0 else cell.add_paragraph()
        set_paragraph_format(p, first_line_indent=False, line_spacing=Pt(20))
        _add_text_with_redzones(p, line, important)
    _set_cell_borders(cell)


def _render_paragraph_block(doc, block: dict, important: list[str]) -> None:
    text = block.get("text", "")
    p = doc.add_paragraph()
    set_paragraph_format(p, first_line_indent=True)
    _add_text_with_redzones(p, text, important)


def _render_kv_table_block(doc, block: dict, important: list[str]) -> None:
    rows = block.get("rows") or []
    if not rows:
        return
    table = doc.add_table(rows=len(rows), cols=2)
    for row in table.rows:
        row.cells[0].width = Cm(3.0)
        row.cells[1].width = Cm(13.0)
    for i, (label, content) in enumerate(rows):
        cell_label = table.cell(i, 0)
        _write_cell_text(
            cell_label, label, important,
            align=WD_PARAGRAPH_ALIGNMENT.CENTER,
        )
        cell_content = table.cell(i, 1)
        if isinstance(content, list):
            _write_cell_multiline(cell_content, content, important)
        else:
            _write_cell_text(cell_content, str(content), important)
    spacer = doc.add_paragraph()
    set_paragraph_format(spacer, first_line_indent=False)


def _render_table_block(doc, block: dict, important: list[str]) -> None:
    headers = block.get("headers") or []
    rows = block.get("rows") or []
    if not headers and not rows:
        return
    n_cols = len(headers) if headers else len(rows[0])
    n_rows_total = (1 if headers else 0) + len(rows)
    table = doc.add_table(rows=n_rows_total, cols=n_cols)
    row_offset = 0
    if headers:
        for ci, h in enumerate(headers):
            _write_cell_text(
                table.cell(0, ci), str(h), important,
                bold=True, align=WD_PARAGRAPH_ALIGNMENT.CENTER,
            )
        row_offset = 1
    for ri, row in enumerate(rows):
        for ci, val in enumerate(row):
            _write_cell_text(
                table.cell(ri + row_offset, ci), str(val), important,
            )
    spacer = doc.add_paragraph()
    set_paragraph_format(spacer, first_line_indent=False)


def _render_ordered_list_block(doc, block: dict, important: list[str]) -> None:
    items = block.get("items") or []
    for idx, item in enumerate(items, 1):
        title_text = item.get("text", "")
        subs = item.get("subs") or []
        # 顶级: "1. xxx" — 不缩进, 直接行首
        p = doc.add_paragraph()
        set_paragraph_format(
            p, first_line_indent=False, space_before=Pt(2), space_after=Pt(0)
        )
        prefix_run = p.add_run(f"{idx}. ")
        set_run_font(prefix_run, FONT_BODY, SIZE_BODY, bold=False)
        _add_text_with_redzones(p, title_text, important)
        # 子项
        for sub in subs:
            p_sub = doc.add_paragraph()
            set_paragraph_format(
                p_sub, first_line_indent=False, space_before=Pt(0),
                left_indent=Pt(28),
            )
            _add_text_with_redzones(p_sub, sub, important)


_BLOCK_RENDERERS = {
    "paragraph": _render_paragraph_block,
    "kv_table": _render_kv_table_block,
    "table": _render_table_block,
    "ordered_list": _render_ordered_list_block,
}


def _render_section(doc, section: dict, important: list[str]) -> None:
    heading = section.get("heading", "").strip()
    if heading:
        add_section_heading(doc, heading)
    for block in section.get("blocks") or []:
        btype = block.get("type")
        renderer = _BLOCK_RENDERERS.get(btype)
        if renderer is None:
            # 未知类型 — 退化成 paragraph 以免崩
            _render_paragraph_block(
                doc,
                {"type": "paragraph", "text": f"[未知 block 类型: {btype}]"},
                important,
            )
            continue
        renderer(doc, block, important)


# ── 页码 ────────────────────────────────────────────────────────


def _add_field(paragraph, instr_text: str) -> None:
    run = paragraph.add_run()
    set_run_font(run, FONT_BODY, SIZE_PAGE_NUMBER, bold=True)
    fldChar1 = OxmlElement("w:fldChar")
    fldChar1.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = instr_text
    fldChar2 = OxmlElement("w:fldChar")
    fldChar2.set(qn("w:fldCharType"), "end")
    run._element.append(fldChar1)
    run._element.append(instr)
    run._element.append(fldChar2)


def add_page_number_footer(doc) -> None:
    for section in doc.sections:
        footer = section.footer
        footer.is_linked_to_previous = False
        p = footer.paragraphs[0]
        p.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
        pf = p.paragraph_format
        pf.line_spacing_rule = WD_LINE_SPACING.SINGLE
        _add_field(p, "PAGE")
        run_slash = p.add_run(" / ")
        set_run_font(run_slash, FONT_BODY, SIZE_PAGE_NUMBER, bold=True)
        _add_field(p, "NUMPAGES")


# ── 输出路径解析 ────────────────────────────────────────────────


_TITLE_SLUG_RE = re.compile(r"[^\w一-龥\-]+", re.UNICODE)


def _slugify_title(title_lines: list[str]) -> str:
    """从 title_lines 抽一个文件名安全的 slug."""
    full = "".join(title_lines or ["汇报"])
    # 去掉"关于" / "的汇报" / "的请示" 这种通用前后缀, 抽中间核心
    full = re.sub(r"^关于", "", full)
    full = re.sub(r"(的汇报|的请示|的报告|汇报|请示)$", "", full)
    # 替换不安全字符
    slug = _TITLE_SLUG_RE.sub("-", full).strip("-")
    # 太长截断
    if len(slug) > 40:
        slug = slug[:40]
    return slug or "汇报"


def _resolve_output_dir(
    output_path: str | None, title_lines: list[str]
) -> tuple[Path, str]:
    """决定主 .docx 的输出目录 + 文件名 (不含目录).

    返回 (out_dir, docx_filename).

    规则:
      - output_path 是绝对路径且以 .docx 结尾 → 用它
      - output_path 是目录 → 在它下面用自动文件名
      - None → ~/.catfish/outputs/YYYY-MM-DD/HHMMSS_<slug>/
    """
    slug = _slugify_title(title_lines)

    if output_path:
        p = Path(os.path.expanduser(output_path)).resolve()
        if p.suffix.lower() == ".docx":
            return p.parent, p.name
        # 否则当目录处理
        return p, f"{slug}.docx"

    now = datetime.now()
    date_dir = now.strftime("%Y-%m-%d")
    time_dir = now.strftime("%H%M%S") + f"_{slug}"
    home = Path.home()
    # 8/14: output → outputs (全局统一)
    return home / ".catfish" / "outputs" / date_dir / time_dir, f"{slug}.docx"


# ── CSV 附件渲染 ────────────────────────────────────────────────


def _render_attachment_csv(att: dict, out_dir: Path) -> Path:
    """渲染 CSV 附件 → 返回写入路径.

    UTF-8 BOM 编码, Excel/Numbers/记事本 直接打开中文不乱码.
    """
    filename = att.get("filename") or "附件"
    # 保证 .csv 扩展名
    if not filename.lower().endswith(".csv"):
        filename = f"{filename}.csv"
    headers = att.get("headers") or []
    rows = att.get("rows") or []

    out_path = out_dir / filename
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, dialect="excel")
        if headers:
            writer.writerow(headers)
        for row in rows:
            # 数值原样, None → ""
            normalized = ["" if v is None else v for v in row]
            writer.writerow(normalized)

    return out_path


def _render_attachment(att: dict, out_dir: Path) -> Path | None:
    """按 kind 调对应渲染器. 不识别的 kind → 跳过 + log."""
    kind = (att.get("kind") or "").lower().strip()
    if kind == "csv":
        return _render_attachment_csv(att, out_dir)
    # 留扩展点: 后续加 xlsx / pdf / json 等
    return None


# ── 主入口 ──────────────────────────────────────────────────────


def render_briefing(
    *,
    title_lines: list[str],
    sections: list[dict[str, Any]],
    attachments: list[dict[str, Any]] | None = None,
    important_phrases: list[str] | None = None,
    output_path: str | None = None,
) -> dict[str, Any]:
    """生成公文汇报 .docx + 可选 CSV 附件.

    返回:
        {
          "docx": "/path/to/主.docx",
          "attachments": ["/path/to/附件1.csv", "/path/to/附件2.csv"],
          "files": [所有文件路径合并 — Companion 用这个生成 pill],
          "audit": "/path/to/.audit.json" | "",
        }
    """
    important = list(important_phrases or [])
    attachments = list(attachments or [])

    # 1. 解析输出目录 + 主文件名
    out_dir, docx_filename = _resolve_output_dir(output_path, title_lines)
    out_dir.mkdir(parents=True, exist_ok=True)
    docx_path = out_dir / docx_filename

    # 2. 渲染主 docx
    doc = Document()
    set_page_margins(doc)
    add_page_number_footer(doc)
    add_title_lines(doc, title_lines or [])
    for section in sections or []:
        _render_section(doc, section, important)
    doc.save(docx_path)

    # 3. 渲染附件
    attachment_paths: list[Path] = []
    for att in attachments:
        p = _render_attachment(att, out_dir)
        if p:
            attachment_paths.append(p)

    # 4. 错别字二审
    audit_path = _maybe_audit(
        title_lines, sections, attachments, important, docx_path
    )

    files = [str(docx_path)] + [str(p) for p in attachment_paths]
    return {
        "docx": str(docx_path),
        "attachments": [str(p) for p in attachment_paths],
        "files": files,
        "audit": str(audit_path) if audit_path else "",
    }


# ── 错别字二审 (可选) ───────────────────────────────────────────


def _flatten_text_for_audit(
    title_lines, sections, attachments, important
) -> str:
    parts: list[str] = list(title_lines or [])
    for sec in sections or []:
        parts.append(sec.get("heading", ""))
        for block in sec.get("blocks") or []:
            t = block.get("type")
            if t == "paragraph":
                parts.append(block.get("text", ""))
            elif t == "kv_table":
                for label, content in block.get("rows") or []:
                    if isinstance(content, list):
                        parts.append(f"{label}: {' / '.join(content)}")
                    else:
                        parts.append(f"{label}: {content}")
            elif t == "table":
                for row in block.get("rows") or []:
                    parts.append(" / ".join(str(v) for v in row))
            elif t == "ordered_list":
                for item in block.get("items") or []:
                    parts.append(item.get("text", ""))
                    parts.extend(item.get("subs") or [])
    return "\n".join(p for p in parts if p)


def _normalize_errors(raw_errors) -> list[dict[str, Any]]:
    """归一化错别字 errors 格式 → [{old, new, pos, category}].

    pycorrector 返回 [(wrong, correct, begin, end), ...] (list of tuple)
    typo_check 返回 [{old, new, pos, category}, ...] (list of dict)
    统一成后者格式让 audit.json 一致, 调用方按 dict 读不挂.
    """
    out: list[dict[str, Any]] = []
    for err in raw_errors or []:
        if isinstance(err, dict):
            out.append({
                "old": err.get("old") or err.get("source") or err.get("wrong", ""),
                "new": err.get("new") or err.get("target") or err.get("correct", ""),
                "pos": err.get("pos") or err.get("position") or err.get("begin", -1),
                "category": err.get("category", ""),
            })
        elif isinstance(err, (list, tuple)) and len(err) >= 2:
            # pycorrector: (wrong, correct, begin, end)
            out.append({
                "old": str(err[0]),
                "new": str(err[1]),
                "pos": int(err[2]) if len(err) > 2 else -1,
                "category": "pycorrector",
            })
    return out


def _maybe_audit(
    title_lines, sections, attachments, important, docx_path: Path
) -> Path | None:
    """生成 .audit.json 旁路文件, 标错别字 / 合规问题. 不改 .docx 本体.

    优先级:
      1. 内置 typo_check.py (0 依赖, 50+ 常见公文错字规则) — 默认
      2. (可选) pycorrector 全量字典 — 如果 hermes venv 真装了 pycorrector + torch

    鸿波 4-30: pycorrector 依赖 torch (~2GB), 重型. 用 typo_check.py mini
    版覆盖最常见错, 客户 demo 90% 效果一样. 想升级真用 pycorrector → 装 torch.
    """
    full_text = _flatten_text_for_audit(
        title_lines, sections, attachments, important
    )

    # 双 backend 合并去重: typo_check mini (公文准) + pycorrector (通用补漏)
    # 鸿波 4-30 实测: mini 字典在公文场景命中率比 pycorrector Kenlm 高 (后者是通用
    # 语言模型, 公文术语 "部署"/"账户" 没特别训练). 合并取并集效果最好.
    all_errors: list[dict[str, Any]] = []
    backends_used: list[str] = []

    # 1. typo_check mini (主, 永远跑, 0 依赖)
    try:
        from typo_check import correct as mini_correct  # type: ignore
        mini_result = mini_correct(full_text)
        mini_errors = _normalize_errors(mini_result.get("errors", []))
        all_errors.extend(mini_errors)
        backends_used.append(f"typo_check ({len(mini_errors)})")
    except Exception as e:
        backends_used.append(f"typo_check 失败: {e}")

    # 2. pycorrector (辅, 装了就跑, 没装跳过)
    try:
        from pycorrector import Corrector  # type: ignore
        corrector = Corrector()
        pyc_result = corrector.correct(full_text)
        pyc_errors = _normalize_errors(pyc_result.get("errors", []))
        # 标记来源 (替换 category)
        for e in pyc_errors:
            e["category"] = "pycorrector"
        all_errors.extend(pyc_errors)
        backends_used.append(f"pycorrector ({len(pyc_errors)})")
    except Exception:
        backends_used.append("pycorrector 跳过 (没装 / kenlm 缺)")

    # 去重: 按 (old, pos) 合并, 保留第一次出现 (typo_check 优先)
    seen: set[tuple[str, int]] = set()
    errors: list[dict[str, Any]] = []
    for e in all_errors:
        key = (e.get("old", ""), e.get("pos", -1))
        if key in seen:
            continue
        seen.add(key)
        errors.append(e)

    backend = " + ".join(backends_used)

    audit_path = docx_path.with_suffix(".audit.json")
    audit_path.write_text(
        json.dumps(
            {
                "docx": str(docx_path),
                "backend": backend,
                "found_errors": len(errors),
                "errors": errors,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return audit_path


__all__ = [
    "render_briefing",
    "FONT_TITLE",
    "FONT_BODY",
    "FONT_HEADING",
    "SIZE_TITLE",
    "SIZE_BODY",
    "RED",
]
