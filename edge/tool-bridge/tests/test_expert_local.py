"""本机专家 (docs/EXPERT-PACK-PLAN.md 第一阶段): 定义校验 / 编译成 profile 要写的东西 / 主小鲶的推荐工具。

全部虚构; skills 根、CATFISH_HOME、hermes home 都在临时目录。
"""
from __future__ import annotations

import os

import pytest

from catfish_tool_bridge import catfish_tools_skill_ops as ops
from catfish_tool_bridge import expert_local, expert_tool, hermes_paths


@pytest.fixture()
def env(tmp_path, monkeypatch):
    skills = tmp_path / "skills"
    for path, kind in (("personal/qual-approve", "workflow"), ("department/eis-login", "script"),
                       ("personal/howto-cert", "doc")):
        d = skills / path
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"---\nname: {d.name}\ndescription: {d.name} 说明\n---\n")
        if kind == "workflow":
            (d / "workflow.yaml").write_text("kind: workflow\nsteps: []\n")
        if kind == "script":
            (d / "script.py").write_text("def render_x():\n    return {}\n")
    monkeypatch.setattr(ops, "_skill_search_roots", lambda: [skills])
    hh = tmp_path / "hermes"
    prof = hh / "profiles" / "expert-qual" / "skills" / "productivity" / "notes"
    prof.mkdir(parents=True)
    (prof / "SKILL.md").write_text("---\nname: notes\n---\n")
    monkeypatch.setattr(hermes_paths, "hermes_home", lambda: hh)
    return {"skills": skills, "tmp": tmp_path}


DEF = {
    "id": "qual", "name": "资质专员", "description": "处理资质申请和证书到期",
    "workflows": ["personal/qual-approve"], "skills": ["department/eis-login", "personal/howto-cert"],
    "routes": [{"when": "审资质待办", "use": "workflow:personal/qual-approve", "ask": ["哪个系统"]},
               {"when": "资质标准问答", "use": "knowledge"},
               {"when": "其它", "use": "decline"}],
    "checks": [{"after": "personal/qual-approve", "verify": "每条写明通过 / 退回和原因"}],
    "tool_groups": ["tasks"], "quick_prompts": ["这周有哪些待审批"],
}


def test_定义校验_一次说全(env):
    bad = {"id": "Bad Id", "name": "", "workflows": ["personal/nope"], "skills": ["personal/qual-approve"],
           "routes": [{"when": "x", "use": "workflow:personal/other"}], "tool_groups": ["nope"]}
    with pytest.raises(ValueError) as e:
        expert_local.normalize(bad)
    msg = str(e.value)
    for needle in ("专家 id", "名字", "工作流 personal/nope 不存在", "是工作流", "不在这个专家选中的", "能力组"):
        assert needle in msg, needle


def test_编译_工具算出来_技能软链_默认关终端(env):
    expert_local.save(DEF)
    c = expert_local.compile("qual")
    assert c["profile_id"] == "expert-qual"
    tools = c["tools_include"]
    assert tools[:2] == ["catfish_run_skill", "catfish_workflow"]
    assert "catfish_wiki_search" in tools, "有「查知识库」这条路就自动带上知识库能力"
    assert "catfish_create_task" in tools and "catfish_form" not in tools and "catfish_browser_click" not in tools
    links = sorted(os.listdir(c["skills_dir"]))
    assert links == ["department__eis-login", "personal__howto-cert", "personal__qual-approve"]
    assert os.path.islink(os.path.join(c["skills_dir"], "personal__qual-approve"))
    assert "terminal" in c["disabled_toolsets"] and "web" in c["disabled_toolsets"]
    assert c["disabled_skills"] == ["notes"]
    soul = c["soul"]
    assert "审资质待办" in soul and 'catfish_run_skill(skill_path="personal/qual-approve"' in soul
    assert "哪个系统" in soul and "切回小鲶" in soul and "每条写明通过" in soul


def test_放开上网和代码(env):
    expert_local.save({**DEF, "allow_web": True, "allow_code": True})
    c = expert_local.compile("qual")
    assert not {"web", "search", "terminal", "code_execution", "file"} & set(c["disabled_toolsets"])
    assert "browser" in c["disabled_toolsets"], "hermes 自带浏览器始终关 (用 catfish 的网页能力)"


def test_改选择后重新编译_软链跟着变(env):
    expert_local.save(DEF)
    expert_local.compile("qual")
    expert_local.save({**DEF, "skills": [], "routes": DEF["routes"][:1]})
    c = expert_local.compile("qual")
    assert sorted(os.listdir(c["skills_dir"])) == ["personal__qual-approve"]


def test_列表_删除(env):
    expert_local.save(DEF)
    rows = expert_local.list_all()
    assert rows[0]["id"] == "qual" and rows[0]["profile_id"] == "expert-qual" and rows[0]["routes"] == 3
    expert_local.delete("qual")
    assert expert_local.list_all() == []


def test_目录里看得到工作流和技能(env):
    c = expert_local.catalog()
    assert [w["skill_path"] for w in c["workflows"]] == ["personal/qual-approve"]
    runnable = {s["skill_path"]: s["runnable"] for s in c["skills"]}
    assert runnable == {"department/eis-login": True, "personal/howto-cert": False}


def test_主小鲶的推荐工具_只推荐(env):
    expert_local.save(DEF)
    assert expert_tool.dispatch("catfish_expert", {"action": "list"})["experts"][0]["name"] == "资质专员"
    r = expert_tool.dispatch("catfish_expert", {"action": "suggest", "expert_id": "qual", "reason": "它管资质"})
    assert r["suggestion"] == {"expert_id": "qual", "profile_id": "expert-qual", "name": "资质专员", "reason": "它管资质"}
    assert expert_tool.dispatch("catfish_expert", {"action": "suggest", "expert_id": "nope"})["ok"] is False
    expert_local.save({**DEF, "enabled": False})
    assert expert_tool.dispatch("catfish_expert", {"action": "list"})["experts"] == []


def test_专家自己看不到推荐工具():
    assert "catfish_expert" not in expert_local.BASE_TOOLS
    assert all("catfish_expert" not in g["tools"] for g in expert_local.TOOL_GROUPS.values())
