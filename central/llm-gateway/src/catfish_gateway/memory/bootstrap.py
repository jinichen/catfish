"""bootstrap_registry — gateway 启动时把 10 个内置 Provider 注册到全局 Registry.

# 为啥不在 import 时自动注册

import 时自动注册会让测试 isolation 困难 (单测想 monkey-patch 某个 provider
得先 unregister). 显式 bootstrap → 测试可以选不调 bootstrap_registry, 各 provider
独立测.

# 顺序

按 priority 升序:
  10  hermes_user_memory (BL-MEMORY-UNIFIED-INJECT 5/16 新加, 真画像最高优先)
  20  session_facts (deprecated, 默认不 inject)
  30  session_meta (需 Python 3.11+)
  40  skills_catalog
  45  stats_guard
  50  session_history
  55  skill_guard
  60  employee_journal
  65  hermes_memory (BL-MEMORY-UNIFIED-INJECT 5/16 新加, 项目事实)
  70  feedback

注意 identity 不在 registry 里 — 它**创建** system message, Registry 是
**追加**到现有 system. identity 应在 Registry 前跑 (app.py middleware 早期).
"""

from __future__ import annotations

import logging

# 5/19 BL-GATEWAY-MEMORY-CODE-DEPRECATED: provider 模块 import 删了 (bootstrap
# 不再 register 任何 provider). 模块本身 .providers/* 保留作 reference, 哪天
# 真要 hard delete 再清.
# 老 imports (历史 — 注释保留方便 git blame 追溯):
#   from .providers.{employee_journal, feedback, hermes_memory, session_facts,
#       session_history, skill_guard, skills_catalog, stats_guard} import ...
#   from .providers.session_meta import SessionMetaProvider (Python 3.11+ only)
from .registry import MemoryRegistry, get_global_registry

logger = logging.getLogger("catfish.gateway.memory.bootstrap")


def bootstrap_registry(registry: MemoryRegistry | None = None) -> MemoryRegistry:
    """5/19 BL-GATEWAY-MEMORY-CODE-DEPRECATED (Phase 2-3 of BL-MEMORY-OWNERSHIP-FIX):
    **不再注册任何 provider**.

    原 10 个 provider 的责任已转给:
      - hermes builtin (HermesUserMemory / HermesMemory / SessionFacts /
        SessionHistory): hermes agent runtime 自带, 我们的 4 个重复 provider 删
      - catfish-memory hermes plugin (EmployeeJournal / SkillsCatalog /
        Feedback / SessionMeta / SkillGuard): edge/hermes-plugins/catfish-memory
        实现, 通过 hermes plugin 体系注入
      - StatsGuard: deprecated, 80% chat 也不触发, 留 backlog 看是否真需要

    架构: Companion → hermes API server 8642 (agent loop + memory inject) →
    catfish-gateway (纯 LLM 代理, 不碰 memory). 详见
    docs/MEMORY-OWNERSHIP-ARCHITECTURE.md + COMPANION-HERMES-AUTH-DESIGN.md.

    Provider 实现文件**保留代码作 reference**, 跟 sessions_browse.py 同模式
    (5/17 BL-CENTRAL-WEB-PURGE-USERDATA 撤回时也是留 module). 真要删 import
    + provider 文件等 ownership 重构 ship 稳定后再说.

    跟 CATFISH_GATEWAY_DISABLE_MEMORY env flag (#18) 双重保险:
      - 空 registry: 列表层就没 provider, inject_all 直接早返
      - env flag: registry 内 short-circuit, 备份保险
    """
    if registry is None:
        registry = get_global_registry()
    # 故意不注册. 全部 provider 走 catfish-memory hermes plugin / hermes builtin.
    logger.info(
        "memory_registry bootstrap: 跳过所有 provider 注册 "
        "(5/19 BL-MEMORY-OWNERSHIP-FIX Phase 2-3, memory 转给 hermes plugin)"
    )
    return registry
