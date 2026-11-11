"""pptx 三方合并 (doc_merge/slides.py, docs/DOC-REVISE-MERGE-PLAN.md §4.2 / §5)。

全部虚构: 「华东区数字化项目」的汇报 PPT, 现场用 python-pptx 生成, 不提交二进制样例; 邮箱都是 example.com。
发起人张三, 李四 / 王五 / 赵六各自改。每份都埋了不同的 docProps/custom.xml 记号 (catfish_rev), 验证它不算改动。
"""
from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import zipfile

import pytest
from PIL import Image
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE
from pptx.opc.constants import CONTENT_TYPE as CT
from pptx.opc.constants import RELATIONSHIP_TYPE as RT
from pptx.opc.package import Part
from pptx.opc.packuri import PackURI
from pptx.util import Inches

from catfish_tool_bridge import doc_merge
from catfish_tool_bridge.doc_merge import Ctx, Return, core

OWNER, LI, WANG, ZHAO = "zhangsan@example.com", "lisi@example.com", "wangwu@example.com", "zhaoliu@example.com"
CTX = Ctx(owner=OWNER, names={OWNER: "张三", LI: "李四", WANG: "王五", ZHAO: "赵六"})
TITLES = ["华东区数字化项目汇报", "项目背景", "项目进度", "风险与对策", "预算", "下一步计划"]

_SOFFICE = shutil.which("soffice") or "/Applications/LibreOffice.app/Contents/MacOS/soffice"
HAS_SOFFICE = os.path.exists(_SOFFICE)


# ── 造测试数据 ───────────────────────────────────────────────────────────────

def _custom_xml(rev: str) -> bytes:
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties" '
            'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
            '<property fmtid="{D5CDD505-2E9C-101B-9397-08002B2CF9AE}" pid="2" name="catfish_rev">'
            f'<vt:lpwstr>{rev}</vt:lpwstr></property></Properties>').encode()


def _mark(prs, rev: str, who: str) -> None:
    """埋记号 + 改文档属性 (作者 / 修改人), 这些都不该算改动。"""
    pkg = prs.part.package
    for rel in pkg._rels.values():
        if rel.reltype == RT.CUSTOM_PROPERTIES:
            rel.target_part.blob = _custom_xml(rev)
            break
    else:
        part = Part(PackURI("/docProps/custom.xml"), CT.OFC_CUSTOM_PROPERTIES, pkg, _custom_xml(rev))
        pkg.relate_to(part, RT.CUSTOM_PROPERTIES)
    prs.core_properties.last_modified_by = who
    prs.core_properties.revision = len(who)


def _save(prs) -> bytes:
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def _png(color: str) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), color).save(buf, "PNG")
    return buf.getvalue()


def _bullets(slide, lines: list[str]) -> None:
    # 正文占位符 (LibreOffice 另存后占位符序号会变, 不按 idx 找)
    body = next(ph for ph in slide.placeholders if ph != slide.shapes.title).text_frame
    body.text = lines[0]
    for t in lines[1:]:
        body.add_paragraph().text = t


def make_base() -> bytes:
    prs = Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[0])
    s.shapes.title.text = TITLES[0]
    s.placeholders[1].text = "2026 年 11 月 · 项目组"
    for title, lines in [("项目背景", ["客户现有 12 个分厂, 系统各自为政", "目标: 统一数据平台"]),
                         ("项目进度", ["需求调研: 完成", "系统设计: 进行中 (60%)", "开发: 未开始"]),
                         ("风险与对策", ["接口文档不全 → 驻场梳理", "分厂配合度低 → 高层推动"])]:
        s = prs.slides.add_slide(prs.slide_layouts[1])
        s.shapes.title.text = title
        _bullets(s, lines)
    s = prs.slides.add_slide(prs.slide_layouts[5])
    s.shapes.title.text = "预算"
    tbl = s.shapes.add_table(3, 2, Inches(1), Inches(2), Inches(6), Inches(1.5)).table
    for r, (a, b) in enumerate([("项目", "金额 (万元)"), ("软件", "48"), ("实施", "20")]):
        tbl.cell(r, 0).text, tbl.cell(r, 1).text = a, b
    s.notes_slide.notes_text_frame.text = "预算口径: 不含税"
    s = prs.slides.add_slide(prs.slide_layouts[1])
    s.shapes.title.text = "下一步计划"
    _bullets(s, ["12 月完成系统设计评审", "1 月启动开发"])
    _mark(prs, "r_20261111_ab12cd:v1", "张三")
    return _save(prs)


