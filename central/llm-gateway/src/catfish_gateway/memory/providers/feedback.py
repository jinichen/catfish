"""FeedbackProvider — 把 inject_feedback 包装成 MemoryProvider.

PoC 选 feedback_inject 是因为它**最小最独立**:
  - 只读 ~/.catfish/feedback.jsonl (没跨表 join)
  - 没后台 writer (现状 hermes 客户端 PUT /api/feedback 写)
  - 内容简单 (10 条 negative bullet)
  - 不影响其它 module (砍它最多影响"员工 👎/改 信号没传给 LLM")

包装策略:
  - 复用 feedback_inject 的 read_recent_negative() + render_feedback_block()
    (不重写逻辑, 只换接口)
  - prefetch() 返渲染后的 block 字符串, Registry 自动 inject

跟现有 inject_feedback() 关系:
  - 当前阶段 app.py 仍调旧 inject_feedback() (PoC 不动主路径)
  - 真迁移时, 在 app.py 删 inject_feedback 调用 + Registry 注册本 provider
  - 测试覆盖: 本 provider 跟 inject_feedback 输出对齐, 切换不变行为
"""

from __future__ import annotations

import logging

from .. import InjectContext

logger = logging.getLogger("catfish.gateway.memory.providers.feedback")


class FeedbackProvider:
    """把员工最近 7 天 negative feedback (👎/改) inject 到 system prompt 末尾.

    priority=70: feedback 是末尾警示位置 — 模型先看完所有上下文 (identity / facts /
    journal), 最后看到"员工反馈过这些坑别犯", 这位置最强 (近因效应).
    """

    name = "feedback"
    priority = 70
    budget_bytes = 2000  # ~500 token, 10 条 bullet 够用

    def prefetch(self, ctx: InjectContext) -> str | None:
        """读 feedback.jsonl 渲染成 block. 没数据返 None."""
        # lazy import 防循环依赖
        from ...feedback_inject import (  # noqa: PLC0415
            read_recent_negative,
            render_feedback_block,
        )

        try:
            items = read_recent_negative()
        except Exception as e:  # noqa: BLE001
            logger.warning("FeedbackProvider read_recent_negative 失败: %s", e)
            return None

        if not items:
            return None

        try:
            block = render_feedback_block(items)
        except Exception as e:  # noqa: BLE001
            logger.warning("FeedbackProvider render_feedback_block 失败: %s", e)
            return None

        if not block.strip():
            return None
        return block
