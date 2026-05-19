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

# BL-LLM-PLAN-WITHOUT-ACT (5/19 扩展): plan-then-stop detection

钟摆历史: qwen-aware 铁律 1 让它 tool_call first → 又卡死循环 → 铁律 4 终止条件 →
qwen 学会"输出 plan JSON 然后 finish_reason=stop, 以为 plan 就是 final answer".

不再加 prompt 铁律 (滑过头). 改 agent loop guard 兜底:
  - assistant content 含 plan 文本 (JSON plan / "step 1" / "第 1 步" / "我将" / "开始执行")
  - finish_reason=stop (或 没 tool_calls)
  - 同范围内**没**任何 productive tool_call
  - → 注入 hint "你给的是 plan 不是 final answer, 必须真调 tool_call 执行 step 1"

跟原有完成承诺 detect 互补:
  - 完成承诺路径: "已生成 X" + 没 tool_call → 触发 (文字幻觉完成)
  - plan-then-stop 路径: "step 1 ... 开始执行" + 没 tool_call → 触发 (只画饼没动手)

# 跟 tool_retry_hint 区别

| | tool_retry_hint (BL-A1.2) | self_critique (BL-A1.3) |
|---|---|---|
| 检测什么 | tool 调了但失败 | 该调 tool 但**没调** |
| 触发 | 连续 tool ok=false | 完成承诺 + 缺对应 tool_call |
| hint | "改参数 / 换工具" | "立刻真调工具, 不要嘴说" |

互补, 一个管"调了不行", 一个管"该调没调".

# 测试

tests/test_self_critique.py:
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

BL-LLM-PLAN-WITHOUT-ACT 扩展 case:
    - JSON plan ({"plan": [...]}) + 没 tool_call → 触发 plan hint
    - "step 1: ... step 2: ..." + 没 tool_call → 触发
    - "我将: 1. ... 2. ... 开始执行" + 没 tool_call → 触发
    - 真完成文字 (含路径 + 大小) → **不**触发 (避免误伤真完成)
    - 同范围有 productive tool_call → **不**触发 (plan + 真做 = OK)
    - plan hint 跟完成承诺 hint 各自幂等 不重复
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

# ─── BL-LLM-PLAN-WITHOUT-ACT (5/19): plan-then-stop 检测 ──────────────────
# qwen 钟摆: 加铁律 4 终止条件后, qwen 学会"输出 plan JSON / step 1 → step 2 ...
# 然后 finish_reason=stop, 以为 plan 就是 final answer". 实际啥都没真做.
# 不再 prompt engineering (滑过头). 用 agent loop guard 兜底.

# 中文 plan 文本 marker (qwen 常用句式)
_PLAN_TEXT_PATTERNS = [
    r"我将\s*[:：]",                # "我将:" / "我将："
    r"我会\s*[:：]",
    r"我打算",
    r"接下来我会",
    r"接下来将",
    r"^\s*步骤\s*[1一]\s*[:：.]",   # "步骤 1:" / "步骤一."
    r"第\s*[1一]\s*步",            # "第 1 步" / "第一步"
    r"开始执行\s*[:：]?\s*$",       # "开始执行:" / "开始执行"
    r"现在开始执行",
    r"^\s*1\.\s.{0,40}\n\s*2\.",   # "1. xxx\n2. ..." 编号列表 plan
    # 英文也兜一下 (qwen 偶尔用)
    r"\bstep\s*1\b\s*[:：.].{0,40}\bstep\s*2\b",
    r"\bI\s+will\s*[:：]",
]
_PLAN_TEXT_REGEX = re.compile("|".join(_PLAN_TEXT_PATTERNS), re.IGNORECASE | re.MULTILINE)

# JSON plan 格式 — compound_intent 教 qwen 输出的格式, qwen 学了就 stop
# 例:  {"plan": [{"step": 1, "action": "...", "what": "..."}, ...]}
_PLAN_JSON_REGEX = re.compile(
    r'"plan"\s*:\s*\[' +              # "plan": [
    r'|"step"\s*:\s*\d+' +             # "step": 1
    r'|"action"\s*:\s*"',              # "action": "..."
    re.IGNORECASE,
)

# "真完成" anti-pattern — 出现这些就**不**触发 plan hint (避免误伤)
# 关键标志: 含具体文件路径 / URL / 字节数 / 行数 / "已" 完成 词
# (完成承诺路径有自己的检测, 这里只防 plan hint 误伤真完成)
_REAL_DELIVERY_PATTERNS = [
    r"/[A-Za-z0-9_\-./]{6,}\.(docx|xlsx|pptx|pdf|md|txt|csv|json|html|png|jpg|svg|zip)\b",
    r"~/[A-Za-z0-9_\-./]{4,}",
    r"\b\d+(\.\d+)?\s*(KB|MB|GB|bytes?)\b",
    r"\b\d+\s*(行|段|字|条记录|个文件)\b",
    r"https?://\S+",
]
_REAL_DELIVERY_REGEX = re.compile("|".join(_REAL_DELIVERY_PATTERNS), re.IGNORECASE)

# plan-then-stop hint 防重复 marker (跟完成承诺独立, 各自幂等)
_PLAN_HINT_MARKER = "[BL-A1.3 plan-then-stop-hint]"

