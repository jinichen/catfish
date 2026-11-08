"""11/08 宣传片专员那次: 小鲶那步写「调 execute_code 生成 Word」「给出公开素材链接」, 校验照过、跑起来做不成。

这里管: 小鲶那步做不到的事拦下来 / 起草时步数和重复的提醒 / 导出 Word 积木真能出文件 /
对话里改总览卡上的草稿 —— 不掉出总览卡、卡上的问题跟着刷新、不能在对话里单独保存 / 卡上一句话改。全部虚构。
"""
from __future__ import annotations

import json

import pytest

from catfish_tool_bridge import catfish_tools_skill_ops as ops
from catfish_tool_bridge import docx_export
from catfish_tool_bridge import expert_builder as eb
from catfish_tool_bridge import skill_workflow as engine
from catfish_tool_bridge import workflow_lint as lint
from catfish_tool_bridge import workflow_store as store
from catfish_tool_bridge import workflow_tool


def _xn(text: str) -> dict:
    return {"kind": "workflow", "steps": [{"id": "a", "xiaonian": text, "returns": {"x": "text"}}]}


@pytest.mark.parametrize("text, why", [
    ("用 execute_code 调 python-docx 生成 docx 文件", "不能调工具"),
    ("把脚本保存到 ~/.catfish/outputs", "不能生成文件"),
    ("整理参考清单, 给出公开素材、配乐链接和版权信息", "上不了网"),
    ("上网查一下同类宣传片", "上不了网"),
    ("把结果发送邮件给同事", "发不了东西"),
])
def test_小鲶那步说要做它做不到的事_拦下来_说清楚换什么(text, why):
    got = lint.claims(_xn(text))
    assert len(got) == 1 and why in got[0] and got[0].startswith("第 1 步")


@pytest.mark.parametrize("text", [
    "只根据前面查到的资料整理参考清单, 不要给网址",
    "汇总: markdown 说已写入知识库",
    "员工要 Word/docx 时返回 docx",
    "生成分镜脚本 markdown, 按场次列画面、台词、时长",
])
def test_叮嘱别做_转述做完的_正常写字_不算(text):
    assert lint.claims(_xn(text)) == []


def test_积木那步_不查说明():
    wf = {"kind": "workflow", "steps": [{"id": "a", "action": "wiki_search", "params": {"query": "上网"}}]}
    assert lint.claims(wf) == []


def test_起草提醒_步数太多_同一块积木抄好几遍_参数没中文名():
    steps = [{"id": f"t{i}", "action": "create_task", "params": {"title": "x"}} for i in range(10)]
    hints = lint.build_hints({"params": {"topic": {"type": "text"}}, "steps": steps})
    assert any("步骤太多" in h for h in hints)
    assert any("for_each" in h and "第 1-3 步" in h for h in hints)
    assert any("topic" in h and "label" in h for h in hints)
    assert lint.build_hints({"params": {"topic": {"type": "text", "label": "主题"}}, "steps": steps[:2]}) == []


def test_给员工看的说法_去掉步骤_id_和工具提示():
    assert lint.plain("第 4 步 (deliver_docx) 没有「x」这块积木 (用 catfish_workflow action=catalog 看有哪些)") \
        == "第 4 步 没有「x」这块积木"


def test_已经装好的工作流照常跑_只查格式():
    assert engine.validate(_xn("调 execute_code 生成 Word 文件"), skill_lookup=lambda _p: None) == []


# ── 导出 Word ─────────────────────────────────────────────────────

