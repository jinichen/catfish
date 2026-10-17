"""工作流 skill (docs/WORKFLOW-SKILL-PLAN.md) —— 校验 / 取值 / 运行 / 对外确认 / 断点续跑 / 草稿保存。

隔离: CATFISH_HOME、skills 根、任务库都指到临时目录; 能力积木全换成替身, 不碰真邮件 / 任务 / 微信。
"""
from __future__ import annotations

import dataclasses
import json

import pytest

from catfish_tool_bridge import catfish_tools_skill_ops as ops
from catfish_tool_bridge import skill_workflow as engine
from catfish_tool_bridge import workflow_catalog as catalog
from catfish_tool_bridge import workflow_store as store
from catfish_tool_bridge import workflow_tool, workflow_xiaonian


@pytest.fixture()
def env(tmp_path, monkeypatch):
    home = tmp_path / "catfish"
    skills = tmp_path / "skills"
    skills.mkdir()
    monkeypatch.setenv("CATFISH_HOME", str(home))
    monkeypatch.setenv("CATFISH_TASK_LIBRARY_PATH", str(tmp_path / "tasks.db"))
    monkeypatch.setattr(ops, "_skill_search_roots", lambda: [skills])
    monkeypatch.setattr(ops, "_local_skills_root", lambda: skills)
    monkeypatch.setattr(ops, "_write_skill_audit", lambda ev: calls["audit"].append(ev))
    calls: dict[str, list] = {"audit": []}

    def stub(name, fn):
        calls.setdefault(name, [])

        def run(args):
            calls[name].append(args)
            return fn(args)
        monkeypatch.setitem(catalog.ACTIONS, name, dataclasses.replace(catalog.ACTIONS[name], run=run))

    stub("email_search", lambda a: {"ok": True, "matches": [{"id": "m1", "subject": "资质申请 A"},
                                                             {"id": "m2", "subject": "午饭"}], "count": 2})
    stub("email_attachment", lambda a: {"ok": True, "path": str(_touch(tmp_path / f"{a['email_id']}.pdf")),
                                        "filename": f"{a['email_id']}.pdf"})
    stub("create_task", lambda a: {"ok": True, "task": {"task_id": "t1", "title": a["title"]}})
    stub("wechat_push", lambda a: {"ok": True})
    return {"home": home, "skills": skills, "calls": calls, "tmp": tmp_path}


def _touch(p):
    p.write_text("x")
    return p


def _xiaonian(monkeypatch, reply):
    monkeypatch.setattr(workflow_xiaonian, "_call_llm", lambda prompt: reply if isinstance(reply, str) else reply(prompt))


WF = {
    "kind": "workflow", "version": "1.0.0",
    "params": {"keyword": {"type": "text", "default": "资质"}},
    "steps": [
        {"id": "mails", "action": "email_search", "params": {"query": "${params.keyword}"}, "expect": {"count_min": 1}},
        {"id": "pick", "xiaonian": "挑出跟资质有关的", "input": "${steps.mails.matches}",
         "returns": {"ids": "list", "summary": "text"}},
        {"id": "files", "action": "email_attachment", "for_each": "${steps.pick.ids}",
         "params": {"email_id": "${item}", "filename": "附件"}, "expect": {"files_min": 1}},
        {"id": "todo", "action": "create_task",
         "params": {"title": "整理资质附件 ${steps.files.count} 份", "body": "${steps.pick.summary}"}},
        {"id": "push", "action": "wechat_push", "params": {"text": "建好任务了: ${steps.todo.task.title}"}},
    ],
}


def _lookup(_):
    return None


def test_校验_常见错误都说得出来(env):
    bad = {"kind": "workflow", "steps": [
        {"id": "a", "action": "nope"},
        {"id": "a", "action": "create_task", "params": {"title": "x", "zz": 1}},
        {"id": "c", "xiaonian": "判断一下"},
        {"id": "d", "action": "create_task", "params": {"title": "${steps.zz.x} ${params.nope} ${item}"}},
        {"id": "e", "skill": "department/missing-skill"},
        {"id": "f", "skill": "department/x", "action": "create_task"},
    ]}
    errs = "\n".join(engine.validate(bad, skill_lookup=_lookup))
    for needle in ("没有「nope」", "重复", "不认这些参数: zz", "returns", "只能引用前面的步骤", "没声明的参数",
                   "for_each", "找不到 skill", "选且只选一个"):
        assert needle in errs, needle
    assert engine.validate(WF, skill_lookup=_lookup) == []


def test_取值_整个引用保留类型_夹在文字里变文字():
    ctx = {"params": {"k": "资质"}, "steps": {"a": {"ids": ["m1", "m2"], "n": 2}}}
    assert engine.resolve("${steps.a.ids}", ctx) == ["m1", "m2"]
    assert engine.resolve("共 ${steps.a.n} 封: ${steps.a.ids}", ctx) == "共 2 封: m1、m2"
    assert engine.resolve({"x": ["${steps.a.ids[1]}"]}, ctx) == {"x": ["m2"]}
    with pytest.raises(engine.StepFailure):
        engine.resolve("${steps.a.nope}", ctx)