def edit(base: bytes, who: str, *fns) -> Return:
    prs = Presentation(io.BytesIO(base))
    for fn in fns:
        fn(prs)
    _mark(prs, f"r_20261111_ab12cd:v1:{who}", who)
    return Return(who, _save(prs), "2026-11-13T10:00:00")


def by_title(prs, title: str):
    return next(s for s in prs.slides if s.shapes.title is not None and s.shapes.title.text == title)


def set_body(title: str, lines: list[str]):
    def fn(prs):
        _bullets(by_title(prs, title), lines)
    return fn


def set_cell(r: int, c: int, text: str):
    def fn(prs):
        s = by_title(prs, "预算")
        next(sh for sh in s.shapes if sh.has_table).table.cell(r, c).text = text
    return fn


def move(title: str, to: int):
    def fn(prs):
        lst = prs.slides._sldIdLst
        el = next(e for e, s in zip(list(lst), prs.slides) if s.shapes.title.text == title)
        lst.remove(el)
        lst.insert(to, el)
    return fn


def delete(title: str):
    def fn(prs):
        lst = prs.slides._sldIdLst
        el = next(e for e, s in zip(list(lst), prs.slides) if s.shapes.title.text == title)
        prs.part.related_part(el.rId).partname = PackURI("/ppt/slides/removed%d.xml" % el.id)
        prs.part.drop_rel(el.rId)
        lst.remove(el)
        prs.part.rename_slide_parts([e.rId for e in lst])   # 不然 python-pptx 再加页会撞名 (PowerPoint 不会)
    return fn


def add(after: str, title: str, lines: list[str], image: bytes | None = None, layout: int = 1, chart: bool = False):
    def fn(prs):
        pos = [s.shapes.title.text if s.shapes.title is not None else "" for s in prs.slides].index(after) + 1
        s = prs.slides.add_slide(prs.slide_layouts[layout])
        s.shapes.title.text = title
        _bullets(s, lines)
        if image:
            s.shapes.add_picture(io.BytesIO(image), Inches(6), Inches(5), Inches(2))
        if chart:
            data = CategoryChartData()
            data.categories = ["Q1", "Q2", "Q3"]
            data.add_series("上线分厂", (2, 5, 9))
            s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(1), Inches(4), Inches(4), Inches(3), data)
        lst = prs.slides._sldIdLst
        el = list(lst)[-1]
        lst.remove(el)
        lst.insert(pos, el)
    return fn


def titles(data: bytes) -> list[str]:
    return [s.shapes.title.text for s in Presentation(io.BytesIO(data)).slides]


def notes(data: bytes) -> list[str]:
    out = []
    for s in Presentation(io.BytesIO(data)).slides:
        tf = s.notes_slide.notes_text_frame if s.has_notes_slide else None
        out.append(tf.text if tf is not None else "")
    return out


def body(data: bytes, title: str) -> str:
    s = by_title(Presentation(io.BytesIO(data)), title)
    return "\n".join(sh.text_frame.text for sh in s.shapes if sh.has_text_frame and sh != s.shapes.title)


def analyze(base, rets):
    return doc_merge.analyze("pptx", base, rets, CTX)


def render(base, rets, decisions=None):
    return doc_merge.render("pptx", base, rets, decisions or {}, CTX)


def to_pdf(data: bytes, tmp_path) -> bytes:
    src = tmp_path / "merged.pptx"
    src.write_bytes(data)
    subprocess.run([_SOFFICE, f"-env:UserInstallation=file://{tmp_path}/lo-profile", "--headless",
                    "--convert-to", "pdf", "--outdir", str(tmp_path), str(src)],
                   check=True, capture_output=True, timeout=240)
    return (tmp_path / "merged.pdf").read_bytes()


def pdf_pages(pdf: bytes) -> int:
    return len(re.findall(rb"/Type\s*/Page(?!s)", pdf))


@pytest.fixture(scope="module")
def base() -> bytes:
    return make_base()


# ── 1. 两人改不同页 ─────────────────────────────────────────────────────────

