"""pptx 同一页里按形状合并 (doc_merge/slides_shapes.py)。测试数据和小工具沿用 test_doc_merge_slides.py 的虚构汇报 PPT。"""
from __future__ import annotations

import io
import zipfile

import pytest
from pptx import Presentation
from pptx.opc.constants import RELATIONSHIP_TYPE as RT
from pptx.util import Inches

from catfish_tool_bridge.doc_merge import Return, core

from .test_doc_merge_slides import (
    HAS_SOFFICE, LI, OWNER, RICH_TITLES, TITLES, WANG, _bullets, _png, _save, analyze, body, by_title, edit,
    lo_resave, make_base, make_rich, notes, pdf_pages, render, set_cell, titles, to_pdf,
)


@pytest.fixture(scope="module")
def base() -> bytes:
    return make_base()


# ── 同一页里按形状合并 ─────────────────────────────────────────────────────

def set_title(title: str, new: str):
    def fn(prs):
        by_title(prs, title).shapes.title.text_frame.paragraphs[0].runs[0].text = new
    return fn


def set_para(title: str, k: int, text: str):
    """只改正文第 k 段 (别的段不动)。"""
    def fn(prs):
        s = by_title(prs, title)
        tf = next(ph for ph in s.placeholders if ph != s.shapes.title).text_frame
        tf.paragraphs[k].runs[0].text = text
    return fn


def _box(prs, title: str = "风险与对策"):
    return next(sh for sh in by_title(prs, title).shapes
                if sh.has_text_frame and sh.text_frame.text.startswith("重点"))


def _pic(prs):
    return next(sh for sh in by_title(prs, "里程碑").shapes if sh.shape_type == 13)


def set_image(png: bytes):
    def fn(prs):
        part = by_title(prs, "里程碑").part
        _pic(prs)._element.blipFill.blip.rEmbed = part.get_or_add_image_part(io.BytesIO(png))[1]
    return fn


def add_box(title: str, text: str, top: float):
    def fn(prs):
        by_title(prs, title).shapes.add_textbox(Inches(5), Inches(top), Inches(3), Inches(0.6)).text_frame.text = text
    return fn


def shape_ids(data: bytes, title: str) -> list[int]:
    return [sh.shape_id for sh in by_title(Presentation(io.BytesIO(data)), title).shapes]


@pytest.fixture(scope="module")
def rich() -> bytes:
    return make_rich()


def test_inpage_title_and_body(base):
    rets = [edit(base, LI, set_title("项目进度", "项目进度 (11 月)")),
            edit(base, WANG, set_para("项目进度", 2, "开发: 已启动"))]
    a = analyze(base, rets)
    assert a["conflicts"] == [] and a["changes"] == {LI: 1, WANG: 1} and a["warnings"] == []
    out = render(base, rets)
    assert titles(out["clean"]) == TITLES[:2] + ["项目进度 (11 月)"] + TITLES[3:]
    assert body(out["clean"], "项目进度 (11 月)") == "需求调研: 完成\n系统设计: 进行中 (60%)\n开发: 已启动"
    assert notes(out["tracked"])[2] == "【合并说明】这页合并了李四 (标题)、王五 (正文)"
    assert out["changes"] == {LI: 1, WANG: 1}


def test_inpage_same_box_different_paragraphs(base):
    rets = [edit(base, LI, set_para("项目进度", 0, "需求调研: 已验收")),
            edit(base, WANG, set_para("项目进度", 2, "开发: 已启动"))]
    assert analyze(base, rets)["conflicts"] == []
    out = render(base, rets)
    assert body(out["clean"], "项目进度") == "需求调研: 已验收\n系统设计: 进行中 (60%)\n开发: 已启动"
    assert out["changes"] == {LI: 1, WANG: 1}


