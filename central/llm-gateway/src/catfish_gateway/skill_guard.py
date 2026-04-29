"""skill_guard — 检测员工触发了 catfish skill 意图但 tools 没就位的强制保护.

# 为啥需要 (踩过坑 2026-04-29 鸿波 demo 反复翻车)

链路: 员工 → Companion → gateway → 上游 LLM API
                ↓                       ↑
                tools 列表            body.tools

Companion 端 `_cachedTools` 模块变量缓存了启动时的 tools 列表. catfish/skills/
新加 / tool-bridge 加新工具 → Companion 不刷 → tools 列表是旧的 → catfish_run_skill
**根本不在 body.tools** → 模型即便看到 system prompt 提示, 也调不出来 → 退回
execute_code 自己手搓 .docx → 字体 / 段结构 / 页码全错.

工程级修复 (跟 stats_guard 同套路):
  1. 检测员工最近 user message 含 skill 意图关键词 (汇报 / 请示 / 立项 ...)
  2. 检查 body.tools 里有没有 catfish_run_skill
  3. 命中且工具就位 → system prompt 加铁律: "禁止 execute_code 自写 python-docx, 必须 catfish_run_skill"
  4. 命中但工具缺失 → system prompt 加诊断信息: "tools 未注册 catfish_run_skill, 告诉员工 Cmd+R 刷新 Companion 后重试"

模型每次推理时**最近 token 必看到**, 不依赖 attention 飘.

# 跟 inject_skills_catalog / inject_stats_guard 的关系

  顺序: identity → session_facts → stats_guard → skills_catalog → **skill_guard** → ...
  inject_skills_catalog: 提供 skill 列表 (静态, 描述能力)
  skill_guard:           检测意图 + 工具就位状况, 触发式强提醒
"""

from __future__ import annotations

import logging
import re
from copy import deepcopy
from typing import Any

logger = logging.getLogger("catfish.gateway.skill_guard")

#: 触发 catfish skill 意图的关键词 (覆盖 leadership-briefing + weekly-report 等)
_SKILL_INTENT_PATTERN = re.compile(
    # leadership-briefing 触发词
    r"汇报材料|工作汇报|月度汇报|季度汇报|年度汇报|进度汇报|情况汇报|汇报"
    r"|请示件|请示文件|请示报告|请示"
    r"|决策事项|立项报告|立项请示|立项"
    r"|重大事项报告|重大事项请示|专题汇报|专项汇报"
    r"|上报材料|呈批件|呈报|报送材料"
    r"|写一份给.{0,4}总|给领导汇报|给公司汇报|给集团汇报|给上级汇报"
    # weekly-report 触发词
    r"|周报材料|周报|本周工作|本周总结|本周做了|一周工作|一周汇总|这周做了"
    # 其他 skill 加进来时往后续加
    ,
    re.IGNORECASE,
)

#: 当 catfish_run_skill 在 tools 里时, 强制要求模型用它
_SKILL_REQUIRED_BLOCK = """

## ⚠ 本次员工要求生成合规公文 — gateway 强制注入, 不能忽略

**铁律 1**: 你**必须**调用 `catfish_run_skill` 工具 (skill_path 选 skill 列表里匹配的那个).
**铁律 2**: **严禁**用 `execute_code` 自己写 python-docx 代码 — skill 已经处理了字体 / 段结构 / 页码 / 表格 / 红字 / 附件全部细节, 你绕开走会输出 `字体微软雅黑 / 段标题自创 / 没有页脚` 这种**不合规**文档. 你绕开走的代价 = 客户拿到合规审计扣分.
**铁律 3**: **严禁**用 `terminal` 装 python-docx 自己跑 — 同理.
**铁律 4**: 不知道 skill 参数怎么传? 先调 `catfish_run_skill(skill_path='...', params={'_help': True})` 拿 schema, 再跟员工对话补全 1-2 个事实, 再正式调.

**自检**: 你的下一个 tool_call 必须是 `catfish_run_skill`. 如果你打算调 `execute_code` / `terminal` / `write_file` 来生成 .docx, **立即停下**, 改调 `catfish_run_skill`.
"""