def test_two_people_edit_different_slides(base):
    rets = [edit(base, LI, set_body("项目进度", ["需求调研: 完成", "系统设计: 完成", "开发: 进行中 (20%)"])),
            edit(base, WANG, set_cell(1, 1, "52"))]
    a = analyze(base, rets)
    assert a["conflicts"] == [] and a["changes"] == {LI: 1, WANG: 1} and a["same"] == []
    out = render(base, rets)
    assert titles(out["clean"]) == TITLES
    assert "开发: 进行中 (20%)" in body(out["clean"], "项目进度")
    prs = Presentation(io.BytesIO(out["clean"]))
    tbl = next(sh for sh in by_title(prs, "预算").shapes if sh.has_table).table
    assert tbl.cell(1, 1).text == "52" and tbl.cell(2, 1).text == "20"
    assert out["changes"] == {LI: 1, WANG: 1}


def test_docprops_only_is_not_a_change(base):
    rets = [edit(base, LI), edit(base, WANG, set_cell(1, 1, "52"))]
    a = analyze(base, rets)
    assert a["same"] == [LI] and a["changes"] == {WANG: 1} and a["warnings"] == []
    # 合并版沿用底稿的记号
    with zipfile.ZipFile(io.BytesIO(render(base, rets)["clean"])) as z:
        assert b"r_20261111_ab12cd:v1<" in z.read("docProps/custom.xml")


# ── 2. 同一页改法不同 → 冲突 ────────────────────────────────────────────────

def test_same_slide_different_edits_conflict(base):
    rets = [edit(base, LI, set_body("项目进度", ["系统设计: 完成"])),
            edit(base, WANG, set_body("项目进度", ["系统设计: 延期两周"]))]
    a = analyze(base, rets)
    assert len(a["conflicts"]) == 1
    c = a["conflicts"][0]
    assert c["kind"] == "slide" and c["where"] == "第 3 页「项目进度」" and c["combined"] is None
    assert c["base"] == "项目进度\n需求调研: 完成\n系统设计: 进行中 (60%)\n开发: 未开始"
    assert [(o["key"], o["who"], o["text"]) for o in c["options"]] == [
        (LI, [LI], "项目进度\n系统设计: 完成"), (WANG, [WANG], "项目进度\n系统设计: 延期两周")]
    assert a["changes"] == {}

    with pytest.raises(core.Undecided):
        render(base, rets)
    with pytest.raises(core.Undecided):        # pptx 没有「自己写」
        render(base, rets, {c["cid"]: {"pick": "text", "text": "随便写"}})
    out = render(base, rets, {c["cid"]: {"pick": WANG}})
    assert body(out["clean"], "项目进度") == "系统设计: 延期两周"
    assert out["changes"] == {WANG: 1}
    assert "这页用的是王五的版本" in notes(out["tracked"])[2]
    out = render(base, rets, {c["cid"]: {"pick": "base"}})
    assert body(out["clean"], "项目进度") == "需求调研: 完成\n系统设计: 进行中 (60%)\n开发: 未开始"
    assert out["changes"] == {}
    assert "这页保留原稿" in notes(out["tracked"])[2]


# ── 3. 改成一样 → 不冲突 ───────────────────────────────────────────────────

def test_same_edit_no_conflict(base):
    fn = set_body("风险与对策", ["接口文档不全 → 驻场梳理", "预算超支 → 分期付款"])
    rets = [edit(base, LI, fn), edit(base, WANG, fn)]
    a = analyze(base, rets)
    assert a["conflicts"] == [] and a["changes"] == {LI: 1, WANG: 1}
    out = render(base, rets)
    assert "预算超支 → 分期付款" in body(out["clean"], "风险与对策")
    assert "这页用的是李四、王五的版本" in notes(out["tracked"])[3]


# ── 4. 一人挪页、一人改页 ──────────────────────────────────────────────────

def test_reorder_and_edit(base):
    rets = [edit(base, LI, move("下一步计划", 2)),
            edit(base, WANG, set_body("风险与对策", ["分厂配合度低 → 高层推动"])),
            edit(base, ZHAO)]
    a = analyze(base, rets)
    assert a["conflicts"] == [] and a["same"] == [ZHAO] and a["changes"] == {LI: 1, WANG: 1}
    out = render(base, rets)
    assert titles(out["clean"]) == ["华东区数字化项目汇报", "项目背景", "下一步计划", "项目进度", "风险与对策", "预算"]
    assert body(out["clean"], "风险与对策") == "分厂配合度低 → 高层推动"
    # 页编号沿用底稿的
    ids = {s.shapes.title.text: s.slide_id for s in Presentation(io.BytesIO(base)).slides}
    assert [s.slide_id for s in Presentation(io.BytesIO(out["clean"])).slides] == [
        ids[t] for t in titles(out["clean"])]


