"""教学 → 工作流草稿 / 嵌套 skill 数据流 / 下载步骤 (docs/WORKFLOW-SKILL-PLAN.md §4.2 §6)。

全部虚构 trace; trace / skills / hermes 配置 / CATFISH_HOME 都指到临时目录 (同 test_skill_freeze.py)。
"""
from __future__ import annotations

import importlib.util
import json
from datetime import date

import pytest

from catfish_tool_bridge import catfish_tools_skill_ops as ops
from catfish_tool_bridge import skill_freeze, skill_register, trace_recorder
from catfish_tool_bridge import workflow_catalog as catalog
from catfish_tool_bridge import workflow_freeze, workflow_store


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    traces, local = tmp_path / "traces", tmp_path / "local-skills"
    traces.mkdir()
    local.mkdir()
    for attr, val in (("TRACE_DIR", traces), ("TRACE_PATH", traces / "active.jsonl"), ("STATE_PATH", traces / "_state.json"),
                      ("LAST_COMPLETED_PATH", traces / "_last_completed.json"), ("ARCHIVE_DIR", traces)):
        monkeypatch.setattr(trace_recorder, attr, val)
    monkeypatch.setenv("CATFISH_SKILLS_DIR", str(tmp_path / "ws-skills"))
    monkeypatch.setattr(skill_register, "LOCAL_SKILLS_ROOT", local)
    monkeypatch.setattr(skill_freeze, "LOCAL_SKILLS_ROOT", local)
    monkeypatch.setattr(skill_register, "HERMES_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path / "catfish"))
    monkeypatch.setattr(ops, "_skill_search_roots", lambda: [local])
    monkeypatch.setattr(ops, "_local_skills_root", lambda: local)
    return {"tmp": tmp_path, "local": local}


def _archive(tmp_path, steps):
    p = tmp_path / "traces" / "session.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        for i, s in enumerate(steps, 1):
            f.write(json.dumps({"seq": i, "ok": True, **s}, ensure_ascii=False) + "\n")
    return str(p)


def test_分类():
    b = {"tool": "catfish_browser_goto", "args": {"url": "http://x"}}
    assert workflow_freeze.classify([b]) == "script"
    assert workflow_freeze.classify([b, {"tool": "catfish_wiki_search"}]) == "script"
    assert workflow_freeze.classify([b, {"tool": "catfish_create_task"}]) == "mixed"
    assert workflow_freeze.classify([{"tool": "catfish_run_skill"}, {"tool": "catfish_create_task"}]) == "workflow"
    assert workflow_freeze.classify([{"tool": "catfish_remember"}]) == "script"


def test_录制名单跟积木目录一致():
    natives = set(catalog.NATIVE_TO_ACTION)
    assert natives <= trace_recorder.RECORDED_TOOLS
    assert {"catfish_browser_download", "catfish_run_skill"} <= trace_recorder.RECORDED_TOOLS


def test_教学_没有网页操作_出工作流草稿_按值认出数据流(iso):
    today = date.today().isoformat()
    path = _archive(iso["tmp"], [
        {"tool": "catfish_run_skill", "args": {"skill_path": "department/demo-download", "params": {"day": today}},
         "result": {"ok": True, "result": {"ok": True}, "files": ["/tmp/dl/附件-1017.pdf"]}},
        {"tool": "catfish_email_create_draft",
         "args": {"to": "boss@example.com", "subject": "附件", "body": "见附件 /tmp/dl/附件-1017.pdf"},
         "result": {"ok": True, "draft_id": "d1"}},
        {"tool": "catfish_create_task", "args": {"title": "/tmp/dl/附件-1017.pdf"}, "result": {"ok": True}},
    ])
    r = skill_freeze.freeze_skill({"name": "dl-and-draft", "namespace": "personal", "description": "下了附件起草邮件",
                                   "session_archive_path": path})
    assert r["kind"] == "workflow" and r["draft"] == "dl-and-draft"
    wf = workflow_store.load_draft("dl-and-draft")["workflow"]
    s1, s2, s3 = wf["steps"]
    assert s1 == {"id": "demo_download", "skill": "department/demo-download", "params": {"day": "${params.date}"}}
    assert wf["params"]["date"]["default"] == "today"
    assert s2["action"] == "email_create_draft" and s2["params"]["to"] == "boss@example.com"
    assert s3["params"]["title"] == "${steps.demo_download.files[0]}", "整个值等于前一步的文件 → 引用"
    assert any("to" in q and "boss@example.com" in q for q in r["questions"]), "拿不准的固定值要问员工"
    assert not (iso["local"] / "personal" / "dl-and-draft").exists(), "草稿没保存前不进 skills"


def test_网页加建任务_拒绝凝固(iso):
    path = _archive(iso["tmp"], [{"tool": "catfish_browser_goto", "args": {"url": "http://intra.example/x"}},
                                  {"tool": "catfish_create_task", "args": {"title": "x"}}])
    r = skill_freeze.freeze_skill({"name": "bad-mix", "session_archive_path": path})
    assert r["ok"] is False and "单独教成一个 skill" in r["error"]


def _load_script(d):
    spec = importlib.util.spec_from_file_location("frozen_demo", d / "script.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_网页下载后嵌套skill_运行时用这次下载的文件(iso, monkeypatch):
    path = _archive(iso["tmp"], [
        {"tool": "catfish_browser_goto", "args": {"url": "http://intra.example/report"},
         "result": {"type": "ok", "current_url": "http://intra.example/report"}},
        {"tool": "catfish_browser_download", "args": {"selector": "#dl"},
         "result": {"type": "ok", "ok": True, "path": "/tmp/day1/report.csv"}},
        {"tool": "catfish_run_skill", "args": {"skill_path": "department/demo-tongbao",
                                               "params": {"attachment": "/tmp/day1/report.csv", "title": "周报"}},
         "result": {"ok": True, "result": {"ok": True}}},
    ])
    r = skill_freeze.freeze_skill({"name": "report-tongbao", "namespace": "personal", "session_archive_path": path})
    assert r["ok"], r
    d = iso["local"] / "personal" / "report-tongbao"
    code = (d / "script.py").read_text()
    assert "catfish_browser_download" in code and "_dig(_outs[2], ['path'])" in code
    assert "/tmp/day1/report.csv" not in code.split("def render_")[1], "不再把教学那天的文件路径焊进去"
    assert "title: str = '周报'" in code or 'title: str = "周报"' in code, "其余字符串参数提成参数, 默认教学那天的值"

    calls = []

    def fake_dispatch(tool, args):
        calls.append((tool, args))
        if tool == "catfish_browser_goto":
            return {"type": "ok", "current_url": "http://intra.example/report"}
        if tool == "catfish_browser_download":
            return {"type": "ok", "ok": True, "path": "/tmp/day2/report.csv"}
        return {"ok": True, "result": {"ok": True}, "files": []}

    from catfish_tool_bridge import catfish_tools
    monkeypatch.setattr(catfish_tools, "dispatch_native", fake_dispatch)
    mod = _load_script(d)
    fn = next(getattr(mod, n) for n in dir(mod) if n.startswith("render_"))
    out = fn(title="月报")
    assert out["ok"] and out["path"] == "/tmp/day2/report.csv" and out["files"] == ["/tmp/day2/report.csv"]
    nested = [a for t, a in calls if t == "catfish_run_skill"][0]
    assert nested["params"] == {"attachment": "/tmp/day2/report.csv", "title": "月报"}