def test_导出_word_标题_列表_表格_加粗_同名不覆盖(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    from docx import Document
    body = ("# 宣传片分镜\n\n## 第一场\n- **画面**: 清晨园区航拍\n1. 推镜\n\n"
            "| 场次 | 时长 |\n|---|---|\n| 1 | 8 秒 |\n\n> 旁白要稳\n")
    r = docx_export.export({"title": "宣传片分镜", "body": body})
    assert r["ok"] and r["path"].endswith("宣传片分镜.docx") and r["files"] == [r["path"]]
    doc = Document(r["path"])
    texts = [p.text for p in doc.paragraphs]
    assert texts[0] == "宣传片分镜" and "宣传片分镜" not in texts[1:], "正文第一行重复的标题不再写一遍"
    assert "第一场" in texts and "画面: 清晨园区航拍" in texts and "旁白要稳" in texts
    bold = [r.text for p in doc.paragraphs for r in p.runs if r.bold]
    assert "画面" in bold
    assert [[c.text for c in row.cells] for row in doc.tables[0].rows] == [["场次", "时长"], ["1", "8 秒"]]
    again = docx_export.export({"title": "宣传片分镜", "body": "x"})
    assert again["path"].endswith("宣传片分镜 (2).docx")
    assert docx_export.export({"title": "a/b", "body": "x", "filename": "../evil"})["path"].startswith(str(tmp_path))


def test_导出_word_内容空的_说清楚():
    r = docx_export.export({"title": "x", "body": "  "})
    assert not r["ok"] and "空的" in r["error"]


def test_导出_word_在工作流里能接前面那步(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    wf = {"kind": "workflow", "params": {}, "steps": [
        {"id": "doc", "action": "export_docx", "params": {"title": "分镜", "body": "${steps.prev.markdown}"}}]}
    assert engine.validate({**wf, "steps": [{"id": "prev", "xiaonian": "写分镜", "returns": {"markdown": "text"}}]
                            + wf["steps"]}, skill_lookup=lambda _p: None) == []
    out = engine.run_block(wf["steps"][0], {"title": "分镜", "body": "# 分镜\n- 第一场"}, {"steps": {}})
    assert out["path"].endswith(".docx") and out["files"] == [out["path"]]


# ── 对话里改总览卡上的草稿 / 卡上一句话改 ─────────────────────────────

@pytest.fixture()
def env(tmp_path, monkeypatch):
    skills = tmp_path / "skills"
    skills.mkdir()
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path / "catfish"))
    monkeypatch.setattr(ops, "_skill_search_roots", lambda: [skills])
    monkeypatch.setattr(ops, "_local_skills_root", lambda: skills)
    return tmp_path


PLAN = {"experts": [{"name": "宣传片专员", "description": "宣传片", "persona": "我是宣传片专员。", "jobs": [
    {"when": "写个分镜", "title": "写分镜脚本", "kind": "new", "goal": "按主题写分镜脚本, 交一份 Word"},
    {"when": "宣传片怎么拍", "title": "拍摄问答", "kind": "knowledge"}]}]}


def _wf(steps):
    return {"title": "写分镜脚本", "description": "写分镜", "workflow": {
        "kind": "workflow", "params": {"topic": {"type": "text", "label": "主题", "default": "园区"}}, "steps": steps}}


DRAFT = [{"id": "script", "xiaonian": "按 ${params.topic} 写分镜脚本 markdown", "returns": {"markdown": "text"}}]
CLAIM = DRAFT + [{"id": "word", "xiaonian": "用 execute_code 调 python-docx 生成 Word 文件", "returns": {"path": "text"}}]
FIXED = DRAFT + [{"id": "word", "action": "export_docx",
                  "params": {"title": "${params.topic} 分镜", "body": "${steps.script.markdown}"}}]


def fake_llm(replies):
    seen = []

    def llm(prompt):
        seen.append(prompt)
        if "拆出要搭哪几个专家" in prompt:
            return json.dumps(PLAN, ensure_ascii=False)
        return json.dumps(replies.pop(0), ensure_ascii=False)
    llm.seen = seen
    return llm


def test_起草时_小鲶那步做不到的事_交回去改成积木(env):
    llm = fake_llm([_wf(CLAIM), _wf(FIXED)])
    b = eb.plan("帮我搭个宣传片制作专员", llm=llm)
    j = b["experts"][0]["jobs"][0]
    assert j["problems"] == [] and "不能调工具" in llm.seen[-1]
    assert [c["label"] for c in j["chain"]][1] == "导出 Word 文件"
    assert "export_docx" in llm.seen[1] and "最多 8 步" in llm.seen[1]