def test_two_different_reorders_use_first(base):
    rets = [edit(base, LI, move("下一步计划", 1)), edit(base, WANG, move("项目背景", 5))]
    a = analyze(base, rets)
    assert any("用的是先交回的李四的顺序" in w for w in a["warnings"])
    out = render(base, rets)
    assert titles(out["clean"]) == ["华东区数字化项目汇报", "下一步计划", "项目背景", "项目进度", "风险与对策", "预算"]


# ── 5. 一人删页、一人改这页 → 冲突 ─────────────────────────────────────────

def test_delete_vs_edit_conflict(base):
    rets = [edit(base, LI, delete("风险与对策")),
            edit(base, WANG, set_body("风险与对策", ["新增: 数据迁移风险"]))]
    a = analyze(base, rets)
    c, = a["conflicts"]
    assert c["where"] == "第 4 页「风险与对策」"
    assert [o["text"] for o in c["options"]] == ["(删掉了这页)", "风险与对策\n新增: 数据迁移风险"]
    assert titles(render(base, rets, {c["cid"]: {"pick": LI}})["clean"]) == [t for t in TITLES if t != "风险与对策"]
    out = render(base, rets, {c["cid"]: {"pick": WANG}})
    assert titles(out["clean"]) == TITLES and body(out["clean"], "风险与对策") == "新增: 数据迁移风险"


def test_delete_only(base):
    out = render(base, [edit(base, LI, delete("项目背景"))])
    assert titles(out["clean"]) == [t for t in TITLES if t != "项目背景"]
    assert out["changes"] == {LI: 1}


# ── 6. 新页带图片 / 图表 ────────────────────────────────────────────────────

def test_new_slide_with_picture_and_chart(base, tmp_path):
    img = _png("#c03030")
    rets = [edit(base, LI, add("项目进度", "里程碑照片", ["11 月 5 日现场验收"], image=img, chart=True)),
            edit(base, WANG, set_cell(2, 1, "25"))]
    out = render(base, rets)
    assert titles(out["clean"]) == TITLES[:3] + ["里程碑照片"] + TITLES[3:]
    prs = Presentation(io.BytesIO(out["clean"]))
    s = by_title(prs, "里程碑照片")
    pic = next(sh for sh in s.shapes if sh.shape_type == 13)
    assert pic.image.blob == img and pic.image.content_type == "image/png"
    chart = next(sh for sh in s.shapes if sh.has_chart).chart
    assert list(chart.plots[0].categories) == ["Q1", "Q2", "Q3"]
    with zipfile.ZipFile(io.BytesIO(out["clean"])) as z:
        names = z.namelist()
        ct = z.read("[Content_Types].xml").decode()
        rels = z.read("ppt/slides/_rels/slide4.xml.rels").decode()
    media = [n for n in names if n.startswith("ppt/media/") and n.endswith(".png")]
    assert len(media) == 1 and 'Extension="png"' in ct
    assert "../media/" in rels and "../charts/" in rels and "slideLayout" in rels
    assert any(n.startswith("ppt/embeddings/") for n in names)
    assert len(names) == len(set(names))
    if HAS_SOFFICE:
        pdf = to_pdf(out["clean"], tmp_path)
        assert pdf_pages(pdf) == 7 and b"/Image" in pdf


# ── 7. 同一处两人各加一页 ──────────────────────────────────────────────────

def test_two_inserts_same_place_keep_both_in_return_order(base):
    a_li = edit(base, LI, add("项目背景", "客户现状调研", ["走访 8 个分厂"]))
    a_wang = edit(base, WANG, add("项目背景", "竞品对比", ["A 方案 / B 方案"]))
    out = render(base, [a_li, a_wang])
    assert titles(out["clean"]) == TITLES[:2] + ["客户现状调研", "竞品对比"] + TITLES[2:]
    out = render(base, [a_wang, a_li])
    assert titles(out["clean"]) == TITLES[:2] + ["竞品对比", "客户现状调研"] + TITLES[2:]
    ids = [s.slide_id for s in Presentation(io.BytesIO(out["clean"])).slides]
    assert len(set(ids)) == len(ids)       # 两人新页原本的编号撞了 (都是 max+1), 合并版重新编
    assert analyze(base, [a_li, a_wang])["changes"] == {LI: 1, WANG: 1}