def test_inpage_same_paragraph_text_conflict(base):
    rets = [edit(base, LI, set_para("项目进度", 1, "系统设计: 进行中 (80%)")),
            edit(base, WANG, set_para("项目进度", 1, "系统设计 (含接口): 进行中 (60%)"))]
    c, = analyze(base, rets)["conflicts"]
    assert c["kind"] == "text" and c["custom"] is True and c["where"] == "第 3 页「项目进度」 · 正文第 2 段"
    assert c["base"] == "系统设计: 进行中 (60%)"
    assert [o["text"] for o in c["options"]] == ["系统设计: 进行中 (80%)", "系统设计 (含接口): 进行中 (60%)"]
    assert c["combined"] == "系统设计 (含接口): 进行中 (80%)"
    with pytest.raises(core.Undecided):
        render(base, rets)
    for pick, want, ch in [({"pick": LI}, "系统设计: 进行中 (80%)", {LI: 1}),
                           ({"pick": "base"}, "系统设计: 进行中 (60%)", {}),
                           ({"pick": "text", "text": c["combined"]}, c["combined"], {OWNER: 1})]:
        out = render(base, rets, {c["cid"]: pick})
        assert body(out["clean"], "项目进度") == f"需求调研: 完成\n{want}\n开发: 未开始"
        assert out["changes"] == ch
    # 改到同一个数: 拼不了
    c2, = analyze(base, [edit(base, LI, set_para("项目进度", 1, "系统设计: 进行中 (80%)")),
                         edit(base, WANG, set_para("项目进度", 1, "系统设计: 进行中 (70%)"))])["conflicts"]
    assert c2["combined"] is None


def test_inpage_move_and_edit_same_box(rich):
    def mv(prs):
        _box(prs).left += Inches(1)

    def tx(prs):
        _box(prs).text_frame.paragraphs[0].runs[0].text = "要点: "
    rets = [edit(rich, LI, mv), edit(rich, WANG, tx)]
    a = analyze(rich, rets)
    assert a["conflicts"] == [] and a["changes"] == {LI: 1, WANG: 1}
    assert any("第 5 页「风险与对策」合并了李四、王五的修改, 请看一眼版式有没有重叠" in w for w in a["warnings"])
    out = render(rich, rets)
    prs = Presentation(io.BytesIO(out["clean"]))
    box = next(sh for sh in by_title(prs, "风险与对策").shapes
               if sh.has_text_frame and sh.text_frame.text.startswith("要点"))
    assert box.left == Inches(2) and box.text_frame.text == "要点: 接口"
    assert box.text_frame.paragraphs[0].runs[1].font.bold is True       # 没动的那个 run 格式还在


def test_inpage_swap_picture_and_edit_text(rich):
    new = _png("#2080f0")
    rets = [edit(rich, LI, set_image(new)), edit(rich, WANG, set_para("里程碑", 0, "现场验收通过"))]
    a = analyze(rich, rets)
    assert a["conflicts"] == [] and a["changes"] == {LI: 1, WANG: 1}
    out = render(rich, rets)
    prs = Presentation(io.BytesIO(out["clean"]))
    s = by_title(prs, "里程碑")
    assert _pic(prs).image.blob == new and body(out["clean"], "里程碑") == "现场验收通过"
    imgs = [r.target_part.blob for r in s.part.rels.values() if r.reltype == RT.IMAGE]
    assert imgs == [new]
    with zipfile.ZipFile(io.BytesIO(out["clean"])) as z:       # 旧图没人用了, 不留在包里
        media = [n for n in z.namelist() if n.startswith("ppt/media/")]
        assert len(media) == 1 and z.read(media[0]) == new
    assert notes(out["tracked"])[3] == "【合并说明】这页合并了李四 (图片)、王五 (正文)"


def test_inpage_two_different_pictures_conflict(rich):
    p1, p2 = _png("#2080f0"), _png("#20c060")
    rets = [edit(rich, LI, set_image(p1)), edit(rich, WANG, set_image(p2), set_para("里程碑", 0, "现场验收通过"))]
    c, = analyze(rich, rets)["conflicts"]
    assert c["kind"] == "slide" and c["custom"] is False and c["where"] == "第 4 页「里程碑」 · 图片"
    assert [o["text"] for o in c["options"]] == ["李四: 换了图片", "王五: 换了图片"] and c["combined"] is None
    with pytest.raises(core.Undecided):
        render(rich, rets, {c["cid"]: {"pick": "text", "text": "x"}})
    out = render(rich, rets, {c["cid"]: {"pick": WANG}})
    assert _pic(Presentation(io.BytesIO(out["clean"]))).image.blob == p2
    assert body(out["clean"], "里程碑") == "现场验收通过" and out["changes"] == {WANG: 2}
    out = render(rich, rets, {c["cid"]: {"pick": "base"}})
    assert _pic(Presentation(io.BytesIO(out["clean"]))).image.blob == _png("#c03030")
    assert out["changes"] == {WANG: 1}


