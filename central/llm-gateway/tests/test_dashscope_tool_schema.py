"""百炼工具 schema 兼容: 嵌套的自由对象可选字段要删, 别的不动。见 dashscope_tool_schema.py。"""

from __future__ import annotations

import copy
from types import SimpleNamespace

from catfish_gateway.dashscope_tool_schema import apply

FREE = {"type": "object", "description": "可选", "properties": {}, "required": []}


def _model(
    api_base="https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    model="openai/qwen3.8-flash",
):
    return SimpleNamespace(
        name="m", upstream=SimpleNamespace(provider=None, model=model, api_base=api_base)
    )


def _delegate_task():
    return {
        "type": "function",
        "function": {
            "name": "delegate_task",
            "parameters": {
                "type": "object",
                "properties": {
                    "tasks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "goal": {"type": "string"},
                                "output_schema": copy.deepcopy(FREE),
                                "images": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["goal"],
                        },
                    },
                    "action": {"type": "string"},
                },
            },
        },
    }


def test_strips_freeform_object_inside_array_items():
    tools = [_delegate_task()]
    params = {"tools": tools}
    removed = apply(params, _model())
    assert removed == ["delegate_task.tasks[].output_schema"]
    item_props = params["tools"][0]["function"]["parameters"]["properties"]["tasks"]["items"][
        "properties"
    ]
    assert set(item_props) == {"goal", "images"}
    # 原件不动 (fallback 到别家模型还要用)
    assert (
        "output_schema"
        in tools[0]["function"]["parameters"]["properties"]["tasks"]["items"]["properties"]
    )


def test_keeps_top_level_and_required_freeform_objects():
    tool = {
        "type": "function",
        "function": {
            "name": "t",
            "parameters": {
                "type": "object",
                "properties": {
                    "options": copy.deepcopy(FREE),  # 第一层: 实测没事, 不删
                    "job": {
                        "type": "object",
                        "properties": {"meta": copy.deepcopy(FREE)},
                        "required": ["meta"],
                    },  # 必填: 不删
                },
            },
        },
    }
    params = {"tools": [tool]}
    assert apply(params, _model()) == []
    assert params["tools"][0] is tool


def test_strips_freeform_object_nested_in_object():
    tool = {
        "type": "function",
        "function": {
            "name": "t",
            "parameters": {
                "type": "object",
                "properties": {
                    "job": {
                        "type": "object",
                        "properties": {"q": {"type": "string"}, "options": {"type": "object"}},
                    }
                },
            },
        },
    }
    params = {"tools": [tool]}
    assert apply(params, _model()) == ["t.job.options"]


def test_other_providers_untouched():
    params = {"tools": [_delegate_task()]}
    assert (
        apply(params, _model(api_base="https://api.deepseek.com", model="deepseek/deepseek-chat"))
        == []
    )
    assert (
        apply({"tools": [_delegate_task()]}, _model(api_base=None, model="gemini/gemini-3.5-flash"))
        == []
    )