def test_identical_inserts_kept_once(base):
    fn = add("预算", "附: 报价明细", ["见附件"])
    out = render(base, [edit(base, LI, fn), edit(base, WANG, fn)])
    assert titles(out["clean"]).count("附: 报价明细") == 1
    assert "这页是李四、王五新加的" in notes(out["tracked"])[5]


def test_insert_after_deleted_slide_falls_back(base):
    rets = [edit(base, LI, add("风险与对策", "风险补充", ["汇率"])), edit(base, WANG, delete("风险与对策"))]
    assert titles(render(base, rets)["clean"]) == ["华东区数字化项目汇报", "项目背景", "项目进度", "风险补充", "预算",
                                                   "下一步计划"]


# ── 8. tracked 版备注 ──────────────────────────────────────────────────────

def test_tracked_notes(base):
    rets = [edit(base, LI, set_cell(1, 1, "52")), edit(base, WANG, add("下一步计划", "联系人", ["王五 138****0000"]))]
    out = render(base, rets)
    n = notes(out["tracked"])
    assert n[4] == "预算口径: 不含税\n【合并说明】这页用的是李四的版本"     # 原有备注后追加
    assert n[6] == "【合并说明】这页是王五新加的"                             # 没有备注页的新建
    assert n[0] == n[1] == ""
    assert all("合并说明" not in t for t in notes(out["clean"]))
    assert notes(out["clean"])[4] == "预算口径: 不含税"


def test_notes_change_counts_as_edit(base):
    def fn(prs):
        by_title(prs, "预算").notes_slide.notes_text_frame.text = "预算口径: 含税"
    out = render(base, [edit(base, LI, fn)])
    assert notes(out["clean"])[4] == "预算口径: 含税" and out["changes"] == {LI: 1}


# ── 9. cid 一致 / 没定完 ───────────────────────────────────────────────────

def test_cid_stable_and_undecided(base):
    rets = [edit(base, LI, set_body("项目进度", ["甲"]), set_cell(1, 1, "50")),
            edit(base, WANG, set_body("项目进度", ["乙"]), set_cell(1, 1, "55"))]
    a1, a2 = analyze(base, rets), analyze(base, rets)
    cids = [c["cid"] for c in a1["conflicts"]]
    assert len(cids) == 2 and cids == [c["cid"] for c in a2["conflicts"]]
    with pytest.raises(core.Undecided):
        render(base, rets, {cids[0]: {"pick": LI}})
    out = render(base, rets, {cids[0]: {"pick": LI}, cids[1]: {"pick": WANG}})
    assert body(out["clean"], "项目进度") == "甲"
    prs = Presentation(io.BytesIO(out["clean"]))
    assert next(sh for sh in by_title(prs, "预算").shapes if sh.has_table).table.cell(1, 1).text == "55"
    assert out["changes"] == {LI: 1, WANG: 1}


# ── 10. 合并版能打开, 页数 / 标题对; LibreOffice 实际转一次 ──────────────────

@pytest.mark.skipif(not HAS_SOFFICE, reason="本机没有 LibreOffice")
def test_full_merge_opens_and_converts(base, tmp_path):
    rets = [edit(base, LI, move("预算", 1), set_body("项目进度", ["系统设计: 完成"]),
                 add("项目进度", "现场照片", ["分厂 3"], image=_png("#3060c0"))),
            edit(base, WANG, delete("项目背景"), add("下一步计划", "附录", ["术语表"])),
            edit(base, ZHAO, set_body("项目进度", ["系统设计: 延期"]))]
    a = analyze(base, rets)
    c, = a["conflicts"]
    out = render(base, rets, {c["cid"]: {"pick": ZHAO}})
    want = ["华东区数字化项目汇报", "预算", "项目进度", "现场照片", "风险与对策", "下一步计划", "附录"]
    for data in (out["clean"], out["tracked"]):
        assert titles(data) == want
        assert pdf_pages(to_pdf(data, tmp_path)) == len(want)
    assert body(out["clean"], "项目进度") == "系统设计: 延期"


