"""BL-LEARN-RECMODE V2 #68 (5/15) — selector 漂移 + DOM mutation summary 单测.

跑法: cd central/llm-gateway && PYTHONPATH=src python -m pytest tests/test_recmode_v2.py -q
"""
from __future__ import annotations

import pytest

from catfish_tool_bridge.recmode import selector_repair as sr


# ─── selector_repair ──────────────────────────────────────


def test_build_repair_messages_structure():
    msgs = sr.build_repair_messages(
        hint={"text": "应用", "near_text": "通讯录", "role": "tab"},
        screenshot_b64="fakebase64",
        context="录制时用户说'点应用 tab 进资质管理'",
    )
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"
    parts = msgs[1]["content"]
    assert any(p.get("type") == "text" and "应用" in p["text"] for p in parts)
    assert any(p.get("type") == "image_url" for p in parts)
    # 截图 URL 拼对
    img = next(p for p in parts if p.get("type") == "image_url")
    assert img["image_url"]["url"].startswith("data:image/png;base64,")
    # 录制时语境含在 prompt 里
    text_part = next(p["text"] for p in parts if p.get("type") == "text")
    assert "点应用 tab 进资质管理" in text_part


def test_build_repair_messages_no_context_uses_fallback():
    msgs = sr.build_repair_messages(
        hint={"text": "x"}, screenshot_b64="b64",
    )
    text = next(p["text"] for p in msgs[1]["content"] if p.get("type") == "text")
    assert "无录制时语音上下文" in text


def test_build_repair_messages_no_screenshot():
    msgs = sr.build_repair_messages(hint={"text": "x"}, screenshot_b64="")
    parts = msgs[1]["content"]
    assert all(p.get("type") != "image_url" for p in parts)


def test_parse_repair_response_with_fence():
    raw = """```json
{
  "found": true,
  "text": "应用市场",
  "near_text": "通讯录",
  "role": "tab",
  "confidence": 0.85,
  "reason": "EIS 改版后'应用'tab 改名'应用市场'"
}
```"""
    out = sr.parse_repair_response(raw)
    assert out["found"] is True
    assert out["text"] == "应用市场"
    assert out["confidence"] == 0.85
    assert "改版" in out["reason"]


def test_parse_repair_response_no_fence():
    raw = '{"found": false, "reason": "DOM 大改 没匹配"}'
    out = sr.parse_repair_response(raw)
    assert out["found"] is False
    assert "DOM 大改" in out["reason"]


def test_parse_repair_response_no_json_returns_found_false():
    out = sr.parse_repair_response("纯 prose 没 JSON")
    assert out["found"] is False
    assert "找不到 JSON" in out["reason"]


def test_parse_repair_response_bad_json_returns_found_false():
    out = sr.parse_repair_response("{ bad json")
    assert out["found"] is False


@pytest.mark.asyncio
async def test_repair_selector_e2e_with_mock_llm(monkeypatch):
    """端到端 mock call_llm → 真 build messages → 真 parse"""
    from catfish_tool_bridge.recmode import aggregator

    fake_response = '{"found": true, "text": "应用市场", "near_text": "通讯录", "role": "tab", "confidence": 0.9, "reason": "ok"}'

    async def fake_call_llm(messages, **kw):
        # 验 messages 含 vision multipart
        assert any(p.get("type") == "image_url"
                   for m in messages for p in (m["content"] if isinstance(m["content"], list) else []))
        # 验 temperature 是 0.2 (repair 要稳)
        assert kw.get("temperature") == 0.2
        return fake_response

    monkeypatch.setattr(aggregator, "call_llm", fake_call_llm)
    out = await sr.repair_selector(
        hint={"text": "应用", "role": "tab"},
        screenshot_b64="fake",
        context="找应用 tab",
        auth_token="fake",
    )
    assert out["found"] is True
    assert out["text"] == "应用市场"
    assert out["confidence"] == 0.9

# 注: dom_summary 测试仍在 central/llm-gateway/tests/test_recmode_v2.py — dom_summary 模块没搬, 留中央 (它只算 DOM diff 不读盘不调 LLM, 不属于录屏数据 leak 范畴).
