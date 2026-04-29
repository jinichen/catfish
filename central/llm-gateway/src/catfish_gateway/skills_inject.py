"""把 catfish skills 列表注入到 system prompt.

跟 inject_session_facts / inject_stats_guard 同套机制:
  - 在最后一条 system message 末尾追加文本
  - 没有 system message 时, 不主动加 (caller 应已经走过 inject_identity)

# 缓存

skills 扫描成本不大 (<50ms), 但每条 chat 都扫一遍也没必要. 用 mtime 失效:
  - 内存里缓存 [skills_list, fingerprint]
  - 每次注入前比较 fingerprint (skills_root 下所有 SKILL.md 的 mtime sum)
  - 不变直接用缓存; 变了重新扫

这样 SKILL.md 改完不用重启 gateway, 但稳态下零成本.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any

from .skills_loader import SkillMeta, discover_skills, format_skills_block

logger = logging.getLogger("catfish.gateway.skills_inject")


# ── 简单内存缓存, fingerprint 失效 ──────────────────────────────

_cache: dict[str, Any] = {
    "fingerprint": None,
    "block": "",
    "skills": [],
}


def _fingerprint(skills: list[SkillMeta]) -> str:
    """skill 列表指纹 — mtime sum + path. 变化即重扫."""
    parts = []
    for s in skills:
        try:
            mt = s.skill_md_path.stat().st_mtime
        except OSError:
            mt = 0
        parts.append(f"{s.skill_path}:{mt}")
    return "|".join(parts)


def _get_skills_block() -> str:
    """缓存版本: 取当前 skills 列表对应的 system prompt 块."""
    skills = discover_skills()
    fp = _fingerprint(skills)
    if fp == _cache["fingerprint"] and _cache["block"]:
        return _cache["block"]

    block = format_skills_block(skills)
    _cache["fingerprint"] = fp
    _cache["block"] = block
    _cache["skills"] = skills
    if skills:
        logger.info(
            "skills_inject: cache 刷新, %d skill, block %d 字节",
            len(skills),
            len(block),
        )
    return block


def reset_cache() -> None:
    """测试用 — 强制下次重扫."""
    _cache["fingerprint"] = None
    _cache["block"] = ""
    _cache["skills"] = []


# ── 主入口 ──────────────────────────────────────────────────────


def inject_skills_catalog(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """在最后一条 system message 末尾追加 skill catalog block.

    没有 skill / 没有 system message → 原样返回.
    幂等 (block 已经在末尾时不重复追加).
    """
    block = _get_skills_block()
    if not block:
        return messages

    if not messages:
        return messages

    # 找最后一条 system
    last_system_idx = -1
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "system":
            last_system_idx = i
            break
    if last_system_idx < 0:
        # 没 system message — 不注入. inject_identity 应已经在前面跑了.
        return messages

    # 幂等: 已有同样 block 不再追加
    current_content = messages[last_system_idx].get("content", "")
    if isinstance(current_content, str) and block.strip() in current_content:
        return messages

    # 深拷贝避免改动 caller 持有的 messages 引用
    out = deepcopy(messages)
    sys_msg = out[last_system_idx]
    if isinstance(sys_msg.get("content"), str):
        sys_msg["content"] = sys_msg["content"].rstrip() + "\n" + block
    else:
        # content 是 multimodal list 等: 转字符串处理太脏, 跳过 (rare)
        logger.debug("skills_inject: system content 不是 str, 跳过")
    return out


__all__ = [
    "inject_skills_catalog",
    "reset_cache",
]