#: 当 catfish_run_skill **不在** tools 里时 — 给模型 + 员工的诊断信息
_SKILL_MISSING_BLOCK = """

## ⚠ tools 列表未注册 catfish_run_skill — gateway 工程级警告

员工触发了公文生成意图 (汇报 / 请示 / 立项 等), 但**当前 chat 请求的 tools 字段里没有 catfish_run_skill 工具**. 链路某处没把 tool-bridge 的新工具同步给你.

**你必须告诉员工** (在你的回复里**必加**这两行):
> ⚠ 检测到 catfish_run_skill 工具未加载, 我无法调用合规公文 skill.
> 请按 **Cmd + R 刷新 Companion** 后重试 — 这会清缓存重拉工具列表. 如果刷新后仍然不行, 可能 tool-bridge 没启动 catfish_run_skill, 联系鲶鱼团队检查.

**同时**: 不要硬上 execute_code 自己写 python-docx — 输出会**字体不对 / 段结构不合规 / 没页脚**, 客户拒收. 直接告诉员工**等链路恢复**.

如果员工坚持要"先凑合一份草稿", 才允许 fallback 到 execute_code, 并在文档开头加一行红字警告: "⚠ 本文未经合规 skill 处理, 仅作内部讨论".
"""


def has_skill_intent(messages: list[dict[str, Any]]) -> bool:
    """检测员工最近的 user message 是否触发了 catfish skill 意图.

    取**最后一条 role=user**, 不扫历史, 因为我们关心当前请求.
    """
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        # multimodal content (list) → 拼成字符串扫
        if isinstance(content, list):
            content = " ".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict)
            )
        if not isinstance(content, str):
            continue
        return bool(_SKILL_INTENT_PATTERN.search(content))
    return False


def has_skill_tool_in_request(body: dict[str, Any]) -> bool:
    """body.tools 里有没有 catfish_run_skill?"""
    tools = body.get("tools") or []
    if not isinstance(tools, list):
        return False
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        # OpenAI 标准格式: {"type": "function", "function": {"name": "..."}}
        fn = tool.get("function") or {}
        if isinstance(fn, dict) and fn.get("name") == "catfish_run_skill":
            return True
        # 兼容 hermes 平铺格式 {"name": "..."}
        if tool.get("name") == "catfish_run_skill":
            return True
    return False


def inject_skill_guard(
    messages: list[dict[str, Any]], body: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """检测 skill 意图 + tools 就位状况, 在最后 system message 末尾追加保护块.

    body=None 时只检测意图 (不检查工具) — 兼容 caller 不传 body 的情况, 但
    这种情况无法判断工具就位, 我们假设就位 (否则警告太吵).
    """
    if not has_skill_intent(messages):
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

    # 决定追加哪个 block
    tool_present = (
        has_skill_tool_in_request(body) if body is not None else True
    )
    block = _SKILL_REQUIRED_BLOCK if tool_present else _SKILL_MISSING_BLOCK

    # 幂等
    sys_msg = messages[last_system_idx]
    cur = sys_msg.get("content", "")
    if isinstance(cur, str) and block.strip()[:60] in cur:
        return messages

    out = deepcopy(messages)
    sys_msg = out[last_system_idx]
    if isinstance(sys_msg.get("content"), str):
        sys_msg["content"] = sys_msg["content"].rstrip() + block
        if logger.isEnabledFor(logging.INFO):
            kind = "REQUIRED" if tool_present else "MISSING"
            logger.info("skill_guard: 注入 %s block (tools=%s)", kind, tool_present)
    return out


__all__ = [
    "has_skill_intent",
    "has_skill_tool_in_request",
    "inject_skill_guard",
]
