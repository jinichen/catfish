"""SkillGuardProvider — 包 skill_guard 成 MemoryProvider.

priority=55: 在 skills_catalog (40) 之后, history (50) 之后. 顺序: 先看到有哪些
skill 可用 → 命中触发铁律 → 看到员工历史 session.

复用 skill_guard 模块的所有判断逻辑 (_last_user_text / _match_skills /
_build_required_block / _build_missing_block). Provider 这里把 inject_skill_guard
拆成两步: 命中判断 + 块构建. 命中返字符串, 不命中返 None.

注:
  原 inject_skill_guard 接受 `body` 参数判断 `has_skill_tool_in_request(body)`
  (检 tools 里有没有 catfish_run_skill). InjectContext 目前不含 tools 字段,
  所以 Provider 暂时假定 tool_present=True (跟原版 body=None 的兼容行为一致).
  未来 P1 可扩 InjectContext 加 tools, Provider 再用真值.
"""

from __future__ import annotations

import logging

from .. import InjectContext

logger = logging.getLogger("catfish.gateway.memory.providers.skill_guard")


class SkillGuardProvider:
    """user 提及 skill 时, 注入"必须用 catfish_run_skill" 铁律.

    跟 stats_guard 同模式: 条件触发. 没命中返 None (~80% chat).
    """

    name = "skill_guard"
    priority = 55
    budget_bytes = 3000  # 铁律 block + 命中 skill 列表 ~1-2 KB

    def prefetch(self, ctx: InjectContext) -> str | None:
        # lazy import 防循环
        from ...skill_guard import (  # noqa: PLC0415
            _build_required_block,
            _last_user_text,
            _match_skills,
        )
        from ...skills_loader import discover_skills  # noqa: PLC0415

        try:
            user_text = _last_user_text(ctx.messages)
            if not user_text:
                return None

            skills = list(discover_skills())
            matched = _match_skills(user_text, skills)
            if not matched:
                return None

            # tool_present=True 跟原 inject_skill_guard(body=None) 一致行为
            # (P1 后扩 InjectContext.tools 再优化)
            block = _build_required_block(matched, skills)
        except Exception as e:  # noqa: BLE001
            logger.warning("SkillGuardProvider 失败: %s", e)
            return None

        if not block or not block.strip():
            return None
        return block
