"""一句话搭专家 (11/08): 拆活 → 起草工作流 (校验不过自动改) → 落地 → 撤回 → 教完自动接上。

模型回复全是替身; skills 根 / CATFISH_HOME 都在临时目录。全部虚构。
"""
from __future__ import annotations

import json

import pytest

from catfish_tool_bridge import catfish_tools_skill_ops as ops
from catfish_tool_bridge import expert_builder as eb
from catfish_tool_bridge import expert_local
from catfish_tool_bridge import workflow_store as store


@pytest.fixture()
def env(tmp_path, monkeypatch):
    skills = tmp_path / "skills"
    d = skills / "personal" / "cert-expiry"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: cert-expiry\ntitle: 证书到期提醒\ndescription: 看证书哪些快到期, 建提醒\n---\n")
    (d / "workflow.yaml").write_text("kind: workflow\nsteps: []\n")
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path / "catfish"))
    monkeypatch.setattr(ops, "_skill_search_roots", lambda: [skills])
    monkeypatch.setattr(ops, "_local_skills_root", lambda: skills)
    return {"skills": skills}


PLAN = {"experts": [{
    "name": "资质专员", "description": "资质邮件、证书到期、资质政策", "persona": "我是资质专员。",
    "quick_prompts": ["这周的资质邮件整理了吗"], "tool_groups": ["tasks"], "decline": "合同审批不归我",
    "jobs": [
        {"when": "整理资质邮件", "title": "整理资质邮件", "kind": "new", "goal": "搜上周带资质的邮件, 建任务",
         "schedule": "0 9 * * 1", "schedule_text": "每周一 9:00"},
        {"when": "证书快到期了吗", "title": "证书到期提醒", "kind": "reuse", "use": "w1"},
        {"when": "资质政策怎么规定", "title": "资质政策问答", "kind": "knowledge"},
        {"when": "去 EIS 下载证书", "title": "下载证书", "kind": "teach", "what": "在 EIS 系统里下载证书扫描件"},
    ]}]}

GOOD_WF = {"title": "整理资质邮件", "description": "每周整理资质邮件", "triggers": ["整理资质邮件"], "workflow": {
    "kind": "workflow", "version": "1.0.0", "params": {"keyword": {"type": "text", "label": "关键词", "default": "资质"}},
    "steps": [{"id": "mails", "action": "email_search", "params": {"query": "${params.keyword}"}},
              {"id": "todo", "action": "create_task", "params": {"title": "资质邮件 ${steps.mails.count} 封"},
               "when": {"of": "${steps.mails.matches}", "is": "not_empty"}}]}}
BAD_WF = {"title": "x", "workflow": {"kind": "workflow", "steps": [{"id": "a", "action": "no_such_block"}]}}


def fake_llm(replies):
    seen = []

    def llm(prompt):
        seen.append(prompt)
        if "拆出要搭哪几个专家" in prompt:
            return json.dumps(PLAN, ensure_ascii=False)
        return json.dumps(replies.pop(0), ensure_ascii=False)
    llm.seen = seen
    return llm


def test_拆活_复用已有的_新搭的起草好_要教的定好名字(env):
    llm = fake_llm([BAD_WF, GOOD_WF])   # 第一次写错, 带着问题再写一次就对了
    b = eb.plan("帮我搭个资质专员: 每周一整理资质邮件建任务, 证书到期提醒, 回答资质政策, 去 EIS 下载证书", llm=llm)
    e = b["experts"][0]
    kinds = [j["kind"] for j in e["jobs"]]
    assert kinds == ["new", "reuse", "knowledge", "teach"]
    new, reuse, _k, teach = e["jobs"]
    assert new["draft"] and new["problems"] == [] and new["schedule"]["cron"] == "0 9 * * 1"
    assert "没有「no_such_block」这块积木" in llm.seen[-1], "第二次起草带上了第一次的问题"
    assert store.load_draft(new["draft"])["workflow"]["steps"][1]["when"]["is"] == "not_empty"
    assert reuse["use"] == "workflow:personal/cert-expiry" and reuse["use_title"]
    assert teach["teach_name"].startswith("t") and "knowledge" in e["tool_groups"]
    assert "证书到期提醒" in eb.summary_text(b)


def test_编号对不上的复用_当成新搭(env):
    plan = json.loads(json.dumps(PLAN))
    plan["experts"][0]["jobs"] = [{"when": "x", "title": "x", "kind": "reuse", "use": "w9"}]
    job = eb._job(plan["experts"][0]["jobs"][0], [], [])
    assert job["kind"] == "new" and job["goal"]


