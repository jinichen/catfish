"""/goal Ralph loop — 锁定目标多轮不偏 (BL-HERMES013-3, 5/11).

# 为啥

借鉴 Hermes 0.13 `/goal` slash command — "agent doesn't forget what you
asked it to do, locked onto a target across turns".

catfish 已有的 L7/L8 plan-only retry + FIX46 请示停顿是**事后纠偏**;
/goal 是**事前锚定** — 员工一句话设定目标, gateway 每轮自动注入 system,
LLM 跑偏时被持续拉回.

# 设计

文件: `~/.catfish/session_goal.txt` (单行文本, 简单 + 跨 session 持久).
**单员工全局**: 当前不区分员工, 同一时间一个 goal. 多员工 / 多会话场景
Q3 再细化 (跟 BL-ARCH1 catfish-web 中央门户路线一起).

# 命令 (gateway 端早期拦截, 不调 LLM, 不计 quota)

  /goal <目标描述>     设新 goal, 覆盖旧
  /goal               显示当前 goal
  /goal clear         清除 goal
  /goal 清除           同上 (中文)
  /goal off           同上

# 注入

每次 chat completion 调 inject_session_goal, 在 last system message 末尾
追加一段:

    ## 🎯 当前锁定目标 (/goal)
    <goal 内容>

    所有 tool_call / response 都应**围绕这个目标**. 跑偏要主动拉回.
    完成后说"已完成 goal", 让员工 /goal clear.

# 跟 employee_journal / session_facts 关系

  journal: 跨 session 工作总结 (BL-FIX40 15KB cap)
  facts:   当前 session 硬事实 (catfish_remember 写)
  goal:    当前会话锁定目标 (slash 命令写)

注入顺序: identity → session_history → journal → facts → **goal (最末)** →
multimodal → tool_capability

goal 注最末 — system 里离 messages 最近, recency bias 强, 模型每轮都看到.
"""
from __future__ import annotations

import logging
from copy import deepcopy
from pathlib import Path
from typing import Any

logger = logging.getLogger("catfish.gateway.session_goals")

#: goal 单文件存储路径
GOAL_PATH = Path.home() / ".catfish" / "session_goal.txt"

#: goal 最大长度 (防 prompt injection 塞超长 fake goal)
MAX_GOAL_LEN = 500


def read_session_goal() -> str | None:
    """读当前 goal, 没设 / 文件损坏 → None. 永不抛."""
    if not GOAL_PATH.exists():
        return None
    try:
        text = GOAL_PATH.read_text(encoding="utf-8").strip()
        if not text:
            return None
        if len(text) > MAX_GOAL_LEN:
            return text[:MAX_GOAL_LEN] + "…[截断]"
        return text
    except OSError as e:
        logger.warning("读 session_goal.txt 失败: %s", e)
        return None


def write_session_goal(goal: str) -> bool:
    """写 goal. 返成功 / 失败."""
    if not goal or not isinstance(goal, str):
        return False
    goal = goal.strip()[:MAX_GOAL_LEN]
    try:
        GOAL_PATH.parent.mkdir(parents=True, exist_ok=True)
        GOAL_PATH.write_text(goal, encoding="utf-8")
        logger.info("/goal set: %s", goal[:100])
        return True
    except OSError as e:
        logger.warning("写 session_goal.txt 失败: %s", e)
        return False


def clear_session_goal() -> bool:
    """清除 goal. 返是否真清了 (不存在算成功)."""
    try:
        GOAL_PATH.unlink(missing_ok=True)
        logger.info("/goal cleared")
        return True
    except OSError as e:
        logger.warning("清 session_goal.txt 失败: %s", e)
        return False


def detect_goal_command(messages: list[dict[str, Any]]) -> tuple[bool, str | None]:
    """检测 messages 最后一条 user 是不是 /goal 命令.

    Returns:
      (is_goal_command, response_text):
        - True + response: 是命令, response 是给员工的回复
        - False + None: 不是命令, caller 走正常 chat
    """
    if not messages:
        return False, None
    last = messages[-1]
    if not isinstance(last, dict) or last.get("role") != "user":
        return False, None
    content = last.get("content", "")
    # multimodal 取 text 部分
    if isinstance(content, list):
        content = " ".join(
            p.get("text", "")
            for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        )
    if not isinstance(content, str):
        return False, None
    text = content.strip()

    # /goal (无参数) → 显示当前
    if text == "/goal":
        cur = read_session_goal()
        if cur:
            return True, f"🎯 当前锁定目标:\n\n{cur}\n\n所有对话都围绕这个目标. 用 `/goal clear` 清除."
        return True, "🎯 还没设目标. 用 `/goal <你想做的事>` 锁定一个, 之后多轮对话不跑偏."

    # /goal clear / /goal 清除 / /goal off
    if text in ("/goal clear", "/goal 清除", "/goal off", "/goal 关闭"):
        clear_session_goal()
        return True, "✓ 已清除目标. 接下来对话不再受 goal 约束."

    # /goal <目标>
    if text.startswith("/goal "):
        goal = text[len("/goal "):].strip()
        if not goal:
            return True, "用法: `/goal <你想做的事>` 锁定目标. 例: `/goal 帮我把 EIS 待办全清空`."
        write_session_goal(goal)
        return (
            True,
            f"🎯 已锁定目标:\n\n**{goal}**\n\n"
            "后续多轮对话我都围绕这个目标做事, 跑偏时会自动拉回. "
            "完成后用 `/goal clear` 清除."
        )

    return False, None


def inject_session_goal(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """在最后 system message 末尾追加当前 goal. goal 空 → 原样返."""
    if not messages:
        return messages

    goal = read_session_goal()
    if not goal:
        return messages

    block = (
        "\n\n## 🎯 当前锁定目标 (员工 /goal 设置)\n\n"
        f"**{goal}**\n\n"
        "**你所有的 tool_call / response 都应围绕这个目标**. 中途如果做别的事, "
        "完成后必须**主动拉回**继续推进 goal — 不要被员工临时插话带偏永久. "
        "完成 goal 后说\"已完成: <goal>\", 提示员工 /goal clear.\n\n"
        "如果员工临时让你做 goal **完全无关**的事 (例 '帮我看下天气'), 做完后回到 goal, "
        "不要假装 goal 没了.\n"
    )

    last_system_idx = -1
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "system":
            last_system_idx = i
            break
    if last_system_idx < 0:
        return messages

    cur = messages[last_system_idx].get("content", "")
    if not isinstance(cur, str):
        return messages
    if "当前锁定目标 (员工 /goal" in cur:
        # 幂等
        return messages

    out = deepcopy(messages)
    out[last_system_idx]["content"] = (
        out[last_system_idx]["content"].rstrip() + block
    )
    if logger.isEnabledFor(logging.INFO):
        logger.info("inject_session_goal: 注入 goal=%s", goal[:60])
    return out


__all__ = [
    "GOAL_PATH",
    "MAX_GOAL_LEN",
    "read_session_goal",
    "write_session_goal",
    "clear_session_goal",
    "detect_goal_command",
    "inject_session_goal",
]
