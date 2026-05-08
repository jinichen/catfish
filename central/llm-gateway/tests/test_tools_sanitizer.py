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


# ============================================================
# BL-FIX4 (5/8) — hermes builtin browser_* 跟 catfish_browser_* 撞时去重
# ============================================================
#
# 现网坑: hermes builtin 暴露 browser_back / browser_cdp / browser_click /
# browser_vision / ... 一族, 同时 tool-bridge 暴露 catfish_browser_goto /
# catfish_browser_snapshot / catfish_browser_click / catfish_browser_fill 一族.
# LLM 看见两套都能调, 走 hermes browser_vision (训练分布最熟), 但 catfish 没配
# vision provider → tool 返 error → LLM 懵, 整轮 BadRequest 400.
# 修法: tools_sanitizer 看到任何 catfish_browser_* 就丢所有 hermes 一族 (browser_*
# 开头但没 catfish_ 前缀的). LLM 只看一套.


def test_dedupe_hermes_browser_when_catfish_present() -> None:
    """有 catfish_browser_* → hermes builtin browser_* 一律丢"""
    body = {
        "tools": [
            # hermes builtin (5 个, 都该被丢)
            {"type": "function", "function": {"name": "browser_back",
             "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "browser_cdp",
             "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "browser_click",
             "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "browser_vision",
             "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "browser_screenshot",
             "parameters": {"type": "object", "properties": {}}}},
            # catfish 一族 (4 个, 该留)
            {"type": "function", "function": {"name": "catfish_browser_goto",
             "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "catfish_browser_snapshot",
             "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "catfish_browser_click",
             "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "catfish_browser_fill",
             "parameters": {"type": "object", "properties": {}}}},
            # 非 browser_ 前缀的, 不动
            {"type": "function", "function": {"name": "memory_save",
             "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "skill_run",
             "parameters": {"type": "object", "properties": {}}}},
        ],
    }
    out = sanitize_tools(body)
    names = [t["function"]["name"] for t in out["tools"]]
    # 5 个 hermes browser_* 全丢
    assert "browser_back" not in names
    assert "browser_cdp" not in names
    assert "browser_click" not in names
    assert "browser_vision" not in names
    assert "browser_screenshot" not in names
    # 4 个 catfish_browser_* 全留
    assert "catfish_browser_goto" in names
    assert "catfish_browser_snapshot" in names
    assert "catfish_browser_click" in names
    assert "catfish_browser_fill" in names
    # 非 browser_ 一族不影响
    assert "memory_save" in names
    assert "skill_run" in names
    # 总数: 11 - 5 = 6
    assert len(out["tools"]) == 6


def test_no_dedupe_when_no_catfish_browser() -> None:
    """没 catfish_browser_* — hermes browser_* 保留, 不动"""
    body = {
        "tools": [
            {"type": "function", "function": {"name": "browser_back",
             "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "browser_vision",
             "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "memory_save",
             "parameters": {"type": "object", "properties": {}}}},
        ],
    }
    out = sanitize_tools(body)
    names = [t["function"]["name"] for t in out["tools"]]
    # 全留
    assert "browser_back" in names
    assert "browser_vision" in names
    assert "memory_save" in names
    assert len(out["tools"]) == 3


def test_dedupe_only_drops_hermes_browser_prefix() -> None:
    """只丢 'browser_' 开头的, 不丢别的"""
    body = {
        "tools": [
            {"type": "function", "function": {"name": "catfish_browser_goto",
             "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "browse_url",  # 不带 _ 后缀, 不丢
             "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "my_browser_thing",  # 中间含 browser_, 不丢
             "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "browser_click",  # 撞, 丢
             "parameters": {"type": "object", "properties": {}}}},
        ],
    }
    out = sanitize_tools(body)
    names = [t["function"]["name"] for t in out["tools"]]
    assert "catfish_browser_goto" in names
    assert "browse_url" in names
    assert "my_browser_thing" in names
    assert "browser_click" not in names  # 唯一被丢的


def test_dedupe_logs_count(caplog: object) -> None:
    """日志里要 log 丢了多少 hermes browser_*"""
    import logging as _logging  # noqa: PLC0415
    body = {
        "tools": [
            {"type": "function", "function": {"name": "catfish_browser_goto",
             "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "browser_back",
             "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "browser_vision",
             "parameters": {"type": "object", "properties": {}}}},
        ],
    }
    with caplog.at_level(_logging.INFO, logger="catfish.gateway.tools_sanitizer"):  # type: ignore[attr-defined]
        sanitize_tools(body)
    matched = [r for r in caplog.records if "BL-FIX4" in r.getMessage()]  # type: ignore[attr-defined]
    assert len(matched) >= 1
    assert "deduped 2" in matched[0].getMessage()


def test_dedupe_has_catfish_helper() -> None:
    """_has_catfish_browser_tools 单独覆盖"""
    from catfish_gateway.tools_sanitizer import _has_catfish_browser_tools

    # 有 catfish_browser_*
    assert _has_catfish_browser_tools([
        {"type": "function", "function": {"name": "catfish_browser_goto"}},
    ])
    # 没
    assert not _has_catfish_browser_tools([
        {"type": "function", "function": {"name": "browser_back"}},
    ])
    # 空
    assert not _has_catfish_browser_tools([])
    # 脏数据不爆
    assert not _has_catfish_browser_tools([None, "string", {"function": "not-dict"}])  # type: ignore[list-item]
