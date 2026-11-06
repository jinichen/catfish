"""11/07 知识库类型定义: 属性读写 / 状态 / 按属性查 / 从正文抽属性 / 写入前备份。"""
import json
from datetime import date

import pytest

from catfish_tool_bridge import wiki_types as wt

CERT = """---
type: entity
title: CCRC 信息安全服务资质软件安全开发一级
entity_type: cert
tags: ['信息安全类']
related: []
---

# CCRC 信息安全服务资质软件安全开发一级

中电福富信息科技有限公司持有的CCRC 信息安全服务资质软件安全开发一级。证书编号CCRC-2019-ISV-SD-210，由中国网络安全审查认证和市场监管大数据中心于2025-06-07颁发，有效期至2028-06-06，等级一级。归类为业务资质，维护人谢竞翔。
"""


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    (tmp_path / "wiki" / "entities").mkdir(parents=True)
    return tmp_path


def _write(home, name, text):
    p = home / "wiki" / "entities" / name
    p.write_text(text, encoding="utf-8")
    return f"wiki/entities/{name}"


def test_内置定义有证书_属性含认证范围和复审日期():
    cert = wt.load_schema()["cert"]
    keys = [p["key"] for p in cert["properties"]]
    assert {"cert_no", "issuer", "valid_until", "review_on", "scope", "maintainer"} <= set(keys)
    assert cert["status"]["warn_days"] == 180
    assert [a["label"] for a in cert["actions"]] == ["官网核查", "到期提醒"]


def test_按固定句式抽属性(home):
    tdef = wt.load_schema()["cert"]
    _fm, body = wt.wiki_files._split_frontmatter(CERT)
    got = wt._regex_extract(body, tdef)
    assert got == {
        "cert_no": "CCRC-2019-ISV-SD-210", "issuer": "中国网络安全审查认证和市场监管大数据中心",
        "issued_on": "2025-06-07", "valid_until": "2028-06-06", "level": "一级",
        "holder": "中电福富信息科技有限公司", "category": "业务资质", "maintainer": "谢竞翔",
    }


def test_编号待核不当编号_编号后面的中文括号不带进来():
    tdef = wt.load_schema()["cert"]
    assert "cert_no" not in wt._regex_extract("证书编号待核：资质列表录为 X", tdef)
    assert wt._regex_extract("正确编号为 x，证书编号CESSCN-2025-RA-C-104（按原件）", tdef)["cert_no"] == "CESSCN-2025-RA-C-104"


def test_写属性只动文件头_正文不变_空值删行():
    tdef = wt.load_schema()["cert"]
    out = wt.set_props(CERT, {"cert_no": "A-1", "valid_until": "2028年6月6日"}, tdef)
    assert 'cert_no: "A-1"' in out and 'valid_until: "2028-06-06"' in out
    assert out.split("\n---", 1)[1] == CERT.split("\n---", 1)[1]
    out2 = wt.set_props(out, {"cert_no": ""}, tdef)
    assert "cert_no:" not in out2
    with pytest.raises(ValueError):
        wt.set_props(CERT, {"nope": "x"}, tdef)


def test_状态按有效期自动算_180天内算即将到期():
    tdef = wt.load_schema()["cert"]
    today = date(2026, 11, 7)
    assert wt.status_of({"valid_until": "2028-06-06"}, tdef, today)["value"] == "有效"
    assert wt.status_of({"valid_until": "2027-03-01"}, tdef, today)["value"] == "即将到期"
    assert wt.status_of({"valid_until": "2026-01-01"}, tdef, today)["value"] == "已过期"
    assert wt.status_of({}, tdef, today)["value"] == "有效期未填"


def test_类型上的行动_参数从属性里取(home):
    rel = _write(home, "a.md", wt.set_props(CERT, {"cert_no": "CCRC-1", "issuer": "认证中心"}, wt.load_schema()["cert"]))
    info = wt.describe(rel)
    assert info["typed"] and info["label"] == "资质证书"
    verify = next(a for a in info["actions"] if a["id"] == "type.cert.verify-official")
    assert "认证中心" in verify["instruction"] and "CCRC-1" in verify["instruction"]
    remind = next(a for a in info["actions"] if a["id"] == "type.cert.expiry-reminder")
    assert "（未填）" in remind["instruction"]  # 有效期还没写进属性


def test_中文细分类型也认成证书(home):
    rel = _write(home, "b.md", CERT.replace("entity_type: cert", "entity_type: 资质"))
    assert wt.describe(rel)["type"] == "cert"


def test_按属性查(home):
    tdef = wt.load_schema()["cert"]
    _write(home, "a.md", wt.set_props(CERT, {"valid_until": "2027-03-01", "maintainer": "谢竞翔"}, tdef))
    _write(home, "b.md", wt.set_props(CERT.replace("软件安全开发一级", "B"), {"valid_until": "2029-01-01", "maintainer": "曾晓静"}, tdef))
    _write(home, "c.md", CERT.replace("软件安全开发一级", "C"))
    r = wt.query({"type": "cert", "where": [{"field": "valid_until", "before": "2027-06-30"}]})
    assert [i["rel_path"] for i in r["items"]] == ["wiki/entities/a.md"]
    assert wt.query({"type": "cert", "where": {"maintainer": "曾晓静"}})["total"] == 1
    assert wt.query({"type": "cert", "where": [{"field": "valid_until", "empty": True}]})["total"] == 1
    assert wt.query({"type": "cert", "status": "有效期未填"})["total"] == 1
    assert not wt.query({"type": "cert", "where": [{"field": "nope", "eq": "x"}]})["ok"]
    assert not wt.query({"type": "nope"})["ok"]


