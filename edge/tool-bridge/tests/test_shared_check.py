"""装的共享内容定时检查 (11/14, shared_check.py): 有新版列出来、撤回了停用、换上能用的版本自动恢复。

全部虚构; HOME / CATFISH_HOME / skills 根在临时目录, 中央走 httpx MockTransport (复用 test_skill_trial 的桩)。
"""
from __future__ import annotations

import json

import httpx
import pytest
import yaml

from catfish_tool_bridge import catfish_tools_install as inst
from catfish_tool_bridge import catfish_tools_skill_ops as ops
from catfish_tool_bridge import expert_local, expert_pack, shared_check, wiki_install

from tests.test_skill_trial import _doc, _meta, _review, gateway, home, hub_files  # noqa: F401  (pytest fixtures)

WIKI_LIST = "/v1/wiki/documents"
WIKI_DOC = "/v1/wiki/documents/dept/研发部/abc123def456"
STATUS = "/v1/hub/installed/status"


@pytest.fixture()
def env(home, gateway, hub_files, monkeypatch):
    monkeypatch.setenv("CATFISH_HOME", str(home["tmp"] / ".catfish"))
    expert_pack.pin_key(home["pub"])   # 中央公钥记在 CATFISH_HOME 下, 换了根要重新记
    # 技能: 真走一遍从中央装 (审计里记的来源 / 版本就是这里写的)
    hub_files["meta"] = _meta(_review(home["key"]))
    r = inst.skill_install({"hub_skill": "department/eis-login@1.0.0", "hub_url": "http://gw/v1/hub", "skip_dry_run": True})
    assert r["ok"], r
    # 专家包: 装好的样子 (expert.yaml, source.kind=pack, origin=hub)
    d = expert_local.experts_dir() / "qual"
    d.mkdir(parents=True)
    (d / "expert.yaml").write_text(yaml.safe_dump({"id": "qual", "name": "资质专员", "enabled": True,
                                                   "source": {"kind": "pack", "origin": "hub", "version": "1.0.0"}},
                                                  allow_unicode=True), encoding="utf-8")
    # 知识页: 真走一遍安装
    doc = {"doc": _doc()}
    gateway["routes"][("GET", WIKI_DOC)] = lambda req: httpx.Response(200, json=doc["doc"])
    assert wiki_install.wiki_install({"hub_namespace": "dept/研发部", "hub_file_id": "abc123def456"})["ok"]
    status = {"skill": ("approved", "1.0.0"), "expert": ("approved", "1.0.0")}

    def st(req):
        body = json.loads(req.content)
        s, e = status["skill"], status["expert"]
        return httpx.Response(200, json={
            "skills": [{**x, "status": s[0], "latest_version": s[1], "withdrawn_by": "boss@x" if s[0] == "withdrawn" else None}
                       for x in body["skills"]],
            "experts": [{**x, "status": e[0], "latest_version": e[1]} for x in body["experts"]]})

    gateway["routes"][("POST", STATUS)] = st
    gateway["routes"][("GET", WIKI_LIST)] = lambda req: httpx.Response(200, json={"documents": [doc["doc"]]})
    return {"status": status, "doc": doc, "gateway": gateway, "skill_dir": home["skills"] / "department" / "eis-login"}


def _expert():
    return yaml.safe_load((expert_local.experts_dir() / "qual" / "expert.yaml").read_text(encoding="utf-8"))


def test_本机装了什么_从安装审计和专家定义里读出来(env):
    s = shared_check.installed_skills()
    assert [(x["namespace"], x["name"], x["version"], x["skill_name"]) for x in s] == [("department", "eis-login", "1.0.0", "eis-login")]
    assert [(x["id"], x["version"]) for x in shared_check.installed_experts()] == [("qual", "1.0.0")]
    assert [x["file_id"] for x in shared_check.installed_wiki()] == ["abc123def456"]


def test_都没变_什么都不列(env):
    r = shared_check.check()
    assert r["ok"] and r["updates"] == [] and r["withdrawn"] == [] and r["disable_skill_names"] == []
    assert r["counts"] == {"skills": 1, "experts": 1, "wiki": 1}
    assert json.loads(shared_check.state_path().read_text())["checked_at"], "结果落盘给早安简报读"


def test_有新版_技能专家知识页都列出来(env):
    env["status"]["skill"] = ("approved", "1.10.0")
    env["status"]["expert"] = ("approved", "1.2.0")
    env["doc"]["doc"] = _doc(updated_at="2026-11-10T00:00:00Z", body_md="正文 v2")
    r = shared_check.check()
    got = {(u["kind"], u.get("latest_version")) for u in r["updates"]}
    assert got == {("skill", "1.10.0"), ("expert", "1.2.0"), ("wiki", None)}


