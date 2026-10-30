"""百炼 (DashScope) 工具 schema 兼容: 去掉嵌套的"自由对象"可选字段。

# 现象 (10/30 鸿波实盘, 「查找认证机构 DSMM 公示」会话)

qwen3.8-flash 调 hermes 的 delegate_task 时, 参数只吐出 `{"tasks": ` 10 个字符
流就结束了, finish_reason=stop。hermes 看到半截 JSON, 按「撞输出上限被截断」
拒绝执行, Companion 红框 "cut off ... hit its output length limit"。员工点
「继续」也一样 —— 当天下午 14:21–17:16 连撞 25+ 次, 实际 completion 只有几十 token。

# 根因 (对照实验, 同一请求直连网关 → 百炼)

delegate_task 的 tasks[].output_schema 是 `{"type": "object", "properties": {}}`
—— 没定义任何字段的"自由对象", 而且嵌在数组元素里。

    原样                         4/4 截在 `{"tasks": `
    保留 type:object, 删空 properties  4/4 照样截
    删掉 output_schema           4/4 正常 (参数 2000–3200 字符)
    自由对象放第一层参数          3/3 正常
    自由对象嵌在对象里一层         2/3 截

所以分界线是「自由对象 + 不在第一层」。百炼服务端解析工具参数时碰到这种结构
就收工, 不是长度问题, 也不是思考开关的问题。

# 修法

只对百炼的模型, 出口前把**非必填**、**不在第一层**的自由对象字段从 tools 里删掉。
必填的不删 (删了模型就调不对), 第一层的不删 (实测没事, 删了白丢能力)。
对 delegate_task 来说丢的是 output_schema —— 可选的结构化输出校验, 子代理照常跑。

tools 先拷一份再改: 同一个 body 在 fallback 时还要发给别家模型, 不能动原件。
"""

from __future__ import annotations

import copy
import logging
from typing import Any

from .thinking_guard import _provider_of

logger = logging.getLogger("catfish.gateway.dashscope_tool_schema")


def _is_freeform_object(schema: Any) -> bool:
    """`{"type": "object"}` 且没定义任何字段 (properties 缺失或为空)。"""
    if not isinstance(schema, dict) or schema.get("type") != "object":
        return False
    props = schema.get("properties")
    return not (isinstance(props, dict) and props)


def _strip(schema: Any, depth: int, path: str, removed: list[str]) -> None:
    """depth = 当前 schema 离参数根有几层对象/数组。根是 0, 根的字段在 1 层。"""
    if not isinstance(schema, dict):
        return
    props = schema.get("properties")
    if isinstance(props, dict):
        required = set(schema.get("required") or [])
        for key in list(props):
            sub = props[key]
            if depth >= 1 and key not in required and _is_freeform_object(sub):
                del props[key]
                removed.append(f"{path}.{key}")
                continue
            _strip(sub, depth + 1, f"{path}.{key}", removed)
    items = schema.get("items")
    if isinstance(items, dict):
        _strip(items, depth + 1, f"{path}[]", removed)


def apply(params: dict, model: Any) -> list[str]:
    """百炼模型: 原地把 params["tools"] 换成剥过的拷贝。返回删掉的字段路径。"""
    tools = params.get("tools")
    if not isinstance(tools, list) or not tools or _provider_of(model) != "dashscope":
        return []
    new_tools = copy.deepcopy(tools)
    removed: list[str] = []
    for tool in new_tools:
        fn = tool.get("function") if isinstance(tool, dict) else None
        if isinstance(fn, dict):
            _strip(fn.get("parameters"), 0, fn.get("name", "?"), removed)
    if removed:
        params["tools"] = new_tools
    return removed


__all__ = ["apply"]
