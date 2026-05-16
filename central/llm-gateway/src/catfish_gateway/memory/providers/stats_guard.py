"""StatsGuardProvider — 包 stats_guard 成 MemoryProvider.

priority=45: 跟 skills_catalog 同档, 在 history 前. 只在 user message 命中"统计意图"
(例 "总共多少 / count / 加起来") 时才注入强制提醒"用 execute_code 算精确数".

复用 stats_guard.has_stats_intent() + _STATS_GUARD_BLOCK.
"""

from __future__ import annotations

import logging

from .. import InjectContext

logger = logging.getLogger("catfish.gateway.memory.providers.stats_guard")


class StatsGuardProvider:
    """统计意图 → 注入强制 execute_code 提醒.

    跟其它 Provider 不同, 这个**条件触发** — 80% 的 chat 命不中, prefetch 返 None.
    命中时返 _STATS_GUARD_BLOCK 字符串.
    """

    name = "stats_guard"
    priority = 45
    budget_bytes = 1500  # _STATS_GUARD_BLOCK 实测 ~600 字节, 留余量

    def prefetch(self, ctx: InjectContext) -> str | None:
        from ...stats_guard import _STATS_GUARD_BLOCK, has_stats_intent  # noqa: PLC0415

        try:
            if not has_stats_intent(ctx.messages):
                return None
        except Exception as e:  # noqa: BLE001
            logger.warning("StatsGuardProvider has_stats_intent 失败: %s", e)
            return None
        # 命中: 返 guard block
        return _STATS_GUARD_BLOCK
