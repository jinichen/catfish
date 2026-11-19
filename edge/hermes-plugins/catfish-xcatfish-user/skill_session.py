"""把当前会话号带给「提议技能」(11/19, 见 tool-bridge skill_from_code.py)。

员工在对话里把活做通、说「固化成技能」时, catfish_propose_skill 要拿到**这次对话**里跑通的那段代码,
否则装出来的技能程序只有步骤注释 (一跑就「未实现」)。tool-bridge 不知道是哪个会话, hermes 的
pre_tool_call 钩子知道 (session_id) —— 用 modify 指令把它塞进参数 `_session_id`。只动这两个工具。

11/19 真走四遍: 模型多半自己用 hermes 的 skill_manage「先把程序写成文件 → 自测 → 装」, 质量不错 (会自造边界数据
测出真 bug)。试过把它拦下改走 catfish_propose_skill —— 没拦到 (它是写文件再跑文件, 跑的代码里没有存文件那一行),
也没必要: 不跟模型的自然做法较劲, 这里只保证走 propose 那条时也是真能跑的技能。
"""
from __future__ import annotations

from typing import Any, Optional

_TOOLS = ("catfish_propose_skill", "catfish_propose_skill_revision")


def skill_session_hook(tool_name: str = "", args: Optional[dict] = None, session_id: str = "",
                       **_kw: Any) -> Optional[dict]:
    try:
        name = str(tool_name or "")
        if not session_id or not any(name == t or name.endswith("_" + t) for t in _TOOLS):
            return None
        return {"action": "modify", "args": {"_session_id": session_id}}
    except Exception:  # noqa: BLE001 — 钩子出错不能挡住工具
        return None
