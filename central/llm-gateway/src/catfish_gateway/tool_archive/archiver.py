"""tool message 处理 — truncate-only stub (6/7 BL-MANIFESTO-CLEAN-DEAD).

## 历史

- 5/11 BL-Q3-ARCHIVE v1: gateway 端 PG archive (违反 BOUNDARY)
- 5/22 BL-CENTRAL-EDGE-TOOL-ARCHIVE Phase 6a: edge 端 tool-bridge 接管 archive,
  gateway PG 路径默认禁用 (env=0)
- 5/26 BL-BOUNDARY: db.py PG 路径砍, content 全员工本机
- 6/7 BL-CATFISH-MANIFESTO clean-up:
  * 删 env=1 回滚选项 (彻底无 PG archive 路径)
  * 这函数只剩 truncate-only fallback
- 6/7 BL-MANIFESTO-CLEAN-DEAD:
  * 删 archive_tool_messages (老入口) + db / features / prompts / reader / router /
    summary_worker 6 个 dead module 整套 rm
  * 这 file 只剩 prepare_tool_messages 一个 wrapper, 是给 app.py 老 caller 留的
    BC stub. app.py 后续重构后直接调 truncate_tool_messages 即可, 这 file 也可去

## 跟 catfish-central-manifesto 公理 4 的关系

"中央 API surface 物理无能". 这函数原先有 env=1 回滚到 PG archive, 是"soft 强制
中央存对话内容" 后门. 6/7 删 — 现在物理上 prepare_tool_messages **永远不写中央
PG**, 只能 truncate 硬切.

## 漏网兜底

第三方 plugin 直接调 LLM 没经过 catfish-tool-bridge → 这里 truncate 硬切 (留头尾
+ 摘要文字, content 不出本机).
"""
from __future__ import annotations

from typing import Any


def prepare_tool_messages(
    messages: list[dict[str, Any]],
    *,
    user_email: str,  # noqa: ARG001 (历史 caller 还传, 留 sig)
    session_id: str | None = None,  # noqa: ARG001
    conversation_id: str | None = None,
    origin_model: str | None = None,
    model_context_window: int | None = None,
) -> list[dict[str, Any]]:
    """gateway app.py 调的统一入口 — **强制走 FIX41 truncate, 永不写 PG**.

    P3.3.30 (6/12): 加 model_context_window + origin_model 两个 kwargs.
      - 都给 → 走动态截 (truncate_tool_messages_dynamic) — 真要爆才截
      - 任一缺 → 走老静态截 (truncate_tool_messages) — BC 跟 5/11 BL-FIX41 一致
    动态截内部异常会自动 fallback 静态截, 不会比老行为差.
    """
    # 故意没 ARG001 — 这两个新参在下面用了
    _ = conversation_id  # 仍接受老 caller 传 (会被忽略)

    if model_context_window is not None and model_context_window > 0:
        # P3.3.30 (6/12): 动态截路径
        from ..tool_msg_truncator import truncate_tool_messages_dynamic  # noqa: PLC0415
        return truncate_tool_messages_dynamic(
            messages,
            model_context_window=model_context_window,
            model_name=origin_model,
        )

    # 老路径 (BC): ctx_window 没传 → 老 static
    from ..tool_msg_truncator import truncate_tool_messages  # noqa: PLC0415
    return truncate_tool_messages(messages)


__all__ = ["prepare_tool_messages"]
