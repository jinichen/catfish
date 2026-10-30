"""DeepSeek 不认 json_schema, 降成 json_object。见 response_format_compat.py。"""

from __future__ import annotations

from types import SimpleNamespace

from catfish_gateway.response_format_compat import apply

SCHEMA_RF = {
    "type": "json_schema",
    "json_schema": {
        "name": "title",
        "schema": {"type": "object", "properties": {"title": {"type": "string"}}},
    },
}


def _model(model="deepseek/deepseek-chat", api_base="https://api.deepseek.com"):
    return SimpleNamespace(
        name="m", upstream=SimpleNamespace(provider=None, model=model, api_base=api_base)
    )


def test_deepseek_json_schema_downgraded_with_hint():
    msgs = [{"role": "user", "content": "起标题"}]
    params = {"messages": msgs, "response_format": dict(SCHEMA_RF)}
    assert apply(params, _model()) is True
    assert params["response_format"] == {"type": "json_object"}
    assert params["messages"][-1]["role"] == "system"
    assert '"title"' in params["messages"][-1]["content"]
    assert len(msgs) == 1, "原 messages 不动 (fallback 还要用)"


def test_other_cases_untouched():
    p = {"messages": [], "response_format": {"type": "json_object"}}
    assert apply(p, _model()) is False
    p = {"messages": [], "response_format": dict(SCHEMA_RF)}
    qwen = _model(model="openai/qwen3.8-flash", api_base="https://x.maas.aliyuncs.com/v1")
    assert apply(p, qwen) is False
    assert p["response_format"]["type"] == "json_schema"
    assert apply({"messages": []}, _model()) is False