def test_落地_工作流装好_专家定义存好_要教的进还不会的事(env):
    b = eb.plan("帮我搭个资质专员: 每周一整理资质邮件", llm=fake_llm([GOOD_WF]))
    r = eb.apply(b["build_id"])
    assert r["ok"] and r["schedules"][0]["cron"] == "0 9 * * 1"
    eid = r["experts"][0]["id"]
    d = expert_local.load(eid)
    assert any(x.startswith("personal/") for x in d["workflows"]) and "personal/cert-expiry" in d["workflows"]
    assert [rt["use"] for rt in d["routes"]][-1] == "decline"
    assert d["todo"][0]["what"] == "在 EIS 系统里下载证书扫描件"
    assert "这件事我还不会" in expert_local.build_soul(expert_local.normalize(d))
    assert store.list_drafts() == [], "保存后草稿收掉"
    with pytest.raises(ValueError, match="已经保存过"):
        eb.apply(b["build_id"])


def test_生效失败_撤回_不留半成品(env):
    b = eb.plan("帮我搭个资质专员: 每周一整理资质邮件", llm=fake_llm([GOOD_WF]))
    r = eb.apply(b["build_id"])
    path = r["schedules"][0]["skill_path"]
    eid = r["experts"][0]["id"]
    back = eb.rollback(b["build_id"])["build"]
    assert back["status"] == "draft"
    assert not (env["skills"] / path).exists(), "新装的工作流删掉"
    assert len(store.list_drafts()) == 1, "退回草稿"
    with pytest.raises(LookupError):
        expert_local.load(eid)
    assert eb.apply(b["build_id"])["ok"], "撤回后还能再保存"


def test_有工作流没搭好_不让保存_可以不勾那件(env):
    b = eb.plan("帮我搭个资质专员", llm=fake_llm([BAD_WF, BAD_WF, BAD_WF]))
    with pytest.raises(ValueError, match="还有工作流没搭好"):
        eb.apply(b["build_id"])
    eb.update(b["build_id"], 1, {"jobs": [{"index": 0, "include": False}]})
    assert eb.apply(b["build_id"])["ok"]


def test_教完_自动接进专家_要重新生效(env):
    b = eb.plan("帮我搭个资质专员", llm=fake_llm([GOOD_WF]))
    eid = eb.apply(b["build_id"])["experts"][0]["id"]
    teach = next(j for j in eb.load(b["build_id"])["experts"][0]["jobs"] if j["kind"] == "teach")
    p = eb.teach_prompt(b["build_id"], 1, 3)["prompt"]
    assert f"name={teach['teach_name']}" in p and "catfish_freeze_skill" in p
    sk = env["skills"] / "personal" / teach["teach_name"]
    sk.mkdir(parents=True)
    (sk / "SKILL.md").write_text(f"---\nname: {teach['teach_name']}\n---\n")
    (sk / "script.py").write_text("def render_x():\n    return {}\n")
    got = eb.attach_taught(teach["teach_name"], f"personal/{teach['teach_name']}")
    assert got["reapply_experts"] == [eid] and got["attached"] == ["资质专员 · 下载证书"]
    d = expert_local.load(eid)
    assert f"skill:personal/{teach['teach_name']}" in [r["use"] for r in d["routes"]]
    assert d["routes"][-1]["use"] == "decline", "不归它管的那条还在最后"
    assert d["todo"] == []
    assert eb.attach_taught(teach["teach_name"], "personal/x")["reapply_experts"] == [], "接过一次不重复"
    assert eb.list_open() == [], "都落地、都教完了, 不再挂着"


def test_话太短_或者拆不出来_说清楚(env):
    with pytest.raises(ValueError, match="多说一点"):
        eb.plan("搭个专家", llm=fake_llm([]))
    with pytest.raises(ValueError, match="没拆出"):
        eb.plan("帮我搭个什么都不做的专家", llm=lambda p: '{"experts": []}')


def test_凝固之后_接上的写进结果_别的照旧(env):
    b = eb.plan("帮我搭个资质专员", llm=fake_llm([GOOD_WF]))
    eid = eb.apply(b["build_id"])["experts"][0]["id"]
    name = next(j for j in eb.load(b["build_id"])["experts"][0]["jobs"] if j["kind"] == "teach")["teach_name"]
    other = {"ok": True, "name": "something-else", "skill_path": "personal/something-else", "summary": "凝固好了"}
    assert eb.after_freeze({}, other) == other
    assert eb.after_freeze({}, {"ok": False, "error": "x"}) == {"ok": False, "error": "x"}
    frozen = {"ok": True, "name": name, "skill_path": f"personal/{name}", "summary": "凝固好了"}
    r = eb.after_freeze({"name": name}, frozen)
    assert "没接进专家" in r["attach_error"] and "reapply_experts" not in r, "技能目录不在: 专家存不上, 说清楚"
    sk = env["skills"] / "personal" / name
    sk.mkdir(parents=True)
    (sk / "SKILL.md").write_text(f"---\nname: {name}\n---\n")
    (sk / "script.py").write_text("def render_x():\n    return {}\n")
    r = eb.after_freeze({"name": name}, frozen)
    assert r["reapply_experts"] == [eid] and "已接进专家: 资质专员 · 下载证书" in r["summary"]
