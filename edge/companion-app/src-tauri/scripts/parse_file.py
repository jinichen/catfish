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
    """PDF 前 N 页 preview + 总页数 + (新) 结构化表格自动识别.

    5/6 BL-D17: 大 PDF 转 Excel 失败的主因是 "preview 5 页, LLM 看完就以为
    懂结构, 直接写 Excel 漏掉后 N-5 页". 加 anchor 模式探测:
      - 找重复主键 (18 位身份证 / 长 ID)
      - 如果 ≥ 5 个 → 进 anchor 模式, 全量结构化输出到 /tmp/xxx_structured.json
      - preview 写明 "已提取 N 条 + schema + 完整路径", LLM 直接 pandas.read_json
        转 Excel, 不用啃 raw text 不爆 context.
    """
    try:
        import pypdfium2 as pdfium  # noqa: PLC0415
    except ImportError:
        raise RuntimeError("pypdfium2 未装. 跑: pip install pypdfium2")

    pdf = pdfium.PdfDocument(str(path))
    total_pages = len(pdf)
    parts: list[str] = [f"## PDF · 共 {total_pages} 页"]

    # 拼全文 (跨页) 做表格识别
    full_text_pieces: list[str] = []
    for i in range(total_pages):
        page = pdf[i]
        textpage = page.get_textpage()
        full_text_pieces.append(textpage.get_text_range() or "")
    full_text = "\n".join(full_text_pieces)

    # 尝试结构化表格识别
    structured = _detect_anchor_table(full_text, path)
    meta: dict[str, Any] = {"page_count": total_pages}

    if structured is not None:
        # 命中: preview 输出 schema + 前 5 条 + 路径; meta 加 structured_path
        records, csv_path, columns = structured
        parts.append("")
        parts.append(f"## 自动识别到结构化表格 ({len(records)} 条记录)")
        parts.append(f"列: {', '.join(columns)}")
        parts.append(f"完整数据 (JSON): {csv_path}")
        parts.append("")
        parts.append("⚠️ LLM 注意: 这是大型结构化表格 ({0} 条), preview 只显前 5 条,".format(len(records)))
        parts.append("   要转 Excel/CSV/分析, 直接 execute_code 跑:")
        parts.append("     import pandas as pd")
        parts.append(f"     df = pd.read_json({csv_path!r})  # 拿完整 {len(records)} 条")
        parts.append("     df.to_excel('output.xlsx', index=False)")
        parts.append("   不要试着读全 PDF 文字 (5万+ 字会爆 context).")
        parts.append("")
        parts.append("### 前 5 条样本")
        for rec in records[:5]:
            line = " | ".join(f"{k}={v}" for k, v in rec.items() if k != "_sub")
            parts.append(line)
            sub = rec.get("_sub") or []
            for s in sub[:3]:
                parts.append("    └─ " + " | ".join(f"{k}={v}" for k, v in s.items()))
            if len(sub) > 3:
                parts.append(f"    └─ (... 还有 {len(sub) - 3} 子项)")

        meta["structured_path"] = csv_path
        meta["structured_count"] = len(records)
        meta["structured_columns"] = columns
    else:
        # 没命中: 走原 preview 5 页流程
        pages_to_show = min(total_pages, PREVIEW_PAGES)
        for i in range(pages_to_show):
            chunk = full_text_pieces[i]
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
    return text, meta


# ============================================================
# PDF 结构化表格识别 (anchor + sub-records 模式)
# ============================================================

# 强主键候选 — 重复 ≥ MIN_ANCHORS 次就当 anchor
# (顺序: 优先级高 → 低)
ANCHOR_PATTERNS: list[tuple[str, "re.Pattern[str]"]] = [
    ("身份证号", __import__("re").compile(r"\b(\d{17}[\dXx])\b")),
    ("长数字ID", __import__("re").compile(r"\b(\d{15,20})\b")),  # 社保号/学号 等
]

# 子记录里常见的"小模式" (险种/科目/月份 等). 命中就当结构化子项
SUB_PATTERNS: list[tuple[str, "re.Pattern[str]"]] = [
    # 社保 5 险种
    ("社保险种", __import__("re").compile(
        r"(养老保险|失业保险|工伤保险|医疗保险|生育保险)[ \t]+"
        r"(\d{4}年\d{1,2}月)[ \t]+(\d{4}年\d{1,2}月)"
        r"(?:[ \t]+(\d{1,2}))?"
    )),
    # 工资条目: 项目名 + 金额
    ("金额条目", __import__("re").compile(
        r"^([一-鿿]{2,8})[ \t]+(-?\d+\.\d{2})\s*$"
    )),
]

MIN_ANCHORS = 5  # 至少 5 条才算"表格"