def test_完整跑_停在对外步骤_确认后跑完(env, monkeypatch):
    _xiaonian(monkeypatch, '```json\n{"ids": ["m1"], "summary": "只有 A 跟资质有关"}\n```')
    r = engine.start("personal/qual", WF, {}, skill_lookup=_lookup)
    assert r["status"] == "waiting_confirm" and r["ok"]
    assert r["pending"]["step"] == "push" and r["pending"]["preview"] == {"text": "建好任务了: 整理资质附件 1 份"}
    assert env["calls"]["wechat_push"] == []
    assert env["calls"]["email_search"] == [{"query": "资质"}]
    assert env["calls"]["create_task"] == [{"title": "整理资质附件 1 份", "body": "只有 A 跟资质有关"}]
    done = engine.confirm(r["run_id"], True)
    assert done["status"] == "done" and len(env["calls"]["wechat_push"]) == 1
    assert len(env["calls"]["create_task"]) == 1, "确认后只跑剩下的, 前面不重跑"
    assert done["files"] and done["files"][0].endswith("m1.pdf")


def test_不同意就不发(env, monkeypatch):
    _xiaonian(monkeypatch, '{"ids": ["m1"], "summary": "s"}')
    r = engine.start("personal/qual", WF, {}, skill_lookup=_lookup)
    d = engine.confirm(r["run_id"], False)
    assert d["status"] == "declined" and env["calls"]["wechat_push"] == []
    assert engine.confirm(r["run_id"], True)["ok"] is False


def test_员工设了不用每次确认_就直接发(env, monkeypatch):
    _xiaonian(monkeypatch, '{"ids": ["m1"], "summary": "s"}')
    store.set_auto_confirm_outward("personal/qual", True)
    r = engine.start("personal/qual", WF, {}, skill_lookup=_lookup)
    assert r["status"] == "done" and len(env["calls"]["wechat_push"]) == 1
    wf = json.loads(json.dumps(WF))
    wf["steps"][3]["confirm"] = True   # 员工自己加的「这步要我确认」不受这个开关影响
    r2 = engine.start("personal/qual", wf, {}, skill_lookup=_lookup)
    assert r2["status"] == "waiting_confirm" and r2["pending"]["step"] == "todo"


def test_中间失败_说清哪步_接着跑不重跑前面(env, monkeypatch):
    replies = iter(['{"ids": ["m1"]}', '{"ids": ["m1"], "summary": "第二次交全了"}'])
    _xiaonian(monkeypatch, lambda p: next(replies))
    r = engine.start("personal/qual", WF, {}, skill_lookup=_lookup)
    assert r["status"] == "failed" and "第 2 步" in r["summary"] and "缺「summary」" in r["error"]
    assert len(env["calls"]["email_search"]) == 1
    r2 = engine.resume(r["run_id"])
    assert r2["status"] == "waiting_confirm"
    assert len(env["calls"]["email_search"]) == 1, "第 1 步不重跑"


def test_expect_不满足就停(env, monkeypatch):
    monkeypatch.setitem(catalog.ACTIONS, "email_search",
                        dataclasses.replace(catalog.ACTIONS["email_search"], run=lambda a: {"ok": True, "matches": [], "count": 0}))
    r = engine.start("personal/qual", WF, {}, skill_lookup=_lookup)
    assert r["status"] == "failed" and "至少 1 项" in r["error"]


def test_积木报错_停在那一步(env, monkeypatch):
    monkeypatch.setitem(catalog.ACTIONS, "email_search",
                        dataclasses.replace(catalog.ACTIONS["email_search"], run=lambda a: {"ok": False, "error": "邮箱没连上"}))
    r = engine.start("personal/qual", WF, {}, skill_lookup=_lookup)
    assert r["status"] == "failed" and r["error"] == "邮箱没连上"


def test_缺必填参数_一步都不跑(env):
    wf = {**WF, "params": {"keyword": {"type": "text", "required": True, "label": "关键词"}}}
    r = engine.start("personal/qual", wf, {}, skill_lookup=_lookup)
    assert r["status"] == "invalid" and "关键词" in r["error"] and env["calls"]["email_search"] == []


def test_小鲶交回不是JSON_算失败():
    with pytest.raises(ValueError):
        workflow_xiaonian.run("x", {}, {"a": "text"}, llm=lambda p: "我觉得是 A")
    assert workflow_xiaonian.run("x", {}, {"n": "number", "ok2": "bool"}, llm=lambda p: '{"n": "3", "ok2": true}') == \
        {"ok": True, "n": 3, "ok2": True}


def _make_script_skill(skills, path="department/demo-login"):
    d = skills / path
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: demo-login\nversion: 1.0.0\ndescription: 演示登录\n---\n")
    (d / "script.py").write_text("def render_demo_login(username='a', max_captcha_retry=3):\n"
                                 "    return {'ok': True, 'user': username}\n")
    return d