def test_建议不写文件_确认后写入且先备份(home):
    rel = _write(home, "a.md", CERT)
    r = wt.propose({"type": "cert", "use_model": False})
    assert r["scanned"] == 1 and r["items"][0]["proposed"]["cert_no"] == "CCRC-2019-ISV-SD-210"
    assert (home / rel).read_text(encoding="utf-8") == CERT
    done = wt.apply({"items": [{"rel_path": rel, "props": r["items"][0]["proposed"]}]})
    assert done["ok"] and done["written"] == 1
    assert 'cert_no: "CCRC-2019-ISV-SD-210"' in (home / rel).read_text(encoding="utf-8")
    backups = list((home / "wiki" / ".props-backups").rglob("a.md"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == CERT
    assert wt.propose({"type": "cert", "use_model": False})["items"] == []


def test_小鲶抽的值必须真在正文里(home, monkeypatch):
    body = CERT.replace("。归类为", "。认证范围：信息系统安全集成服务。复审日期2027年5月1日。归类为")
    rel = _write(home, "a.md", body)
    reply = {"0": {"scope": "信息系统安全集成服务", "review_on": "2027-05-01", "cert_no": "编的"}}
    monkeypatch.setattr(wt, "_call_model", lambda prompt: json.dumps(reply, ensure_ascii=False))
    r = wt.propose({"type": "cert"})
    got = r["items"][0]
    assert got["proposed"]["scope"] == "信息系统安全集成服务" and got["source"]["scope"] == "model"
    assert got["proposed"]["review_on"] == "2027-05-01"
    assert got["proposed"]["cert_no"] == "CCRC-2019-ISV-SD-210" and got["source"]["cert_no"] == "pattern"
    assert rel == got["rel_path"]


def test_本机补充定义_加属性和行动(home):
    (home / "wiki-type-schema.json").write_text(json.dumps({"types": {
        "cert": {"properties": [{"key": "bid_use", "label": "投标用途"}], "actions": [{"id": "type.cert.x", "label": "X"}]},
        "project": {"label": "项目", "properties": [{"key": "owner", "label": "负责人"}]},
    }}, ensure_ascii=False), encoding="utf-8")
    schema = wt.load_schema()
    assert "bid_use" in [p["key"] for p in schema["cert"]["properties"]]
    assert "cert_no" in [p["key"] for p in schema["cert"]["properties"]]
    assert [a["id"] for a in schema["cert"]["actions"]][-1] == "type.cert.x"
    assert schema["project"]["label"] == "项目"


def test_只改自己知识库里的条目(home):
    shared = home / "wiki-shared" / "x"
    shared.mkdir(parents=True)
    (shared / "a.md").write_text(CERT, encoding="utf-8")
    r = wt.set_props_file("wiki-shared/x/a.md", {"cert_no": "1"})
    assert not r["ok"]


def test_小鲶读条目带类型信息_只给props就只改属性(home):
    from catfish_tool_bridge import catfish_tools

    rel = _write(home, "a.md", CERT)
    got = catfish_tools._dispatch_native_inner("catfish_wiki_read", {"rel_path": rel})
    assert got["type_info"]["label"] == "资质证书"
    r = catfish_tools._dispatch_native_inner("catfish_wiki_update", {"rel_path": rel, "props": {"valid_until": "2028-06-06"}})
    assert r["ok"] and r["changed"]
    text = (home / rel).read_text(encoding="utf-8")
    assert 'valid_until: "2028-06-06"' in text and text.split("\n---", 1)[1] == CERT.split("\n---", 1)[1]
    assert wt.tool_dispatch("catfish_wiki_query", {"type": "cert", "status": "有效"})["total"] == 1


def test_待核_疑似_未录入_无限期都不当属性值(home, monkeypatch):
    tdef = wt.load_schema()["cert"]
    body = "持有的X。**证书编号待核**：资质列表录为 CCRC-2019-ISV-SI-1628，疑似误录，需按证书原件补正。由中国通信企业协会于2023-01-25颁发，有效期至2028-06-06，等级无等级。"
    got = wt._regex_extract(body, tdef)
    assert "cert_no" not in got
    assert got["valid_until"] == "2028-06-06" and got["level"] == "无"
    rel = _write(home, "a.md", CERT.replace("证书编号CCRC-2019-ISV-SD-210，", "资质列表中证书编号为占位值「1」，").replace("有效期至2028-06-06", "长期有效"))
    reply = {"0": {"cert_no": "未录入（资质列表中为占位值「1」）", "valid_until": "无限期", "level": "无等级"}}
    monkeypatch.setattr(wt, "_call_model", lambda prompt: json.dumps(reply, ensure_ascii=False))
    r = wt.propose({"type": "cert"})
    proposed = r["items"][0]["proposed"]
    assert "cert_no" not in proposed and "valid_until" not in proposed
    assert rel
