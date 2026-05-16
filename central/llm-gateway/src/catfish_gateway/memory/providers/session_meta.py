"""SessionMetaProvider — 包 session_meta.build_meta_block() 成 MemoryProvider.

priority=30: 时间感放偏前 (在 facts/skills 之间), 模型先看到"距上次 N 天" 再看具体内容.

复用 session_meta 模块:
  - build_meta_block() 已经返字符串, 包装最简单
  - 不动 tick() (tick 是后台写, 不是 inject 行为, 留在 app.py 末尾)
"""

from __future__ import annotations

import logging

from .. import InjectContext

logger = logging.getLogger("catfish.gateway.memory.providers.session_meta")


class SessionMetaProvider:
    """注入 '距上次 N 天 / 今天第 N 次' 时间元.

    第一次安装 (没 session_meta.json) → prefetch 返 None.
    """

    name = "session_meta"
    priority = 30
    budget_bytes = 500  # ~125 token, 2-3 行 markdown 够

    def prefetch(self, ctx: InjectContext) -> str | None:
        from ...session_meta import build_meta_block  # noqa: PLC0415

        try:
            block = build_meta_block()
        except Exception as e:  # noqa: BLE001
            logger.warning("SessionMetaProvider build_meta_block 失败: %s", e)
            return None
        if not block or not block.strip():
            return None
        return block