def test_inpage_both_add_a_box(base):
    rets = [edit(base, LI, add_box("项目进度", "注: 数据截至 11/10", 6)),
            edit(base, WANG, add_box("项目进度", "负责人: 王五", 6.6))]
    a = analyze(base, rets)
    assert a["conflicts"] == [] and a["changes"] == {LI: 1, WANG: 1}
    assert any("请看一眼版式有没有重叠" in w for w in a["warnings"])
    out = render(base, rets)
    assert body(out["clean"], "项目进度").endswith("注: 数据截至 11/10\n负责人: 王五")
    ids = shape_ids(out["clean"], "项目进度")
    assert len(ids) == 4 and len(set(ids)) == 4          # 两人新形状原来的编号一样 (都是 4)
    assert "李四 (「注: 数据截至 11/1」)、王五 (「负责人: 王五」)" in notes(out["tracked"])[2]


def test_inpage_delete_vs_edit_shape(rich):
    def rm(prs):
        el = _box(prs)._element
        el.getparent().remove(el)

    def tx(prs):
        _box(prs).text_frame.paragraphs[0].runs[1].text = "接口和数据"
    rets = [edit(rich, LI, rm), edit(rich, WANG, tx)]
    c, = analyze(rich, rets)["conflicts"]
    assert c["kind"] == "slide" and c["where"] == "第 5 页「风险与对策」 · 「重点: 接口」"
    assert [o["text"] for o in c["options"]] == ["李四: 删掉了这个形状", "王五: 改成: 重点: 接口和数据"]
    out = render(rich, rets, {c["cid"]: {"pick": LI}})
    assert "重点" not in body(out["clean"], "风险与对策")
    out = render(rich, rets, {c["cid"]: {"pick": WANG}})
    assert "重点: 接口和数据" in body(out["clean"], "风险与对策")


def _cell(data: bytes, r: int, c: int) -> str:
    s = by_title(Presentation(io.BytesIO(data)), "预算")
    return next(sh for sh in s.shapes if sh.has_table).table.cell(r, c).text


def test_inpage_table_cells(base):
    rets = [edit(base, LI, set_cell(1, 1, "52")), edit(base, WANG, set_cell(2, 1, "25"))]
    a = analyze(base, rets)
    assert a["conflicts"] == [] and a["changes"] == {LI: 1, WANG: 1}
    out = render(base, rets)
    assert (_cell(out["clean"], 1, 1), _cell(out["clean"], 2, 1), _cell(out["clean"], 1, 0)) == ("52", "25", "软件")
    rets = [edit(base, LI, set_cell(1, 1, "52")), edit(base, WANG, set_cell(1, 1, "55"), set_cell(2, 0, "实施费"))]
    c, = analyze(base, rets)["conflicts"]
    assert c["kind"] == "text" and c["where"] == "第 5 页「预算」 · 表格第 2 行第 2 列"
    assert c["base"] == "48" and [o["text"] for o in c["options"]] == ["52", "55"] and c["combined"] is None
    out = render(base, rets, {c["cid"]: {"pick": WANG}})
    assert (_cell(out["clean"], 1, 1), _cell(out["clean"], 2, 0)) == ("55", "实施费") and out["changes"] == {WANG: 2}
    out = render(base, rets, {c["cid"]: {"pick": "text", "text": "53.5"}})
    assert _cell(out["clean"], 1, 1) == "53.5" and out["changes"] == {WANG: 1, OWNER: 1}


def test_inpage_notes_by_paragraph(base):
    def three(prs):
        by_title(prs, "预算").notes_slide.notes_text_frame.text = "口径: 不含税\n汇率: 7.1\n审批: 待定"
    b = Presentation(io.BytesIO(base))
    three(b)
    b3 = _save(b)

    def note(old: str, new: str):
        def fn(prs):
            tf = by_title(prs, "预算").notes_slide.notes_text_frame
            tf.text = tf.text.replace(old, new)
        return fn
    rets = [edit(b3, LI, note("不含税", "含税")), edit(b3, WANG, note("待定", "已批"))]
    a = analyze(b3, rets)
    assert a["conflicts"] == [] and a["changes"] == {LI: 1, WANG: 1}
    out = render(b3, rets)
    assert notes(out["clean"])[4] == "口径: 含税\n汇率: 7.1\n审批: 已批"
    assert notes(out["tracked"])[4].endswith("【合并说明】这页合并了李四 (备注)、王五 (备注)")
    c, = analyze(b3, [edit(b3, LI, note("7.1", "7.2")), edit(b3, WANG, note("7.1", "7.0"))])["conflicts"]
    assert c["kind"] == "text" and c["where"] == "第 5 页「预算」 · 备注第 2 段"


