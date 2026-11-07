"""工作流 · 条件 (11/08): when 不满足就跳过; 分支 = 两步写相反的条件; 只能简单比较, 写不出表达式。"""
from __future__ import annotations

import dataclasses

import pytest

from catfish_tool_bridge import catfish_tools_skill_ops as ops
from catfish_tool_bridge import skill_workflow as engine
from catfish_tool_bridge import workflow_catalog as catalog
from catfish_tool_bridge import workflow_xiaonian


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path / "catfish"))
    monkeypatch.setattr(ops, "_write_skill_audit", lambda ev: None)
    calls: dict[str, list] = {}

    def stub(name, fn):
        calls[name] = []
        monkeypatch.setitem(catalog.ACTIONS, name, dataclasses.replace(
            catalog.ACTIONS[name], run=lambda a, n=name: (calls[n].append(a), fn(a))[1]))
    stub("email_search", lambda a: {"ok": True, "matches": [{"id": "m1"}] if a["query"] == "有" else [], "count": 1 if a["query"] == "有" else 0})
    stub("create_task", lambda a: {"ok": True, "task": {"task_id": "t1", "title": a["title"]}})
    stub("wechat_push", lambda a: {"ok": True})
    return calls


def _lookup(_):
    return None


def wf(keyword):
    return {"kind": "workflow", "version": "1.0.0", "steps": [
        {"id": "mails", "action": "email_search", "params": {"query": keyword}},
        {"id": "check", "xiaonian": "核对", "input": "${steps.mails.matches}", "returns": {"ok": "bool", "problem": "text"},
         "when": {"of": "${steps.mails.matches}", "is": "not_empty"}},
        {"id": "warn", "action": "create_task", "params": {"title": "不合格: ${steps.check.problem}"},
         "when": {"of": "${steps.check.ok}", "is": "false"}},
        {"id": "done", "action": "create_task", "params": {"title": "合格, 归档"},
         "when": {"of": "${steps.check.ok}", "is": "true"}},
    ]}


def test_分支_不合格才建提醒(env, monkeypatch):
    monkeypatch.setattr(workflow_xiaonian, "_call_llm", lambda p: '{"ok": false, "problem": "缺金额"}')
    r = engine.start("t/x", wf("有"), {}, skill_lookup=_lookup)
    assert r["status"] == "done"
    assert [s["status"] for s in r["steps"]] == ["done", "done", "done", "skipped"]
    assert [c["title"] for c in env["create_task"]] == ["不合格: 缺金额"]


def test_没有邮件_后面都跳过_不报错(env, monkeypatch):
    monkeypatch.setattr(workflow_xiaonian, "_call_llm", lambda p: pytest.fail("不该调模型"))
    r = engine.start("t/x", wf("无"), {}, skill_lookup=_lookup)
    assert r["status"] == "done"
    assert [s["status"] for s in r["steps"]] == ["done", "skipped", "skipped", "skipped"]
    assert env["create_task"] == []


def test_比较大小_和_包含():
    ctx = {"params": {"n": 3}, "steps": {"a": {"count": 5, "tags": ["资质", "合同"], "title": "资质申请"}}}
    assert engine.when_holds({"of": "${steps.a.count}", "is": "gt", "to": "${params.n}"}, ctx)[0]
    assert not engine.when_holds({"of": "${steps.a.count}", "is": "le", "to": 4}, ctx)[0]
    assert engine.when_holds({"of": "${steps.a.tags}", "is": "contains", "to": "合同"}, ctx)[0]
    assert engine.when_holds({"of": "${steps.a.title}", "is": "contains", "to": "资质"}, ctx)[0]
    assert engine.when_holds({"of": "${steps.a.tags}", "is": "ge", "to": 2}, ctx)[0]   # 列表按项数比
    with pytest.raises(engine.StepFailure):
        engine.when_holds({"of": "${steps.a.title}", "is": "gt", "to": 1}, ctx)


def test_校验_条件写错说得出来():
    bad = {"kind": "workflow", "steps": [
        {"id": "a", "action": "create_task", "params": {"title": "x"}, "when": {"of": "${steps.b.x}", "is": "true"}},
        {"id": "b", "action": "create_task", "params": {"title": "x"}, "when": {"of": "1 + 1", "is": "gt"}},
        {"id": "c", "action": "create_task", "params": {"title": "x"}, "when": {"of": "${steps.a.ok}", "is": "between"}},
        {"id": "d", "action": "create_task", "params": {"title": "x"}, "when": {"of": "${steps.a.ok}", "is": "empty", "to": 1}},
    ]}
    errs = " / ".join(engine.validate(bad, skill_lookup=_lookup))
    assert "只能引用前面的步骤" in errs
    assert "of 要写成一个" in errs and "要写 to" in errs
    assert "is 只能是" in errs and "不用写 to" in errs


def test_引用被跳过那步的结果_说清楚(env, monkeypatch):
    flow = {"kind": "workflow", "version": "1.0.0", "steps": [
        {"id": "mails", "action": "email_search", "params": {"query": "无"}},
        {"id": "pick", "xiaonian": "挑", "returns": {"summary": "text"}, "when": {"of": "${steps.mails.matches}", "is": "not_empty"}},
        {"id": "todo", "action": "create_task", "params": {"title": "${steps.pick.summary}"}},
    ]}
    r = engine.start("t/x", flow, {}, skill_lookup=_lookup)
    assert r["status"] == "failed" and "被跳过了" in r["error_human"]
