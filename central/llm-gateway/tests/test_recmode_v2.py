"""BL-LEARN-RECMODE V2 #68 (5/15) — selector 漂移 + DOM mutation summary 单测.

跑法: cd central/llm-gateway && PYTHONPATH=src python -m pytest tests/test_recmode_v2.py -q
"""
from __future__ import annotations

import pytest

from catfish_gateway.recmode import selector_repair as sr, dom_summary as ds


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
    from catfish_gateway.recmode import aggregator

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


# ─── dom_summary ──────────────────────────────────────────


def test_summarize_empty():
    assert ds.summarize_mutation_snapshot({"added": {}, "removed": {}, "attrs": 0}) == "DOM 无变化"
    assert ds.summarize_mutation_snapshot({}) == "DOM 无变化"


def test_summarize_added_only():
    snap = {"added": {"div.app-icon": 12, "span.label": 12}, "removed": {}, "attrs": 0}
    s = ds.summarize_mutation_snapshot(snap)
    assert "+12 div.app-icon" in s
    assert "+12 span.label" in s


def test_summarize_added_removed_attrs():
    snap = {
        "added": {"div.app-icon": 12},
        "removed": {"div.login-form": 3, "input.user": 1},
        "attrs": 5,
    }
    s = ds.summarize_mutation_snapshot(snap)
    assert "+12 div.app-icon" in s
    assert "-3 div.login-form" in s
    assert "5 attr 变化" in s


def test_summarize_top_5_only():
    """超 5 个 selector 只取 top 5 (按 count 降序)"""
    added = {f"div.x-{i}": 100 - i for i in range(10)}  # x-0=100, x-1=99, ...
    snap = {"added": added, "removed": {}, "attrs": 0}
    s = ds.summarize_mutation_snapshot(snap)
    # x-0 至 x-4 应在, x-5+ 不在
    assert "+100 div.x-0" in s
    assert "+96 div.x-4" in s
    assert "div.x-5" not in s


def test_is_significant_mutation():
    assert ds.is_significant_mutation({"added": {"a": 3, "b": 3}, "removed": {}, "attrs": 0}, threshold=5) is True
    assert ds.is_significant_mutation({"added": {"a": 2}, "removed": {}, "attrs": 0}, threshold=5) is False
    # 仅 attr 变化不算 significant
    assert ds.is_significant_mutation({"added": {}, "removed": {}, "attrs": 100}, threshold=5) is False
    assert ds.is_significant_mutation({}, threshold=5) is False


def test_merge_snapshots():
    a = {"added": {"div.x": 5}, "removed": {"span.y": 2}, "attrs": 1}
    b = {"added": {"div.x": 3, "p.z": 4}, "removed": {}, "attrs": 5}
    merged = ds.merge_snapshots([a, b])
    assert merged["added"]["div.x"] == 8
    assert merged["added"]["p.z"] == 4
    assert merged["removed"]["span.y"] == 2
    assert merged["attrs"] == 6


def test_merge_empty():
    merged = ds.merge_snapshots([])
    assert merged == {"added": {}, "removed": {}, "attrs": 0}


def test_inject_observer_js_is_iife():
    """JS 是 IIFE, 装幂等 (再调返 'already_installed')"""
    js = ds.INJECT_OBSERVER_JS
    assert js.strip().startswith("(function()")
    assert "MutationObserver" in js
    assert "__catfishGetMutations" in js
    assert "already_installed" in js