# ── 版式 / 分节 / 页间链接 ─────────────────────────────────────────────────

def test_layout_missing_in_base_brings_master(base, tmp_path):
    def fn(prs):
        prs.slide_layouts[3].name = "双栏对比 (新)"
        add("项目进度", "方案对比", ["左: 自建"], layout=3)(prs)
    rets = [edit(base, LI, fn)]
    a = analyze(base, rets)
    assert any("版式「双栏对比 (新)」原稿里没有" in w for w in a["warnings"])
    out = render(base, rets)
    prs = Presentation(io.BytesIO(out["clean"]))
    assert len(prs.slide_masters) == 2
    assert by_title(prs, "方案对比").slide_layout.name == "双栏对比 (新)"
    assert titles(out["clean"]) == TITLES[:3] + ["方案对比"] + TITLES[3:]
    ids = [int(e.get("id")) for e in prs.part._element.iter(
        "{http://schemas.openxmlformats.org/presentationml/2006/main}sldMasterId")]
    for m in prs.slide_masters:
        ids += [int(e.get("id")) for e in m._element.iter(
            "{http://schemas.openxmlformats.org/presentationml/2006/main}sldLayoutId")]
    assert len(ids) == len(set(ids))
    if HAS_SOFFICE:
        assert pdf_pages(to_pdf(out["clean"], tmp_path)) == 7


_P14 = "http://schemas.microsoft.com/office/powerpoint/2010/main"


def _sectioned(base: bytes) -> bytes:
    prs = Presentation(io.BytesIO(base))
    ids = [s.slide_id for s in prs.slides]
    secs = [("开场", ids[:2]), ("正文", ids[2:5]), ("收尾", ids[5:])]
    xml = "".join(f'<p14:section name="{n}" id="{{0000000{i}-0000-0000-0000-000000000000}}"><p14:sldIdLst>'
                  + "".join(f'<p14:sldId id="{x}"/>' for x in xs) + "</p14:sldIdLst></p14:section>"
                  for i, (n, xs) in enumerate(secs))
    from lxml import etree
    ext = etree.fromstring(
        f'<p:extLst xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:p14="{_P14}">'
        f'<p:ext uri="{{521415D9-36F7-43E2-AB2F-B90AF26B5E84}}"><p14:sectionLst>{xml}</p14:sectionLst></p:ext>'
        "</p:extLst>")
    prs.part._element.append(ext)
    return _save(prs)


def test_sections_follow_merge(base):
    b = _sectioned(base)
    rets = [edit(b, LI, add("风险与对策", "补充风险", ["汇率"])), edit(b, WANG, delete("项目背景"))]
    out = render(b, rets)
    prs = Presentation(io.BytesIO(out["clean"]))
    by_id = {s.slide_id: s.shapes.title.text for s in prs.slides}
    got = [(sec.get("name"), [by_id[int(e.get("id"))] for e in sec.iter(f"{{{_P14}}}sldId")])
           for sec in prs.part._element.iter(f"{{{_P14}}}section")]
    assert got == [("开场", ["华东区数字化项目汇报"]), ("正文", ["项目进度", "风险与对策", "补充风险", "预算"]),
                   ("收尾", ["下一步计划"])]


def test_link_to_deleted_slide_removed(base):
    b = Presentation(io.BytesIO(base))
    src = by_title(b, "项目背景")
    src.placeholders[1].click_action.target_slide = by_title(b, "风险与对策")
    base2 = _save(b)
    out = render(base2, [edit(base2, LI, delete("风险与对策"))])
    assert any("链接去掉了" in w for w in out["warnings"])
    prs = Presentation(io.BytesIO(out["clean"]))
    assert all(r.reltype != RT.SLIDE for r in by_title(prs, "项目背景").part.rels.values())
    # 链到的页被别人换成新版本: 链接改指新版本那页
    out = render(base2, [edit(base2, LI, set_body("风险与对策", ["新"]))])
    prs = Presentation(io.BytesIO(out["clean"]))
    tgt = [r.target_part for r in by_title(prs, "项目背景").part.rels.values() if r.reltype == RT.SLIDE]
    assert tgt == [by_title(prs, "风险与对策").part]
    with zipfile.ZipFile(io.BytesIO(out["clean"])) as z:
        assert len([n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)]) == 6


