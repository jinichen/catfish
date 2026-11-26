"""11/26 知识库本体的约束 (wiki_constraints / wiki_constraints_report): 选项 / 格式 / 日期 / 不晚于今天 / 不重复 / 日期先后 /
关系最多几个 (反向写法一起算) / 不能成环 / 属性跟关系对齐 / 约束检查和一键改 / 所有写入口都过。全部虚构。"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from catfish_tool_bridge import wiki_constraints as wc
from catfish_tool_bridge import wiki_constraints_report as wr
from catfish_tool_bridge import wiki_files, wiki_guard, wiki_type_defs, wiki_types
from catfish_tool_bridge import wiki_write_tools as wt


@pytest.fixture()
def home(tmp_path, monkeypatch):
    h = tmp_path / ".catfish"
    for sub in ("wiki/entities", "wiki/concepts", "wiki/queries"):
        (h / sub).mkdir(parents=True)
    monkeypatch.setenv("CATFISH_HOME", str(h))
    return h


def _page(h: Path, rel: str, title: str, body: str = "正文。", **fm):
    lines = [f"title: {title}", "type: entity"] + [f"{k}: {v}" for k, v in fm.items()]
    (h / rel).write_text("---\n" + "\n".join(lines) + "\n---\n\n" + body + "\n", encoding="utf-8")


def _fm(h: Path, rel: str) -> str:
    return wiki_files._split_frontmatter((h / rel).read_text())[0]


def _prop(type_key: str, key: str) -> dict:
    return next(p for p in wiki_types.load_schema()[type_key]["properties"] if p["key"] == key)


# ── 单个值 ────────────────────────────────────────────────────────

def test_选项_日期_不晚于今天_太长_待核():
    level = _prop("cert", "level")
    assert wc.value_problem(level, "甲级") is None
    assert "只能是" in wc.value_problem(level, "／认证范围／复审")
    assert wc.value_problem(level, "") is None, "空 = 清掉, 总是可以"
    issued = _prop("cert", "issued_on")
    assert "不是日期" in wc.value_problem(issued, "2016-01-29（2016-01-01 实施）")
    assert "不是真实的日期" in wc.value_problem(issued, "2026-02-30")
    assert "不能晚于今天" in wc.value_problem(issued, "2099-01-01", today=date(2026, 11, 26))
    assert wc.value_problem(_prop("cert", "valid_until"), "2099-01-01") is None, "有效期至可以在以后"
    assert "待核" in wc.value_problem(_prop("cert", "cert_no"), "ABC-1（待核）")
    assert "太长了" in wc.value_problem(_prop("cert", "issuer"), "某" * 61)


def test_格式_邮箱电话信用代码网址金额_登记号():
    assert wc.value_problem(_prop("person", "email"), "zhang@example.com") is None
    assert "不是邮箱" in wc.value_problem(_prop("person", "email"), "zhang at example")
    for ok in ("13800138000", "0591-87654321", "+86-13800138000", "400-800-1234"):
        assert wc.value_problem(_prop("person", "phone"), ok) is None, ok
    assert "不是电话" in wc.value_problem(_prop("person", "phone"), "12345")
    assert wc.credit_code_ok("91350100M000100Y43") and not wc.credit_code_ok("91350100M000100Y44")
    assert "校验码" in wc.value_problem(_prop("org", "credit_code"), "91350100M000100Y44")
    assert wc.value_problem(_prop("org", "website"), "https://www.example.com/a") is None
    assert wc.value_problem(_prop("project", "amount"), "120万元") is None
    assert wc.value_problem(_prop("project", "amount"), "1,200,000元") is None
    assert "不是金额" in wc.value_problem(_prop("project", "amount"), "大概一百多万")
    assert wc.value_problem(_prop("software", "reg_no"), "2019SR123456") is None
    assert "SR" in wc.value_problem(_prop("software", "reg_no"), "软著123")


def test_写之前统一写法_引号_大小写_别名():
    cat = _prop("cert", "category")
    assert wiki_types._clean(cat, "“业务资质”") == "业务资质"
    assert wiki_types._clean(cat, "基础资质类") == "基础体系"
    assert wiki_types._clean(_prop("org", "credit_code"), "91350100m000100y43 ") == "91350100M000100Y43"
    assert wiki_types._clean(_prop("person", "email"), "Zhang@Example.com") == "zhang@example.com"


def test_职务不再跟标题同一个字段():
    person = wiki_types.load_schema()["person"]
    keys = {p["key"] for p in person["properties"]}
    assert "job_title" in keys and "title" not in keys
    for tdef in wiki_types.load_schema().values():
        assert not ({p["key"] for p in tdef["properties"]} & wc.RESERVED_KEYS)


def test_本机补充里旧的职务key跟着改(home):
    (home / "wiki-type-schema.json").write_text(
        '{"types": {"person": {"properties": [{"key": "title", "label": "岗位"}], "hidden_properties": ["title"]}}}', encoding="utf-8")
    person = wiki_types.merge_types(wiki_types.load_builtin(), wiki_types.load_local(), keep_hidden=True)["person"]
    job = next(p for p in person["properties"] if p["key"] == "job_title")
    assert job["label"] == "岗位" and job["hidden"] and not any(p["key"] == "title" for p in person["properties"])


# ── 写入口 ────────────────────────────────────────────────────────

def test_改属性_不合规的不写_说原因_合规的写(home):
    _page(home, "wiki/entities/c.md", "某证书", entity_type="cert")
    r = wiki_types.set_props_file("wiki/entities/c.md", {"level": "／认证范围／复审"})
    assert not r["ok"] and "只能是" in r["error"] and r["problems"][0]["key"] == "level"
    assert "level:" not in _fm(home, "wiki/entities/c.md")
    r = wiki_types.set_props_file("wiki/entities/c.md", {"level": "甲级", "issued_on": "2020年1月2日"})
    assert r["ok"] and r["changed"]
    assert 'level: "甲级"' in _fm(home, "wiki/entities/c.md") and 'issued_on: "2020-01-02"' in _fm(home, "wiki/entities/c.md")


def test_日期先后_只查这次改到的(home):
    _page(home, "wiki/entities/c.md", "某证书", entity_type="cert", issued_on='"2020-01-02"')
    r = wiki_types.set_props_file("wiki/entities/c.md", {"valid_until": "2019-12-31"})
    assert not r["ok"] and "颁发日期不能晚于有效期至" in r["error"]
    assert wiki_types.set_props_file("wiki/entities/c.md", {"valid_until": "2023-01-01"})["ok"]
    # 老数据里已经错着的 (复审日期早于颁发), 不挡改别的
    _page(home, "wiki/entities/d.md", "另一张", entity_type="cert", issued_on='"2020-01-02"', review_on='"2019-01-01"')
    assert wiki_types.set_props_file("wiki/entities/d.md", {"issuer": "某认证中心"})["ok"]


def test_编号同类里不能重复(home):
    _page(home, "wiki/entities/a.md", "证书甲", entity_type="cert", cert_no='"CCRC-2024-001"')
    _page(home, "wiki/entities/b.md", "证书乙", entity_type="cert")
    r = wiki_types.set_props_file("wiki/entities/b.md", {"cert_no": "ccrc 2024 001"})
    assert not r["ok"] and "跟「证书甲」重复" in r["error"]
    assert wiki_types.set_props_file("wiki/entities/a.md", {"cert_no": "CCRC-2024-001"})["ok"], "自己跟自己不算重复"


def test_新建条目_坏的属性跳过并点名_好的照写(home):
    r = wt.tool_wiki_create({"kind": "entity", "title": "某项目", "subtype": "project", "body": "x",
                             "props": {"stage": "进行中吧", "customer": "某客户"}})
    assert r["ok"] and r["props"] == {"customer": "某客户"} and "阶段" in r["props_error"]


def test_整篇覆写里改坏的属性也拦(home):
    _page(home, "wiki/entities/c.md", "某证书", entity_type="cert", level='"二级"')
    content = (home / "wiki/entities/c.md").read_text().replace('level: "二级"', 'level: "超级"')
    r = wt.tool_wiki_update({"rel_path": "wiki/entities/c.md", "content": content})
    assert not r["ok"] and "属性不对" in r["error"]
    ok = (home / "wiki/entities/c.md").read_text().replace("正文。", "正文改了。")
    assert wt.tool_wiki_update({"rel_path": "wiki/entities/c.md", "content": ok})["ok"]


def test_整理属性_抽出来不在选项里的不当建议(home):
    _page(home, "wiki/entities/c.md", "某证书", entity_type="cert", body="等级：与本证书一致。证书编号：ABC-123。")
    got = wiki_types._regex_extract("等级：与本证书一致。", wiki_types.load_schema()["cert"])
    assert "level" not in got


# ── 关系: 最多几个 / 成环 ─────────────────────────────────────────

def test_一个人最多隶属一个部门_反向的包含也算(home):
    _page(home, "wiki/entities/z.md", "张三", entity_type="person", related='[{name: "企发部", rel: "隶属"}]')
    _page(home, "wiki/entities/q.md", "企发部", entity_type="department")
    _page(home, "wiki/entities/r.md", "人力部", entity_type="department")
    bad, _ = wiki_guard.relation_problems("person", [{"name": "企发部", "rel": "隶属"}, {"name": "人力部", "rel": "隶属"}],
                                          self_title="张三", rel_path="wiki/entities/z.md")
    assert len(bad) == 1 and "最多隶属1个" in bad[0]
    # 换部门 (删旧的加新的) 不拦
    assert wiki_guard.relation_problems("person", [{"name": "人力部", "rel": "隶属"}], self_title="张三",
                                        rel_path="wiki/entities/z.md") == ([], [])
    # 人力部那页写「包含 张三」= 张三又隶属人力部
    bad, _ = wiki_guard.relation_problems("department", [{"name": "张三", "rel": "包含"}], self_title="人力部",
                                          rel_path="wiki/entities/r.md")
    assert len(bad) == 1 and "张三" in bad[0]


def test_老数据里已经超了的_不挡无关的修改(home):
    _page(home, "wiki/entities/z.md", "张三", entity_type="person",
          related='[{name: "企发部", rel: "隶属"}, {name: "人力部", rel: "隶属"}, {name: "李四", rel: "协作"}]')
    _page(home, "wiki/entities/q.md", "企发部", entity_type="department")
    _page(home, "wiki/entities/r.md", "人力部", entity_type="department")
    _page(home, "wiki/entities/l.md", "李四", entity_type="person")
    rel = [{"name": "企发部", "rel": "隶属"}, {"name": "人力部", "rel": "隶属"}]
    assert wiki_guard.relation_problems("person", rel, self_title="张三", rel_path="wiki/entities/z.md") == ([], [])
    rep = wr.report()
    assert rep["summary"].get("relation_max") == 1


def test_隶属不能绕成环(home):
    _page(home, "wiki/entities/a.md", "一部", entity_type="department", related='[{name: "二部", rel: "隶属"}]')
    _page(home, "wiki/entities/b.md", "二部", entity_type="department", related='[{name: "三部", rel: "隶属"}]')
    _page(home, "wiki/entities/c.md", "三部", entity_type="department")
    bad, _ = wiki_guard.relation_problems("department", [{"name": "一部", "rel": "隶属"}], self_title="三部",
                                          rel_path="wiki/entities/c.md")
    assert len(bad) == 1 and "绕成环" in bad[0]
    # 三部「包含」二部 跟「二部 隶属 三部」是同一条 (反向写法), 不成环也不多出上级
    assert wiki_guard.relation_problems("department", [{"name": "二部", "rel": "包含"}], self_title="三部",
                                        rel_path="wiki/entities/c.md")[0] == []
    # 三部「包含」一部 = 一部又多一个上级
    assert "最多隶属1个" in wiki_guard.relation_problems("department", [{"name": "一部", "rel": "包含"}], self_title="三部",
                                                       rel_path="wiki/entities/c.md")[0][0]
    r = wt.tool_wiki_update({"rel_path": "wiki/entities/c.md",
                             "content": "---\ntitle: 三部\ntype: entity\nentity_type: department\n"
                                        'related: [{name: "一部", rel: "隶属"}]\n---\n\n正文。\n'})
    assert not r["ok"] and "绕成环" in r["error"]


def test_蒸馏那条路_没给rel_path按标题认_不会平白多出上级(home):
    _page(home, "wiki/entities/z.md", "张三", entity_type="person", related='[{name: "企发部", rel: "隶属"}]')
    _page(home, "wiki/entities/q.md", "企发部", entity_type="department")
    assert wiki_guard.bad_relations("person", [{"name": "企发部", "rel": "隶属"}], self_title="张三") == ([], [])


# ── 属性 ↔ 关系 ─────────────────────────────────────────────────

def test_写了部门就顺手补上隶属关系(home):
    _page(home, "wiki/entities/z.md", "张三", entity_type="person", related="[]")
    _page(home, "wiki/entities/q.md", "企发部", entity_type="department")
    r = wiki_types.set_props_file("wiki/entities/z.md", {"department": "企发部"})
    assert r["ok"] and r["aligned"][0]["result"] == "added"
    assert wiki_files._parse_related(_fm(home, "wiki/entities/z.md")) == [{"name": "企发部", "rel": "隶属"}]


def test_持有单位_关系补在公司那一页(home):
    _page(home, "wiki/entities/c.md", "ISO 9001", entity_type="cert")
    _page(home, "wiki/entities/o.md", "某公司", entity_type="org", related="[]")
    r = wiki_types.set_props_file("wiki/entities/c.md", {"holder": "某公司"})
    assert r["aligned"][0]["result"] == "added"
    assert {"name": "ISO 9001", "rel": "持有"} in wiki_files._parse_related(_fm(home, "wiki/entities/o.md"))


def test_已经隶属别的部门_不自动改关系_约束检查里给两种改法(home):
    _page(home, "wiki/entities/z.md", "张三", entity_type="person", related='[{name: "人力部", rel: "隶属"}]')
    _page(home, "wiki/entities/q.md", "企发部", entity_type="department")
    _page(home, "wiki/entities/r.md", "人力部", entity_type="department")
    r = wiki_types.set_props_file("wiki/entities/z.md", {"department": "企发部"})
    assert r["aligned"][0]["result"] == "skipped"
    issue = next(i for i in wr.report()["issues"] if i["kind"] == "link_conflict")
    assert issue["fix"] == {"op": "replace_relation", "rel": "隶属", "old": "人力部", "name": "企发部"}
    assert issue["alt_fix"]["value"] == "人力部"
    out = wr.fix({"items": [{**issue["fix"], "rel_path": issue["rel_path"]}]})
    assert out["ok"] and out["done"] == 1
    assert wiki_files._parse_related(_fm(home, "wiki/entities/z.md")) == [{"name": "企发部", "rel": "隶属"}]


def test_约束检查_关系里有属性没填_一键填(home):
    _page(home, "wiki/entities/z.md", "张三", entity_type="person", related='[{name: "企发部", rel: "隶属"}]')
    _page(home, "wiki/entities/q.md", "企发部", entity_type="department", level='"超级部"')
    rep = wr.report()
    assert rep["summary"]["link_prop"] == 1 and rep["summary"]["bad_value"] == 1
    assert rep["summary"]["missing_required"] >= 1
    issue = next(i for i in rep["issues"] if i["kind"] == "link_prop")
    assert wr.fix({"items": [{**issue["fix"], "rel_path": issue["rel_path"]}]})["ok"]
    assert 'department: "企发部"' in _fm(home, "wiki/entities/z.md")
    # 一键改照样过写入校验
    bad = next(i for i in rep["issues"] if i["kind"] == "bad_value")
    out = wr.fix({"items": [{**bad["fix"], "value": "也不对", "rel_path": bad["rel_path"]}]})
    assert not out["ok"] and "只能是" in out["errors"][0]["error"]


def test_约束检查_重复_日期先后(home):
    _page(home, "wiki/entities/a.md", "证书甲", entity_type="cert", cert_no='"X-1"', issued_on='"2022-01-01"', valid_until='"2021-01-01"')
    _page(home, "wiki/entities/b.md", "证书乙", entity_type="cert", cert_no='"x 1"')
    rep = wr.report({"type": "cert"})
    assert rep["summary"]["duplicate"] == 2 and rep["summary"]["rule"] == 1


def test_小鲶用_query_check(home):
    _page(home, "wiki/entities/a.md", "证书甲", entity_type="cert")
    r = wiki_types.tool_dispatch("catfish_wiki_query", {"type": "cert", "check": True})
    assert r["ok"] and r["summary"]["missing_required"] == 1


def test_属性卡片_带选项必填和老数据的问题(home):
    _page(home, "wiki/entities/a.md", "证书甲", entity_type="cert", level='"与本证书一致"')
    info = wiki_types.describe("wiki/entities/a.md")
    level = next(p for p in info["properties"] if p["key"] == "level")
    assert level["options"] and "不在选项里" in level["problem"]
    assert "证书编号" in info["missing_required"]


# ── 类型定义页 ──────────────────────────────────────────────────

def test_类型定义页能改选项必填不重复格式(home):
    v = wiki_type_defs.apply({"op": "update_property", "type": "org", "key": "contact",
                              "property": {"options": "张三、李四", "required": True, "unique": True, "format": ""}})
    contact = next(p for t in v["types"] if t["key"] == "org" for p in t["properties"] if p["key"] == "contact")
    assert contact["options"] == ["张三", "李四"] and contact["required"] and contact["unique"]
    assert v["formats"]["email"] == "邮箱"
    assert any("最多隶属" in x for t in v["types"] if t["key"] == "person" for x in t["relation_limits"])
    assert not wiki_type_defs.apply({"op": "update_property", "type": "org", "key": "contact", "property": {"format": "qq"}})["ok"]
    assert not wiki_type_defs.apply({"op": "update_property", "type": "cert", "key": "valid_until",
                                     "property": {"options": ["2027-01-01"]}})["ok"]
