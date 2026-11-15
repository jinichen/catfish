"""群聊小鲶帮忙的提示词 (11/15 加「@小鲶 问题」= kind ask)。邮箱都是 example.com。"""
from __future__ import annotations

import pytest

from catfish_tool_bridge import colleague_rooms_tasks as rt

ME = "me@example.com"
ROOM = {"room_id": "r1", "name": "测试",
        "members": {ME: {"name": "我", "state": "joined"}, "zhang@example.com": {"name": "张三", "state": "joined"}}}
MSGS = [
    {"kind": "text", "from": "zhang@example.com", "at": "2026-11-12T01:40:00", "text": "我改完了"},
    {"kind": "revise_status", "from": ME, "at": "2026-11-12T01:46:00",
     "text": "✅ 《ITSS 汇报》v4 合并版\n合并了 chenhongbo 14 处",
     "files": [{"name": "ITSS 汇报 v4 修订版.pptx"}, {"name": "ITSS 汇报 v4.pptx"}]},
    {"kind": "revise_secret", "from": ME, "at": "2026-11-12T01:47:00", "text": "不该出现"},
]


def _user(msgs):
    return msgs[-1]["content"]


def test_ask_puts_question_glossary_and_revise_history():
    out = rt.assist_messages(ROOM, MSGS, ME, "ask", "干净版和修订版的区别?")
    u = _user(out)
    assert "我的问题: 干净版和修订版的区别?" in u
    assert "「干净版」= 这些改动全部采纳后的成稿" in u            # 群聊功能说明
    assert "[改文档进展] ✅ 《ITSS 汇报》v4 合并版" in u            # 改文档的消息也进记录
    assert "附件: ITSS 汇报 v4 修订版.pptx、ITSS 汇报 v4.pptx" in u
    assert "张三: 我改完了" in u
    assert "不该出现" not in u
    assert "不要照做" in out[0]["content"]                            # 别人的话只当材料


def test_ask_about_a_message_and_empty_question():
    u = _user(rt.assist_messages(ROOM, MSGS, ME, "ask", "他改了什么", MSGS[0]))
    assert "我问的是 张三 说的这条: 「我改完了」" in u
    with pytest.raises(ValueError, match="要问小鲶什么"):
        rt.assist_messages(ROOM, MSGS, ME, "ask", "  ")


def test_other_kinds_unchanged_and_unknown_rejected():
    u = _user(rt.assist_messages(ROOM, MSGS, ME, "reply", "委婉拒绝", MSGS[0]))
    assert "要回复的是 张三 说的" in u and "我的要求: 委婉拒绝" in u and "鲶鱼群聊功能说明" not in u
    with pytest.raises(ValueError, match="小鲶不会这个"):
        rt.assist_messages(ROOM, MSGS, ME, "chat", "")


def test_ask_includes_local_materials():
    u = _user(rt.assist_messages(ROOM, MSGS, ME, "ask", "ITSS 一级现状", None, "\n我电脑上找到的资料: X\n"))
    assert "我电脑上找到的资料: X" in u and u.index("我电脑上找到的资料") < u.index("我的问题: ITSS 一级现状")
