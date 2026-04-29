"""统计意图强制守卫 — 员工要精确数 / 算时, 强制模型 execute_code, 不许自数.

# 为啥需要 (踩过坑 2026-04-29 鸿波 demo)

LLM 在 enumeration 任务上**物理限制** — token-level attention 不擅长数 30+ items.
长 context 累加更糟 (5+5+5 累计起来会数错). SOUL.md § 数据统计 = 代码统计
**软纪律不稳** — 模型偷懒不写代码就自己看. 鸿波反馈"已修正多次仍出错".

工程级兜底 (跟 SOUL.md 配套硬规则):

  - chat_completions 入口正则扫员工最近一句 user message
  - 命中 "多少/统计/总数/合计/分组" 等统计关键词
  - 在 system prompt 末尾追加 STATS_GUARD_BLOCK 强制提醒
  - 模型每次推理时**最近 token 必看到**, 不依赖 attention 飘

# 跟 SOUL.md 的关系

  SOUL.md § 数据统计 = 代码统计: **常驻** 软纪律, 启动时注入, 整个 session 在
  stats_guard (本模块): **触发式** 硬提醒, 仅当员工要统计才注入

# 跟 inject_session_facts 的关系

同 inject 机制, 都是在最后一条 system message 末尾追加文本. 顺序:
  inject_identity → inject_session_facts → inject_stats_guard → 其他 (sanitize / multimodal)
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("catfish.gateway.stats_guard")

#: 统计意图关键词. 多语言混合 — 中文 + 英文都覆盖, 客户场景常见.
#: 加 \b 防止 "totalize" 等词误匹配.
_STATS_PATTERNS = re.compile(
    r"多少[条个项行只张份]"
    r"|统计|总数|总共|总计|合计|累计"
    r"|平均|最大|最小|最高|最低"
    r"|分组|按.*分类|按.*分布|占比"
    r"|行数|条数|计数|计算"
    r"|出现.{0,4}次|去重"
    r"|\bcount\b|\bsum\b|\baverage\b|\btotal\b|\bgroup\s*by\b|\baggregate\b",
    re.IGNORECASE,
)

#: 命中后追加到 system prompt 末尾的文本块.
#: 短而硬, 不解释 (SOUL.md 已经解释过了, 这里是工程级最后通牒).
_STATS_GUARD_BLOCK = """

## ⚠ 本次员工要精确统计 (gateway 强制注入, 不能忽略)

**铁律**: 你**必须** `execute_code` 用 Python 算 (pandas / len / sum / groupby /
collections.Counter), **严禁**自己数 / 估 / 累加. LLM enumeration 30+ 条**必错**,
这是 token attention 的物理限制, 不是 prompt 能调的.

**模板**:
```python
# CSV / Excel
import pandas as pd
df = pd.read_csv(path)   # 或 pd.read_excel
print(f"总行数: {len(df)}")
print(df.groupby('column').size().to_string())
print(f"满足条件: {(df['col'] > 100).sum()}")

# 上下文已抓的数据 (write 进 list 再算)
data = [...]
print(f"总数: {len(data)}")
from collections import Counter
print(Counter(d['type'] for d in data))
```

数据还在工具结果 / 网页 / 大文件里? **先** write_file 落到 /tmp/*.jsonl 或 .csv,
再 execute_code 处理. 不要在 context 里累加.

如果你这次回复**不**调 execute_code 而直接给数字, **你大概率错了**, 员工会再纠正.
"""


def has_stats_intent(messages: list[dict[str, Any]] | None) -> bool:
    """检测最近一句 user message 是否要求统计.

    扫**最后一条** role='user' 消息. 之前的 user 消息是历史轮次, 不算当前需求.

    Returns:
        True 如果命中统计关键词. False 否则 (含: 没 user message / content 空 /
        不匹配模式).
    """
    if not messages:
        return False
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        text = ""
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            # multimodal: 拼所有 text part
            text = " ".join(
                p.get("text", "")
                for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            )
        if not text:
            return False
        return bool(_STATS_PATTERNS.search(text))
    return False


def inject_stats_guard(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """命中统计意图 → 在最后一条 system message 末尾追加强制提醒.

    设计:
      - 没 system message → 不强加 (跟 inject_identity / inject_session_facts 一致)
      - 没命中统计意图 → 原样返
      - 命中 + 有 system → 复制 list, 修改 last system message content 后返
      - content 是 list (multimodal): append text part
      - content 是其他类型: 不动 (保守)
    """
    if not has_stats_intent(messages):
        return messages

    last_system_idx = -1
    for i, msg in enumerate(messages):
        if isinstance(msg, dict) and msg.get("role") == "system":
            last_system_idx = i

    if last_system_idx < 0:
        return messages

    new_messages = list(messages)
    sys_msg = dict(new_messages[last_system_idx])
    content = sys_msg.get("content", "")
    if isinstance(content, str):
        sys_msg["content"] = content.rstrip() + _STATS_GUARD_BLOCK
    elif isinstance(content, list):
        new_content = list(content)
        new_content.append({"type": "text", "text": _STATS_GUARD_BLOCK})
        sys_msg["content"] = new_content
    else:
        return messages

    new_messages[last_system_idx] = sys_msg
    logger.info("stats_guard 命中: 注入强制 execute_code 提醒到 system prompt")
    return new_messages