def _detect_anchor_table(
    full_text: str, source_path: Path
) -> tuple[list[dict[str, Any]], str, list[str]] | None:
    """探测 anchor + sub-records 模式. 命中返回 (records, json_path, columns).

    策略:
      1. 找最有效的 anchor 模式 (重复次数最多)
      2. 用 anchor 切片, 每片提取 anchor 前的"姓名/序号", anchor 后的"子记录"
      3. 子记录用 SUB_PATTERNS 启发式抽
      4. 同 anchor 跨页重复 (PDF 翻页) → 合并 sub records
    """
    import hashlib  # noqa: PLC0415
    import json  # noqa: PLC0415
    import re as _re  # noqa: PLC0415

    # 先把头尾常见噪音去掉, 防干扰 anchor 检测
    cleaned = full_text
    for pat in [
        r"第\s*\d+\s*页\s*\(\s*共\s*\d+\s*页\s*\)",
        r"统一社会信用代码\(组织机构代码\):",
        r"社会保险登记号:",
        r"单位名称:", r"校验码:", r"查询流水号:", r"查询日期:",
        r"仅限申请高新资质使用",
        # 干掉典型"长 ID 噪音": 单位社会信用代码 (91 开头 18 位) /
        # 查询流水号 (20 位以上). 它们会跟身份证号正则混淆.
        r"\b91\d{16}\b",          # 信用代码
        r"\b\d{20,}\b",           # 查询流水号 (≥20 位)
    ]:
        cleaned = _re.sub(pat, " ", cleaned)

    # 选 anchor: 第一个 ≥ MIN_ANCHORS 命中的
    anchor_name = None
    anchor_re = None
    matches: list[Any] = []
    for name, pat in ANCHOR_PATTERNS:
        ms = list(pat.finditer(cleaned))
        # 去重 (同一个 ID 出现多次 = 跨页, 仍然算 1 次)
        unique = len({m.group(1) for m in ms})
        if unique >= MIN_ANCHORS:
            anchor_name = name
            anchor_re = pat
            matches = ms
            break

    if anchor_re is None:
        return None

    # 按 anchor 切片
    raw: list[dict[str, Any]] = []
    for i, m in enumerate(matches):
        anchor_val = m.group(1)
        before = cleaned[max(0, m.start() - 150):m.start()]
        # 锚点前找"[序号 ]中文名"
        # 支持中文 (2-8 字) / 英文 (1-4 个 Cap word) 两种姓名
        name_m = _re.search(
            r"(?:(\d+)[ \t]+)?"
            r"((?:[一-鿿·・]{2,8})|(?:[A-Z][a-z]+(?:[ \t]+[A-Z][a-z]+){0,3}))"
            r"\s*$",
            before,
        )
        seq = int(name_m.group(1)) if name_m and name_m.group(1) else None
        name = name_m.group(2) if name_m else ""

        nxt = matches[i + 1].start() if i + 1 < len(matches) else len(cleaned)
        chunk = cleaned[m.end():nxt]

        # 提取子记录 (尝试每个 SUB_PATTERN)
        sub_items: list[dict[str, Any]] = []
        for sub_name, sub_re in SUB_PATTERNS:
            for sm in sub_re.finditer(chunk):
                groups = sm.groups()
                if sub_name == "社保险种":
                    ins, s_ym, e_ym, mn = groups
                    months = int(mn) if mn and 1 <= int(mn) <= 12 else None
                    sub_items.append({
                        "类型": ins, "起始": s_ym, "截止": e_ym, "月数": months,
                    })
                elif sub_name == "金额条目":
                    item, amount = groups
                    sub_items.append({"项目": item, "金额": float(amount)})
            if sub_items:
                break  # 命中一个 sub_pattern 就停

        raw.append({
            "序号": seq,
            "姓名": name,
            anchor_name: anchor_val,
            "_sub": sub_items,
        })

    # 同 anchor 跨页合并 + 滤掉姓名为空 (噪音 ID 没有人名锚) 的条目
    by_anchor: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for r in raw:
        a = r[anchor_name]
        if not r.get("姓名"):
            continue  # 没姓名 → 大概率是噪音 ID (信用代码/流水号), 跳过
        if a not in by_anchor:
            by_anchor[a] = {**r, "_sub": []}
            order.append(a)
        # 子记录去重合并
        seen = {tuple(s.items()) for s in by_anchor[a]["_sub"]}
        for s in r["_sub"]:
            t = tuple(s.items())
            if t not in seen:
                by_anchor[a]["_sub"].append(s)
                seen.add(t)
    records = [by_anchor[a] for a in order]

    # 重新顺序编号
    for i, r in enumerate(records, 1):
        r["序号"] = i

    # 列名
    columns = ["序号", "姓名", anchor_name]
    if records and records[0].get("_sub"):
        sub_keys = list(records[0]["_sub"][0].keys())
        columns += [f"子记录({k})" for k in sub_keys]

    # dump JSON 到 /tmp (LLM 用 pandas.read_json 读)
    sha = hashlib.sha1(str(source_path).encode()).hexdigest()[:8]
    out_path = f"/tmp/catfish_pdf_{sha}_structured.json"
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=None)
    except Exception:
        return None

    return records, out_path, columns


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
