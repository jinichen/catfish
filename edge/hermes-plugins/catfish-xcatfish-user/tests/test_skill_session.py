"""11/19 「固化成技能」要带上这次对话里跑通的代码: pre_tool_call 钩子把会话号塞给 catfish_propose_skill。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("_skill_session", Path(__file__).parent.parent / "skill_session.py")
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def test_只给提议技能塞会话号():
    hook = mod.skill_session_hook
    assert hook(tool_name="mcp__catfish_tools__catfish_propose_skill", args={}, session_id="s1") == \
        {"action": "modify", "args": {"_session_id": "s1"}}
    assert hook(tool_name="catfish_propose_skill_revision", args={}, session_id="s2")["args"] == {"_session_id": "s2"}
    assert hook(tool_name="mcp__catfish_tools__catfish_install_proposal", args={}, session_id="s1") is None
    assert hook(tool_name="execute_code", args={}, session_id="s1") is None
    assert hook(tool_name="catfish_propose_skill", args={}, session_id="") is None
    assert hook(tool_name=None, args=None) is None

