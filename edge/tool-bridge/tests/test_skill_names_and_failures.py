"""10/19 鸿波「技能清单对普通办公人员只能靠猜」: 中文名 / 一句话说明 / 改名 / 一次性补名; 工作流失败说人话; 专家起草。

全部虚构; skills 根、CATFISH_HOME 在临时目录; 模型调用用桩。
"""
from __future__ import annotations

import json

import pytest
import yaml

from catfish_tool_bridge import catfish_tools_skill_ops as ops
from catfish_tool_bridge import expert_local, skill_names
from catfish_tool_bridge import skill_workflow as engine
from catfish_tool_bridge import workflow_store, workflow_tool


def _skill(root, path, fm=None, body=""):
    d = root / path
    d.mkdir(parents=True)
    head = "---\n" + yaml.safe_dump(fm or {"name": d.name}, allow_unicode=True, sort_keys=False) + "---\n" if fm is not False else ""
    (d / "SKILL.md").write_text(head + body, encoding="utf-8")
    return d


# ── 中文名 / 说明 ─────────────────────────────────────────────────

def test_中文名按顺序取_title_标题破折号_描述第一句_目录名(tmp_path):
    a = _skill(tmp_path, "department/eis-login", {"name": "eis-login", "description": "x"},
               "# eis-login — 登录 EIS 一站式信息门户（支持验证码）\n")
    assert skill_names.skill_title(a) == "登录 EIS 一站式信息门户"
    b = _skill(tmp_path, "personal/digest", {"name": "digest", "title": "每周资质邮件摘要"}, "# digest — 别的\n")
    assert skill_names.skill_title(b) == "每周资质邮件摘要", "title 优先"
    c = _skill(tmp_path, "personal/sum", {"name": "sum", "description": "员工丢来视频要摘要时用，本地转写。"}, "# sum\n")
    assert skill_names.skill_title(c) == "员工丢来视频要摘要时用"
    d = _skill(tmp_path, "external/in-depth-research", {"name": "x", "description": "Conduct research"}, "# x\n")
    assert skill_names.skill_title(d) == "in-depth-research", "没中文就用目录名"
    long = _skill(tmp_path, "personal/long", {"name": "l", "title": "一二三四五六七八九十一二三四五六七八"})
    assert skill_names.skill_title(long).endswith("…") and len(skill_names.skill_title(long)) == 16


def test_写给模型的指令改成人话(tmp_path):
    d = _skill(tmp_path, "department/eis-checkin",
               {"name": "eis-checkin", "description": '⚠️ MUST CALL: 员工说"上班打卡"/"上班签到"/"打上班卡"等关键词时, **立即调用此 skill 的 tool_call** (catfish_run_skill)'},
               "# eis-checkin — 登录 EIS 后点上班打卡按钮\n")
    disp = skill_names.skill_display(d)
    assert disp == {"title": "登录 EIS 后点上班打卡按钮", "summary": "说「上班打卡」「上班签到」「打上班卡」时用"}


def test_改名只动title一行_没有就插在name后(tmp_path):
    d = _skill(tmp_path, "personal/x", {"name": "x", "description": "多行\n说明"}, "# x\n正文\n")
    skill_names.set_title(d, "我的新名字")
    text = (d / "SKILL.md").read_text()
    assert text.splitlines()[1] == "name: x" and text.splitlines()[2] == 'title: "我的新名字"'
    assert "正文" in text and "说明" in text
    skill_names.set_title(d, "再改一次")
    assert text.count("title:") == 1 and (d / "SKILL.md").read_text().count("title:") == 1
    assert skill_names.skill_title(d) == "再改一次"
    with pytest.raises(ValueError):
        skill_names.set_title(d, "  ")


def test_只能改自己电脑上的技能(tmp_path, monkeypatch):
    local = tmp_path / "local"
    monkeypatch.setattr(ops, "_local_skills_root", lambda: local)
    mine = _skill(local, "personal/x")
    repo = _skill(tmp_path / "repo", "department/y")
    h = skill_names.pim_handlers({"path": str(mine), "title": "好名字"})
    assert h["pim/skillname_set"]()["title"] == "好名字"
    with pytest.raises(ValueError, match="自己电脑"):
        skill_names.pim_handlers({"path": str(repo), "title": "改不了"})["pim/skillname_set"]()
    with pytest.raises(ValueError):
        skill_names.pim_handlers({"path": str(local / "personal/../../repo/department/y"), "title": "x"})["pim/skillname_set"]()


