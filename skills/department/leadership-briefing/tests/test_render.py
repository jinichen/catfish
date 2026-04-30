"""leadership-briefing 渲染测试 — blocks 模式 + CSV 附件 + 输出目录.

覆盖:
  - 5 段顺序齐全
  - 4 种 block 类型分别能渲染 (paragraph / kv_table / table / ordered_list)
  - 同一段可混排多个 block
  - CSV 附件生成 + UTF-8 BOM 编码 + 跟主 docx 同目录
  - output_path 指定 vs 默认 (~/.catfish/output/...) 路径解析
  - 红字高亮在正文 / 表格 / 列表全部生效
  - 字体名硬编码进 XML
  - 页码 PAGE / NUMPAGES 域字段
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

from script import (  # noqa: E402
    FONT_BODY,
    FONT_HEADING,
    FONT_TITLE,
    render_briefing,
)


# ── Fixture: 复刻 PDF 样板 + 2 个附件 ────────────────────────────


@pytest.fixture
def pdf_sample(tmp_path):
    """4 段固定逻辑顺序 (概况/分项/问题/下一步), heading 文本 LLM 自由发挥."""
    return dict(
        title_lines=[
            "关于电子与智能化工程专业承包一级资质所需",
            "建造师持证人员补位的汇报",
        ],
        sections=[
            # § 一 概况 (heading 自由命名 — 这里用"总体情况")
            {"heading": "一、电子与智能化资质总体情况", "blocks": [
                {"type": "paragraph", "text": (
                    "公司持证人员 5 人, 尚缺 1 人. 5 名持证人员明细详见附件 1."
                )},
            ]},
            # § 二 分项情况说明 (heading 自由命名 — 这里用"业务影响明细")
            {"heading": "二、业务影响及缺口明细", "blocks": [
                {"type": "paragraph", "text": "该缺口已对业务产生影响:"},
                {"type": "paragraph", "text": "泉州公司及省政企均反馈, ..."},
                {"type": "kv_table", "rows": [
                    ["用人部门", "已与人力部沟通, 挂靠在集成能力中心"],
                    ["公司归属", "先入职北福, 后转中电"],
                    ["待遇标准", [
                        "1. 月工资: 税后 2000/月",
                        "2. 五险一金: 公司缴纳. 不享受企业年金.",
                        "3. 年度人工成本: 43785 元 (详见附件 2).",
                    ]],
                    ["社医保", "由公司统一缴纳支付"],
                ]},
            ]},
            # § 三 存在问题 (heading 自由命名 — 这里用"主要问题")
            {"heading": "三、当前面临的主要问题", "blocks": [
                {"type": "paragraph", "text": (
                    "若直接变更法人, 该资质证书会被暂停. 必须先完成补位, "
                    "才能推进法人变更."
                )},
            ]},
            # § 四 下一步计划 (heading 自由命名 — 这里用"工作安排")
            {"heading": "四、下一步工作安排", "blocks": [
                {"type": "ordered_list", "items": [
                    {"text": "启动一级建造师招聘工作:", "subs": [
                        "(1) 学历本科及以上",
                        "(2) 重点关注一级建造师执业资格",
                    ]},
                    {"text": "完成补位后, 推进法人变更.", "subs": []},
                    {"text": "后续管理:", "subs": [
                        "集成能力中心负责考勤与工时管理.",
                    ]},
                ]},
            ]},
        ],
        attachments=[
            {
                "filename": "附件1-资质人员清单",
                "kind": "csv",
                "headers": ["序号", "姓名", "证书编号", "专业", "状态"],
                "rows": [
                    [1, "张三", "闽01-123456", "机电工程", "在岗"],
                    [2, "李四", "闽01-234567", "建筑工程", "在岗"],
                ],
            },
            {
                "filename": "附件2-补位方案预算明细",
                "kind": "csv",
                "headers": ["费用类别", "金额 (元/年)", "说明"],
                "rows": [
                    ["员工工资", 32400, "税后 2000/月 × 12"],
                    ["五险一金", 11385, "公司缴纳"],
                    ["合计", 43785, ""],
                ],
            },
        ],
        important_phrases=[
            "尚缺 1 人", "公司归属", "先入职北福, 后转中电", "不享受企业年金",
        ],
        output_path=str(tmp_path / "汇报.docx"),
    )


# ── 基础: 文件生成 ──────────────────────────────────────────────


def test_main_docx_generated(pdf_sample):
    result = render_briefing(**pdf_sample)
    assert Path(result["docx"]).exists()
    assert Path(result["docx"]).stat().st_size > 1000


def test_can_reopen(pdf_sample):
    from docx import Document

    result = render_briefing(**pdf_sample)
    doc = Document(result["docx"])
    assert len(doc.paragraphs) >= 6


# ── 4 段固定逻辑顺序, heading 文本 LLM 自由 ───────────────────


def test_four_sections_with_flexible_headings(pdf_sample):
    """4 段顺序固定, 但 heading 文本由 LLM 自由命名 (鸿波 4-30 改).

    我们检查段编号 一/二/三/四 顺序, 不检查具体字眼.
    """
    from docx import Document
    import re

    result = render_briefing(**pdf_sample)
    doc = Document(result["docx"])
    # 找所有 一、 / 二、 / 三、 / 四、 开头的段落
    section_pattern = re.compile(r"^([一二三四五六七八九])、")
    found_numbers = []
    for p in doc.paragraphs:
        m = section_pattern.match(p.text.strip())
        if m:
            found_numbers.append(m.group(1))

    # 必须正好 4 段, 顺序 一/二/三/四
    assert found_numbers == ["一", "二", "三", "四"], (
        f"应有 4 段 (一/二/三/四), 实际 {found_numbers}"
    )


# ── 标题 + 字体 ────────────────────────────────────────────────


def test_title_two_lines_centered(pdf_sample):
    from docx import Document
    from docx.enum.text import WD_PARAGRAPH_ALIGNMENT

    result = render_briefing(**pdf_sample)
    doc = Document(result["docx"])
    first_two = doc.paragraphs[:2]
    assert "建造师" in first_two[1].text
    for p in first_two:
        assert p.alignment == WD_PARAGRAPH_ALIGNMENT.CENTER
        assert p.runs[0].bold is True


def test_title_font_is_fangzheng(pdf_sample):
    from docx import Document
    from docx.oxml.ns import qn

    result = render_briefing(**pdf_sample)
    doc = Document(result["docx"])
    rFonts = doc.paragraphs[0].runs[0]._element.find(qn("w:rPr")).find(qn("w:rFonts"))
    assert rFonts.get(qn("w:eastAsia")) == FONT_TITLE


def test_section_heading_font_is_heihei(pdf_sample):
    """段标题黑体加粗 — 不再检查具体字眼 (heading 自由), 只检查 一、二、三、四 开头的段."""
    import re
    from docx import Document
    from docx.oxml.ns import qn

    result = render_briefing(**pdf_sample)
    doc = Document(result["docx"])
    section_re = re.compile(r"^[一二三四]、")
    found = 0
    for para in doc.paragraphs:
        if section_re.match(para.text.strip()) and para.runs:
            run = para.runs[0]
            rFonts = run._element.find(qn("w:rPr")).find(qn("w:rFonts"))
            assert rFonts.get(qn("w:eastAsia")) == FONT_HEADING, (
                f"段标题 {para.text!r} 字体应是黑体"
            )
            assert run.bold is True, f"段标题 {para.text!r} 应加粗"
            found += 1
    assert found == 4, f"应找到 4 个段标题, 实际 {found}"


# ── block 类型 ────────────────────────────────────────────────


def test_kv_table_in_section(pdf_sample):
    """fixture 里 § 二有一个 2 列 kv_table (4 行: 用人部门/公司归属/待遇标准/社医保)."""
    from docx import Document

    result = render_briefing(**pdf_sample)
    doc = Document(result["docx"])

    assert len(doc.tables) == 1, f"应该 1 个 kv_table, 实际 {len(doc.tables)}"
    table = doc.tables[0]
    assert len(table.rows[0].cells) == 2
    assert len(table.rows) == 4
    labels = [r.cells[0].text.strip() for r in table.rows]
    assert labels == ["用人部门", "公司归属", "待遇标准", "社医保"]


def test_paragraph_blocks_present(pdf_sample):
    """段内 paragraph block 渲染成功 — § 二有 2 段段落."""
    from docx import Document

    result = render_briefing(**pdf_sample)
    doc = Document(result["docx"])
    all_text = "\n".join(p.text for p in doc.paragraphs)
    assert "该缺口已对业务产生影响" in all_text
    assert "泉州公司" in all_text


def test_ordered_list_in_section(pdf_sample):
    """fixture 里 § 四 (下一步) 是 1./2./3. + (1)(2) 数字层级, 不是表格."""
    import re
    from docx import Document

    result = render_briefing(**pdf_sample)
    doc = Document(result["docx"])

    # 找 § 四 段标题 (任意"四、..."开头)
    section_idx = next(
        i for i, p in enumerate(doc.paragraphs)
        if re.match(r"^四、", p.text.strip())
    )
    after = [p.text for p in doc.paragraphs[section_idx + 1 :]]
    assert any(p.startswith("1. ") for p in after)
    assert any(p.startswith("2. ") for p in after)
    assert any(p.startswith("3. ") for p in after)
    assert any("(1)" in p for p in after)
    assert any("(2)" in p for p in after)


def test_section_three_has_problem_paragraph(pdf_sample):
    """fixture § 三 (存在问题) 段是段落, 含"必须先完成补位" 字样."""
    import re
    from docx import Document

    result = render_briefing(**pdf_sample)
    doc = Document(result["docx"])
    section_idx = next(
        i for i, p in enumerate(doc.paragraphs)
        if re.match(r"^三、", p.text.strip())
    )
    body = doc.paragraphs[section_idx + 1].text
    assert "必须" in body or "推进" in body


def test_mixed_blocks_in_one_section(tmp_path):
    """同一段混排 paragraph + table + paragraph + ordered_list 都渲染."""
    from docx import Document

    result = render_briefing(
        title_lines=["test"],
        sections=[
            {"heading": "二、问题与影响", "blocks": [
                {"type": "paragraph", "text": "前言段落."},
                {"type": "table",
                 "headers": ["问题", "影响"],
                 "rows": [["A", "B"], ["C", "D"]]},
                {"type": "paragraph", "text": "尾声段落."},
                {"type": "ordered_list", "items": [
                    {"text": "动作 1", "subs": ["(1) 子项"]},
                ]},
            ]},
        ],
        output_path=str(tmp_path / "mixed.docx"),
    )
    doc = Document(result["docx"])
    assert len(doc.tables) == 1
    all_text = "\n".join(p.text for p in doc.paragraphs)
    assert "前言段落" in all_text
    assert "尾声段落" in all_text
    assert "1. 动作 1" in all_text


# ── 红字高亮 ────────────────────────────────────────────────────


def test_important_phrase_is_red(pdf_sample):
    from docx import Document
    from docx.shared import RGBColor

    result = render_briefing(**pdf_sample)
    doc = Document(result["docx"])

    targets = ["尚缺 1 人", "先入职北福", "公司归属", "不享受企业年金"]

    def scan_runs(runs):
        for run in runs:
            for t in targets:
                if t in run.text:
                    color = run.font.color.rgb
                    if color is not None and color == RGBColor(0xFF, 0x00, 0x00):
                        return True
        return False

    found = False
    for para in doc.paragraphs:
        if scan_runs(para.runs):
            found = True
            break
    if not found:
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    for para in cell.paragraphs:
                        if scan_runs(para.runs):
                            found = True
                            break
    assert found


# ── 页码 ────────────────────────────────────────────────────────


def test_page_number_field(pdf_sample):
    from docx import Document
    from docx.oxml.ns import qn

    result = render_briefing(**pdf_sample)
    doc = Document(result["docx"])
    footer = doc.sections[0].footer
    instr_texts = []
    for para in footer.paragraphs:
        for run in para.runs:
            for instr in run._element.iter(qn("w:instrText")):
                if instr.text:
                    instr_texts.append(instr.text.strip())
    joined = " ".join(instr_texts)
    assert "PAGE" in joined and "NUMPAGES" in joined


# ── CSV 附件 ────────────────────────────────────────────────────


def test_attachments_generated(pdf_sample):
    """两个 CSV 附件都生成 + result.attachments / files 含路径."""
    result = render_briefing(**pdf_sample)
    assert len(result["attachments"]) == 2
    for ap in result["attachments"]:
        assert Path(ap).exists()
        assert ap.endswith(".csv")
    # files 字段应是 docx + attachments 合并
    assert len(result["files"]) == 3
    assert result["files"][0] == result["docx"]


def test_attachments_same_dir_as_docx(pdf_sample):
    """附件跟主 .docx 必须**同目录**."""
    result = render_briefing(**pdf_sample)
    docx_dir = Path(result["docx"]).parent
    for ap in result["attachments"]:
        assert Path(ap).parent == docx_dir


def test_attachment_filename_pattern(pdf_sample):
    """附件文件名格式: 附件N-XXX.csv"""
    result = render_briefing(**pdf_sample)
    names = [Path(p).name for p in result["attachments"]]
    assert "附件1-资质人员清单.csv" in names
    assert "附件2-补位方案预算明细.csv" in names


def test_attachment_csv_is_utf8_bom(pdf_sample):
    """CSV 必须 UTF-8 BOM 编码 (Excel 打开中文不乱码)."""
    result = render_briefing(**pdf_sample)
    first = result["attachments"][0]
    raw = Path(first).read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "CSV 缺 UTF-8 BOM"


def test_attachment_csv_content_correct(pdf_sample):
    """CSV 内容: 表头 + 数据行齐全."""
    result = render_briefing(**pdf_sample)
    first = result["attachments"][0]
    with Path(first).open("r", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    assert rows[0] == ["序号", "姓名", "证书编号", "专业", "状态"]
    assert len(rows) == 3  # 1 表头 + 2 数据行
    assert rows[1][1] == "张三"


def test_csv_filename_extension_auto_added(tmp_path):
    """attachment.filename 没带 .csv 时自动加."""
    result = render_briefing(
        title_lines=["test"],
        sections=[{"heading": "一、背景与现状", "blocks": [
            {"type": "paragraph", "text": "x"},
        ]}],
        attachments=[
            {"filename": "无扩展名附件",  # 不带 .csv
             "kind": "csv",
             "headers": ["a"], "rows": [["b"]]},
        ],
        output_path=str(tmp_path / "test.docx"),
    )
    names = [Path(p).name for p in result["attachments"]]
    assert names == ["无扩展名附件.csv"]


# ── 输出路径解析 ────────────────────────────────────────────────


def test_output_path_explicit_docx(tmp_path):
    """output_path 是 .docx 路径 → 用这个路径."""
    target = tmp_path / "subdir" / "我的汇报.docx"
    result = render_briefing(
        title_lines=["test"],
        sections=[{"heading": "一、背景与现状", "blocks": [
            {"type": "paragraph", "text": "x"},
        ]}],
        output_path=str(target),
    )
    assert result["docx"] == str(target.resolve())


def test_output_path_default_uses_catfish_output(tmp_path, monkeypatch):
    """没传 output_path → 应在 ~/.catfish/output/YYYY-MM-DD/HHMMSS_<slug>/ 下."""
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    result = render_briefing(
        title_lines=["关于鲶鱼 PoC 立项的请示"],
        sections=[{"heading": "一、背景与现状", "blocks": [
            {"type": "paragraph", "text": "x"},
        ]}],
    )
    out = Path(result["docx"])
    assert ".catfish/output/" in str(out)
    # 含日期目录
    assert any(part.startswith("20") for part in out.parts)


def test_output_path_directory(tmp_path):
    """output_path 是目录 → 在该目录下用 slug 文件名."""
    out_dir = tmp_path / "保存到这里"
    result = render_briefing(
        title_lines=["关于 X 的请示"],
        sections=[{"heading": "一、背景与现状", "blocks": [
            {"type": "paragraph", "text": "x"},
        ]}],
        output_path=str(out_dir),
    )
    assert Path(result["docx"]).parent == out_dir.resolve()
    assert Path(result["docx"]).suffix == ".docx"


# ── 兼容性 + 边界 ───────────────────────────────────────────────


def test_minimal_input(tmp_path):
    """最简: 只传 title + 1 段."""
    result = render_briefing(
        title_lines=["最简汇报"],
        sections=[{"heading": "一、背景与现状", "blocks": [
            {"type": "paragraph", "text": "一段话."},
        ]}],
        output_path=str(tmp_path / "min.docx"),
    )
    assert Path(result["docx"]).exists()


def test_no_attachments(tmp_path):
    """不传 attachments → result.attachments == []."""
    result = render_briefing(
        title_lines=["t"],
        sections=[{"heading": "一、背景与现状", "blocks": [
            {"type": "paragraph", "text": "x"},
        ]}],
        output_path=str(tmp_path / "no-att.docx"),
    )
    assert result["attachments"] == []
    assert len(result["files"]) == 1


def test_unknown_block_type_does_not_crash(tmp_path):
    """未知 block type → 退化成 paragraph, 不崩."""
    result = render_briefing(
        title_lines=["t"],
        sections=[{"heading": "一、背景与现状", "blocks": [
            {"type": "weirdtype", "text": "x"},
        ]}],
        output_path=str(tmp_path / "weird.docx"),
    )
    assert Path(result["docx"]).exists()
