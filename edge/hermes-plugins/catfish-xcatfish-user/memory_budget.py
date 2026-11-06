"""常驻记忆 (MEMORY.md / USER.md) 写入时的硬上限 (11/06)。memory_enforce_hook 一进来先过这里, 不调模型。

# 为什么

鸿波 11/06「memory.md 又快满了」: 21 条 3779/4000 字, 没有一条重复 —— 是每次员工纠正 / 定口径小鲶就记一条做事规矩,
而且一条写一段 (6 条超过 200 字, 最长 577 字, 自己都写着「细节已移入 skill/wiki, 此处只留触发指针」)。
原来只有 memory_router 的 propose_skill_hint 软提示, 模型照写不误。

# 规矩

- 单条上限: MEMORY.md 200 字, USER.md 150 字。超了拦下, 让它写短; 细节写进 skill / 知识库, 这里只留一句指针。
  改写 (replace) 时把一条长的改短是在变好, 放行 (哪怕还超上限)。
- 快满了 (加上这条超过上限的 80%) 就不许再新加条目: 跟同类的已有条目合成一条 (replace), 或者细节挪去 skill / 知识库。
  合并 (replace) 只要不超过 hermes 自己的总上限就放行 —— 不然快满时什么都存不进去。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger("catfish.xcatfish_user.plugin")

MAX_ENTRY = {"memory": 200, "user": 150}
FULL_RATIO = 0.8
_SEP = "\n§\n"
_DEFAULT_LIMIT = {"memory": 4000, "user": 2000}   # 鲶鱼规定配置里的值 (hermes 出厂 2200 / 1375)
_FILE = {"memory": "MEMORY.md", "user": "USER.md"}


def _hermes_home() -> Path:
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env)
    try:
        from hermes_constants import get_hermes_home  # noqa: PLC0415
        return Path(get_hermes_home())
    except Exception:  # noqa: BLE001
        return Path.home() / ".hermes"  # windows-parity: 只在 hermes_constants 导不进时兜底, hermes 进程内总能导进


def _limit(target: str) -> int:
    try:
        import yaml  # noqa: PLC0415
        cfg = yaml.safe_load((_hermes_home() / "config.yaml").read_text(encoding="utf-8")) or {}
        mem = cfg.get("memory") or {}
        key = "user_char_limit" if target == "user" else "memory_char_limit"
        return int(mem.get(key) or _DEFAULT_LIMIT[target])
    except Exception:  # noqa: BLE001
        return _DEFAULT_LIMIT[target]


def _entries(target: str) -> list[str]:
    try:
        text = (_hermes_home() / "memories" / _FILE[target]).read_text(encoding="utf-8")
    except OSError:
        return []
    return [e.strip() for e in text.split("§") if e.strip()]


def _total(entries: list[str]) -> int:
    return len(_SEP.join(entries))


def check(target: str, action: str, content: str, old_text: str = "",
          entries: Optional[list[str]] = None, limit: Optional[int] = None) -> Optional[str]:
    """不合规返回给模型看的拦截说明, 合规返回 None。entries / limit 给测试用。"""
    if target not in MAX_ENTRY or action not in ("add", "replace"):
        return None
    content = (content or "").strip()
    entries = _entries(target) if entries is None else entries
    limit = _limit(target) if limit is None else limit
    cap = MAX_ENTRY[target]
    name = _FILE[target]
    old_entry = next((e for e in entries if old_text and old_text in e), "") if action == "replace" else ""

    if len(content) > cap and not (old_entry and len(content) < len(old_entry)):
        return (
            f"⚠️ 这条有 {len(content)} 字, {name} 单条最多 {cap} 字 (每次对话都整篇带上)。"
            "改短: 只写这条规矩本身 (做什么 / 不做什么 / 以什么为准), 去掉举例、来龙去脉、谁哪天定的; "
            "细节 / 步骤写进对应 skill (catfish_propose_skill) 或知识库 (catfish_wiki_update / catfish_wiki_create), "
            "这里只留一句指针 (如「X 的口径见知识库[[页面名]]」)。"
        )
    used = _total(entries)
    if action == "add":
        after = used + len(_SEP) + len(content)
        if after > limit * FULL_RATIO:
            return (
                f"⚠️ {name} 已用 {used}/{limit} 字 ({used * 100 // max(limit, 1)}%), 快满了, 不再新加条目。"
                "先看已有条目里有没有同一类的规矩, 有就合成一条 (memory action=replace, old_text=那条里的一段原文); "
                "没有就把这条的细节写进 skill / 知识库, 再把已有条目里最长的那条改短腾出地方。"
            )
    else:
        after = used - len(old_entry) + len(content)
        if after > limit and len(content) > len(old_entry):
            return f"⚠️ 改完 {name} 会到 {after}/{limit} 字, 超过上限。先把别的长条目改短, 或把这条的细节挪去 skill / 知识库。"
    return None
