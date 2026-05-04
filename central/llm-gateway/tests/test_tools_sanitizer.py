"""tools_sanitizer 单测 —— 防客户端发畸形 tool 把整个请求挂掉。

历史 bug:
    Companion 早期版本: function: t.input_schema (扁平 schema 当 function 用),
    导致 function.name 缺失 → Gemini KeyError: 'name'。

防御契约:
    - 完全合规的 tool 原样保留
    - 缺 function.name → 丢, log warning
    - type != "function" → 丢
    - function 不是 dict → 丢
    - parameters 缺失或非 dict → 补默认 empty object schema (不丢, 这是可恢复的)
    - tool 不是 dict → 丢
    - body 没 tools / tools 不是 list → 不动, 直接返回
"""
from __future__ import annotations

from catfish_gateway.tools_sanitizer import sanitize_tools


# ---------- happy path ----------

def test_valid_tools_pass_through() -> None:
    body = {
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "search",
                    "description": "...",
                    "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "click",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ],
    }
    out = sanitize_tools(body)
    assert len(out["tools"]) == 2
    assert out["tools"][0]["function"]["name"] == "search"
    assert out["tools"][1]["function"]["name"] == "click"


# ---------- 边界: 没 tools / 空 / 非 list ----------

def test_no_tools_field() -> None:
    body = {"model": "x"}
    out = sanitize_tools(body)
    assert out == {"model": "x"}


def test_empty_tools_list() -> None:
    body = {"tools": []}
    out = sanitize_tools(body)
    assert out["tools"] == []


def test_tools_not_list_unchanged() -> None:
    body = {"tools": "not a list"}
    out = sanitize_tools(body)
    assert out["tools"] == "not a list"


# ---------- 丢畸形 ----------

def test_drops_tool_missing_name() -> None:
    """这就是历史 bug 的精确重现"""
    body = {
        "tools": [
            {"type": "function", "function": {"description": "x"}},  # 缺 name
            {"type": "function", "function": {"name": "ok", "parameters": {"type": "object"}}},
        ],
    }
    out = sanitize_tools(body)
    assert len(out["tools"]) == 1
    assert out["tools"][0]["function"]["name"] == "ok"


def test_drops_tool_with_empty_string_name() -> None:
    body = {
        "tools": [
            {"type": "function", "function": {"name": "", "parameters": {}}},
            {"type": "function", "function": {"name": "   ", "parameters": {}}},
            {"type": "function", "function": {"name": "real", "parameters": {"type": "object"}}},
        ],
    }
    out = sanitize_tools(body)
    assert len(out["tools"]) == 1
    assert out["tools"][0]["function"]["name"] == "real"


def test_drops_tool_with_non_string_name() -> None:
    body = {
        "tools": [
            {"type": "function", "function": {"name": 123, "parameters": {}}},
            {"type": "function", "function": {"name": None, "parameters": {}}},
        ],
    }
    out = sanitize_tools(body)
    assert out["tools"] == []


def test_drops_tool_with_wrong_type() -> None:
    body = {
        "tools": [
            {"type": "code_execution", "function": {"name": "exec"}},  # 非 function
            {"type": "function", "function": {"name": "ok", "parameters": {}}},
        ],
    }
    out = sanitize_tools(body)
    assert len(out["tools"]) == 1


def test_drops_tool_missing_function_field() -> None:
    body = {
        "tools": [
            {"type": "function"},  # 缺 function
            {"type": "function", "function": "not a dict"},
            {"type": "function", "function": {"name": "ok", "parameters": {}}},
        ],
    }
    out = sanitize_tools(body)
    assert len(out["tools"]) == 1


def test_drops_non_dict_tool() -> None:
    body = {
        "tools": [
            "string entry",
            None,
            42,
            {"type": "function", "function": {"name": "ok", "parameters": {}}},
        ],
    }
    out = sanitize_tools(body)
    assert len(out["tools"]) == 1


# ---------- 修补 parameters ----------

def test_missing_parameters_gets_default() -> None:
    body = {
        "tools": [
            {"type": "function", "function": {"name": "noargs"}},
        ],
    }
    out = sanitize_tools(body)
    assert len(out["tools"]) == 1
    params = out["tools"][0]["function"]["parameters"]
    assert params == {"type": "object", "properties": {}}


def test_non_dict_parameters_gets_replaced() -> None:
    body = {
        "tools": [
            {"type": "function", "function": {"name": "x", "parameters": "garbage"}},
            {"type": "function", "function": {"name": "y", "parameters": None}},
        ],
    }
    out = sanitize_tools(body)
    assert len(out["tools"]) == 2
    for t in out["tools"]:
        assert t["function"]["parameters"] == {"type": "object", "properties": {}}


# ---------- 历史 bug 完整重现 ----------

