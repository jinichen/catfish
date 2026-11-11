"""群里一起改文档 · Word 合并 (doc_merge/word*.py)。测试文档都是现场用 python-docx 生成的虚构方案。"""
from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
import zipfile
from collections import Counter
from pathlib import Path

import docx
import pytest
from lxml import etree

from catfish_tool_bridge.doc_merge import Ctx, Return, Undecided, analyze, render
from catfish_tool_bridge.doc_merge.word_norm import accept_all, text
from catfish_tool_bridge.doc_merge.word_pkg import w

CTX = Ctx("o@x", {"a@x": "王五", "b@x": "李四", "c@x": "赵六", "o@x": "张三"}, "2026-11-13T09:00:00")


def _base() -> bytes:
    d = docx.Document()
    d.add_heading("XX 项目实施方案", 0)
    d.add_heading("1 项目背景", 1)
    d.add_paragraph("本项目为某市智慧水务平台建设, 覆盖 12 个区县。")
    p = d.add_paragraph("现有系统建于 2015 年, ")
    p.add_run("运维成本高").bold = True
    p.add_run(", 数据分散。")
    d.add_heading("2 实施计划", 1)
    d.add_paragraph("第一阶段: 需求调研, 2 周。")
    d.add_paragraph("第二阶段: 系统开发, 8 周。")
    d.add_paragraph("第三阶段: 上线试运行, 4 周。")
    d.add_heading("3 报价", 1)
    d.add_paragraph("实施周期 3 个月, 总价 48 万。")
    t = d.add_table(rows=4, cols=3)
    t.style = "Table Grid"
    for i, row in enumerate([["项目", "数量", "单价"], ["服务器", "4", "3.5 万"], ["软件许可", "1", "20 万"], ["实施服务", "1", "14 万"]]):
        for j, v in enumerate(row):
            t.cell(i, j).text = v
    d.add_paragraph("以上报价含税。")
    d.sections[0].header.paragraphs[0].text = "XX 公司 · 内部资料"
    b = io.BytesIO()
    d.save(b)
    return b.getvalue()


BASE = _base()


def _edit(fn, data: bytes = BASE) -> bytes:
    d = docx.Document(io.BytesIO(data))
    fn(d)
    b = io.BytesIO()
    d.save(b)
    return b.getvalue()


def _set(p, s: str) -> None:
    for r in p.runs[1:]:
        r._r.getparent().remove(r._r)
    p.runs[0].text = s


def _para(d, start: str):
    return next(p for p in d.paragraphs if p.text.startswith(start))


def _texts(data: bytes) -> list[str]:
    d = docx.Document(io.BytesIO(data))
    return [p.text for p in d.paragraphs]


def _cells(data: bytes) -> list[list[str]]:
    d = docx.Document(io.BytesIO(data))
    return [[c.text for c in r.cells] for r in d.tables[0].rows]