def test_一次性补名_只给没名字的_试过不再调模型(tmp_path, monkeypatch):
    local = tmp_path / "local"
    monkeypatch.setattr(ops, "_local_skills_root", lambda: local)
    _skill(local, "department/eis-login", {"name": "eis-login"}, "# eis-login — 登录 EIS 门户\n")
    _skill(local, "external/in-depth-research", {"name": "r", "description": "Conduct research"}, "# r\n")
    _skill(local, "personal/shenpi", {"name": "s", "description": "本次我实际跑通了审批流程"}, "# s\n")
    calls = []

    def llm(prompt):
        calls.append(prompt)
        assert "eis-login" not in prompt, "有像样中文名的不送去起名"
        return json.dumps({"names": {"1": "深度调研", "2": "EIS 资质审批"}})

    r = skill_names.backfill_once(llm=llm)
    assert sorted(r["named"].values()) == ["EIS 资质审批", "深度调研"]
    assert skill_names.skill_title(local / "external/in-depth-research") == "深度调研"
    assert skill_names.backfill_once(llm=llm)["named"] == {} and len(calls) == 1, "试过的不再调模型"


def test_补名时模型不通_下次还会再试(tmp_path, monkeypatch):
    local = tmp_path / "local"
    monkeypatch.setattr(ops, "_local_skills_root", lambda: local)
    _skill(local, "external/r", {"name": "r"}, "# r\n")

    def down(_p):
        raise RuntimeError("连不上模型")

    assert skill_names.backfill_once(llm=down)["ok"] is False
    assert skill_names.backfill_once(llm=lambda p: '{"names": {"1": "调研"}}')["named"]


# ── 工作流: 中文名 / 步骤链 / 失败说人话 / 做成了什么 ─────────────────

@pytest.fixture()
def wf_env(tmp_path, monkeypatch):
    skills = tmp_path / "skills"
    monkeypatch.setattr(ops, "_skill_search_roots", lambda: [skills])
    monkeypatch.setattr(workflow_tool, "_roots", lambda: [skills])
    monkeypatch.setattr(workflow_tool, "_install_root", lambda: skills)
    return skills


def _wf(steps, params=None):
    return {"kind": "workflow", "version": "1.0.0", "params": params or {}, "steps": steps}


def test_工作流列表_中文名_步骤链标出对外_参数(wf_env):
    wf = _wf([{"id": "mails", "action": "email_search", "params": {"query": "${params.kw}"}},
              {"id": "sum", "xiaonian": "逐封出摘要，列成表", "returns": {"summary": "text"}},
              {"id": "push", "action": "wechat_push", "params": {"text": "${steps.sum.summary}"}}],
             {"kw": {"type": "text", "label": "搜索关键词", "default": "资质"}})
    workflow_store.install("weekly-digest", "personal", "每周理资质邮件", [], wf, wf_env, title="每周资质邮件摘要")
    [w] = workflow_tool.workflows()
    assert w["title"] == "每周资质邮件摘要" and w["skill_path"] == "personal/weekly-digest"
    assert [c["label"] for c in w["chain"]] == ["搜邮件", "小鲶：逐封出摘要", "推送到我的微信"]
    assert [c["outward"] for c in w["chain"]] == [False, False, True]
    assert w["params"] == [{"key": "kw", "label": "搜索关键词", "type": "text", "default": "资质", "required": False}]


def _run(steps, stub_blocks, monkeypatch, **kw):
    """跑一个工作流, 积木 / 技能结果用桩。"""
    def fake_block(st, args, ctx):
        return stub_blocks[st["id"]](args) if callable(stub_blocks[st["id"]]) else stub_blocks[st["id"]]
    monkeypatch.setattr(engine, "run_block", fake_block)
    return engine.start("personal/t", _wf(steps), {}, title="测试工作流", skill_lookup=lambda p: None, **kw)


def test_失败说人话_结果太少要放宽条件(wf_env, monkeypatch):
    r = _run([{"id": "mails", "action": "email_search", "params": {"query": "资质"}, "expect": {"count_min": 1}}],
             {"mails": {"ok": True, "matches": []}}, monkeypatch, trigger="ui")
    assert r["status"] == "failed"
    assert r["error_human"].startswith("第 1 步「搜邮件」只拿到 0 条，要求至少 1 条")
    run = workflow_store.list_runs(1)[0]
    assert run["error_fix"] == "conditions" and run["trigger"] == "ui" and run["title"] == "测试工作流"
    assert run["error"] == "结果只有 0 项, 要求至少 1 项", "原始报错留着给 IT 看"


def test_失败说人话_引用前一步没有的字段要改工作流(wf_env, monkeypatch):
    r = _run([{"id": "search", "action": "email_search", "params": {"query": "资质"}},
              {"id": "pick", "action": "email_read", "params": {"email_id": "${steps.search.matches}"}}],
             {"search": {"ok": True, "items": []}, "pick": {"ok": True}}, monkeypatch)
    assert "第 2 步要用第 1 步「搜邮件」的「matches」" in r["error_human"]
    assert workflow_store.list_runs(1)[0]["error_fix"] == "workflow"


def test_失败说人话_要登录(wf_env, monkeypatch):
    def boom(_a):
        raise engine.StepFailure("还没登录鲶鱼账号", "block")
    r = _run([{"id": "mails", "action": "email_search", "params": {"query": "q"}}], {"mails": boom}, monkeypatch)
    assert "需要先登录" in r["error_human"] and workflow_store.list_runs(1)[0]["error_fix"] == "login"


