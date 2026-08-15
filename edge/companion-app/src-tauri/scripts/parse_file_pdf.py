"""PDF preview + 结构化表格自动识别 (anchor + sub-records)。

8/15 从 parse_file.py 搬出来。

# 为什么 PDF 单独一个文件

`_detect_anchor_table` 一个人就 129 行, 是整个 parse_file 里最复杂的一段
启发式, 而它**只有一个调用者** —— 同文件的 `parse_pdf_preview` (第 314 行)。
两个绑在一起搬, 外面看不出区别。

# 这段启发式在解决什么 (5/6 BL-D17)

大 PDF 转 Excel 老是漏数据, 根因不是解析错, 是 **preview 只给 5 页**:
LLM 看完前 5 页就以为摸清了结构, 直接开写 Excel, 后面 N-5 页整片丢掉,
而且丢得很安静 —— 输出看起来是完整的一张表。

所以这里不只给 preview, 还主动扫全文找"anchor 模式" (身份证号这类强主键,
重复出现 ≥ MIN_ANCHORS 次就认为是一张表的行标识), 把识别出来的结构化记录
单独写一个 CSV 出去, 让 LLM 有全量数据可用, 不必靠前 5 页脑补。

改 MIN_ANCHORS 或 ANCHOR_PATTERNS 之前请先想清楚这条: **宁可不识别, 不可
识别错**。识别错的表比没有表更糟 —— 前者会被当成权威数据用。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from parse_file_common import PREVIEW_PAGES, _truncate

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
