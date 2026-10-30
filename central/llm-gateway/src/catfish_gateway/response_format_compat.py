"""DeepSeek 不认 response_format=json_schema, 降成 json_object + 把 schema 写进提示。

# 现象 (10/30 鸿波在配额日志里看到)

「对话 · 起标题」(hermes:aux:title_generation) 走 deepseek 时每次都 400:

    DeepseekException - This response_format type is unavailable now

日志里最早 10/29 10:05 就有, 用 deepseek 的会话一个标题都没起出来过。

# 实测 (直连网关 → DeepSeek, 10/30)

    json_schema   400 (上面那句)
    json_object   200 {"title": "..."}  —— 提示里没写 "json" 也行
    text          200

# 修法

只对 DeepSeek: json_schema → json_object, 并在 messages 末尾补一条 system,
把原 schema 原样给模型, 让它照字段输出 (json_object 只保证是 JSON, 不管字段)。
messages 换成新列表, 不动原件 —— fallback 到别家时还要发原请求。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .thinking_guard import _provider_of

logger = logging.getLogger("catfish.gateway.response_format_compat")


def apply(params: dict, model: Any) -> bool:
    """DeepSeek 上把 json_schema 降级。改了返回 True。"""
    rf = params.get("response_format")
    if not isinstance(rf, dict) or rf.get("type") != "json_schema":
        return False
    if _provider_of(model) != "deepseek":
        return False
    js = rf.get("json_schema") or {}
    schema = js.get("schema") if isinstance(js, dict) else None
    params["response_format"] = {"type": "json_object"}
    if schema:
        hint = "只输出一个 JSON 对象, 符合这个 JSON Schema:\n" + json.dumps(
            schema, ensure_ascii=False,
        )
        params["messages"] = [*(params.get("messages") or []), {"role": "system", "content": hint}]
    logger.info(
        "response_format: %s 不支持 json_schema, 降成 json_object (schema=%s)",
        getattr(model, "name", "?"), (js.get("name") if isinstance(js, dict) else None) or "-",
    )
    return True


__all__ = ["apply"]
