"""doc_merge csv 三方合并 (docs/DOC-REVISE-MERGE-PLAN.md §5 csv 行)。数据都是虚构的报价表。"""
from __future__ import annotations

import codecs

import pytest

from catfish_tool_bridge.doc_merge import Ctx, Return, analyze, core, render
from catfish_tool_bridge.doc_merge import table_csv

WW, LS, ZL, ZS = "wangwu@example.com", "lisi@example.com", "zhaoliu@example.com", "zhangsan@example.com"
CTX = Ctx(owner=ZS, names={WW: "王五", LS: "李四", ZL: "赵六", ZS: "张三"})

HEAD = ["序号", "项目", "单价", "数量", "备注"]
ROWS = [["平台", "48", "1", ""], ["培训", "5", "2", "按人天"], ["实施", "20", "3", ""], ["运维", "8", "4", ""]]


def csv_of(rows, *, enc="utf-8", nl="\r\n", delim=",", bom=False) -> bytes:
    lines = [delim.join(HEAD)] + [delim.join([str(i)] + r) for i, r in enumerate(rows, 1)]
    text = nl.join(lines) + nl
    data = text.encode(enc)
    return codecs.BOM_UTF8 + data if bom else data


def changed(**kw):
    out = []
    for r in ROWS:
        r = list(r)
        if r[0] in kw:
            for i, v in kw[r[0]].items():
                r[i] = v
        out.append(r)
    return out


BASE = csv_of(ROWS)


def lines(b: bytes, enc="utf-8") -> list[str]:
    return b.decode(enc).splitlines()


def test_different_cells_and_same_value():
    a = csv_of(changed(平台={1: "52"}))
    b = csv_of(changed(实施={2: "4"}, 平台={1: "52"}))
    res = analyze("csv", BASE, [Return(WW, a), Return(LS, b)], CTX)
    assert res["conflicts"] == [] and res["changes"] == {WW: 1, LS: 2}
    out = render("csv", BASE, [Return(WW, a), Return(LS, b)], {}, CTX)
    assert out["tracked"] == out["clean"]
    assert lines(out["clean"])[1:4] == ["1,平台,52,1,", "2,培训,5,2,按人天", "3,实施,20,4,"]


def test_conflict_decisions_and_where():
    a = csv_of(changed(平台={1: "52"}))
    b = csv_of(changed(平台={1: "55"}))
    rets = [Return(WW, a), Return(LS, b)]
    res = analyze("csv", BASE, rets, CTX)
    (c,) = res["conflicts"]
    assert c["where"] == "表格 · C2（项目=平台，列「单价」）" and c["base"] == "48"
    assert [o["text"] for o in c["options"]] == ["52", "55"]
    with pytest.raises(core.Undecided):
        render("csv", BASE, rets, {}, CTX)
    assert lines(render("csv", BASE, rets, {c["cid"]: {"pick": LS}}, CTX)["clean"])[1] == "1,平台,55,1,"
    assert lines(render("csv", BASE, rets, {c["cid"]: {"pick": "base"}}, CTX)["clean"])[1] == "1,平台,48,1,"
    out = render("csv", BASE, rets, {c["cid"]: {"pick": "text", "text": " 50 "}}, CTX)
    assert lines(out["clean"])[1] == "1,平台,50,1," and out["changes"] == {ZS: 1}
    assert analyze("csv", BASE, rets, CTX)["conflicts"][0]["cid"] == c["cid"]


def test_insert_delete_reorder_by_key_and_serial_renumbered():
    a = csv_of(ROWS[:1] + [["咨询", "10", "1", ""]] + ROWS[1:])        # 插行, 序号重编
    b = csv_of([r for r in ROWS if r[0] != "运维"])                     # 删行
    c = csv_of(sorted(changed(培训={2: "6"}), key=lambda r: -int(r[1])))   # 排序 + 改培训数量
    rets = [Return(WW, a), Return(LS, b), Return(ZL, c)]
    res = analyze("csv", BASE, rets, CTX)
    assert res["conflicts"] == [] and res["changes"] == {WW: 1, LS: 1, ZL: 2}
    out = lines(render("csv", BASE, rets, {}, CTX)["clean"])
    assert out == ["序号,项目,单价,数量,备注", "1,平台,48,1,", "2,咨询,10,1,", "3,实施,20,3,", "4,培训,5,6,按人天"]


