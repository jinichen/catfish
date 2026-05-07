"""
self_critique.py — BL-A1.3 (5/8 ship): 检测 LLM "幻觉完成" 强制真做.

# 为啥要这模块

5/7 鸿波: "鲶鱼说'已生成 docx' 但磁盘上没文件" — 真 Agent 灵魂问题之一.
LLM 经常**承诺完成**(说"已保存/已完成") 但**实际没调对应工具**, 这是文字幻觉.
鸿波 4-29 demo 反复翻车的真因.

# 两阶段策略

**阶段 1 (5/8 ship)**: 工程级检查, 0 LLM 调用, 0 quota.
  - 扫 messages 最近 N 条
  - assistant content 含 "已生成 / 已保存 / 已完成 / 已创建" 等承诺关键词
  - 但**没有**对应的 execute_code / catfish_run_skill / write_file tool call
  - → 注入 system hint 给 LLM 下一次调用: "你说'X 已完成', 但实际没调工具.
    立刻调 execute_code 真做出来, 不要重复说'已完成'."

**阶段 2 (5/22 后, P2)**: 真 LLM critique.
  - 用 qwen-flash 评估 LLM 回复完成度
  - 输出 JSON {complete, reasons, missing}
  - 不通过 → 主 LLM retry

阶段 1 已经能拦 80% 的 "幻觉完成". 5/14 demo 用阶段 1 够.

# 跟 tool_retry_hint 区别

| | tool_retry_hint (BL-A1.2) | self_critique (BL-A1.3) |
|---|---|---|
| 检测什么 | tool 调了但失败 | 该调 tool 但**没调** |
| 触发 | 连续 tool ok=false | 完成承诺 + 缺对应 tool_call |
| hint | "改参数 / 换工具" | "立刻真调工具, 不要嘴说" |

互补, 一个管"调了不行", 一个管"该调没调".

# 测试

10 单测 (tests/test_self_critique.py):
    - 普通 chat 不触发
    - 含完成承诺 + 有对应 execute_code → 不触发 (真做了)
    - 含完成承诺 + 没 tool_call → 触发
    - 含完成承诺 + 有 tool_call 但失败 (ok=false) → 不触发 (那是 retry hint 管)
    - 多种承诺关键词都识别 (已生成/已保存/已完成/已创建/已修改)
    - hint 不重复注入
    - 不修改原 messages
    - tool 名 keyword 匹配 (execute_code / catfish_run_skill / write_file)
    - 短 content 不触发 (避免 "已知" "已经" 误判)
    - 完成承诺 + 提议未来动作 ("我马上...") 不触发
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger("catfish.gateway.self_critique")

# 完成承诺关键词 (LLM 经常说这些, 但要有 tool_call 配套)
_COMPLETION_PROMISE_PATTERNS = [
    r"已生成",
    r"已保存",
    r"已完成",
    r"已创建",
    r"已修改",
    r"已写入",
    r"已输出",
    r"已写好",
    r"已经生成",
    r"已经保存",
    r"已经完成",
    r"已经创建",
    r"已经修改",
    r"已经写入",
    r"已成功",
    r"成功生成",
    r"成功保存",
    r"成功创建",
    r"saved successfully",
    r"created successfully",
    r"written successfully",
]
_COMPLETION_PROMISE_REGEX = re.compile("|".join(_COMPLETION_PROMISE_PATTERNS), re.IGNORECASE)

# 排除 — 这些是"未来意图"不是"已完成", 不触发
_FUTURE_INTENT_PATTERNS = [
    r"我?马上",
    r"我?立即",
    r"我?这就",
    r"准备生成",
    r"准备保存",
    r"即将",
    r"将会",
    r"will create",
    r"will save",
    r"about to",
]
_FUTURE_INTENT_REGEX = re.compile("|".join(_FUTURE_INTENT_PATTERNS), re.IGNORECASE)

# 真做事的工具名 (有这些 tool_call 才算"真做")
_PRODUCTIVE_TOOL_NAMES = {
    "execute_code",
    "python",
    "bash",
    "shell_exec",
    "sh",
    "catfish_run_skill",
    "write_file",
    "edit_file",
    "create_file",
    "save_file",
    "tauri_save_file",
    "memory_save",
    "catfish_remember",
}

# hint 防重复注入 marker
_HINT_MARKER = "[BL-A1.3 self-critique-hint]"

# 触发后注入的 system hint
_HINT_TEMPLATE = (
    f"{_HINT_MARKER}\n"
    "**完成度检查不通过**: 你刚才说\"{quoted_promise}\", 但前面**没有**对应的 tool 调用 "
    "(execute_code / catfish_run_skill / write_file 等), 也就是**没真做**.\n\n"
    "这是文字幻觉, 真 Agent 不能这样:\n"
    "1. 立刻**真调工具**做出来 (例如 execute_code 写 docx 到磁盘)\n"
    "2. 调完后再用 chat 文字汇报路径 + 大小\n"
    "3. **不要**重复\"已完成\"那种空话, 没真做就别说做了\n\n"
    "央企客户要的是真文件实物, 不是 chat 里的承诺."
)

# 扫多深 (倒数 N 条 messages, 看完成承诺 vs 对应 tool_call)
_SCAN_DEPTH = 6


def _has_completion_promise(content: str) -> tuple[bool, str]:
    """content 含完成承诺 + 不含未来意图 → 返 (True, 匹配的关键词).

    'X 已经生成' 触发, '我马上生成 X' 不触发, '已知 / 已经'(没承诺动词) 不触发.
    """
    if not content or not isinstance(content, str):
        return False, ""

    # 先排除未来意图. 如果含 "马上 / 即将" 等未来词紧靠完成动词, 跳过.
    # 简化: 整个 content 含未来词 + 完成词, 但完成词被未来词限定 → 整体不触发.
    # 真实: "我马上生成 X" 含"生成" 但加了"马上" → 整体认作未来.
    has_future = bool(_FUTURE_INTENT_REGEX.search(content))

    promise_match = _COMPLETION_PROMISE_REGEX.search(content)
    if not promise_match:
        return False, ""

    if has_future:
        # 含未来意图词 — 检查 promise 是否在 future 词附近 (前后 20 字)
        # 简化: 如果整体 content 短 (< 100 字) 且含 future, 认作 future-only
        if len(content) < 100:
            return False, ""
        # 长 content 同时有"我马上做" + "已经做了 Y", 复杂 case 不优化, 触发
        # (false positive 比 false negative 好 — 多让 LLM 真做一次也无害)

    return True, promise_match.group(0)


def _has_productive_tool_call_recent(messages: list, depth: int = _SCAN_DEPTH) -> bool:
    """扫倒数 depth 条 messages, 看是否有 productive tool 的 tool_call (调到了).

    有 tool_call 就算真做. 不管 tool 结果成功失败 (那是 tool_retry_hint 的事).
    """
    if not messages:
        return False
    tail = messages[-depth:]
    for msg in tail:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "assistant":
            continue
        tool_calls = msg.get("tool_calls") or []
        if not isinstance(tool_calls, list):
            continue
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") or {}
            name = fn.get("name") or tc.get("name")
            if name in _PRODUCTIVE_TOOL_NAMES:
                return True
    return False


def has_existing_hint(messages: list) -> bool:
    """是否已注入过 BL-A1.3 hint."""
    for msg in messages[-15:]:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "system":
            continue
        content = msg.get("content")
        if isinstance(content, str) and _HINT_MARKER in content:
            return True
    return False


def inject_completion_critique_hint(messages: list) -> list:
    """检测 LLM 幻觉完成 → 注入 hint 强制真做.

    触发条件: 倒数 _SCAN_DEPTH 条里
        a. 最近一条 assistant content 含完成承诺关键词
        b. 但同范围内 NO productive tool_call

    Returns:
        新 messages list (没触发返回原引用)
    """
    if not messages:
        return messages
    if has_existing_hint(messages):
        return messages

    # 找最近一条 assistant 含完成承诺
    tail = messages[-_SCAN_DEPTH:]
    promise_msg_idx = None
    promise_keyword = None
    for i, msg in enumerate(reversed(tail)):
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content") or ""
        if not isinstance(content, str):
            continue
        # 短 content 跳过 (避免 "已知" 之类误判)
        if len(content.strip()) < 8:
            continue
        has, kw = _has_completion_promise(content)
        if has:
            promise_msg_idx = len(tail) - 1 - i
            promise_keyword = kw
            break

    if promise_msg_idx is None:
        return messages

    # 看同范围内有没有 productive tool_call
    if _has_productive_tool_call_recent(messages, depth=_SCAN_DEPTH):
        # 真做了 (调了 execute_code 之类), 不触发
        return messages

    # 触发: LLM 说"已完成"但没真调 tool
    new_messages = list(messages)
    hint = _HINT_TEMPLATE.format(quoted_promise=promise_keyword or "已完成")
    new_messages.append({"role": "system", "content": hint})
    logger.info(
        "self-critique hint injected: promise_keyword=%r, no productive tool_call in tail",
        promise_keyword,
    )
    return new_messages
