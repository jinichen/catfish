"""SessionFactsProvider — 包 session_facts inject 成 MemoryProvider.

priority=20: 员工**显式硬事实** 优先级最高 (仅次于 identity/persona). 模型必须最先
看到员工"明确告诉过"的事 (例 EIS=http, 密码=keychain://x), 避免长 attention 后忘.

复用 session_facts 模块:
  - read_session_facts() → revision list dict
  - render_facts_block() → markdown 字符串
"""

from __future__ import annotations

import logging

from .. import InjectContext

logger = logging.getLogger("catfish.gateway.memory.providers.session_facts")


class SessionFactsProvider:
    """注入员工 catfish_remember 工具记的硬事实 (含 revision 历史).

    跟 hermes memory tool 的 USER.md/memories/ 互补不重叠:
      - hermes memory tool: agent **主动**记 (LLM 判断啥重要)
      - session_facts: 员工**显式**告诉 (员工明确要求记)
    两条都留 — agent 自动 + 员工手动, 信号源不同, 不互斥.
    """

    name = "session_facts"
    priority = 20
    # BL-MEMORY-INJECT-OPTIMIZE (5/16): 砍 revision history 后单条 ≤ 50 字节,
    # 50 条 ~ 2.5KB. 但实盘 (鸿波 12:08): 砍后仍触 4000 budget — 员工攒 fact 多
    # 且 value 长 (URL / 长 config). 调到 6000 wiggle. 仍超就再调.
    budget_bytes = 6000

    def prefetch(self, ctx: InjectContext) -> str | None:
        from ...session_facts import _current, _safe_inline, read_session_facts  # noqa: PLC0415

        try:
            facts = read_session_facts()
        except Exception as e:  # noqa: BLE001
            logger.warning("SessionFactsProvider read 失败: %s", e)
            return None
        if not facts:
            return None

        # BL-MEMORY-INJECT-OPTIMIZE B (5/16 鸿波 'inject 28KB 太大'):
        # 不调 session_facts.render_facts_block (它含"已更新 N 次, 上次值: X" 占大头),
        # 自渲染**简化版**: 只输出 current value, 砍 revision history.
        #
        # 风险: 失去 BL-MM1 复述纪律 (模型可能不知道员工改过同 key). 但实盘数据
        # 反映 revision history 占 facts 50%+, 而员工实际改值场景少, 这个 trade-off
        # 合理. 真要恢复 — CATFISH_FACTS_KEEP_REVISIONS=1 操作员一键开.
        try:
            return _render_compact(facts)
        except Exception as e:  # noqa: BLE001
            logger.warning("SessionFactsProvider 渲染失败: %s", e)
            return None


def _render_compact(facts: dict) -> str | None:
    """简化版渲染: 只 current value, 不含 revision history. 比 render_facts_block 省 50%+."""
    import os  # noqa: PLC0415

    from ...session_facts import _current, _safe_inline  # noqa: PLC0415

    # 一键回滚: env 开 → 用老 render_facts_block (含 revision)
    if os.environ.get("CATFISH_FACTS_KEEP_REVISIONS", "0") == "1":
        from ...session_facts import render_facts_block  # noqa: PLC0415
        return render_facts_block(facts)

    if not facts:
        return None
    lines = [
        "## 当前 session 已确认的硬事实 (员工明确告诉过, gateway 自动注入)",
        "",
        "这些是员工在本 session 内明确告诉你的事实, 你**必须遵守**, 不要再问 / 不要忘 / 不要瞎猜:",
        "",
    ]
    for key, revisions in sorted(facts.items()):
        if not revisions:
            continue
        current_val = _safe_inline(_current(revisions))
        lines.append(f"- **{key}**: {current_val}")
    lines.append("")
    lines.append(
        "员工要更新这些事实 → 调 catfish_remember(key, value) 重存. "
        "session 结束员工自己 rm ~/.catfish/session_facts.json 清空."
    )
    return "\n".join(lines)