_PLAN_HINT_TEMPLATE = (
    f"{_PLAN_HINT_MARKER}\n"
    "**plan-then-stop 检测**: 你刚输出了**计划文本** (例: \"我将...\", "
    "\"step 1 → step 2\", JSON plan 等), 但**没有发任何 tool_call**, "
    "也没有产生真实交付物 (文件路径 / URL / 字节数).\n\n"
    "**plan 不是 final answer**. 员工要的是真东西, 不是一段计划文字.\n\n"
    "立刻按下面走:\n"
    "1. 选 plan 里的**第 1 步**, 现在就 emit 对应的 tool_call (catfish_run_skill / "
    "execute_code / write_file 等), 真调出去.\n"
    "2. **本轮**只发 tool_call, 不要再讲 plan, 不要再说\"我将...\".\n"
    "3. 等 tool result 回来, 下一轮再决定要不要继续 step 2 或者收尾.\n\n"
    "如果信息不够开干, 调 clarify 工具问员工. 但**不允许**只输出 plan 然后 stop."
)

# 扫多深 (倒数 N 条 messages, 看完成承诺 vs 对应 tool_call)
_SCAN_DEPTH = 6

# plan 文本的最短长度 — 短于这个不判 plan (避免普通对话误伤)
_PLAN_MIN_LEN = 20


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


def _has_plan_intent(content: str) -> tuple[bool, str]:
    """content 是不是 plan 文本 (qwen "plan-then-stop" 模式).

    判断标志:
      1. 含 JSON plan 关键字段 (`"plan": [` / `"step": N` / `"action": "..."`)
      2. 或含中文 plan 句式 ("我将:" / "step 1" / "第 1 步" / "开始执行")

    例外: content 含真交付物标志 (具体路径 / URL / 字节数) → 视为真完成, 不判 plan.

    Returns:
        (是不是 plan, 匹配到的关键词片段)
    """
    if not content or not isinstance(content, str):
        return False, ""
    if len(content.strip()) < _PLAN_MIN_LEN:
        return False, ""

    # 含真交付物 → 不当 plan (避免误伤真完成的 final answer)
    if _REAL_DELIVERY_REGEX.search(content):
        return False, ""

    # JSON plan 优先 (信号最强)
    json_m = _PLAN_JSON_REGEX.search(content)
    if json_m:
        return True, json_m.group(0)

    # 中文 / 英文 plan 句式
    text_m = _PLAN_TEXT_REGEX.search(content)
    if text_m:
        return True, text_m.group(0)

    return False, ""


def has_existing_hint(messages: list) -> bool:
    """是否已注入过 BL-A1.3 完成承诺 hint.

    BL-FIX8 (5/8): 兼容老 system role + 新 user role 两种历史.
    """
    for msg in messages[-15:]:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") not in ("system", "user"):
            continue
        content = msg.get("content")
        if isinstance(content, str) and _HINT_MARKER in content:
            return True
    return False


def has_existing_plan_hint(messages: list) -> bool:
    """是否已注入过 plan-then-stop hint (独立于完成承诺 hint, 各自幂等)."""
    for msg in messages[-15:]:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") not in ("system", "user"):
            continue
        content = msg.get("content")
        if isinstance(content, str) and _PLAN_HINT_MARKER in content:
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
    # BL-FIX8 (5/8): role 改 user 不用 system. 跟 BL-FIX6 tool_retry_hint 同款 —
    # Qwen Go gRPC adapter 中段 system 撞**空 reason 400**, 改 user 则等价于"员工
    # 又说一句", OpenAI 标准接受.
    new_messages = list(messages)
    hint = _HINT_TEMPLATE.format(quoted_promise=promise_keyword or "已完成")
    new_messages.append({"role": "user", "content": hint})
    logger.info(
        "self-critique hint injected (role=user, BL-FIX8): promise_keyword=%r, "
        "no productive tool_call in tail",
        promise_keyword,
    )
    return new_messages


def inject_plan_then_stop_hint(messages: list) -> list:
    """BL-LLM-PLAN-WITHOUT-ACT: 检测 plan-then-stop → 注入 hint 强制真做.

    触发条件: 倒数 _SCAN_DEPTH 条里
        a. 最近一条 assistant content 是 plan 文本 (JSON plan / "step 1" /
           "我将" / "开始执行" 等), 长度 ≥ _PLAN_MIN_LEN
        b. content 不含真交付物标志 (路径 / URL / 字节数) — 避免误伤真完成
        c. 同范围内 NO productive tool_call — 真画饼没动手

    Returns:
        新 messages list (没触发返回原引用)
    """
    if not messages:
        return messages
    if has_existing_plan_hint(messages):
        return messages

    tail = messages[-_SCAN_DEPTH:]
    plan_keyword = None
    for msg in reversed(tail):
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content") or ""
        if not isinstance(content, str):
            continue
        is_plan, kw = _has_plan_intent(content)
        if is_plan:
            plan_keyword = kw
            break

    if plan_keyword is None:
        return messages

    # 真做了 (plan + 真调 tool) → 不触发
    if _has_productive_tool_call_recent(messages, depth=_SCAN_DEPTH):
        return messages

    # 触发: 只画饼没动手
    new_messages = list(messages)
    new_messages.append({"role": "user", "content": _PLAN_HINT_TEMPLATE})
    logger.info(
        "plan-then-stop hint injected (BL-LLM-PLAN-WITHOUT-ACT): "
        "plan_keyword=%r, no productive tool_call in tail",
        plan_keyword,
    )
    return new_messages


def inject_self_critique(messages: list) -> list:
    """聚合入口: 同时跑两条 detect (完成承诺 + plan-then-stop), 各自幂等.

    顺序: 先 completion-promise (老路径优先, "已完成" 信号更强),
    再 plan-then-stop (qwen-aware 兜底). 两者互不冲突, 一轮最多注入两条 hint.
    """
    out = inject_completion_critique_hint(messages)
    out = inject_plan_then_stop_hint(out)
    return out
