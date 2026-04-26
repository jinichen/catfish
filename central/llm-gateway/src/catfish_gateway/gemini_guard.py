"""Gemini-only request hardening —— 防 native tool_code / code_execution 退化。

背景:
    Gemini 2.x/3.x (Pro / Flash 都有) 在以下情况下会输出 native "code_execution"
    或 "tool_code" 模式 —— 它会在 assistant content 里写 Python 伪代码:

        ```tool_code
        print(some_invented_function(args))
        ```

    然后 LiteLLM 在 OpenAI schema 转译时, 有些情况会把它包成一个伪 tool_call,
    function name 是模型瞎编的 (例如 `mcp_xxx_yyy`), arguments 是 Python 字面量。
    我们前端拿到这种 "工具调用", 实际 dispatch_tool 时报 unknown tool, 用户看到
    "工具调用了但没结果" 的卡死状态。

触发条件 (实验观察):
    1. tools=[] 或不传 tools 字段 → Gemini 自动启用 code_execution 心智
    2. 模型规模较小 (Flash 比 Pro 严重)
    3. 用户问题里包含 "查 / 看 / 找" 等动词 → Gemini 倾向"我应该调个工具"

防御:
    在 system message 末尾加一句明确禁止 tool_code 块的话, 同时建议它"没工具就
    直接回答"。Gemini 对 system instruction 服从度很高 (Google 文档明确建议
    把工具相关规约写 system), 这条加进去后 native 退化几乎绝迹。

只对 Gemini 加, 因为:
    - OpenAI 兼容 (Qwen / GPT) 没这毛病
    - 加在所有模型上无害但增加 prompt token, 浪费

非破坏性:
    - 只 append 到 system, 不改 user/assistant 消息
    - 如果已有 system, append 到末尾用 \n 分隔
    - 如果没 system, 不加 (避免 inject_identity 没工作时这里也无脑加)
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("catfish.gateway.gemini_guard")

# 这一句加进 system 后, Gemini Flash/Pro native code_execution 退化基本消失。
# 写得直接, 因为模型对礼貌性废话权重低。中英双语 hedge —— 中文给中文 prompt, 英文给
# 英文 prompt; 双语版总 token ~80, 占用极小但鲁棒性高。
GEMINI_GUARD_INSTRUCTION = (
    "\n\n[CATFISH-GEMINI-GUARD] "
    "Do NOT emit ```tool_code or ```python code blocks pretending to call tools. "
    "If the user gives you tools via the OpenAI tools API, call them via tool_calls "
    "(name + arguments JSON). If no tools are available, answer the user's question "
    "directly in natural language — do NOT invent function names like "
    "`mcp_xxx_yyy(...)` or `print(some_func())`. "
    "禁止输出 ```tool_code 或 ```python 代码块假装调用工具; "
    "没有可用工具时直接用自然语言回答用户。"
)


def _is_gemini(model) -> bool:
    """upstream.model 像 'gemini/...' 就是 Gemini 家族。

    LiteLLM 命名约定: provider/model_name —— 我们就靠 'gemini/' 前缀判定,
    覆盖 gemini-2.x / gemini-3.x / preview / flash / pro 全部子型号。
    """
    upstream_model = getattr(getattr(model, "upstream", None), "model", "") or ""
    return upstream_model.lower().startswith("gemini/")


def harden_for_gemini(body: dict[str, Any], model) -> dict[str, Any]:
    """如果是 Gemini, 给 system message append 防退化指令; 否则原样返回。

    设计选择:
        - mutates body in-place 并返回, 调用方习惯链式
        - 不依赖 body 里有 system message —— 没 system 就跳过 (上游 inject_identity
          应该已经加过, 没加说明客户端明确不要 system, 我们尊重)
        - tools 字段不动 —— 防退化的根本是 system instruction, tools 只是次要
    """
    if not _is_gemini(model):
        return body

    messages = body.get("messages") or []
    if not isinstance(messages, list):
        return body

    # 找第一条 system message (OpenAI 协议: system 必须在最前)
    system_idx = next(
        (i for i, m in enumerate(messages) if isinstance(m, dict) and m.get("role") == "system"),
        -1,
    )
    if system_idx < 0:
        # 没 system 就不强加 —— 尊重客户端意图
        logger.debug("gemini guard: no system message in body, skip")
        return body

    sys_msg = messages[system_idx]
    content = sys_msg.get("content")
    if isinstance(content, str):
        if "[CATFISH-GEMINI-GUARD]" in content:
            return body  # 幂等: 别重复 append
        sys_msg["content"] = content + GEMINI_GUARD_INSTRUCTION
    elif isinstance(content, list):
        # OpenAI 多模态 system —— content 是 part 数组, 找第一个 text part
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                txt = part.get("text") or ""
                if "[CATFISH-GEMINI-GUARD]" in txt:
                    return body
                part["text"] = txt + GEMINI_GUARD_INSTRUCTION
                break
        else:
            # 全是 image 没 text? 加一段
            content.append({"type": "text", "text": GEMINI_GUARD_INSTRUCTION.strip()})
    else:
        # content 是 None / int / 怪东西 —— 不动, log 一下方便排查
        logger.warning(
            "gemini guard: system message content is unexpected type %s, skip",
            type(content).__name__,
        )
        return body

    has_tools = bool(body.get("tools"))
    logger.info(
        "gemini guard applied to %s (tools=%s, system_idx=%d)",
        getattr(model, "name", "?"),
        len(body.get("tools") or []) if has_tools else "empty",
        system_idx,
    )
    return body