def test_草稿_试跑_保存_之后用run_skill跑(env, monkeypatch):
    _make_script_skill(env["skills"])
    wf = {"kind": "workflow", "params": {"who": {"type": "text", "default": "zhou"}},
          "steps": [{"id": "login", "skill": "department/demo-login", "params": {"username": "${params.who}"}},
                    {"id": "todo", "action": "create_task", "params": {"title": "已登录 ${steps.login.user}"}}]}
    cat = workflow_tool.dispatch("catfish_workflow", {"action": "catalog"})
    assert any(s["skill_path"] == "department/demo-login" and s["params"][0]["name"] == "username" for s in cat["skills"])
    d = workflow_tool.dispatch("catfish_workflow", {"action": "draft", "name": "demo-flow", "description": "演示",
                                                   "triggers": ["演示一下"], "workflow": wf})
    assert d["ok"] and d["problems"] == []
    t = workflow_tool.dispatch("catfish_workflow", {"action": "trial", "name": "demo-flow"})
    assert t["status"] == "done" and env["calls"]["create_task"][-1] == {"title": "已登录 zhou"}
    p = workflow_tool.dispatch("catfish_workflow", {"action": "save", "name": "demo-flow"})
    assert p["needs_confirmation"]
    s = workflow_tool.dispatch("catfish_workflow", {"action": "save", "name": "demo-flow", "confirmed": True})
    assert s["ok"] and (env["skills"] / "personal/demo-flow/workflow.yaml").exists()
    md = (env["skills"] / "personal/demo-flow/SKILL.md").read_text()
    assert "kind: workflow" in md and "catfish_run_skill" in md and "演示一下" in md
    r = ops.run_skill({"skill_path": "personal/demo-flow", "params": {"who": "li"}})
    assert r["status"] == "done" and env["calls"]["create_task"][-1] == {"title": "已登录 li"}
    assert workflow_tool.dispatch("catfish_workflow", {"action": "list"})["workflows"][0]["skill_path"] == "personal/demo-flow"


def test_工作流里不能套工作流_说明型skill不能当一步(env):
    _make_script_skill(env["skills"])
    (env["skills"] / "personal/flow-a").mkdir(parents=True)
    (env["skills"] / "personal/flow-a/workflow.yaml").write_text("kind: workflow\nsteps: []\n")
    (env["skills"] / "personal/howto").mkdir(parents=True)
    (env["skills"] / "personal/howto/SKILL.md").write_text("---\nname: howto\n---\n")
    wf = {"kind": "workflow", "steps": [{"id": "a", "skill": "personal/flow-a"}, {"id": "b", "skill": "personal/howto"}]}
    errs = "\n".join(engine.validate(wf, skill_lookup=workflow_tool.skill_lookup))
    assert "不能在工作流里再套工作流" in errs and "说明型 skill" in errs


def test_工具里没有确认_界面才有(env):
    from catfish_tool_bridge.catfish_tool_schemas import CATFISH_NATIVE_TOOLS
    schema = next(t for t in CATFISH_NATIVE_TOOLS if t["name"] == "catfish_workflow")
    assert not {"confirm", "set_auto_confirm"} & set(schema["input_schema"]["properties"]["action"]["enum"])
    assert workflow_tool.dispatch("catfish_workflow", {"action": "confirm", "run_id": "r_20261017000000_abcdef"})["ok"] is False
    assert "pim/workflow_confirm" in workflow_tool.pim_handlers({})


def test_审计只有名字不记参数值(env, monkeypatch):
    _xiaonian(monkeypatch, '{"ids": ["m1"], "summary": "s"}')
    store.set_auto_confirm_outward("personal/qual", True)
    engine.start("personal/qual", WF, {"keyword": "绝密关键词"}, skill_lookup=_lookup)
    ev = env["calls"]["audit"][-1]
    assert ev["param_keys"] == ["keyword"] and "绝密关键词" not in json.dumps(ev, ensure_ascii=False)


def test_对外积木都标了outward():
    outward = {a.name for a in catalog.ACTIONS.values() if a.level == catalog.OUTWARD}
    assert {"form_send", "form_remind", "form_close", "wiki_publish", "wechat_push"} <= outward
    for name in ("email_create_draft", "create_task"):
        assert catalog.ACTIONS[name].level == catalog.LOCAL


def test_小鲶说明里的引用也会填上_也会校验(env, monkeypatch):
    seen = []
    _xiaonian(monkeypatch, lambda p: (seen.append(p), '{"n": 1}')[1])
    wf = {"kind": "workflow", "params": {"topic": {"default": "资质"}},
          "steps": [{"id": "j", "xiaonian": "挑出跟「${params.topic}」有关的", "input": "x", "returns": {"n": "number"}}]}
    assert engine.start("personal/x", wf, {}, skill_lookup=_lookup)["status"] == "done"
    assert "挑出跟「资质」有关的" in seen[0]
    bad = {"kind": "workflow", "steps": [{"id": "j", "xiaonian": "看 ${params.nope}", "returns": {"n": "number"}}]}
    assert any("没声明的参数" in e for e in engine.validate(bad, skill_lookup=_lookup))
