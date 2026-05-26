"""STUB — DEPRECATED 5/26 (hermes 0.14 原生 /goal + /subgoal 替代).

# 历史

BL-HERMES013-3 (5/11) catfish 在 hermes 0.13 之前抢先实现 /goal Ralph loop
(原 ~250 行: read/write/clear/detect_goal_command/inject_session_goal). 5/19
catfish cutover 到 hermes (Companion → hermes 8642 → gateway 8999) 后, hermes
0.14 (v2026.5.16) **原生 /goal + /subgoal** (#25449), catfish 这一套变成:

  - detect_goal_command: 死代码 (hermes 端先拦, gateway 永远收不到 /goal)
  - inject_session_goal: 状态分裂源 (catfish 注入 ~/.catfish/session_goal.txt,  # noqa: BOUNDARY (docstring 历史描述)
    hermes 自己也注入 hermes 的 goal 状态, 两套互不知道)

# 5/26 鸿波拍板砍

砍 catfish 整套, 让 hermes 原生 /goal 接管. 净 -385 LOC (gateway 210 +
companion 175 + BriefingCard 输入框).

# 这个 stub 留作

- fail-loud: 任何 `from .session_goals import ...` 立刻抛
- git blame 历史指引

# 关联

- hermes 0.14 release notes: https://github.com/NousResearch/hermes-agent/releases/tag/v2026.5.16
- catfish 老实现 BL-HERMES013-3: docs/HERMES-013-ALIGN.md L137-143
- 同批砍: edge/companion-app/src-tauri/src/commands/session_goal.rs
- 同批清: BriefingCard 🎯 今日重点输入框 (调 session_goal_write Tauri)
"""
from __future__ import annotations

_DEPRECATED_NOTICE = (
    "catfish session_goals 5/26 砍 — hermes 0.14 原生 /goal + /subgoal 替代. "
    "Companion 用户输 /goal xxx 现在被 hermes 直接拦 (catfish gateway 永远收不到)."
)


def __getattr__(name: str):  # noqa: D401
    raise RuntimeError(f"[DEPRECATED 5/26] {_DEPRECATED_NOTICE} (attr: {name})")