def test_historical_bug_input_schema_as_function() -> None:
    """Companion 早期版本: function 字段是 input_schema (扁平 jsonschema), 没 name。
    Gemini 会 KeyError, sanitizer 丢掉这种垃圾。"""
    body = {
        "tools": [
            # 这就是早期错误的形状
            {"type": "function", "function": {"type": "object", "properties": {}}},
            # 旁边一个合规的, 应该被留下
            {
                "type": "function",
                "function": {
                    "name": "good_tool",
                    "description": "fine",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ],
    }
    out = sanitize_tools(body)
    assert len(out["tools"]) == 1
    assert out["tools"][0]["function"]["name"] == "good_tool"


# ---------- mixed real-world 场景 ----------

def test_real_world_mixed() -> None:
    body = {
        "tools": [
            # 1 合规 (type 是 object, 有 properties)
            {"type": "function", "function": {"name": "search", "parameters": {"type": "object", "properties": {"q": {"type": "string"}}}}},
            # 2 缺 name (旧 bug)
            {"type": "function", "function": {"description": "?"}},
            # 3 type 错
            {"type": "code_execution", "function": {"name": "exec", "parameters": {}}},
            # 4 合规, 缺 parameters → 补默认
            {"type": "function", "function": {"name": "ping"}},
            # 5 完全错
            "garbage",
        ],
    }
    out = sanitize_tools(body)
    assert len(out["tools"]) == 2
    names = sorted(t["function"]["name"] for t in out["tools"])
    assert names == ["ping", "search"]
    # ping 拿到默认 parameters
    ping = next(t for t in out["tools"] if t["function"]["name"] == "ping")
    assert ping["function"]["parameters"] == {"type": "object", "properties": {}}


# ---------- DeepSeek 严格 schema 兼容 (BL-D11 5/4) ----------
# DeepSeek V4 严格校验 parameters.type 必须 "object", 不允许 None / 缺失.
# OpenAI / Qwen / Gemini 容忍这些, 但 sanitizer 兜底统一 schema 防分裂.


def test_deepseek_params_type_null_replaced_to_object() -> None:
    """historical: browser_back 工具 parameters={'type': null} 让 DeepSeek 400"""
    body = {
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "browser_back",
                    "parameters": {"type": None},
                },
            },
        ],
    }
    out = sanitize_tools(body)
    assert len(out["tools"]) == 1
    params = out["tools"][0]["function"]["parameters"]
    assert params["type"] == "object"
    assert params["properties"] == {}


def test_deepseek_params_no_type_added_object() -> None:
    """parameters 是 dict 但完全没 type 字段 → 加 type=object"""
    body = {
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "no_type_tool",
                    "parameters": {},
                },
            },
        ],
    }
    out = sanitize_tools(body)
    params = out["tools"][0]["function"]["parameters"]
    assert params["type"] == "object"
    assert params["properties"] == {}


def test_deepseek_params_empty_string_type_replaced() -> None:
    """parameters.type 是空字符串 → 改 object"""
    body = {
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "empty_type_tool",
                    "parameters": {"type": ""},
                },
            },
        ],
    }
    out = sanitize_tools(body)
    params = out["tools"][0]["function"]["parameters"]
    assert params["type"] == "object"


def test_deepseek_object_type_missing_properties_added() -> None:
    """parameters.type='object' 但缺 properties → 加 properties={}"""
    body = {
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "no_properties",
                    "parameters": {"type": "object"},
                },
            },
        ],
    }
    out = sanitize_tools(body)
    params = out["tools"][0]["function"]["parameters"]
    assert params["type"] == "object"
    assert params["properties"] == {}


def test_deepseek_existing_properties_preserved() -> None:
    """parameters.type='object' 且 properties 已有 → 不动"""
    body = {
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "with_properties",
                    "parameters": {
                        "type": "object",
                        "properties": {"q": {"type": "string"}},
                        "required": ["q"],
                    },
                },
            },
        ],
    }
    out = sanitize_tools(body)
    params = out["tools"][0]["function"]["parameters"]
    assert params["type"] == "object"
    assert params["properties"] == {"q": {"type": "string"}}
    assert params["required"] == ["q"]


def test_deepseek_non_object_type_kept_warn_not_dropped(caplog) -> None:
    """parameters.type 是非 'object' (e.g. 'string') → 不改不丢, 但 log warn.
    各 provider 兼容性差, 但是客户端写的, 网关不背这锅.
    """
    body = {
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "weird_type",
                    "parameters": {"type": "string"},
                },
            },
        ],
    }
    out = sanitize_tools(body)
    assert len(out["tools"]) == 1
    params = out["tools"][0]["function"]["parameters"]
    assert params["type"] == "string"  # 不动
    # caplog 里应该有 warning


def test_deepseek_realworld_browser_back_full() -> None:
    """完整重现 5/4 鸿波撞的 case: Companion 发的 browser_back 长这样"""
    body = {
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "browser_back",
                    "description": "浏览器返回上一页",
                    "parameters": {},  # 空 dict, 没 type
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "browser_goto",
                    "description": "导航到 URL",
                    "parameters": {
                        "type": "object",
                        "properties": {"url": {"type": "string"}},
                        "required": ["url"],
                    },
                },
            },
        ],
    }
    out = sanitize_tools(body)
    assert len(out["tools"]) == 2
    # browser_back 被补全
    back = next(t for t in out["tools"] if t["function"]["name"] == "browser_back")
    assert back["function"]["parameters"] == {"type": "object", "properties": {}}
    # browser_goto 完全不动
    goto = next(t for t in out["tools"] if t["function"]["name"] == "browser_goto")
    assert goto["function"]["parameters"]["properties"] == {"url": {"type": "string"}}
    assert goto["function"]["parameters"]["required"] == ["url"]