def test_delete_vs_modify_and_two_inserts_same_place():
    a = csv_of([r for r in ROWS if r[0] != "运维"])
    b = csv_of(changed(运维={1: "9"}))
    res = analyze("csv", BASE, [Return(WW, a), Return(LS, b)], CTX)
    (c,) = res["conflicts"]
    assert c["options"][0]["text"] == "删除这一行"
    out = lines(render("csv", BASE, [Return(WW, a), Return(LS, b)], {c["cid"]: {"pick": WW}}, CTX)["clean"])
    assert not any("运维" in ln for ln in out)
    x = csv_of(ROWS[:2] + [["咨询", "10", "1", ""]] + ROWS[2:])
    y = csv_of(ROWS[:2] + [["差旅", "3", "2", ""]] + ROWS[2:])
    out = lines(render("csv", BASE, [Return(WW, x), Return(LS, y), Return(ZL, x)], {}, CTX)["clean"])
    assert [ln.split(",")[1] for ln in out[1:]] == ["平台", "培训", "咨询", "差旅", "实施", "运维"]


def test_unchanged_is_same_and_bytes_identical():
    res = analyze("csv", BASE, [Return(WW, BASE)], CTX)
    assert res["same"] == [WW] and res["changes"] == {}
    assert render("csv", BASE, [Return(WW, BASE)], {}, CTX)["clean"] == BASE


def test_gb18030_roundtrip_keeps_encoding_and_newlines():
    base = csv_of(ROWS, enc="gb18030", nl="\r\n")
    with pytest.raises(UnicodeDecodeError):
        base.decode("utf-8")
    # 一个人没改: 逐字节一样
    assert render("csv", base, [Return(WW, base)], {}, CTX)["clean"] == base
    # 有人改了 (他用 utf-8 + BOM、\n 存的): 写回还是 gb18030 + \r\n, 没改的行原样
    a = csv_of(changed(培训={3: "按人天 (含教材)"}), enc="utf-8", nl="\n", bom=True)
    out = render("csv", base, [Return(WW, a)], {}, CTX)["clean"]
    assert not out.startswith(codecs.BOM_UTF8)
    text = out.decode("gb18030")
    assert "\r\n" in text and text.count("\r\n") == len(ROWS) + 1
    assert text.splitlines()[2] == "2,培训,5,2,按人天 (含教材)"
    assert out.replace("按人天 (含教材)".encode("gb18030"), "按人天".encode("gb18030")) == base


def test_utf8_bom_semicolon_and_lf_preserved():
    base = csv_of(ROWS, delim=";", nl="\n", bom=True)
    a = csv_of(changed(平台={1: "52"}), delim=";", nl="\n", bom=True)
    out = render("csv", base, [Return(WW, a)], {}, CTX)["clean"]
    assert out.startswith(codecs.BOM_UTF8) and b"\r\n" not in out
    assert out[3:].decode("utf-8").splitlines()[1] == "1;平台;52;1;"
    rows, d = table_csv.parse(out)
    assert d.delimiter == ";" and d.encoding == "utf-8-sig"


def test_quoted_fields_and_new_column():
    base = '项目,单价,说明\r\n平台,48,"含部署, 培训"\r\n实施,20,\r\n'.encode("utf-8")
    a = '项目,单价,数量,说明\r\n平台,48,1,"含部署, 培训"\r\n实施,20,3,\r\n'.encode("utf-8")
    b = '项目,单价,说明\r\n平台,50,"含部署, 培训"\r\n实施,20,\r\n'.encode("utf-8")
    res = analyze("csv", base, [Return(WW, a), Return(LS, b)], CTX)
    assert res["conflicts"] == [] and res["changes"] == {WW: 1, LS: 1}      # 新增一列算一处
    out = render("csv", base, [Return(WW, a), Return(LS, b)], {}, CTX)["clean"].decode("utf-8")
    assert out == '项目,单价,数量,说明\r\n平台,50,1,"含部署, 培训"\r\n实施,20,3,\r\n'
