"""客户端发来的 OpenAI tools 数组防御性清洗。

为啥单独一文件:
    历史一次坑 (2026-04-26): Companion 早期版本把 tool-bridge 的 input_schema
    误当成整个 function 字段, 发出去的 tool 缺 name —— Qwen / OpenAI 路径宽容
    不报错, 但 LiteLLM 转 Gemini functionDeclarations 时直接 `KeyError: 'name'`
    把整个聊天 500。

    根因当然是客户端 schema 错, 但网关该兜底 —— 一个畸形 tool 不该掀翻整个请求。
    我们丢掉坏的, 留下能用的, 在日志里点名让客户端开发自己看。

OpenAI 标准:
    {"type": "function",
     "function": {"name": "...", "description": "...", "parameters": <schema>}}

最低要求:
    - 顶层 type == "function"
    - function.name 是非空字符串
    - function.parameters 至少能 dict-cast (没有就给个 empty object schema)
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("catfish.gateway.tools_sanitizer")


def sanitize_tools(body: dict[str, Any]) -> dict[str, Any]:
    """原地修 body["tools"] —— 丢畸形条目, 修补能补的字段。

    返回原 body (mutate in-place + return), 调用方习惯链式。
    body 里没 tools / 不是 list / 空数组 都直接返回, 不报错。
    """
    tools = body.get("tools")
    if not isinstance(tools, list):
        return body

    cleaned: list[dict[str, Any]] = []
    dropped: list[str] = []
    fixed: list[str] = []

    for idx, tool in enumerate(tools):
        if not isinstance(tool, dict):
            dropped.append(f"#{idx}(非 dict)")
            continue

        if tool.get("type") != "function":
            dropped.append(f"#{idx}(type!=function: {tool.get('type')!r})")
            continue

        fn = tool.get("function")
        if not isinstance(fn, dict):
            dropped.append(f"#{idx}(function 缺失或非 dict)")
            continue

        name = fn.get("name")
        if not isinstance(name, str) or not name.strip():
            dropped.append(f"#{idx}(function.name 非法: {name!r})")
            continue

        # parameters 没设 / 不是 dict → 补默认 empty object schema
        # Gemini 严格要求 parameters 是 object-shaped, 没有的话其 mapper 也会出错
        params = fn.get("parameters")
        if not isinstance(params, dict):
            fn["parameters"] = {"type": "object", "properties": {}}
            fixed.append(f"#{idx}(name={name}: 补默认 parameters)")

        cleaned.append(tool)

    if dropped:
        logger.warning(
            "tools sanitizer dropped %d/%d: %s",
            len(dropped), len(tools), "; ".join(dropped[:5]),
        )
    if fixed:
        logger.info(
            "tools sanitizer fixed %d/%d: %s",
            len(fixed), len(tools), "; ".join(fixed[:5]),
        )

    body["tools"] = cleaned
    return body
