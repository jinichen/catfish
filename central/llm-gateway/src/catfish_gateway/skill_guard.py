"""skill_guard — 工程级强制保护, 防 LLM 绕开 catfish skill 自走 execute_code.

# 设计原则 (BL-SKILL-METADATA-DYNAMIC, 5/15 鸿波 '半半的工作造成更大困恼')

**零硬编码**. 每个 skill 在 SKILL.md frontmatter 自己声明:
- `triggers`: 关键词列表 (员工 user message 命中任一 → 触发铁律)
- `kind`: "procedural" (script.py 自跑出文件) 或 "instructional" (script.py 只
  返指令, agent 接力 read_file / write_file)

skill_guard 启动时 / 每次 chat 时:
1. 从 `discover_skills()` 拿当前 skill 列表
2. 把所有 triggers 合并成动态 regex (跟 skill_path 反向映射)
3. 检测员工 user message 命中哪个 skill 的 triggers
4. 在 system prompt 末尾追加铁律 — **铁律里的 skill_path 是命中那个 skill 真名**,
   不是写死的字符串

加新 skill 只需: 写 SKILL.md frontmatter, **不动 gateway 代码**.

# 跟 inject_skills_catalog / inject_stats_guard 的关系

  顺序: identity → session_facts → stats_guard → skills_catalog → **skill_guard** → ...
  inject_skills_catalog: 提供 skill 列表 (静态, 描述能力)
  skill_guard:           检测意图 + 工具就位状况, 触发式强提醒 (动态)

# 历史

  - BL-FIX-SKILL-GUARD (2026-04-29): 第一版, hardcode 公文关键词
  - BL-SKILL-INTENT-PPT (2026-05-15): 加 PPT/magazine 触发词 (硬编码)
  - BL-SKILL-METADATA-DYNAMIC (2026-05-15): 删全部硬编码, 改 dynamic
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from copy import deepcopy
from typing import Any

logger = logging.getLogger("catfish.gateway.skill_guard")


# ─── trigger → skill 映射 (动态构建) ─────────────────────────────


def _build_trigger_map(
    skills: Iterable[Any],
) -> tuple[re.Pattern[str] | None, dict[str, str]]:
    """从 skill 列表构建 (regex, trigger→skill_path map).

    Args:
        skills: SkillMeta 列表 (有 .triggers / .skill_path / .deprecated 属性)

    Returns:
        (compiled_regex | None, {trigger_lower: skill_path}).
        regex 为 None 时表示无任何 trigger → has_skill_intent 永远返 False.

    设计:
    - deprecated skill 的 trigger 不参与匹配 (下线 skill 不该被推荐)
    - trigger 用 word-boundary `\b` 包起来防止子串误匹配 ("PPT" 不该匹配 "POPPET")
      但中文没 word boundary, 直接放进去
    - 长 trigger 排前面 (re.compile 时按长度倒序), 这样 "立项报告" 优先匹配
      "立项" 这种短词
    """
    trigger_to_skill: dict[str, str] = {}
    for s in skills:
        if getattr(s, "deprecated", False):
            continue
        skill_path = getattr(s, "skill_path", "")
        triggers = getattr(s, "triggers", ()) or ()
        for t in triggers:
            t = str(t).strip()
            if not t:
                continue
            key = t.lower()
            # 同一 trigger 命中多个 skill — 第一个赢 (skill 列表已按 path 字母序排)
            # 实际意味着同名词请在不同 skill triggers 里去重
            if key not in trigger_to_skill:
                trigger_to_skill[key] = skill_path

    if not trigger_to_skill:
        return None, {}

    # 按 trigger 长度倒序, 长的优先 (防 "立项报告" 被 "立项" 抢)
    sorted_triggers = sorted(trigger_to_skill.keys(), key=len, reverse=True)

    # 拼 regex. ASCII trigger 用 word boundary, 中文不用
    parts = []
    for t in sorted_triggers:
        is_ascii = all(ord(c) < 128 for c in t)
        if is_ascii:
            parts.append(rf"\b{re.escape(t)}\b")
        else:
            parts.append(re.escape(t))
    pattern = re.compile("(" + "|".join(parts) + ")", re.IGNORECASE)
    return pattern, trigger_to_skill


# ─── catfish skill_path 显式提及 (永远触发, 不依赖 SKILL.md triggers) ───────
#
# 当员工在 user message 里直接 "用 creative/foo skill" / "调 department/bar"
# 形式点名某个 skill, 不管 triggers 命没命中, 都强制触发铁律 + 把那个 skill_path
# 列进推荐列表. 这里硬编码"命名空间前缀"是无奈 — skill path 第一级是部门 / 类
# 别, 不在 SKILL.md 里, 而是 directory layout. 想完全动态需要扫 skills root
# 第一层目录, 实现成本不值. 容忍这一个硬编码.
_SKILL_PATH_RE = re.compile(
    r"[a-z][a-z0-9_-]+/[a-z][a-z0-9_-]+",
    re.IGNORECASE,
)


def _extract_explicit_skill_paths(
    text: str, skills: Iterable[Any]
) -> list[str]:
    """从 user message 抽出员工显式点名的 skill_path. 只返回 skills 列表里真存在的."""
    known = {getattr(s, "skill_path", ""): s for s in skills}
    found: list[str] = []
    for m in _SKILL_PATH_RE.findall(text):
        if m in known and not getattr(known[m], "deprecated", False):
            if m not in found:
                found.append(m)
    return found


# ─── 公开 API ──────────────────────────────────────────────────


def has_skill_intent(
    messages: list[dict[str, Any]],
    skills: Iterable[Any] | None = None,
) -> bool:
    """检测最近 user message 是否触发任何 skill 意图.

    Args:
        messages: 当前请求 messages 数组.
        skills:   SkillMeta 列表. None 时调 discover_skills() 取实时列表.

    Returns:
        True 表示命中 (trigger 关键词 或 显式 skill_path 提及). False 表示纯闲聊.
    """
    if skills is None:
        from .skills_loader import discover_skills  # noqa: PLC0415, 避免循环 import

        skills = discover_skills()
    skills = list(skills)

    user_text = _last_user_text(messages)
    if not user_text:
        return False

    # 1. trigger 关键词命中
    pattern, _ = _build_trigger_map(skills)
    if pattern is not None and pattern.search(user_text):
        return True

    # 2. 显式 skill_path 提及
    if _extract_explicit_skill_paths(user_text, skills):
        return True

    return False


def has_skill_tool_in_request(body: dict[str, Any]) -> bool:
    """body.tools 里有没有 catfish_run_skill?"""
    tools = body.get("tools") or []
    if not isinstance(tools, list):
        return False
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function") or {}
        if isinstance(fn, dict) and fn.get("name") == "catfish_run_skill":
            return True
        if tool.get("name") == "catfish_run_skill":
            return True
    return False


def inject_skill_guard(
    messages: list[dict[str, Any]],
    body: dict[str, Any] | None = None,
    skills: Iterable[Any] | None = None,
) -> list[dict[str, Any]]:
    """检测 skill 意图 + tools 就位状况, 在最后 system message 末尾追加保护块.

    Args:
        messages: 当前 messages 数组.
        body:     完整 chat body, 用于判断 catfish_run_skill 在 tools 里没. None
                  时假设工具就位 (省调 — 跟历史行为兼容).
        skills:   SkillMeta 列表. None 时调 discover_skills().
    """
    if skills is None:
        from .skills_loader import discover_skills  # noqa: PLC0415

        skills = discover_skills()
    skills = list(skills)

    user_text = _last_user_text(messages)
    if not user_text:
        return messages

    matched = _match_skills(user_text, skills)
    if not matched:
        return messages

    if not messages:
        return messages

    # 找最后 system 段
    last_system_idx = -1
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "system":
            last_system_idx = i
            break
    if last_system_idx < 0:
        return messages

    tool_present = (
        has_skill_tool_in_request(body) if body is not None else True
    )

    block = (
        _build_required_block(matched, skills)
        if tool_present
        else _build_missing_block(matched)
    )

    # 幂等 — 追加过同块就不再追
    sys_msg = messages[last_system_idx]
    cur = sys_msg.get("content", "")
    block_marker = "## ⚠ 本次员工要求用 catfish skill"
    if isinstance(cur, str) and block_marker in cur:
        return messages

    out = deepcopy(messages)
    sys_msg = out[last_system_idx]
    if isinstance(sys_msg.get("content"), str):
        sys_msg["content"] = sys_msg["content"].rstrip() + block
        if logger.isEnabledFor(logging.INFO):
            kind = "REQUIRED" if tool_present else "MISSING"
            logger.info(
                "skill_guard: 注入 %s block, 命中 skill=%s (tools=%s)",
                kind, matched, tool_present,
            )
    return out


# ─── helpers ───────────────────────────────────────────────────


def _last_user_text(messages: list[dict[str, Any]]) -> str:
    """取最后一条 role=user 的 text content. multimodal 拼合 text parts."""
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict)
            )
        if isinstance(content, str):
            return content
        return ""
    return ""


def _match_skills(text: str, skills: list[Any]) -> list[str]:
    """返回命中的 skill_path 列表, 按相关度排 (显式 path > trigger 命中)."""
    explicit = _extract_explicit_skill_paths(text, skills)

    by_trigger: list[str] = []
    pattern, trigger_to_skill = _build_trigger_map(skills)
    if pattern is not None:
        for m in pattern.findall(text):
            sp = trigger_to_skill.get(m.lower())
            if sp and sp not in by_trigger and sp not in explicit:
                by_trigger.append(sp)

    return explicit + by_trigger


def _build_required_block(
    matched: list[str], skills: list[Any]
) -> str:
    """命中 skill + tool 就位 → 拼铁律块 (动态含真命中 skill_path / 真 kind 接力)."""
    skill_by_path = {getattr(s, "skill_path", ""): s for s in skills}

    # 列推荐 skill 行
    lines: list[str] = [""]
    lines.append("## ⚠ 本次员工要求用 catfish skill 出成果 — gateway 强制注入, 不能忽略")
    lines.append("")
    lines.append("**铁律 1**: 你**必须**调用 `catfish_run_skill` 工具. 命中的 skill (按相关度排):")
    lines.append("")
    for sp in matched:
        s = skill_by_path.get(sp)
        if not s:
            continue
        kind_tag = " 🔧 procedural" if getattr(s, "kind", "procedural") == "procedural" else " 📜 instructional"
        desc_first = (getattr(s, "description", "") or "").split("\n", 1)[0][:120]
        lines.append(f"- `{sp}`{kind_tag} — {desc_first}")
    lines.append("")
    lines.append(
        "**铁律 2**: **严禁**用 `execute_code` 自己写 python-docx / python-pptx / "
        "手搓 HTML — skill 已经处理了字体 / 段结构 / 页码 / 版式 / 表格等全部细节, "
        "你绕开走 = 输出不合规, 客户拒收 + 审计扣分."
    )
    lines.append("")
    lines.append(
        '**铁律 3 (反"嘴炮", 5/15 鸿波撞)**: **严禁**只**说**"我现在用 X 工具" 然后**不真调**就停. '
        "文字承诺 ≠ 真干活. 你的下一条消息**必须**真发出 tool_call, 不能光描述步骤后 stop."
    )
    lines.append("")
    lines.append(
        "**铁律 4**: 不知道 skill 参数怎么传? 先调 "
        "`catfish_run_skill(skill_path='...', params={'_help': True})` 拿 schema, "
        "再跟员工对话补全 1-2 个事实, 再正式调."
    )
    lines.append("")
    lines.append(
        "**铁律 5 (反幻觉)**: catfish skill **不在 `skills_list()` 输出里** — `skills_list` "
        "只列 hermes skill. 不要跑 `skills_list` 验证 catfish_run_skill 能不能调, 也不要跑 "
        "`catfish skills install/pull/browse` 这些**不存在的命令**. 直接调就行."
    )
    lines.append("")
    lines.append(
        "**铁律 6 (反复用幻觉)**: 第一次 catfish_run_skill 调用成功后, 员工说\"再生成一份\" / "
        "\"改某条\" → 你**继续调 catfish_run_skill** (改 params), 不要重新验证 skill 是否存在, "
        "也不要 fallback 自己写代码."
    )
    lines.append("")

    # 指令型 skill 接力 — 真有命中的 instructional skill 才注入
    instructional_paths = [
        sp for sp in matched
        if getattr(skill_by_path.get(sp), "kind", "procedural") == "instructional"
    ]
    if instructional_paths:
        lines.append(
            "**铁律 7 (指令型 skill 接力)**: 命中的 skill 里有 📜 instructional 类 "
            f"({', '.join('`' + p + '`' for p in instructional_paths)}). 调完 catfish_run_skill 后, "
            "返回值含 `is_instructional: true` + `preferred_template` + `output_target`. "
            "**真活由你接力完成**:"
        )
        lines.append("")
        lines.append("1. `read_file(preferred_template)` — 拿到模板骨架")
        lines.append("2. 按 input 内容填模板 body (改 HTML body / .docx 段落, 保留 head/CSS/style)")
        lines.append("3. `write_file(output_target, <完整产物>)`")
        lines.append("4. 回员工: '✅ 已生成: <output_target>'")
        lines.append("")
        lines.append(
            "每一步都**必须真发 tool_call**, 不能光在文字里描述. 这是反嘴炮铁律的特化版."
        )
        lines.append("")

    lines.append(
        "**自检**: 下一个 tool_call 必须是 `catfish_run_skill`. 如果你打算调 "
        "`execute_code` / `terminal` / `write_file` / `skills_list` 来\"先验证 skill 存在\""
        "或\"自己写脚本生成\", **立即停下**, 改调 `catfish_run_skill`."
    )
    lines.append("")
    return "\n".join(lines)


def _build_missing_block(matched: list[str]) -> str:
    """命中 skill 但 tools 里没 catfish_run_skill → 警告员工 Cmd+R."""
    matched_list = ", ".join(f"`{p}`" for p in matched) or "(本次命中的 skill)"
    return f"""

## ⚠ 本次员工要求用 catfish skill 出成果, 但 catfish_run_skill 工具未加载 — gateway 警告

员工意图命中: {matched_list}. 但**当前 chat 请求的 tools 字段里没有 catfish_run_skill 工具**.
链路某处没把 tool-bridge 的新工具同步给你.

**你必须告诉员工** (在你的回复里**必加**这两行):
> ⚠ 检测到 catfish_run_skill 工具未加载, 我无法调用合规 skill.
> 请按 **Cmd + R 刷新 Companion** 后重试. 如果刷新后仍然不行, 联系鲶鱼团队检查 tool-bridge.

**同时**: 不要硬上 execute_code 自己写 python-docx / python-pptx / 手搓 HTML — 输出会**字体不对 /
段结构不合规 / 不专业**, 客户拒收. 直接告诉员工**等链路恢复**.

如果员工坚持要"先凑合一份草稿", 才允许 fallback 到 execute_code, 并在文档开头加红字警告:
"⚠ 本文未经合规 skill 处理, 仅作内部讨论".
"""


__all__ = [
    "has_skill_intent",
    "has_skill_tool_in_request",
    "inject_skill_guard",
    "_build_trigger_map",  # 给单测用
]