def test_对话里改草稿_不掉出总览卡_卡上问题跟着变_不能在对话里单独保存(env):
    b = eb.plan("帮我搭个宣传片制作专员", llm=fake_llm([_wf(FIXED)] * 1))
    name = b["experts"][0]["jobs"][0]["draft"]
    r = workflow_tool.dispatch("catfish_workflow", {"action": "draft", "name": name, **_wf(CLAIM)})
    assert not r["ok"] and "不要跟员工说改好了" in r["note"]
    assert all("(word)" not in p for p in r["problems"]), "给小鲶的问题也是大白话"
    assert store.load_draft(name)["from_build"] == b["build_id"][1:7]
    assert store.list_drafts()[0]["from_build"], "还挂在总览卡上"
    card = eb.load(b["build_id"])["experts"][0]["jobs"][0]
    assert card["problems"] and "不能调工具" in card["problems"][0], "不用重新打开卡, 问题已经刷新"
    saved = workflow_tool.dispatch("catfish_workflow", {"action": "save", "name": name, "confirmed": True})
    assert not saved["ok"] and "总览卡" in saved["error"]
    ok = workflow_tool.dispatch("catfish_workflow", {"action": "draft", "name": name, **_wf(FIXED)})
    assert ok["ok"] and eb.load(b["build_id"])["experts"][0]["jobs"][0]["problems"] == []


def test_卡上一句话改_在现有草稿上改_卡跟着刷新(env, monkeypatch):
    b = eb.plan("帮我搭个宣传片制作专员", llm=fake_llm([_wf(CLAIM), _wf(CLAIM), _wf(CLAIM)]))
    assert b["experts"][0]["jobs"][0]["problems"], "三轮都没改好, 卡上标出来"
    llm = fake_llm([_wf(FIXED)])
    monkeypatch.setattr(eb, "_llm", llm)
    nb = eb.revise(b["build_id"], 1, 0, "")
    j = nb["experts"][0]["jobs"][0]
    assert j["problems"] == [] and j["draft"] == b["experts"][0]["jobs"][0]["draft"]
    assert "现在的工作流" in llm.seen[0] and "把这些问题修好" in llm.seen[0] and "第 2 步 是小鲶" in llm.seen[0]
    monkeypatch.setattr(eb, "_llm", fake_llm([_wf(FIXED[:1])]))
    nb = eb.revise(b["build_id"], 1, 0, "不要 Word 了")
    assert len(nb["experts"][0]["jobs"][0]["chain"]) == 1
    with pytest.raises(ValueError, match="不是新搭的"):
        eb.revise(b["build_id"], 1, 1, "x")


def test_保存前按草稿现在的样子再查一遍(env):
    b = eb.plan("帮我搭个宣传片制作专员", llm=fake_llm([_wf(FIXED)]))
    name = b["experts"][0]["jobs"][0]["draft"]
    d = store.load_draft(name)
    store.save_draft({**d, "workflow": _wf(CLAIM)["workflow"]})   # 编辑器里改坏了, 卡还没刷新
    with pytest.raises(ValueError, match="不能调工具"):
        eb.apply(b["build_id"])


def test_步骤链上小鲶那步的名字_参数换成中文名_取不到的去掉():
    P = {"params": {"topic": {"type": "text", "label": "主题"}, "duration": {"type": "text", "label": "时长"}}}
    label = lambda t, wf=P: engine.step_label({"id": "a", "xiaonian": t}, wf)  # noqa: E731
    assert label("按 ${params.topic}、${params.duration} 写分镜") == "小鲶：按 主题、时长 写分镜"
    assert label("按 ${params.x}、${steps.a.b} 写分镜") == "小鲶：写分镜"
    assert label("核对 ${steps.m.matches} 的附件", None) == "小鲶：核对的附件"