def test_技能撤回_停用且不再执行_装上能用的版本后自动恢复(env):
    env["status"]["skill"] = ("withdrawn", None)
    r = shared_check.check()
    assert r["disable_skill_names"] == ["eis-login"] and r["withdrawn"][0]["withdrawn_by"] == "boss@x"
    run = ops.run_skill({"skill_path": "department/eis-login"})
    assert shared_check.is_skill_withdrawn(env["skill_dir"]) is not None
    assert run["ok"] is False and "撤回" in run["error"]
    # 再查一次: 已停用的不重复让 Rust 停
    assert shared_check.check()["disable_skill_names"] == []
    # 员工换上了能用的版本 (中央说这个版本 approved) → 恢复
    env["status"]["skill"] = ("approved", "1.0.0")
    r = shared_check.check()
    assert r["enable_skill_names"] == ["eis-login"] and shared_check.is_skill_withdrawn(env["skill_dir"]) is None


def test_专家包撤回_本机停用_能用了再恢复_员工自己关的不动(env):
    env["status"]["expert"] = ("withdrawn", None)
    r = shared_check.check()
    assert r["apply_experts"] == ["qual"] and _expert()["enabled"] is False and _expert()["source"]["withdrawn"]["status"] == "withdrawn"
    assert shared_check.check()["apply_experts"] == [], "已停用的不再重复 apply"
    env["status"]["expert"] = ("approved", "1.0.0")
    r = shared_check.check()
    assert r["apply_experts"] == ["qual"] and _expert()["enabled"] is True and "withdrawn" not in _expert()["source"]
    # 员工自己关掉的: 撤回也好、恢复也好都不碰
    d = _expert(); d["enabled"] = False
    (expert_local.experts_dir() / "qual" / "expert.yaml").write_text(yaml.safe_dump(d, allow_unicode=True), encoding="utf-8")
    env["status"]["expert"] = ("withdrawn", None)
    assert shared_check.check()["apply_experts"] == []
    env["status"]["expert"] = ("approved", "1.0.0")
    assert shared_check.check()["apply_experts"] == [] and _expert()["enabled"] is False


def test_知识页撤回_本机标已撤回不删_看不到了的只列不动(env):
    env["doc"]["doc"] = _doc(review_status="withdrawn", withdrawn_by="boss@x", stale_after_unpublish=True, body_md="")
    r = shared_check.check()
    assert [w["kind"] for w in r["withdrawn"]] == ["wiki"]
    md = wiki_install._shared_root() / "dept" / "研发部" / "abc123def456.md"
    assert "review_status: withdrawn" in md.read_text() and "正文 资质评估流程" in md.read_text()
    # 列表里没了、单独问 403 → 只列 hidden
    env["gateway"]["routes"][("GET", WIKI_LIST)] = lambda req: httpx.Response(200, json={"documents": []})
    env["gateway"]["routes"][("GET", WIKI_DOC)] = lambda req: httpx.Response(403, json={"detail": "x"})
    r = shared_check.check()
    assert [(h["kind"], h["status"]) for h in r["hidden"]] == [("wiki", "hidden")] and r["withdrawn"] == []


def test_连不上中央_记错误_不动已停用的(env):
    env["status"]["skill"] = ("withdrawn", None)
    shared_check.check()
    env["gateway"]["routes"].pop(("POST", STATUS))
    r = shared_check.check()
    assert r["ok"] is False and r["errors"]
    assert shared_check.is_skill_withdrawn(env["skill_dir"]) is not None, "问不到的维持原状, 不会被当成恢复"
    assert r["enable_skill_names"] == []


def test_知识页更新前_本机改过的先备份_没改过的不备份(env):
    d = wiki_install._shared_root() / "dept" / "研发部"
    env["doc"]["doc"] = _doc(updated_at="2026-11-10T00:00:00Z", body_md="正文 v2")
    r = wiki_install.wiki_install({"hub_namespace": "dept/研发部", "hub_file_id": "abc123def456"})
    assert r["ok"] and "local_backup" not in r, "装进来以后没动过 → 不备份"
    (d / "abc123def456.md").write_text((d / "abc123def456.md").read_text() + "\n我自己加的一行\n", encoding="utf-8")
    env["doc"]["doc"] = _doc(updated_at="2026-11-11T00:00:00Z", body_md="正文 v3")
    r = wiki_install.wiki_install({"hub_namespace": "dept/研发部", "hub_file_id": "abc123def456"})
    assert r["ok"] and "我自己加的一行" in open(r["local_backup"], encoding="utf-8").read()
    assert "wiki-shared" in r["local_backup"] and "/backups/" in r["local_backup"]
    assert "正文 v3" in (d / "abc123def456.md").read_text()
