"""群里一起改文档 · 合并核心 (doc_merge/core.py)、文本合并 (text.py)、文件里的记号 (marker.py)。"""
from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
from pathlib import Path

import docx
import pytest

from catfish_tool_bridge.doc_merge import Ctx, Return, Undecided, analyze, fmt_of, marker, render
from catfish_tool_bridge.doc_merge.core import Unit, combine_text, merge_seq

CTX = Ctx("o@x", {"a@x": "王五", "b@x": "李四"})


def U(s: str) -> list[Unit]:
    return [Unit(x, x) for x in s]


def _view(segs):
    return [(s.kind, s.start, s.end, "".join(u.text for u in s.new), [(o.key, "".join(u.text for u in o.units)) for o in s.options])
            for s in segs if s.kind != "keep"]


def test_核心_都跟底稿比_不是两两依次合():
    # A 改 c、C 也改 c (不同) → 冲突; B 改 f → 直接合
    segs = merge_seq(U("abcdefg"), {"A": U("abXdefg"), "B": U("abcdeYg"), "C": U("abZdefg")}, ["A", "B", "C"])
    assert _view(segs) == [("conflict", 2, 3, "", [("A", "X"), ("C", "Z")]), ("change", 5, 6, "Y", [])]


def test_核心_首尾相接不算压到():
    segs = merge_seq(U("abcdefg"), {"A": U("abdefg"), "B": U("abcXefg")}, ["A", "B"])
    assert [s[0] for s in _view(segs)] == ["change", "change"]


def test_核心_同一处各插_都留_一样的只留一份():
    segs = merge_seq(U("abc"), {"A": U("ab1c"), "B": U("ab2c"), "C": U("ab1c")}, ["A", "B", "C"])
    ch = [s for s in segs if s.kind == "change"]
    assert [("".join(u.text for u in s.new), s.who) for s in ch] == [("1", ["A", "C"]), ("2", ["B"])]


def test_核心_拼合建议():
    assert combine_text("实施周期 3 个月, 总价 48 万。", {"A": "实施周期 4 个月, 总价 48 万。", "B": "实施周期 3 个月, 总价 52 万 (含培训)。"}) \
        == "实施周期 4 个月, 总价 52 万 (含培训)。"
    assert combine_text("总价 48 万", {"A": "总价 50 万", "B": "总价 52 万"}) is None   # 改到同一个数: 拼不了


def test_格式识别():
    assert fmt_of("方案.DOCX") == "docx" and fmt_of("a.md") == "text" and fmt_of("x.csv") == "csv"
    assert fmt_of("a.pdf") is None and fmt_of("a.doc") is None


BASE_MD = "# 周报\n\n本周完成:\n- 接口联调\n- 测试用例 30 条\n\n下周计划:\n- 上线\n".encode("utf-8")


def test_文本_不同行合并_同一行冲突():
    a = BASE_MD.replace("测试用例 30 条".encode(), "测试用例 45 条".encode())
    b = BASE_MD.replace("- 上线\n".encode(), "- 上线\n- 培训\n".encode())
    out = render("text", BASE_MD, [Return("a@x", a), Return("b@x", b)], {}, CTX)
    assert out["clean"].decode() == "# 周报\n\n本周完成:\n- 接口联调\n- 测试用例 45 条\n\n下周计划:\n- 上线\n- 培训\n"
    c = BASE_MD.replace("测试用例 30 条".encode(), "测试用例 40 条".encode())
    an = analyze("text", BASE_MD, [Return("a@x", a), Return("b@x", c)], CTX)
    assert len(an["conflicts"]) == 1 and an["conflicts"][0]["where"] == "第 5 行"
    cid = an["conflicts"][0]["cid"]
    with pytest.raises(Undecided):
        render("text", BASE_MD, [Return("a@x", a), Return("b@x", c)], {}, CTX)
    got = render("text", BASE_MD, [Return("a@x", a), Return("b@x", c)], {cid: {"pick": "text", "text": "- 测试用例 50 条"}}, CTX)
    assert "测试用例 50 条" in got["clean"].decode()


def test_文本_保持编码和换行():
    base = "第一行\r\n第二行\r\n".encode("gb18030")
    a = "第一行\r\n第二行改\r\n".encode("gb18030")
    out = render("text", base, [Return("a@x", a)], {}, CTX)
    assert out["clean"] == a


@pytest.mark.skipif(not shutil.which("git"), reason="没装 git")
def test_文本_跟_git_merge_file_一致():
    base = "\n".join(f"第 {i} 行" for i in range(1, 21)).encode() + b"\n"
    a = base.replace("第 3 行".encode(), "第 3 行 A".encode()).replace("第 15 行".encode(), "第 15 行 A".encode())
    b = base.replace("第 8 行".encode(), "第 8 行 B".encode()).replace("第 20 行\n".encode(), "第 20 行\n第 21 行\n".encode())
    with tempfile.TemporaryDirectory() as td:
        for n, d in (("a", a), ("o", base), ("b", b)):
            Path(td, n).write_bytes(d)
        subprocess.run(["git", "merge-file", "-p", "a", "o", "b"], cwd=td, check=True, capture_output=True)
        expect = subprocess.run(["git", "merge-file", "-p", "a", "o", "b"], cwd=td, capture_output=True).stdout
    assert render("text", base, [Return("a@x", a), Return("b@x", b)], {}, CTX)["clean"] == expect


def _docx() -> bytes:
    d = docx.Document()
    d.add_paragraph("测试")
    b = io.BytesIO()
    d.save(b)
    return b.getvalue()


def test_记号_埋_读_去掉():
    data = _docx()
    assert marker.read(data) is None
    got = marker.embed(data, "r_20261111_ab12cd:v1")
    assert marker.read(got) == "r_20261111_ab12cd:v1"
    assert docx.Document(io.BytesIO(got)).paragraphs[0].text == "测试"
    again = marker.embed(got, "r_20261111_ab12cd:v2")
    assert marker.read(again) == "r_20261111_ab12cd:v2"
    clean = marker.strip(again)
    assert marker.read(clean) is None
    docx.Document(io.BytesIO(clean))
    assert marker.read(b"not a zip") is None
    with pytest.raises(ValueError):
        marker.embed(data, "张三")


SOFFICE = shutil.which("soffice") or ("/Applications/LibreOffice.app/Contents/MacOS/soffice"
                                       if Path("/Applications/LibreOffice.app/Contents/MacOS/soffice").exists() else None)


@pytest.mark.skipif(not SOFFICE, reason="没装 LibreOffice")
def test_记号_别的软件另存还在():
    got = marker.embed(_docx(), "r_20261111_ab12cd:v1")
    with tempfile.TemporaryDirectory() as td:
        Path(td, "in.docx").write_bytes(got)
        subprocess.run([SOFFICE, "--headless", f"-env:UserInstallation=file://{td}/lo", "--convert-to", "docx:MS Word 2007 XML",
                        "--outdir", str(Path(td, "out")), str(Path(td, "in.docx"))], check=True, capture_output=True, timeout=120)
        assert marker.read(Path(td, "out", "in.docx").read_bytes()) == "r_20261111_ab12cd:v1"
