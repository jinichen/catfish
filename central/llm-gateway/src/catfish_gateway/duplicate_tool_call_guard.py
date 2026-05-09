"""
duplicate_tool_call_guard.py — BL-FIX24 (5/9 ship): 拦 LLM 重复跑同一段 tool_call.

# 为啥要这模块

5/9 鸿波 demo 现场: 鲶鱼真的 emit execute_code (跑了 5 次 docx 生成),
**每次跑同一段 code 产同一个文件**. 鲶鱼记不住自己刚做过, 而且每轮都主动问
"需要继续更新吗?" 拉鸿波回 "立刻执行" 又重做. 死循环.

跟 self_critique (BL-A1.3) 区别:
| | self_critique | duplicate_guard (本模块) |
|---|---|---|
| 检测什么 | 该调没调 (嘴说不做) | 调了又调 (重复做) |
| 触发 | 完成承诺 + 缺 tool_call | 同 productive tool 调 ≥2 次, arguments hash 一样 |
| hint | 立刻真调 | 别再调, 已做过 |

互补 — 一个治"该做没做", 一个治"做了又做".

# 设计

1. 扫倒数 N 条 messages (默认 _SCAN_DEPTH=12)
2. 抓 assistant.tool_calls 里的 productive tool (execute_code / catfish_run_skill 等)
3. 算 arguments 的 sha256 (normalized JSON)
4. 计数: 某个 (tool_name, hash) 出现 ≥ _DUP_THRESHOLD=2 次 → 注入 system hint
5. hint 防重复注入 marker

# 不该触发的情况

- 不同 tool 调用 (catfish_browser_screenshot 多次截不同元素 — args 不一样, hash 不同, 不触发)
- 同 tool 不同 arguments (execute_code 跑不同代码 — hash 不同, 不触发)
- 单次调用 (没重复, 不触发)

# 测试

15 单测 (tests/test_duplicate_tool_call_guard.py):
- 重复 execute_code 同 args ≥ 2 次 → 触发
- 重复 catfish_run_skill 同 args → 触发
- 不同 tool 调用 → 不触发
- 同 tool 不同 args → 不触发
- 单次 → 不触发
- 非 productive tool (browser_screenshot) → 不触发
- hint marker 防重复
- 不修改原 messages
- empty messages → 不触发
- 多种 productive tool 混合
- arguments JSON 顺序无关 (normalize)
- arguments 是 string vs dict 都识别
- threshold 边界 (1 次不触发, 2 次触发)
- 跨 N 条扫描深度
- multipart content 不影响
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

logger = logging.getLogger("catfish.gateway.duplicate_tool_call_guard")

# 真做事的工具名 — 跟 self_critique 一致
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
}

# 扫多深
_SCAN_DEPTH = 12

# 重复触发阈值 (≥ 这个次数才注入 hint)
_DUP_THRESHOLD = 2

# hint 防重复注入 marker
_HINT_MARKER = "[BL-FIX24 duplicate-tool-call-guard]"

# 触发后注入的 system hint
_HINT_TEMPLATE = (
    f"{_HINT_MARKER}\n"
    "**重复检测**: 你最近调了 {count} 次相同的 `{tool_name}` "
    "(arguments hash 一样, 等于跑同一段代码 N 次产同一个文件).\n\n"
    "这是无意义的重做, 真 Agent 不能这样:\n"
    "1. **这次不要再调** `{tool_name}` (除非员工**明确**要新数据 / 新参数)\n"
    "2. 直接告诉员工 \"我已经做过这个 X 次, 文件在 ~/.catfish/output/...\""
    " 等他给**新指令**\n"
    "3. **不要主动问** \"需要再做一次吗 / 需要继续吗\" — 这种问句会拉员工"
    "回\"立刻执行\" 你又重做. 沉默等指令是对的.\n\n"
    "鸿波 5/9 反馈: \"是不是现在对于任务的完成情况没有一个好的评估手段?\". "
    "你需要识别\"已做过\" 不再重复."
)


def _normalize_arguments(args: Any) -> str:
    """把 tool_call.function.arguments 转成 normalized JSON 字符串.

    LiteLLM / OpenAI 的 arguments 是 str (JSON-encoded), 也可能是已解析的 dict.
    JSON dump 时 sort_keys=True + ensure_ascii=False 让顺序无关, 中文不转 \\uXXXX.
    解析失败原样返回 (落到 sha256 仍 deterministic).
    """
    if isinstance(args, dict):
        try:
            return json.dumps(args, sort_keys=True, ensure_ascii=False)
        except Exception:
            return repr(args)
    if isinstance(args, str):
        # 尝试 parse + normalize, 失败用原 str
        try:
            return json.dumps(json.loads(args), sort_keys=True, ensure_ascii=False)
        except Exception:
            return args
    return str(args)


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def _extract_productive_tool_calls(messages: list, depth: int = _SCAN_DEPTH) -> list[tuple[str, str]]:
    """从最近 depth 条 messages 抽 (tool_name, arguments_hash) 列表.

    只看 productive tool (会真改文件 / 真跑代码 的). 非 productive (例如
    browser_screenshot / read_file) 重复调没问题, 不该拦.
    """
    if not messages:
        return []
    tail = messages[-depth:]
    out: list[tuple[str, str]] = []
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
            name = fn.get("name", "")
            if name not in _PRODUCTIVE_TOOL_NAMES:
                continue
            args_hash = _sha256(_normalize_arguments(fn.get("arguments", "")))
            out.append((name, args_hash))
    return out


def _has_existing_hint(messages: list) -> bool:
    """看 messages 末尾是不是已注入过 hint, 防一次请求重复注入."""
    if not messages:
        return False
    # 只看最近 3 条 (最新一次注入应在最尾)
    for msg in messages[-3:]:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content", "")
        if isinstance(content, str) and _HINT_MARKER in content:
            return True
    return False


def inject_duplicate_guard_hint(messages: list) -> list:
    """主入口: 扫 messages, 检测重复 productive tool_call, 触发就注入 user hint.

    入参 messages 不被原地修改 — 返回**新 list** (沿用 self_critique 同惯例).

    注入位置: 末尾 user role hint (跟 self_critique 一致, 让 LLM 在下次回复
    前看到这条提醒).
    """
    if not messages or not isinstance(messages, list):
        return messages

    # 防重复
    if _has_existing_hint(messages):
        return messages

    extracted = _extract_productive_tool_calls(messages)
    if len(extracted) < _DUP_THRESHOLD:
        return messages

    # 计数 (tool_name, hash) 组合
    counter: dict[tuple[str, str], int] = {}
    for key in extracted:
        counter[key] = counter.get(key, 0) + 1

    # 找出最严重重复
    worst = max(counter.items(), key=lambda kv: kv[1])
    (tool_name, _hash), count = worst

    if count < _DUP_THRESHOLD:
        return messages

    hint = _HINT_TEMPLATE.format(count=count, tool_name=tool_name)
    logger.warning(
        "BL-FIX24 duplicate-tool-call-guard: %s 重复 %d 次, 注入 hint 阻止再调",
        tool_name, count,
    )

    # 拷一份 (不改原 messages, 跟 self_critique 一致)
    new_messages = list(messages)
    new_messages.append({"role": "user", "content": hint})
    return new_messages
