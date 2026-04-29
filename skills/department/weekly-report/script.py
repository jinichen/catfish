"""weekly-report skill — 用 openpyxl 渲染员工周报 .xlsx.

严格按鸿波 4-29 提供的 .xlsx 样板:
  - 单 Sheet, 6 列: 序号 / 项目-事项名称 / 本周进度 / 下周计划 / 计划完成时间 / 备注
  - 表头加粗 + 浅蓝底纹 (D9E7F5)
  - 边框全实线 0.5pt
  - 多行单元格自动换行 + 自动行高
  - 列宽: 5 / 15 / 40 / 40 / 15 / 20
  - 字体: 微软雅黑 (周报跟公文不同, 不强制方正小标宋)
  - 文件名: 周报-<员工>-<YYYYMMDD>.xlsx

调用入口: render_weekly_report(...) — 见 SKILL.md
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

# ── 样式常量 ────────────────────────────────────────────────────
FONT_NAME = "微软雅黑"
FONT_SIZE = 11
HEADER_FILL_COLOR = "D9E7F5"  # 浅蓝
BORDER_STYLE = "thin"
BORDER_COLOR = "000000"

# 6 列定义 (顺序固定, 跟你公司模板对齐)
COLUMNS = [
    ("序号", 5),
    ("项目/事项名称", 15),
    ("本周进度", 40),
    ("下周计划", 40),
    ("计划完成时间", 15),
    ("备注", 20),
]


@dataclass
class WeeklyItem:
    category: str
    this_week: str
    next_week: str = "持续中"
    deadline: str = ""
    note: str = ""


# ── 输出路径解析 ────────────────────────────────────────────────


_NAME_SLUG_RE = re.compile(r"[^\w一-龥\-]+", re.UNICODE)


def _slugify(s: str) -> str:
    return _NAME_SLUG_RE.sub("-", s).strip("-") or "员工"


def _resolve_output_path(
    output_path: str | None, employee_name: str, week_label: str
) -> Path:
    """决定输出路径.

    - 有 output_path → 用它
    - 无 → ~/Desktop/周报-<员工>-<YYYYMMDD>.xlsx
      日期从 week_label 抽取 (匹配 YYYYMMDD / YYYY-MM-DD / YYYY年MM月DD日 等)
    """
    if output_path:
        p = Path(os.path.expanduser(output_path))
        if not p.suffix.lower() in (".xlsx", ".xls"):
            p = p / f"周报-{_slugify(employee_name)}.xlsx"
        return p.resolve()

    # 抽日期 - 优先匹配末尾的 YYYYMMDD
    date_str = _extract_date_yyyymmdd(week_label) or datetime.now().strftime("%Y%m%d")
    fname = f"周报-{_slugify(employee_name)}-{date_str}.xlsx"
    return (Path.home() / "Desktop" / fname).resolve()


_DATE_RES = [
    re.compile(r"(\d{4})[年\-/](\d{1,2})[月\-/](\d{1,2})"),  # 2026年4月24日 / 2026-4-24
    re.compile(r"(\d{4})(\d{2})(\d{2})"),                    # 20260424
]


def _extract_date_yyyymmdd(text: str) -> str | None:
    """从文本里抽日期, 返回 YYYYMMDD. 只取最后一个匹配 (周报通常用周末日期)."""
    for re_ in _DATE_RES:
        matches = list(re_.finditer(text))
        if matches:
            m = matches[-1]
            y, mo, d = m.group(1), m.group(2), m.group(3)
            return f"{int(y):04d}{int(mo):02d}{int(d):02d}"
    return None


# ── 渲染 ────────────────────────────────────────────────────────


def _make_border() -> Border:
    side = Side(style=BORDER_STYLE, color=BORDER_COLOR)
    return Border(left=side, right=side, top=side, bottom=side)


def _make_font(bold: bool = False) -> Font:
    return Font(name=FONT_NAME, size=FONT_SIZE, bold=bold)


def _make_header_fill() -> PatternFill:
    return PatternFill(
        start_color=HEADER_FILL_COLOR,
        end_color=HEADER_FILL_COLOR,
        fill_type="solid",
    )


def _make_align(*, wrap: bool = True) -> Alignment:
    return Alignment(
        horizontal="center", vertical="center", wrap_text=wrap
    )


def render_weekly_report(
    *,
    employee_name: str,
    week_label: str,
    items: list[dict[str, str] | WeeklyItem],
    output_path: str | None = None,
) -> dict[str, Any]:
    """渲染周报 → .xlsx.

    返回:
        {
          "xlsx": "/Users/.../Desktop/周报-X-YYYYMMDD.xlsx",
          "files": ["...xlsx"],   # Companion 用这个生成 FilePill
        }
    """
    if not items:
        raise ValueError("items 不能为空 — 至少要有 1 条本周事项")

    # 归一化 items
    norm_items = [
        WeeklyItem(**item) if isinstance(item, dict) else item for item in items
    ]

    out_path = _resolve_output_path(output_path, employee_name, week_label)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    wb = Workbook()
    ws = wb.active
    ws.title = "周报"

    border = _make_border()
    header_font = _make_font(bold=True)
    body_font = _make_font(bold=False)
    header_fill = _make_header_fill()
    align = _make_align()

    # ── 第 1 行留空, 视觉留白 (跟公司模板一致, B 列开始) ──
    # ── 第 2 行: 表头 (B-G) ──
    for col_idx, (header_text, _width) in enumerate(COLUMNS, start=2):
        cell = ws.cell(row=2, column=col_idx, value=header_text)
        cell.font = header_font
        cell.fill = header_fill
        cell.border = border
        cell.alignment = align

    # ── 第 3 行起: 数据 ──
    for row_idx, item in enumerate(norm_items, start=3):
        # 序号 (B 列)
        ws.cell(row=row_idx, column=2, value=row_idx - 2)
        # 项目/事项名称 (C 列)
        ws.cell(row=row_idx, column=3, value=item.category)
        # 本周进度 (D 列)
        ws.cell(row=row_idx, column=4, value=item.this_week)
        # 下周计划 (E 列)
        ws.cell(row=row_idx, column=5, value=item.next_week)
        # 计划完成时间 (F 列)
        ws.cell(row=row_idx, column=6, value=item.deadline)
        # 备注 (G 列)
        ws.cell(row=row_idx, column=7, value=item.note)

        # 全行样式
        for col_idx in range(2, 8):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.font = body_font
            cell.border = border
            # 序号居中, 其他左对齐 + wrap
            if col_idx == 2:
                cell.alignment = Alignment(
                    horizontal="center", vertical="center", wrap_text=True
                )
            else:
                cell.alignment = Alignment(
                    horizontal="left", vertical="center", wrap_text=True
                )

    # ── 列宽 (A 留 2 字符空白, B-G 按 COLUMNS 配置) ──
    ws.column_dimensions[get_column_letter(1)].width = 2
    for col_idx, (_header, width) in enumerate(COLUMNS, start=2):
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    # ── 行高自适应 (每行根据本周进度文本估算) ──
    ws.row_dimensions[2].height = 24
    for row_idx, item in enumerate(norm_items, start=3):
        # 行高估算: 取所有文本 max 行数 (按 \n + 换行宽度), 每行 18pt
        max_lines = max(
            _estimate_lines(item.this_week, 40),
            _estimate_lines(item.next_week, 40),
            _estimate_lines(item.category, 15),
            1,
        )
        ws.row_dimensions[row_idx].height = max(20, max_lines * 18)

    wb.save(out_path)

    return {
        "xlsx": str(out_path),
        "files": [str(out_path)],
    }


def _estimate_lines(text: str, col_width_chars: int) -> int:
    """估算文本占多少行 (按显式 \n + 自动换行)."""
    if not text:
        return 1
    explicit = text.count("\n") + 1
    # 自动换行: 中文 1 字符 ~= 1 width unit, 但中文每行能塞 col_width / 2 个汉字
    chars_per_line = max(8, col_width_chars * 2)  # 粗估
    auto = sum(
        max(1, len(line) // chars_per_line + (1 if len(line) % chars_per_line else 0))
        for line in text.split("\n")
    )
    return max(explicit, auto)


__all__ = [
    "render_weekly_report",
    "WeeklyItem",
    "COLUMNS",
    "FONT_NAME",
    "HEADER_FILL_COLOR",
]
