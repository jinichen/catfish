"""HermesMemoryProvider — 读 hermes 0.13 ~/.hermes/memories/{USER,MEMORY}.md 注入.

BL-MEMORY-UNIFIED-INJECT (5/16):
之前 catfish gateway 没 provider 读 hermes memory 文件, 完全靠 LLM 主动调
memory(action="search") 才能用上. 实测 LLM 经常忘 search → 之前记的 memory
对话里"似乎不存在". 加 inject 走双轨 (auto inject + LLM 可 search), 兜底.

设计选择: 重 inject + 轻 search 双轨, 不再纯 search-only.
- 优点: 总有上下文 (不依赖 LLM 想到 search)
- 缺点: token 涨 (USER.md 1375 chars + MEMORY.md 2200 chars = ~3.5KB)
- budget 控制: 这俩 provider 各 1500-2500 字节, 实盘看调

跟 BL-PRIVACY-PRINCIPLES 一致: 仅读员工本机文件, 中心不知情.
"""
from __future__ import annotations

import logging
from pathlib import Path

from .. import InjectContext

logger = logging.getLogger("catfish.gateway.memory.providers.hermes_memory")

# hermes ENTRY_DELIMITER (同 hermes-agent/tools/memory_tool.py 的常量)
_ENTRY_DELIMITER = "\n§\n"


def _read_entries(path: Path) -> list[str]:
    """读 hermes memory 文件, 按 § 分隔解析 entries. 文件不在 / 空 返空 list."""
    if not path.exists():
        return []
    try:
        content = path.read_text(encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        logger.warning("读 %s 失败: %s", path, e)
        return []
    if not content.strip():
        return []
    return [s.strip() for s in content.split(_ENTRY_DELIMITER) if s.strip()]


def _hermes_memories_dir() -> Path:
    """hermes 0.13 memory 文件目录, 跟 hermes-agent/tools/memory_tool.py 的
    get_memory_dir() 同 — get_hermes_home() / 'memories'."""
    return Path.home() / ".hermes" / "memories"


class HermesUserMemoryProvider:
    """读 ~/.hermes/memories/USER.md (target=user 写的: 员工身份/关系/偏好).

    priority 10 = 最高 (在所有 catfish 自家 provider 之前 inject) — hermes
    memory 是 LLM 主动写的"真画像", 比 catfish 自动 distill / journal 推断的
    可靠度高.
    """

    name = "hermes_user_memory"
    priority = 10  # 最高优先级
    budget_bytes = 1800  # 略大于 hermes 默认 user_char_limit 1375, 留 markdown 包装空间

    def prefetch(self, ctx: InjectContext) -> str | None:
        entries = _read_entries(_hermes_memories_dir() / "USER.md")
        if not entries:
            return None
        # 渲染成 markdown, 让 LLM 明确这是"员工本人画像"
        lines = [
            "## 关于员工本人 (hermes USER memory, 跨 session 累积)",
            "",
            "这些是员工本人的身份/关系/偏好, LLM (你之前的版本) 调 memory(target=user) "
            "明确记下的. 比 distill 自动推断的可靠.",
            "",
        ]
        for e in entries:
            lines.append(f"- {e}")
        lines.append("")
        return "\n".join(lines)


class HermesMemoryProvider:
    """读 ~/.hermes/memories/MEMORY.md (target=memory 写的: 项目/技术/操作事实).

    priority 65 = 介于 employee_journal (60) 跟 feedback (70) 之间. 项目事实
    比反馈优先级低 (反馈是"员工说他不喜欢什么", 行为驱动更强).
    """

    name = "hermes_memory"
    priority = 65
    budget_bytes = 2600  # 略大于 hermes 默认 memory_char_limit 2200

    def prefetch(self, ctx: InjectContext) -> str | None:
        entries = _read_entries(_hermes_memories_dir() / "MEMORY.md")
        if not entries:
            return None
        lines = [
            "## 项目 / 技术事实 (hermes MEMORY, 跨 session 累积)",
            "",
            "这些是非员工本人的项目背景 / 技术环境 / 操作流程事实, LLM 调 "
            "memory(target=memory) 记下的.",
            "",
        ]
        for e in entries:
            lines.append(f"- {e}")
        lines.append("")
        return "\n".join(lines)
