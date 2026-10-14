"""P57: /new /undo 的确认 —— 一句话 + 回 1/2 (10/14 鸿波「怎么还是这么麻烦的回复？」)。不 import hermes。"""
from __future__ import annotations

import asyncio
import time

import plugin_approval_ux as ux
import plugin_slash_confirm_ux as p57
import plugin_weixin_zh as zh

# hermes run.py _maybe_confirm_destructive_slash 原样拼的 message
_HERMES_NEW = (
    "⚠️ **Confirm /new**\n\n"
    "This starts a fresh session and discards the current conversation history.\n\n"
    "Choose:\n• **Approve Once** — proceed this time only\n"
    "• **Always Approve** — proceed and silence this prompt permanently\n"
    "• **Cancel** — keep current conversation\n\n"
    "_Text fallback: reply `/approve`, `/always`, or `/cancel`._"
)


def test_prompt_is_one_sentence_and_1_2():
    for cmd, detail in [("new", ""), ("undo", "This removes the last user/assistant exchange from history."),
                        ("undo", "This removes the last 3 user turns from history."), ("clear", "")]:
        s = p57.confirm_prompt(cmd, detail)
        assert "回复 1 " in s and "2 不要" in s, s
        for bad in ("/approve", "/always", "/cancel", "总是", "_", "**"):
            assert bad not in s, (bad, s)
    assert "最近 3 轮" in p57.confirm_prompt("undo", "This removes the last 3 user turns from history.")
    assert "长期记忆还在" in p57.confirm_prompt("new")


class _Ev:
    def __init__(self, text):
        self.text = text
        self.source = "src"


class _Runner:
    def _session_key_for_source(self, source):
        return "k1"


def _run_request(message=_HERMES_NEW, command="new"):
    got = {}

    async def orig(self, *, event, command, title, message, handler):
        got.update(message=message, handler=handler)
        return message

    out = asyncio.run(p57.wrap_request(orig, ux)(_Runner(), event=_Ev("/new"), command=command,
                                                    title=f"/{command}", message=message,
                                                    handler=_fake_handler))
    return out, got


async def _fake_handler(choice):
    return f"HERMES:{choice}"


def test_request_replaces_message_and_records_time(monkeypatch):
    monkeypatch.setattr(ux, "_PROMPT_SENT_AT", 0.0)
    before = time.time()
    out, got = _run_request()
    assert out == p57.confirm_prompt("new")
    assert ux._PROMPT_SENT_AT >= before                      # P56 据此放行重复的「1」
    monkeypatch.setattr(ux, "_LAST_RESOLVED_AT", 0.0)
    assert asyncio.run(got["handler"]("once")) == "HERMES:once"
    assert ux._LAST_RESOLVED_AT >= before
    assert asyncio.run(got["handler"]("cancel")) == "好, 不开新对话, 接着聊。"


def _drive(text, pending=True, tool_approval=False):
    seen = []

    async def orig(self, event):
        seen.append(event.text)
        return "ORIG"

    h = p57.wrap_handle_message(orig, ux, lambda k: {"confirm_id": "1"} if pending else None,
                                lambda k: tool_approval)
    asyncio.run(h(_Runner(), _Ev(text)))
    return seen[0]


def test_replies_become_hermes_confirm_commands():
    assert _drive("1") == "/approve"
    assert _drive("好的") == "/approve"
    assert _drive("１") == "/approve"
    assert _drive("2") == "/cancel"
    assert _drive("不要") == "/cancel"
    assert _drive("等下\n2") == "/cancel"                    # 微信合并的两条, 最后一行是回答


def test_leaves_everything_else_alone():
    assert _drive("帮我查一下英伟达股价") == "帮我查一下英伟达股价"   # 认不出 → 普通消息
    assert _drive("/approve") == "/approve"                        # 本来就是命令
    assert _drive("1", pending=False) == "1"                       # 没有确认在等
    assert _drive("1", tool_approval=True) == "1"                  # 执行审批在等 → 归 P52


def test_wechat_reset_notice_drops_model_block():
    notice = ("✨ 新会话已启动！\n\n◆ Model: `catfish-public-qwen-flash`\n◆ Provider: custom\n"
              "◆ Context: 128K tokens (detected)\n◆ Endpoint: http://127.0.0.1:8999\n✦ 提示：/debug uploads a bundle")
    assert zh._translate_hermes_zh(notice) == "✨ 新对话开始了。"
