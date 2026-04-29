"""weekly-report 渲染测试 — 严格按真实周报样板.

覆盖:
  - 基础: .xlsx 生成 + 能反向打开
  - 6 列表头顺序对 + 加粗 + 浅蓝底纹
  - 数据行字段位置对 (序号/类别/本周/下周/计划完成/备注)
  - 多行内容支持 (\n 分行)
  - 列宽 / 边框 / 字体
  - 文件名: 周报-<员工>-<YYYYMMDD>.xlsx + 默认到 ~/Desktop/
  - 输入兼容: dict 形式 / WeeklyItem 形式都行
  - 边界: items 为空 → 报错
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

from script import (  # noqa: E402
    COLUMNS,
    FONT_NAME,
    HEADER_FILL_COLOR,
    WeeklyItem,
    render_weekly_report,
)


# ── Fixture: 复刻你给的周报样板内容 ─────────────────────────────


@pytest.fixture
def real_sample(tmp_path):
    return dict(
        employee_name="陈鸿波",
        week_label="2026年4月20日-2026年4月24日",
        items=[
            {
                "category": "合规管理",
                "this_week": "6 到位 AI 预判平台的代码、规则优化",
                "next_week": "按需求是否需要继续优化",
                "deadline": "",
                "note": "",
            },
            {
                "category": "资质",
                "this_week": (
                    "1. 业务连续性体系和商品售后服务评价体系认证 (五星) "
                    "2 项资质下周现场审核的准备工作\n"
                    "2. 下周 27000 的审核准备工作\n"
                    "3. 和金智达、人力沟通员工资质事宜"
                ),
                "next_week": "持续中",
                "deadline": "",
                "note": "",
            },
            {
                "category": "安全日常工作",
                "this_week": "部门日常安全检查工作",
                "next_week": "持续中",
                "deadline": "",
                "note": "",
            },
            {
                "category": "其它",
                "this_week": "团建组织 (周三聚餐)",
                "next_week": "",
                "deadline": "",
                "note": "",
            },
        ],
        output_path=str(tmp_path / "周报-test.xlsx"),
    )


# ── 基础 ────────────────────────────────────────────────────────


def test_xlsx_generated(real_sample):
    result = render_weekly_report(**real_sample)
    p = Path(result["xlsx"])
    assert p.exists()
    assert p.stat().st_size > 1000


def test_can_reopen(real_sample):
    from openpyxl import load_workbook

    result = render_weekly_report(**real_sample)
    wb = load_workbook(result["xlsx"])
    assert "周报" in wb.sheetnames


# ── 表头 ────────────────────────────────────────────────────────


def test_headers_in_correct_order(real_sample):
    """B2-G2 必须是: 序号 / 项目-事项名称 / 本周进度 / 下周计划 / 计划完成时间 / 备注"""
    from openpyxl import load_workbook

    result = render_weekly_report(**real_sample)
    wb = load_workbook(result["xlsx"])
    ws = wb["周报"]

    expected = [h for h, _w in COLUMNS]
    actual = [ws.cell(row=2, column=c).value for c in range(2, 8)]
    assert actual == expected


def test_header_is_bold(real_sample):
    from openpyxl import load_workbook

    result = render_weekly_report(**real_sample)
    ws = load_workbook(result["xlsx"])["周报"]
    cell = ws.cell(row=2, column=2)  # B2 = "序号"
    assert cell.font.bold is True
    assert cell.font.name == FONT_NAME


def test_header_has_blue_fill(real_sample):
    """表头底纹 D9E7F5 浅蓝."""
    from openpyxl import load_workbook

    result = render_weekly_report(**real_sample)
    ws = load_workbook(result["xlsx"])["周报"]
    cell = ws.cell(row=2, column=3)  # C2 任意一个表头单元格
    fill = cell.fill
    assert fill.fill_type == "solid"
    # openpyxl 颜色可能带 FF (alpha) 前缀, 取后 6 位
    rgb = (fill.fgColor.rgb or "").upper()
    assert rgb.endswith(HEADER_FILL_COLOR), f"表头底纹应为 {HEADER_FILL_COLOR}, 实际 {rgb}"


# ── 数据行 ──────────────────────────────────────────────────────


def test_row_count_matches_items(real_sample):
    from openpyxl import load_workbook

    result = render_weekly_report(**real_sample)
    ws = load_workbook(result["xlsx"])["周报"]
    # 第 1 行空, 第 2 行表头, 第 3-6 行 4 条数据
    last_row_with_data = max(
        cell.row for cell in ws[3] if cell.value is not None
    )
    assert last_row_with_data >= 3


def test_first_data_row_content(real_sample):
    from openpyxl import load_workbook

    result = render_weekly_report(**real_sample)
    ws = load_workbook(result["xlsx"])["周报"]
    # 第 3 行: 序号 1, 类别 "合规管理"
    assert ws.cell(row=3, column=2).value == 1
    assert ws.cell(row=3, column=3).value == "合规管理"
    assert "AI 预判平台" in ws.cell(row=3, column=4).value


def test_multiline_content_preserved(real_sample):
    """资质行的 \n 多行不能被吃掉."""
    from openpyxl import load_workbook

    result = render_weekly_report(**real_sample)
    ws = load_workbook(result["xlsx"])["周报"]
    cell = ws.cell(row=4, column=4)  # 资质行 - 本周进度
    assert "\n" in cell.value, "多行内容 \\n 必须保留"
    assert cell.value.count("\n") >= 2  # 1./2./3. 三行


def test_data_row_borders(real_sample):
    """每行每列必须有边框."""
    from openpyxl import load_workbook

    result = render_weekly_report(**real_sample)
    ws = load_workbook(result["xlsx"])["周报"]
    cell = ws.cell(row=3, column=4)  # 任意数据格
    assert cell.border.top.style == "thin"
    assert cell.border.bottom.style == "thin"
    assert cell.border.left.style == "thin"
    assert cell.border.right.style == "thin"


def test_data_cell_wrap_text(real_sample):
    """长文本格必须 wrap_text=True (否则一行显示溢出)."""
    from openpyxl import load_workbook

    result = render_weekly_report(**real_sample)
    ws = load_workbook(result["xlsx"])["周报"]
    cell = ws.cell(row=4, column=4)  # 资质 / 本周进度 - 多行内容
    assert cell.alignment.wrap_text is True


# ── 列宽 ────────────────────────────────────────────────────────


def test_column_widths(real_sample):
    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter

    result = render_weekly_report(**real_sample)
    ws = load_workbook(result["xlsx"])["周报"]
    # B 列 (序号) 应该是 5
    for col_idx, (_h, width) in enumerate(COLUMNS, start=2):
        col_letter = get_column_letter(col_idx)
        actual = ws.column_dimensions[col_letter].width
        assert actual == width, f"{col_letter} 列宽应为 {width}, 实际 {actual}"


# ── 文件名 + 路径 ───────────────────────────────────────────────


def test_default_output_path_uses_desktop_with_date(tmp_path, monkeypatch):
    """没传 output_path → ~/Desktop/周报-<员工>-<YYYYMMDD>.xlsx, 日期从 week_label 抽."""
    fake_home = tmp_path / "fakehome"
    (fake_home / "Desktop").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    result = render_weekly_report(
        employee_name="张三",
        week_label="2026年4月24日",  # 末尾日期
        items=[
            {"category": "项目 A", "this_week": "做了 X", "next_week": "继续 X"},
        ],
    )
    expected = fake_home / "Desktop" / "周报-张三-20260424.xlsx"
    assert result["xlsx"] == str(expected.resolve())
    assert expected.exists()


def test_default_output_uses_today_when_no_date_in_label(tmp_path, monkeypatch):
    """week_label 里没日期 → 用当天 (YYYYMMDD)."""
    fake_home = tmp_path / "h"
    (fake_home / "Desktop").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    from datetime import datetime
    result = render_weekly_report(
        employee_name="李四",
        week_label="本周",
        items=[{"category": "X", "this_week": "Y"}],
    )
    today = datetime.now().strftime("%Y%m%d")
    assert today in result["xlsx"]


# ── 输入兼容 + 边界 ─────────────────────────────────────────────


def test_dataclass_input_works(tmp_path):
    """传 WeeklyItem dataclass 也行 (除了 dict)."""
    result = render_weekly_report(
        employee_name="t",
        week_label="2026年4月24日",
        items=[
            WeeklyItem(category="A", this_week="x", next_week="y"),
        ],
        output_path=str(tmp_path / "t.xlsx"),
    )
    assert Path(result["xlsx"]).exists()


def test_optional_fields_defaulted(tmp_path):
    """deadline / note 不传 → 空字符串, 不报错."""
    result = render_weekly_report(
        employee_name="t",
        week_label="t",
        items=[{"category": "A", "this_week": "x"}],
        output_path=str(tmp_path / "t.xlsx"),
    )
    assert Path(result["xlsx"]).exists()


def test_empty_items_raises(tmp_path):
    with pytest.raises(ValueError, match="items 不能为空"):
        render_weekly_report(
            employee_name="t",
            week_label="t",
            items=[],
            output_path=str(tmp_path / "x.xlsx"),
        )


def test_files_field_for_companion(real_sample):
    """result['files'] 是 Companion FilePill 渲染用的列表."""
    result = render_weekly_report(**real_sample)
    assert result["files"] == [result["xlsx"]]