@pytest.mark.skipif(not HAS_SOFFICE, reason="本机没有 LibreOffice")
def test_inpage_untrusted_shape_ids_fall_back_to_whole_page(rich, tmp_path):
    prs = Presentation(io.BytesIO(lo_resave(rich, tmp_path)))       # LibreOffice 把页里的形状重新编了号
    _bullets(by_title(prs, "项目进度"), ["需求调研: 完成", "系统设计: 完成"])
    rets = [Return(LI, _save(prs)), edit(rich, WANG, set_title("项目进度", "项目进度 (11 月)"))]
    a = analyze(rich, rets)
    c, = a["conflicts"]
    assert c["kind"] == "slide" and c["where"] == "第 3 页「项目进度」" and len(c["options"]) == 2
    assert "第 3 页「项目进度」: 李四这份是用别的软件导出的, 页里的形状对不上号, 这页只能整页选用" in a["warnings"]
    out = render(rich, rets, {c["cid"]: {"pick": LI}})
    assert body(out["clean"], "项目进度") == "需求调研: 完成\n系统设计: 完成"
    out = render(rich, rets, {c["cid"]: {"pick": WANG}})
    assert titles(out["clean"])[2] == "项目进度 (11 月)"
    assert body(out["clean"], "项目进度 (11 月)") == "需求调研: 完成\n系统设计: 进行中 (60%)\n开发: 未开始"


def test_inpage_cids_stable_and_output_sound(rich, tmp_path):
    rets = [edit(rich, LI, set_image(_png("#2080f0")), set_para("项目进度", 1, "系统设计: 进行中 (80%)"),
                 add_box("项目进度", "注: 数据截至 11/10", 6), set_cell(1, 1, "52")),
            edit(rich, WANG, set_para("里程碑", 0, "现场验收通过"), set_para("项目进度", 1, "系统设计: 暂停"),
                 add_box("项目进度", "负责人: 王五", 6.6), set_cell(1, 1, "55"), set_title("预算", "预算 (万元)"))]
    a1, a2 = analyze(rich, rets), analyze(rich, rets)
    cids = [c["cid"] for c in a1["conflicts"]]
    assert len(cids) == 2 and len(set(cids)) == 2 and cids == [c["cid"] for c in a2["conflicts"]]
    assert [c["where"] for c in a1["conflicts"]] == ["第 3 页「项目进度」 · 正文第 2 段",
                                                     "第 6 页「预算」 · 表格第 2 行第 2 列"]
    with pytest.raises(core.Undecided):
        render(rich, rets, {cids[0]: {"pick": LI}})
    out = render(rich, rets, {cids[0]: {"pick": LI}, cids[1]: {"pick": "text", "text": "53"}})
    want = RICH_TITLES[:5] + ["预算 (万元)", "下一步计划"]
    counts = {"项目进度": 4, "里程碑": 4, "预算 (万元)": 2, "风险与对策": 4}
    for data in (out["clean"], out["tracked"]):
        assert titles(data) == want
        prs = Presentation(io.BytesIO(data))
        for t, n in counts.items():
            assert len(by_title(prs, t).shapes) == n, t
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            assert len(z.namelist()) == len(set(z.namelist()))
    prs = Presentation(io.BytesIO(out["clean"]))
    assert next(sh for sh in by_title(prs, "预算 (万元)").shapes if sh.has_table).table.cell(1, 1).text == "53"
    assert _pic(prs).image.blob == _png("#2080f0")
    assert out["changes"] == {LI: 3, WANG: 3, OWNER: 1}
    if HAS_SOFFICE:
        pdf = to_pdf(out["clean"], tmp_path)
        assert pdf_pages(pdf) == len(want) and b"/Image" in pdf