def test_程序炸了也说清第几步(wf_env, monkeypatch):
    def boom(_a):
        raise KeyError("x")
    r = _run([{"id": "mails", "action": "email_search", "params": {"query": "q"}}], {"mails": boom}, monkeypatch)
    assert r["error_human"].startswith("第 1 步「搜邮件」出了程序错误")


def test_跑完了说做成了什么(wf_env, monkeypatch):
    r = _run([{"id": "mails", "action": "email_search", "params": {"query": "q"}},
              {"id": "task", "action": "create_task", "params": {"title": "x"}}],
             {"mails": {"ok": True, "items": [1]}, "task": {"ok": True, "title": "10/13-10/19 资质邮件摘要"}}, monkeypatch)
    assert r["status"] == "done"
    run = workflow_store.list_runs(1)[0]
    assert "《10/13-10/19 资质邮件摘要》" in run["outcome"], run["outcome"]
    assert "搜邮件" not in run["outcome"], "只读的步骤不算做成了什么"
    assert "跑完了" in r["summary"]


# ── 专家: 目录带中文名; 让小鲶帮我填 ───────────────────────────────

@pytest.fixture()
def expert_env(tmp_path, monkeypatch):
    skills = tmp_path / "skills"
    _skill(skills, "personal/qual-approve", {"name": "qual-approve", "title": "资质审批流程"}).joinpath(
        "workflow.yaml").write_text("kind: workflow\nsteps: []\n")
    d = _skill(skills, "department/eis-login", {"name": "eis-login"}, "# eis-login — 登录 EIS 门户\n")
    (d / "script.py").write_text("def render_x():\n    return {}\n")
    _skill(skills, "personal/weekly", {"name": "weekly", "title": "写周报"})
    monkeypatch.setattr(ops, "_skill_search_roots", lambda: [skills])
    return skills


def test_专家目录带中文名和来源(expert_env):
    cat = expert_local.catalog()
    wf = cat["workflows"][0]
    assert wf["title"] == "资质审批流程" and wf["source"] == "personal"
    sk = {s["skill_path"]: s for s in cat["skills"]}
    assert sk["department/eis-login"]["title"] == "登录 EIS 门户" and sk["department/eis-login"]["runnable"]


def test_让小鲶帮我填_只用已有的_编号换成路径(expert_env):
    seen = {}

    def llm(prompt):
        seen["p"] = prompt
        return json.dumps({"workflows": ["w1"], "skills": ["s1", "s99"],
                           "routes": [{"when": "审资质待办", "use": "w1", "ask": ["哪个项目"]},
                                      {"when": "资质标准问答", "use": "knowledge"},
                                      {"when": "乱写的", "use": "s42"},
                                      {"when": "写周报", "use": "decline"}],
                           "tool_groups": ["tasks", "bogus"], "persona": "我是资质专员。", "quick_prompts": ["这周有哪些待审批"]},
                          ensure_ascii=False)

    got = expert_local.suggest("资质专员", "处理资质申请审批", llm=llm)["suggestion"]
    assert "资质审批流程" in seen["p"] and "写周报" in seen["p"]
    assert got["workflows"] == ["personal/qual-approve"]
    assert got["skills"] == ["department/eis-login"], "s99 不存在, 丢掉"
    assert [r["use"] for r in got["routes"]] == ["workflow:personal/qual-approve", "knowledge", "decline"]
    assert got["tool_groups"] == ["tasks", "knowledge"], "不认识的能力组丢掉; 有查知识库就带上 knowledge"
    expert_local.normalize({"id": "qual", "name": "资质专员", **got})  # 起草出来的能直接通过校验


def test_让小鲶帮我填_没合适的就说清楚(expert_env):
    with pytest.raises(ValueError, match="没在你已有的"):
        expert_local.suggest("x", "y", llm=lambda p: '{"routes": [{"when": "a", "use": "knowledge"}]}')
    with pytest.raises(ValueError, match="先填名字"):
        expert_local.suggest("", "y", llm=lambda p: "{}")


def test_老记录按原始报错补人话_不改文件(wf_env, monkeypatch):
    r = _run([{"id": "mails", "action": "email_search", "params": {"query": "q"}, "expect": {"count_min": 1}}],
             {"mails": {"ok": True, "matches": []}}, monkeypatch)
    run = workflow_store.load_run(r["run_id"])
    run.pop("error_human"); run.pop("error_fix")          # 模拟 10/19 之前的记录
    workflow_store.save_run(run)
    before = (workflow_store.runs_dir() / f"{r['run_id']}.json").read_text()
    [ui] = workflow_tool.runs_for_ui(5, None)
    assert ui["error_human"].startswith("第 1 步「搜邮件」只拿到 0 条") and ui["error_fix"] == "conditions"
    assert (workflow_store.runs_dir() / f"{r['run_id']}.json").read_text() == before