def _body_texts(data: bytes, accept: bool) -> list[str]:
    root = etree.fromstring(zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml"))
    if accept:
        accept_all(root)
    return [text(c) for c in root.find(w("body")) if c.tag in (w("p"), w("tbl"))]


def _revisions(data: bytes) -> Counter:
    root = etree.fromstring(zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml"))
    return Counter(etree.QName(e).localname + ":" + (e.get(w("author")) or "") for e in root.iter()
                   if isinstance(e.tag, str) and etree.QName(e).localname in ("ins", "del", "rPrChange", "pPrChange"))


def _check_tracked(out: dict) -> None:
    """修订版「全部接受」= 干净版。"""
    assert _body_texts(out["tracked"], accept=True) == _body_texts(out["clean"], accept=False)


def _r(who: str, fn, at: str = "2026-11-12T10:00:00") -> Return:
    return Return(who, _edit(fn), at)


def test_不同段落_自动合并_修订作者对():
    rs = [_r("a@x", lambda d: _set(_para(d, "第一阶段"), "第一阶段: 需求调研, 3 周。")),
          _r("b@x", lambda d: _set(_para(d, "第三阶段"), "第三阶段: 上线试运行, 6 周。"), "2026-11-12T11:00:00")]
    an = analyze("docx", BASE, rs, CTX)
    assert an["conflicts"] == [] and an["changes"] == {"a@x": 1, "b@x": 1} and an["same"] == []
    out = render("docx", BASE, rs, {}, CTX)
    t = _texts(out["clean"])
    assert "第一阶段: 需求调研, 3 周。" in t and "第三阶段: 上线试运行, 6 周。" in t
    _check_tracked(out)
    rev = _revisions(out["tracked"])
    assert rev["ins:王五"] >= 1 and rev["del:王五"] >= 1 and rev["ins:李四"] >= 1


def test_同一段改法不同_冲突_拼合建议_三种定法():
    rs = [_r("a@x", lambda d: _set(_para(d, "实施周期"), "实施周期 4 个月, 总价 48 万。")),
          _r("b@x", lambda d: _set(_para(d, "实施周期"), "实施周期 3 个月, 总价 52 万 (含培训)。"))]
    an = analyze("docx", BASE, rs, CTX)
    assert len(an["conflicts"]) == 1
    c = an["conflicts"][0]
    assert c["where"] == "「3 报价」下第 1 段"
    assert c["base"] == "实施周期 3 个月, 总价 48 万。"
    assert [o["key"] for o in c["options"]] == ["a@x", "b@x"]
    assert c["combined"] == "实施周期 4 个月, 总价 52 万 (含培训)。"
    with pytest.raises(Undecided):
        render("docx", BASE, rs, {}, CTX)
    assert "实施周期 3 个月, 总价 52 万 (含培训)。" in _texts(render("docx", BASE, rs, {c["cid"]: {"pick": "b@x"}}, CTX)["clean"])
    assert "实施周期 3 个月, 总价 48 万。" in _texts(render("docx", BASE, rs, {c["cid"]: {"pick": "base"}}, CTX)["clean"])
    out = render("docx", BASE, rs, {c["cid"]: {"pick": "text", "text": c["combined"]}}, CTX)
    assert c["combined"] in _texts(out["clean"])
    _check_tracked(out)
    assert _revisions(out["tracked"])["ins:张三"] >= 1   # 发起人自己定的算他改的


def test_改成一样_不冲突_两人都记上():
    fn = lambda d: _set(_para(d, "实施周期"), "实施周期 4 个月, 总价 48 万。")  # noqa: E731
    an = analyze("docx", BASE, [_r("a@x", fn), _r("b@x", fn)], CTX)
    assert an["conflicts"] == [] and an["changes"] == {"a@x": 1, "b@x": 1}


def test_没改动的交回():
    an = analyze("docx", BASE, [Return("a@x", BASE), _r("b@x", lambda d: _set(_para(d, "第一阶段"), "x"))], CTX)
    assert an["same"] == ["a@x"]


def test_表格_不同格子不冲突_同一格冲突():
    def a(d):
        d.tables[0].cell(1, 2).text = "3.8 万"
    def b(d):
        d.tables[0].cell(2, 1).text = "2"
    def c(d):
        d.tables[0].cell(1, 2).text = "4 万"
    an = analyze("docx", BASE, [_r("a@x", a), _r("b@x", b)], CTX)
    assert an["conflicts"] == []
    out = render("docx", BASE, [_r("a@x", a), _r("b@x", b)], {}, CTX)
    assert _cells(out["clean"])[1:3] == [["服务器", "4", "3.8 万"], ["软件许可", "2", "20 万"]]
    _check_tracked(out)
    an = analyze("docx", BASE, [_r("a@x", a), _r("c@x", c)], CTX)
    assert len(an["conflicts"]) == 1 and "第 2 行第 3 列" in an["conflicts"][0]["where"]


def test_表头改了_别人改了格子_冲突不丢():
    def a(d):
        d.tables[0].cell(0, 2).text = "单价 (含税)"
    def b(d):
        d.tables[0].cell(3, 2).text = "16 万"
    an = analyze("docx", BASE, [_r("a@x", a), _r("b@x", b)], CTX)
    assert len(an["conflicts"]) == 1
    assert {o["key"] for o in an["conflicts"][0]["options"]} == {"a@x", "b@x"}


def test_同一处各插一段_都保留按交回先后():
    def a(d):
        _para(d, "第二阶段").insert_paragraph_before("第一阶段补充: 现场勘察。")
    def b(d):
        _para(d, "第二阶段").insert_paragraph_before("第一阶段补充: 访谈各区县。")
    rs = [_r("a@x", a), _r("b@x", b, "2026-11-12T12:00:00")]
    assert analyze("docx", BASE, rs, CTX)["conflicts"] == []
    t = _texts(render("docx", BASE, rs, {}, CTX)["clean"])
    i = t.index("第一阶段补充: 现场勘察。")
    assert t[i + 1] == "第一阶段补充: 访谈各区县。" and t[i + 2].startswith("第二阶段")


def test_一个删一个改_冲突():
    def a(d):
        p = _para(d, "第三阶段")._p
        p.getparent().remove(p)
    rs = [_r("a@x", a), _r("b@x", lambda d: _set(_para(d, "第三阶段"), "第三阶段: 上线试运行, 6 周。"))]
    an = analyze("docx", BASE, rs, CTX)
    assert len(an["conflicts"]) == 1
    c = an["conflicts"][0]
    out = render("docx", BASE, rs, {c["cid"]: {"pick": "a@x"}}, CTX)
    assert not any(x.startswith("第三阶段") for x in _texts(out["clean"]))
    _check_tracked(out)


def test_只调格式_对上改文字_用改文字的并警告():
    def a(d):
        for r in _para(d, "第二阶段").runs:
            r.bold = True
    rs = [_r("a@x", a), _r("b@x", lambda d: _set(_para(d, "第二阶段"), "第二阶段: 系统开发, 10 周。"))]
    an = analyze("docx", BASE, rs, CTX)
    assert an["conflicts"] == []
    assert any("王五 只调了格式" in x for x in an["warnings"])
    assert "第二阶段: 系统开发, 10 周。" in _texts(render("docx", BASE, rs, {}, CTX)["clean"])


def test_只调格式_一个人_合进来():
    def a(d):
        for r in _para(d, "第二阶段").runs:
            r.bold = True
    out = render("docx", BASE, [_r("a@x", a)], {}, CTX)
    p = _para(docx.Document(io.BytesIO(out["clean"])), "第二阶段")
    assert all(r.bold for r in p.runs)
    assert _revisions(out["tracked"])["rPrChange:王五"] >= 1


def test_批注_只加批注也合进来_两人批注同一段都在():
    def a(d):
        p = _para(d, "实施周期")
        d.add_comment(p.runs, text="总价要再核一下", author="王五")
    def b(d):
        p = _para(d, "实施周期")
        d.add_comment(p.runs, text="周期偏紧", author="李四")
    rs = [_r("a@x", a), _r("b@x", b)]
    an = analyze("docx", BASE, rs, CTX)
    assert an["conflicts"] == [] and an["same"] == []
    out = render("docx", BASE, rs, {}, CTX)
    d = docx.Document(io.BytesIO(out["clean"]))
    texts = sorted(c.text for c in d.comments)
    assert texts == ["周期偏紧", "总价要再核一下"]
    root = etree.fromstring(zipfile.ZipFile(io.BytesIO(out["clean"])).read("word/document.xml"))
    refs = {e.get(w("id")) for e in root.iter(w("commentReference"))}
    assert refs == {c.comment_id for c in d.comments} or len(refs) == 2


def test_批注所在段被别人改了_批注锚到新段():
    def a(d):
        d.add_comment(_para(d, "第一阶段").runs, text="两周不够", author="王五")
    rs = [_r("a@x", a), _r("b@x", lambda d: _set(_para(d, "第一阶段"), "第一阶段: 需求调研, 3 周。"))]
    out = render("docx", BASE, rs, {}, CTX)
    d = docx.Document(io.BytesIO(out["clean"]))
    assert [c.text for c in d.comments] == ["两周不够"]
    root = etree.fromstring(zipfile.ZipFile(io.BytesIO(out["clean"])).read("word/document.xml"))
    p = next(x for x in root.iter(w("p")) if text(x).startswith("第一阶段"))
    assert p.find(f".//{w('commentReference')}") is not None


def _png() -> bytes:
    from PIL import Image  # noqa: PLC0415
    b = io.BytesIO()
    Image.new("RGB", (40, 20), (200, 30, 30)).save(b, "PNG")
    return b.getvalue()


def test_新加的图片_带进合并版():
    png = _png()
    def a(d):
        _para(d, "以上报价").insert_paragraph_before().add_run().add_picture(io.BytesIO(png))
    rs = [_r("a@x", a), _r("b@x", lambda d: _set(_para(d, "第一阶段"), "第一阶段: 需求调研, 3 周。"))]
    out = render("docx", BASE, rs, {}, CTX)
    for data in (out["clean"], out["tracked"]):
        d = docx.Document(io.BytesIO(data))
        blobs = [p.blob for p in d.part.package.parts if p.partname.startswith("/word/media/")]
        assert png in blobs
        root = etree.fromstring(zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml"))
        rid = next(e.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed")
                   for e in root.iter("{http://schemas.openxmlformats.org/drawingml/2006/main}blip"))
        assert d.part.related_parts[rid].blob == png


def test_页眉_合并():
    def a(d):
        d.sections[0].header.paragraphs[0].text = "XX 公司 · 内部资料 · 请勿外传"
    out = render("docx", BASE, [_r("a@x", a), _r("b@x", lambda d: _set(_para(d, "第一阶段"), "x"))], {}, CTX)
    assert docx.Document(io.BytesIO(out["clean"])).sections[0].header.paragraphs[0].text == "XX 公司 · 内部资料 · 请勿外传"


def test_底稿里已有修订_先全部接受再比():
    # 底稿里留着一处没接受的修订 (插入「智慧」两字): 接受后跟交回稿比, 交回稿没动这段就不算改
    def add_rev(d):
        p = _para(d, "本项目为")
        ins = etree.SubElement(p._p, w("ins"))
        ins.set(w("id"), "77")
        ins.set(w("author"), "某人")
        r = etree.SubElement(ins, w("r"))
        t = etree.SubElement(r, w("t"))
        t.text = "(补)"
    base = _edit(add_rev)
    ed = _edit(lambda d: _set(_para(d, "第一阶段"), "第一阶段: 需求调研, 3 周。"), base)
    an = analyze("docx", base, [Return("a@x", ed)], CTX)
    assert an["conflicts"] == [] and an["changes"] == {"a@x": 1}
    out = render("docx", base, [Return("a@x", ed)], {}, CTX)
    assert any(x.endswith("(补)") for x in _texts(out["clean"]))


def test_analyze_render_冲突编号一致():
    rs = [_r("a@x", lambda d: _set(_para(d, "实施周期"), "A")), _r("b@x", lambda d: _set(_para(d, "实施周期"), "B")),
          _r("c@x", lambda d: setattr(d.tables[0].cell(1, 1), "text", "5")),
          _r("o@x", lambda d: setattr(d.tables[0].cell(1, 1), "text", "6"))]
    an1, an2 = analyze("docx", BASE, rs, CTX), analyze("docx", BASE, rs, CTX)
    cids = [c["cid"] for c in an1["conflicts"]]
    assert len(cids) == 2 and cids == [c["cid"] for c in an2["conflicts"]]
    out = render("docx", BASE, rs, {cids[0]: {"pick": "b@x"}, cids[1]: {"pick": "c@x"}}, CTX)
    assert "B" in _texts(out["clean"]) and _cells(out["clean"])[1][1] == "5"
    _check_tracked(out)


SOFFICE = shutil.which("soffice") or ("/Applications/LibreOffice.app/Contents/MacOS/soffice"
                                       if Path("/Applications/LibreOffice.app/Contents/MacOS/soffice").exists() else None)


@pytest.mark.skipif(not SOFFICE, reason="没装 LibreOffice")
def test_别的软件另存_没改不算改_修订版能打开():
    with tempfile.TemporaryDirectory() as td:
        src = Path(td, "in.docx")
        src.write_bytes(BASE)
        out_dir = Path(td, "out")
        subprocess.run([SOFFICE, "--headless", f"-env:UserInstallation=file://{td}/lo", "--convert-to",
                        "docx:MS Word 2007 XML", "--outdir", str(out_dir), str(src)], check=True, capture_output=True, timeout=120)
        resaved = (out_dir / "in.docx").read_bytes()
        an = analyze("docx", BASE, [Return("a@x", resaved), _r("b@x", lambda d: _set(_para(d, "第一阶段"), "第一阶段: 3 周。"))], CTX)
        assert an["same"] == ["a@x"] and an["conflicts"] == []
        # 修订版交给 LibreOffice 打开 (转 odt): 认得出修订, 作者对
        rs = [_r("a@x", lambda d: _set(_para(d, "第一阶段"), "第一阶段: 需求调研, 3 周。")),
              _r("b@x", lambda d: setattr(d.tables[0].cell(1, 2), "text", "3.8 万"))]
        tracked = render("docx", BASE, rs, {}, CTX)["tracked"]
        Path(td, "t.docx").write_bytes(tracked)
        subprocess.run([SOFFICE, "--headless", f"-env:UserInstallation=file://{td}/lo", "--convert-to", "odt",
                        "--outdir", str(out_dir), str(Path(td, "t.docx"))], check=True, capture_output=True, timeout=120)
        content = zipfile.ZipFile(out_dir / "t.odt").read("content.xml").decode("utf-8")
        assert content.count("<text:changed-region") >= 2
        assert "王五" in content and "李四" in content
