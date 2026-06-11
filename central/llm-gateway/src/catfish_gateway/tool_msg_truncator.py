"""tool_msg_truncator — BL-FIX41 (5/11 鸿波 demo 前夜).

# 为啥需要

BL-FIX23 L6 + BL-FIX40 上线之后, 鸿波实测 log 仍然 context overflow 117-125%:

```
BL-FIX2 pre-unwrap: total=199, tool_msgs=63, tool_with_image_marker=0, user_multipart=37
context overflow: prompt_tokens=149623 (117%)
```

journal 已经从 50KB 砍到 6KB (BL-FIX40 生效), 但 `tool_msgs=63` 累积了 100-200KB
原始数据 — 一条 execute_code 跑 pytest 的 stdout 就 5-20KB, browser_snapshot
轻松 10KB+, 一旦 ReAct 链跑十几轮就把 128K context 全占了.

# 设计

每条 `role=tool` message 的 `content` 字符串如果 > 2KB, 截成:

```
<前 1KB>
...[已截断 N 字, BL-FIX41]...
<后 1KB>
```

- **消息数量保持不变** — Hermes ReAct / Companion / OpenAI tool-calling 依赖
  assistant ↔ tool 一对一配对, 删 tool message 会把 chain 切断, LLM 报
  "tool_call_id not found" / "unmatched tool call" 之类的错.
- **保前 + 保后** — 前 1KB 通常是 status / 关键路径 / 标题, 后 1KB 是 final
  result / exit code / 总结. 中间循环输出 / 重复日志最该砍.
- **2KB 阈值** — 50 条 × 2KB = 100KB, 留 28KB 给 system + journal + user
  messages, 在 128K context 下安全.
- **图片 unwrap 之后跑** — `unwrap_tool_images` 把含图 tool message 重组成
  user multipart, 之后剩下的 role=tool 都是纯文本, 可以放心 byte 级截.

# 跟其他 FIX 关系

| FIX     | 砍啥                    | 上限         |
|---------|-------------------------|--------------|
| BL-FIX40| employee_journal 注入   | 15KB         |
| BL-FIX41| role=tool message 内容  | 2KB / 条     |
| 之后    | LLM cache 摘要 (Q3)     | -            |

两个 FIX 互补 — journal 是 prompt 顶部一次性塞, tool message 是中间累积塞.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any

logger = logging.getLogger("catfish.gateway.tool_msg_truncator")

#: 单条 role=tool message content 字节上限 (BL-FIX41)
#: P3.3.30 (6/12): 2_000 → 20_000. 老 2K 对 catfish 工作流 (xlsx 修改 / 长 stdout)
#:   太激进 — 多轮修改同一文件时, 上一轮的完整状态被截到 2K 后 LLM 看不到中间项
#:   开始编造 (鸿波 6/11 实测周报反复改 6-7 轮才修对). 20K 给单 tool 留够空间,
#:   配合 dynamic 截 (truncate_tool_messages_dynamic) 真要爆 context 才截.
MAX_BYTES_PER_TOOL = 20_000

#: 触发截断的最小尺寸 — content 小于这个不截.
#: 留 200 字节 slack 给截断分隔标记 (~60 字节), 保证幂等
#: (跑两次结果一致, 不会因为标记本身把内容推过阈值再切一刀).
MIN_BYTES_TO_TRUNCATE = MAX_BYTES_PER_TOOL + 200

#: P3.3.30 (6/12) 动态截 default safety margin — context_window 必须留多少 token
#: 给 response + tokenizer 误差余量 (LiteLLM 自身 10-25% 误差). 10K 安全.
DYNAMIC_SAFETY_MARGIN_TOKENS = 10_000

#: P3.3.30 动态截 default response token estimate — 假设 LLM 还要回多少 token.
#: 5K 覆盖常见 chat 响应 (200-2000 tokens). 长输出 (周报 1-2K) 也够.
DYNAMIC_RESPONSE_RESERVE_TOKENS = 5_000


def _truncate_text(text: str, max_bytes: int = MAX_BYTES_PER_TOOL) -> tuple[str, int]:
    """字节级前后保留截断. 返 (新文本, 截掉的字节数). 不超限则原样.

    保前 max_bytes//2 字节 + ...[已截断 N 字, BL-FIX41]... + 后 max_bytes//2 字节.
    """
    encoded = text.encode("utf-8")
    n = len(encoded)
    if n <= max_bytes:
        return text, 0

    half = max_bytes // 2
    head_bytes = encoded[:half]
    tail_bytes = encoded[-half:]
    cut_n = n - max_bytes

    # 兜底 decode (utf-8 多字节字符可能切半)
    head = head_bytes.decode("utf-8", errors="ignore")
    tail = tail_bytes.decode("utf-8", errors="ignore")

    sep = f"\n...[已截断 {cut_n} 字节 (BL-FIX41 tool content cap)]...\n"
    return head + sep + tail, cut_n


def truncate_tool_messages(
    messages: list[dict[str, Any]],
    *,
    max_bytes_per_tool: int = MAX_BYTES_PER_TOOL,
) -> list[dict[str, Any]]:
    """对所有 role=tool message 的 content 做硬截断 (字节级).

    保留消息数量 (Hermes ReAct / OpenAI tool-calling chain 完整).
    仅处理 content 是 str 的情况; list / 其他类型跳过 (含图的已被
    `unwrap_tool_images` 拎出来, 这里不该再撞到).

    返回新 list (deepcopy), 不改原 messages.
    """
    if not messages:
        return messages

    out = deepcopy(messages)
    truncated_count = 0
    total_cut_bytes = 0

    for i, m in enumerate(out):
        if not isinstance(m, dict):
            continue
        if m.get("role") != "tool":
            continue
        content = m.get("content")
        if not isinstance(content, str):
            # list / dict / None — 跳过, 不该出现, 出现也不动
            continue
        if len(content.encode("utf-8")) < MIN_BYTES_TO_TRUNCATE:
            continue

        new_content, cut_n = _truncate_text(content, max_bytes_per_tool)
        if cut_n > 0:
            out[i]["content"] = new_content
            truncated_count += 1
            total_cut_bytes += cut_n

    if truncated_count > 0:
        logger.info(
            "BL-FIX41 truncate_tool_messages: 截断 %d 条 role=tool message, "
            "省 %d 字节 (~%dK tokens). cap=%d B/条.",
            truncated_count, total_cut_bytes,
            total_cut_bytes // 4000,  # 粗略 1 token ≈ 4 bytes
            max_bytes_per_tool,
        )

    return out


def truncate_tool_messages_dynamic(
    messages: list[dict[str, Any]],
    *,
    model_context_window: int,
    model_name: str | None = None,
    response_reserve_tokens: int = DYNAMIC_RESPONSE_RESERVE_TOKENS,
    safety_margin_tokens: int = DYNAMIC_SAFETY_MARGIN_TOKENS,
    max_bytes_per_tool: int = MAX_BYTES_PER_TOOL,
) -> list[dict[str, Any]]:
    """P3.3.30 (6/12 鸿波): 动态截 — 真要爆 context 才截, 否则原样返.

    # 为啥要动态截

    BL-FIX41 老静态截 (无脑每条 tool > 2K 就截) 对 catfish 工作流是灾难:

      - 鸿波 6/11 实测周报反复修改场景: AI 第 1 轮读 xlsx (tool result 5-7KB
        完整 7 项) → 改 → 写; 第 2 轮你说"改第 N 项" 时, 上一轮 tool result 在
        chat 历史里**已经被静态截到 2K**, 中间项目 3-6 全在截断标记之后, AI 看
        不到, 它**不承认看不到**, 直接编造下一步内容. 6-7 轮才修对.

      - 静态截设计本意是 ReAct loop 50+ 轮 tool 防 128K 爆, 但你工作台单次
        最多 5-15 轮 tool, 远远到不了爆 context 的程度.

    # 动态算法

    1. 算当前 prompt tokens (fallback.estimate_prompt_tokens, 含 messages + tools)
    2. 计算可用 budget = context_window - response_reserve - safety_margin
    3. prompt_tokens <= budget → 原样返 (深拷贝防误改)
    4. 超 budget → 走老 truncate_tool_messages 静态截 (兜底保 context)
    5. token 估算异常 → 走老静态截 (保守降级, 跟老行为一致)

    # 风险 & 保险

    LiteLLM token_counter 10-25% 误差 → safety_margin=10K 吸收
    异常 fallback → 永远不会 worse than old static behavior

    Args:
        messages: chat messages 数组
        model_context_window: 当前 model 的 context window (从 model.context_window 取)
        model_name: LiteLLM 完整 model 名, 给 estimate_prompt_tokens 用. None 走 char/2 兜底
        response_reserve_tokens: 留给 LLM response 的 token 数 (default 5K)
        safety_margin_tokens: tokenizer 误差余量 (default 10K)
        max_bytes_per_tool: 超 budget 时单条 tool 截到多大 (default 20K)

    Returns:
        新 messages list (deepcopy). 不动原 messages.
    """
    if not messages:
        return messages

    # ── 1. budget 计算 ──
    if model_context_window <= 0:
        # ctx_window 不合法, 保守走老静态截
        logger.warning(
            "truncate_tool_messages_dynamic: model_context_window=%d 不合法, "
            "fallback 走静态截",
            model_context_window,
        )
        return truncate_tool_messages(messages, max_bytes_per_tool=max_bytes_per_tool)

    budget = model_context_window - response_reserve_tokens - safety_margin_tokens
    if budget <= 0:
        # 极小 ctx_window (< 15K), 留不出余量, 全截
        logger.warning(
            "truncate_tool_messages_dynamic: ctx_window=%d 太小 (response=%d + margin=%d "
            "超出), fallback 走静态截",
            model_context_window, response_reserve_tokens, safety_margin_tokens,
        )
        return truncate_tool_messages(messages, max_bytes_per_tool=max_bytes_per_tool)

    # ── 2. 当前 prompt token 估算 (复用 fallback.estimate_prompt_tokens) ──
    try:
        from .fallback import estimate_prompt_tokens  # noqa: PLC0415
        prompt_tokens = estimate_prompt_tokens(messages, model=model_name)
    except Exception as e:  # noqa: BLE001
        # 算 token 整个挂 → 保守走静态截 (跟老行为完全一致)
        logger.warning(
            "truncate_tool_messages_dynamic: estimate_prompt_tokens 异常 (%s), "
            "fallback 走静态截",
            e,
        )
        return truncate_tool_messages(messages, max_bytes_per_tool=max_bytes_per_tool)

    # ── 3. 决策 ──
    if prompt_tokens <= budget:
        # 没爆 — 原样返 (deepcopy 防 caller 改 messages 影响原始)
        logger.debug(
            "truncate_tool_messages_dynamic: prompt=%d <= budget=%d (ctx=%d - resp=%d "
            "- margin=%d), 不截",
            prompt_tokens, budget, model_context_window, response_reserve_tokens,
            safety_margin_tokens,
        )
        return deepcopy(messages)

    # 超 budget — 走老静态截兜底
    logger.info(
        "truncate_tool_messages_dynamic: prompt=%d > budget=%d (ctx=%d - resp=%d "
        "- margin=%d), 走静态截 cap=%d",
        prompt_tokens, budget, model_context_window, response_reserve_tokens,
        safety_margin_tokens, max_bytes_per_tool,
    )
    return truncate_tool_messages(messages, max_bytes_per_tool=max_bytes_per_tool)


__all__ = [
    "MAX_BYTES_PER_TOOL",
    "DYNAMIC_SAFETY_MARGIN_TOKENS",
    "DYNAMIC_RESPONSE_RESERVE_TOKENS",
    "truncate_tool_messages",
    "truncate_tool_messages_dynamic",
]
