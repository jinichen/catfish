"""11/07 类型定义页: 一次改一处, 只写本机补充文件; 内置的能改名 / 隐藏 / 恢复, 不能删。"""
import json

import pytest

from catfish_tool_bridge import wiki_type_defs as defs
from catfish_tool_bridge import wiki_types as wt


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    (tmp_path / "wiki" / "entities").mkdir(parents=True)
    return tmp_path


def _type(view, key):
    return next(t for t in view["types"] if t["key"] == key)


def test_看内置定义_来源标内置(home):
    v = defs.view()
    cert = _type(v, "cert")
    assert cert["origin"] == "builtin" and cert["status"]["warn_days"] == 180
    assert all(p["origin"] == "builtin" and not p["hidden"] for p in cert["properties"])


def test_加属性_改名_删除_都只写本机文件(home):
    v = defs.apply({"op": "add_property", "type": "cert", "property": {"label": "投标用途", "type": "text", "hint": "能用在哪些标"}})
    added = next(p for p in _type(v, "cert")["properties"] if p["label"] == "投标用途")
    assert added["origin"] == "local" and added["key"].startswith("custom_")
    local = json.loads((home / "wiki-type-schema.json").read_text(encoding="utf-8"))
    assert local["types"]["cert"]["properties"][0]["label"] == "投标用途"
    assert "cert_no" in [p["key"] for p in wt.load_schema()["cert"]["properties"]]   # 内置的还在
    v = defs.apply({"op": "update_property", "type": "cert", "key": added["key"], "property": {"label": "适用投标"}})
    assert any(p["label"] == "适用投标" for p in _type(v, "cert")["properties"])
    v = defs.apply({"op": "delete_property", "type": "cert", "key": added["key"]})
    assert not any(p["key"] == added["key"] for p in _type(v, "cert")["properties"])
    assert (home / "wiki-type-schema.json.bak").is_file()


def test_内置属性_改名覆盖_隐藏恢复_不能删(home):
    v = defs.apply({"op": "update_property", "type": "cert", "key": "maintainer", "property": {"label": "资质维护人"}})
    p = next(p for p in _type(v, "cert")["properties"] if p["key"] == "maintainer")
    assert p["label"] == "资质维护人" and p["origin"] == "builtin+local"
    assert not defs.apply({"op": "delete_property", "type": "cert", "key": "maintainer"})["ok"]
    defs.apply({"op": "hide_property", "type": "cert", "key": "maintainer"})
    assert "maintainer" not in [p["key"] for p in wt.load_schema()["cert"]["properties"]]
    v = defs.view()
    assert next(p for p in _type(v, "cert")["properties"] if p["key"] == "maintainer")["hidden"]
    defs.apply({"op": "unhide_property", "type": "cert", "key": "maintainer"})
    assert "maintainer" in [p["key"] for p in wt.load_schema()["cert"]["properties"]]


def test_属性名重复_类型不对_都拒(home):
    assert not defs.apply({"op": "add_property", "type": "cert", "property": {"label": "证书编号"}})["ok"]
    assert not defs.apply({"op": "add_property", "type": "cert", "property": {"label": "x", "type": "number"}})["ok"]
    assert not defs.apply({"op": "add_property", "type": "cert", "property": {"label": ""}})["ok"]
    assert not defs.apply({"op": "add_property", "type": "nope", "property": {"label": "x"}})["ok"]


def test_加行动_要做的事里写属性名会换成值(home):
    v = defs.apply({"op": "add_action", "type": "cert", "action": {"label": "续期准备", "instruction": "按 {有效期至} 和 {证书编号} 列续期材料清单"}})
    act = next(a for a in _type(v, "cert")["actions"] if a["label"] == "续期准备")
    assert act["origin"] == "local"
    assert not defs.apply({"op": "add_action", "type": "cert", "action": {"label": "空的"}})["ok"]
    rel = "wiki/entities/c.md"
    (home / rel).write_text('---\ntype: entity\ntitle: C\nentity_type: cert\nvalid_until: "2028-06-06"\n---\n\n正文\n', encoding="utf-8")
    info = wt.describe(rel)
    got = next(a for a in info["actions"] if a["label"] == "续期准备")["instruction"]
    assert got == "按 2028-06-06 和 （未填） 列续期材料清单"
    defs.apply({"op": "hide_action", "type": "cert", "id": "type.cert.verify-official"})
    assert "type.cert.verify-official" not in [a["id"] for a in wt.describe(rel)["actions"]]
    assert not defs.apply({"op": "delete_action", "type": "cert", "id": "type.cert.expiry-reminder"})["ok"]


def test_改提醒天数(home):
    defs.apply({"op": "set_warn_days", "type": "cert", "warn_days": 240})
    assert wt.load_schema()["cert"]["status"]["warn_days"] == 240
    assert wt.load_schema()["cert"]["status"]["from"] == "valid_until"
    assert not defs.apply({"op": "set_warn_days", "type": "person", "warn_days": 30})["ok"]


def test_新建类型_从知识库已有的细分类型挑(home):
    (home / "wiki" / "entities" / "s.md").write_text("---\ntype: entity\ntitle: S\nentity_type: system\n---\n\n正文\n", encoding="utf-8")
    v = defs.view()
    assert {"key": "system", "count": 1} in v["subtypes"]
    v = defs.apply({"op": "add_type", "type": "system", "label": "业务系统"})
    assert _type(v, "system")["origin"] == "local"
    defs.apply({"op": "add_property", "type": "system", "property": {"label": "上线日期", "type": "date"}})
    assert wt.describe("wiki/entities/s.md")["label"] == "业务系统"
    assert not defs.apply({"op": "add_type", "type": "cert", "label": "x"})["ok"]
    assert not defs.apply({"op": "delete_type", "type": "cert"})["ok"]
    v = defs.apply({"op": "delete_type", "type": "system"})
    assert "system" not in [t["key"] for t in v["types"]]