def _comment(prs, title: str, text: str) -> None:
    """给某页加一条老式批注 (连 commentAuthors)。"""
    pkg = prs.part.package
    authors = Part(PackURI("/ppt/commentAuthors.xml"), CT.PML_COMMENT_AUTHORS, pkg, (
        '<p:cmAuthorLst xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
        '<p:cmAuthor id="0" name="李四" initials="LS" lastIdx="1" clrIdx="0"/></p:cmAuthorLst>').encode())
    prs.part.relate_to(authors, RT.COMMENT_AUTHORS)
    cm = Part(PackURI("/ppt/comments/comment1.xml"), CT.PML_COMMENTS, pkg, (
        '<p:cmLst xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
        '<p:cm authorId="0" dt="2026-11-12T09:00:00.000" idx="1"><p:pos x="10" y="10"/>'
        f'<p:text>{text}</p:text></p:cm></p:cmLst>').encode())
    by_title(prs, title).part.relate_to(cm, RT.COMMENTS)


def test_comments_not_merged_but_warned(base):
    def fn(prs):
        _comment(prs, "项目进度", "进度数字再核对一下")
        set_body("项目进度", ["系统设计: 完成"])(prs)
    rets = [edit(base, LI, fn)]
    a = analyze(base, rets)
    assert any("李四在他那份第 3 页「项目进度」上的批注没合进来" in w for w in a["warnings"])
    out = render(base, rets)
    assert body(out["clean"], "项目进度") == "系统设计: 完成"
    with zipfile.ZipFile(io.BytesIO(out["clean"])) as z:
        assert not [n for n in z.namelist() if n.startswith("ppt/comments/")]
    # 只加了批注、没改内容: 不算 same, 页不动, 有提示
    a = analyze(base, [edit(base, WANG, lambda prs: _comment(prs, "预算", "48 万是否含税?"))])
    assert a["same"] == [] and a["changes"] == {} and any("批注没合进来" in w for w in a["warnings"])


# ── 另存不算改动: 页签名按语义比 (WPS / LibreOffice 会重写整份 XML) ─────────────

def make_rich() -> bytes:
    """在底稿上再加: 图片 + 图表页、带格式的文本框、组合形状、二级段落。"""
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.util import Pt
    prs = Presentation(io.BytesIO(make_base()))
    add("项目进度", "里程碑", ["现场验收"], image=_png("#c03030"), chart=True)(prs)
    s = by_title(prs, "风险与对策")
    tf = s.shapes.add_textbox(Inches(1), Inches(6), Inches(4), Inches(1)).text_frame
    tf.text = "重点: "
    r = tf.paragraphs[0].add_run()
    r.text, r.font.bold, r.font.size = "接口", True, Pt(20)
    r.font.color.rgb = RGBColor(0xC0, 0, 0)
    g = s.shapes.add_group_shape()
    g.shapes.add_shape(MSO_SHAPE.OVAL, Inches(6), Inches(1), Inches(1), Inches(1)).text = "A"
    g.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(7.5), Inches(1), Inches(1), Inches(1))
    para = s.placeholders[1].text_frame.add_paragraph()
    para.text, para.level = "子项: 汇率", 1
    _mark(prs, "r_20261111_ab12cd:v1", "张三")
    return _save(prs)


RICH_TITLES = TITLES[:3] + ["里程碑"] + TITLES[3:]


def lo_resave(data: bytes, tmp_path) -> bytes:
    """用 LibreOffice 原样另存一次 (模拟同事用别的软件打开、什么都没改就保存)。"""
    d = tmp_path / "lo"
    d.mkdir(exist_ok=True)
    src = d / "in.pptx"
    src.write_bytes(data)
    out = d / "out"
    subprocess.run([_SOFFICE, f"-env:UserInstallation=file://{tmp_path}/lo-profile", "--headless",
                    "--convert-to", "pptx:Impress MS PowerPoint 2007 XML", "--outdir", str(out), str(src)],
                   check=True, capture_output=True, timeout=240)
    return (out / "in.pptx").read_bytes()


