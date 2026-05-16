"""EmployeeJournalProvider — 包 inject_employee_journal 成 MemoryProvider.

priority=60: journal/distilled 是跨 session 内容总结, 位置靠后 (session_history 之后).

复用 employee_journal 模块:
  - read_journal() — 老 journal 最近 5KB
  - memory_distill.read_distilled_facts() — 蒸馏老段精华 ~3KB

两段式注入 (BL-MEMORY-DISTILL-LIVE 5/16):
  ### A. 长期事实 (LLM 蒸馏老 journal)
  ### B. 最近 journal 段 (全文)
"""

from __future__ import annotations

import logging

from .. import InjectContext

logger = logging.getLogger("catfish.gateway.memory.providers.employee_journal")


class EmployeeJournalProvider:
    """注入 LLM 蒸馏的长期事实 + 最近 journal 全文 (两段式).

    内容来源:
      A. ~/.catfish/distilled_facts.md (memory_distill 后台生成)
      B. ~/.catfish/employee_journal.md 最近 INJECT_MAX_BYTES (5KB) 尾段

    跟现有 inject_employee_journal() 函数同样的两段式渲染.
    """

    name = "employee_journal"
    priority = 60
    # BL-MEMORY-INJECT-OPTIMIZE C (5/16): 改二选一后, 单段 ≤ 5KB. budget 4000 够.
    # 老两段式 distilled (~3KB) + journal (~5KB) = 8KB 现在剩 3-5KB.
    budget_bytes = 5000

    def prefetch(self, ctx: InjectContext) -> str | None:
        # lazy import 防循环
        from ...employee_journal import read_journal  # noqa: PLC0415

        try:
            journal = read_journal()
        except Exception as e:  # noqa: BLE001
            logger.warning("EmployeeJournalProvider read_journal 失败: %s", e)
            journal = ""

        try:
            from ...memory_distill import read_distilled_facts  # noqa: PLC0415
            distilled = read_distilled_facts()
        except Exception as e:  # noqa: BLE001
            logger.warning("read_distilled_facts 失败: %s", e)
            distilled = ""

        if not journal.strip() and not distilled.strip():
            return None

        # BL-MEMORY-INJECT-OPTIMIZE C (5/16 鸿波 'inject 28KB 太大'):
        # 二选一 — distilled (老段精华) 优先, 没有 fallback journal (最近段全文).
        # 不再两段都 inject (两段信息重叠, 浪费 3-5KB).
        #
        # 风险: distilled 没有最近 chat 细节. 但: 最近 chat 已经在 messages 里
        # (LLM 直接看到), distilled 补充的是"老 session 知识". 二选一合理.
        #
        # 一键回滚 CATFISH_JOURNAL_BOTH_SEGMENTS=1 → 走老两段式.
        import os  # noqa: PLC0415
        if os.environ.get("CATFISH_JOURNAL_BOTH_SEGMENTS", "0") == "1":
            return self._render_both_segments(distilled, journal)

        if distilled.strip():
            # distilled 是 memory_distill 后台从老 journal 抽的精华, 更紧凑高价值
            return (
                "## 📝 员工长期记忆 (LLM 蒸馏精华, ~/.catfish/distilled_facts.md)\n\n"
                "下面是从员工 journal 抽出的**长期事实** (人/项目/偏好/决策). "
                "任何时候提到员工的工作方式 / 项目 / 偏好, 优先用这里. "
                "最近 chat 细节已在 messages 历史里, 不重复 inject.\n\n"
                f"{distilled.strip()}"
            )

        # distilled 还没生成 (memory_distill 第一次跑前) → fallback 最近 journal
        return (
            "## 📝 员工最近 journal (~/.catfish/employee_journal.md 尾部)\n\n"
            "下面是员工最近工作总结. 你**必须**通读, 理解员工当前做什么 / 偏好什么. "
            "员工提到\"上次 / 之前 / 那个 X\"时优先在这里找.\n\n"
            f"{journal.strip()}"
        )

    def _render_both_segments(self, distilled: str, journal: str) -> str:
        """老两段式渲染 — 操作员开 CATFISH_JOURNAL_BOTH_SEGMENTS=1 时用."""
        parts: list[str] = ["## 📝 员工长期日记 (两段式 - 操作员显式开)"]
        if distilled.strip():
            parts.append(f"\n### A. 长期事实\n\n{distilled.strip()}")
        if journal.strip():
            parts.append(f"\n### B. 最近 journal\n\n{journal.strip()}")
        return "\n".join(parts)
