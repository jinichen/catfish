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
MAX_BYTES_PER_TOOL = 2_000

#: 触发截断的最小尺寸 — content 小于这个不截.
#: 留 200 字节 slack 给截断分隔标记 (~60 字节), 保证幂等
#: (跑两次结果一致, 不会因为标记本身把内容推过阈值再切一刀).
MIN_BYTES_TO_TRUNCATE = MAX_BYTES_PER_TOOL + 200


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


__all__ = [
    "MAX_BYTES_PER_TOOL",
    "truncate_tool_messages",
]
