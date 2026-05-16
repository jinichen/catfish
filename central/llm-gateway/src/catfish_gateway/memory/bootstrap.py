"""bootstrap_registry — gateway 启动时把 8 个内置 Provider 注册到全局 Registry.

# 为啥不在 import 时自动注册

import 时自动注册会让测试 isolation 困难 (单测想 monkey-patch 某个 provider
得先 unregister). 显式 bootstrap → 测试可以选不调 bootstrap_registry, 各 provider
独立测.

# 顺序

按 priority 升序:
  20  session_facts
  30  session_meta (需 Python 3.11+)
  40  skills_catalog
  45  stats_guard
  50  session_history
  55  skill_guard
  60  employee_journal
  70  feedback

注意 identity 不在 registry 里 — 它**创建** system message, Registry 是
**追加**到现有 system. identity 应在 Registry 前跑 (app.py middleware 早期).
"""

from __future__ import annotations

import logging
import sys

from .providers.employee_journal import EmployeeJournalProvider
from .providers.feedback import FeedbackProvider
from .providers.session_facts import SessionFactsProvider
from .providers.session_history import SessionHistoryProvider
from .providers.skill_guard import SkillGuardProvider
from .providers.skills_catalog import SkillsCatalogProvider
from .providers.stats_guard import StatsGuardProvider
from .registry import MemoryRegistry, get_global_registry

logger = logging.getLogger("catfish.gateway.memory.bootstrap")


def bootstrap_registry(registry: MemoryRegistry | None = None) -> MemoryRegistry:
    """注册 8 个内置 Provider 到 registry (默认全局).

    返回填好的 registry. 重复调安全 (Provider.register 同名替换).

    SessionMetaProvider 需 Python 3.11+ (session_meta.py 用 datetime.UTC).
    旧 Python 自动跳过, 不抛.
    """
    if registry is None:
        registry = get_global_registry()

    registry.register(SessionFactsProvider())

    if sys.version_info >= (3, 11):
        # lazy import 防 Python 3.10 撞 session_meta.py 顶部的 datetime.UTC
        from .providers.session_meta import SessionMetaProvider  # noqa: PLC0415
        registry.register(SessionMetaProvider())
    else:
        logger.warning(
            "bootstrap: Python < 3.11, SessionMetaProvider 跳过 "
            "(session_meta.py 依赖 datetime.UTC)"
        )

    registry.register(SkillsCatalogProvider())
    registry.register(StatsGuardProvider())
    registry.register(SessionHistoryProvider())
    registry.register(SkillGuardProvider())
    registry.register(EmployeeJournalProvider())
    registry.register(FeedbackProvider())

    logger.info(
        "memory_registry bootstrap: 注册完成 %d provider", len(registry.list_providers())
    )
    return registry