@pytest.mark.skipif(not HAS_SOFFICE, reason="本机没有 LibreOffice")
def test_libreoffice_resave_is_same(tmp_path):
    rich = make_rich()
    lo = lo_resave(rich, tmp_path)
    with zipfile.ZipFile(io.BytesIO(lo)) as z:       # 确实被重写了 (不是原样拷贝)
        assert z.read("ppt/slides/slide3.xml") != zipfile.ZipFile(io.BytesIO(rich)).read("ppt/slides/slide3.xml")
    # LibreOffice 还把页编号按顺序重排了 (原稿第 4 页是后加的 262), 只能按内容认页
    assert [s.slide_id for s in Presentation(io.BytesIO(lo)).slides] != [
        s.slide_id for s in Presentation(io.BytesIO(rich)).slides]
    a = analyze(rich, [Return(LI, lo)])
    assert a == {"conflicts": [], "changes": {}, "same": [LI], "warnings": []}
    out = render(rich, [Return(LI, lo)])
    assert titles(out["clean"]) == RICH_TITLES and all("合并说明" not in t for t in notes(out["tracked"]))


@pytest.mark.skipif(not HAS_SOFFICE, reason="本机没有 LibreOffice")
def test_libreoffice_resave_plus_one_edit(tmp_path):
    rich = make_rich()
    prs = Presentation(io.BytesIO(lo_resave(rich, tmp_path)))
    _bullets(by_title(prs, "项目进度"), ["需求调研: 完成", "系统设计: 完成"])
    ret = Return(LI, _save(prs))
    other = edit(rich, WANG, set_cell(1, 1, "52"))
    a = analyze(rich, [ret, other])
    assert a["conflicts"] == [] and a["changes"] == {LI: 1, WANG: 1} and a["warnings"] == []
    out = render(rich, [ret, other])
    assert titles(out["clean"]) == RICH_TITLES
    assert body(out["clean"], "项目进度") == "需求调研: 完成\n系统设计: 完成"
    n = notes(out["tracked"])
    assert "这页用的是李四的版本" in n[2] and "这页用的是王五的版本" in n[5]
    assert sum("合并说明" in t for t in n) == 2
    # 只有那一页换成了 LibreOffice 存的版本, 其它页还是原稿的 XML
    merged, orig = Presentation(io.BytesIO(out["clean"])), Presentation(io.BytesIO(rich))
    for t in RICH_TITLES:
        same_xml = by_title(merged, t).part.blob == by_title(orig, t).part.blob
        assert same_xml == (t not in ("项目进度", "预算")), t
    if HAS_SOFFICE:
        assert pdf_pages(to_pdf(out["clean"], tmp_path)) == len(RICH_TITLES)


@pytest.mark.parametrize("change", ["bold", "color", "size", "move", "bullet", "level", "image", "chart", "geom"])
def test_semantic_signature_sees_real_changes(change):
    from pptx.dml.color import RGBColor
    from pptx.util import Pt
    rich = make_rich()

    def fn(prs):
        s = by_title(prs, "风险与对策")
        box = next(sh for sh in s.shapes if sh.has_text_frame and sh.text_frame.text.startswith("重点"))
        run = box.text_frame.paragraphs[0].runs[-1]
        body_tf = s.placeholders[1].text_frame
        if change == "bold":
            run.font.bold = False
        elif change == "color":
            run.font.color.rgb = RGBColor(0, 0x80, 0)
        elif change == "size":
            run.font.size = Pt(24)
        elif change == "move":
            box.left += Inches(0.2)
        elif change == "bullet":
            pPr = body_tf.paragraphs[0]._p.get_or_add_pPr()
            pPr.insert(0, pPr.makeelement("{http://schemas.openxmlformats.org/drawingml/2006/main}buNone", {}))
        elif change == "level":
            body_tf.paragraphs[2].level = 2
        elif change == "image":
            pic = next(sh for sh in by_title(prs, "里程碑").shapes if sh.shape_type == 13)
            pic._element.blipFill.blip.rEmbed = by_title(prs, "里程碑").part.get_or_add_image_part(
                io.BytesIO(_png("#2080f0")))[1]
        elif change == "chart":
            ch = next(sh for sh in by_title(prs, "里程碑").shapes if sh.has_chart).chart
            data = CategoryChartData()
            data.categories = ["Q1", "Q2", "Q3"]
            data.add_series("上线分厂", (2, 6, 9))
            ch.replace_data(data)
        elif change == "geom":
            grp = next(sh for sh in s.shapes if sh.shape_type == 6)
            grp.shapes[1]._element.spPr.prstGeom.set("prst", "roundRect")
    a = analyze(rich, [edit(rich, LI, fn)])
    assert a["changes"] == {LI: 1} and a["same"] == []
