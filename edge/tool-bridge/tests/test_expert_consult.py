"""BL-FED2.3 (5/12) — 跨员工路由 catfish_expert_consult 测试.

覆盖:
- 路由策略 (preferred / 自动 / 全离线 / 排除自己 / 候选空)
- HTTP wrapper (by-expertise / a2a_ask 调用 + 错误处理)
- tool_expert_consult E2E (mock http_get / http_post)
- 错误传染 (denied / transport / config)
"""
from __future__ import annotations

from typing import Any

import pytest

from catfish_tool_bridge import expert_consult


@pytest.fixture(autouse=True)
def from_sub_alice(monkeypatch):
    monkeypatch.setenv("CATFISH_USER_SUB", "alice@ffcs.cn")
    monkeypatch.setenv("CATFISH_REGISTRY_URL", "http://registry-mock:8998")
    monkeypatch.setenv("CATFISH_GATEWAY_URL", "http://gateway-mock:8999")


# ─────────────────────────────────────────────────
# _select_target — 选 target 策略
# ─────────────────────────────────────────────────


def _make_match(sub: str, online: bool = True, dept: str = "") -> dict[str, Any]:
    return {
        "sub": sub,
        "department": dept,
        "expertise": ["资质"],
        "online": online,
        "last_seen": "2026-05-12T10:00:00+00:00",
    }


def test_select_empty_matches():
    chosen, reason = expert_consult._select_target([], "", "alice@ffcs.cn")
    assert chosen is None
    assert "黄页空" in reason


def test_select_only_self():
    """黄页里只有 alice 自己 — 不能路由."""
    matches = [_make_match("alice@ffcs.cn", online=True)]
    chosen, reason = expert_consult._select_target(matches, "", "alice@ffcs.cn")
    assert chosen is None
    assert "你自己" in reason


def test_select_auto_first_online():
    """自动路由 — 选第一个在线."""
    matches = [
        _make_match("bob@ffcs.cn", online=True),
        _make_match("charlie@ffcs.cn", online=True),
    ]
    chosen, reason = expert_consult._select_target(matches, "", "alice@ffcs.cn")
    assert chosen["sub"] == "bob@ffcs.cn"
    assert "自动路由" in reason


def test_select_auto_skips_offline():
    """前面 offline 后面 online — 选 online."""
    matches = [
        _make_match("bob@ffcs.cn", online=False),
        _make_match("charlie@ffcs.cn", online=True),
    ]
    chosen, reason = expert_consult._select_target(matches, "", "alice@ffcs.cn")
    assert chosen["sub"] == "charlie@ffcs.cn"


def test_select_auto_all_offline():
    matches = [
        _make_match("bob@ffcs.cn", online=False),
        _make_match("charlie@ffcs.cn", online=False),
    ]
    chosen, reason = expert_consult._select_target(matches, "", "alice@ffcs.cn")
    assert chosen is None
    assert "都不在线" in reason


def test_select_excludes_self_in_list():
    matches = [
        _make_match("alice@ffcs.cn", online=True),  # 自己
        _make_match("bob@ffcs.cn", online=True),
    ]
    chosen, _ = expert_consult._select_target(matches, "", "alice@ffcs.cn")
    assert chosen["sub"] == "bob@ffcs.cn"


def test_select_preferred_sub_match():
    matches = [
        _make_match("bob@ffcs.cn", online=True),
        _make_match("charlie@ffcs.cn", online=True),
    ]
    chosen, reason = expert_consult._select_target(matches, "charlie@ffcs.cn", "alice@ffcs.cn")
    assert chosen["sub"] == "charlie@ffcs.cn"
    assert "preferred_sub" in reason


def test_select_preferred_sub_not_in_matches():
    matches = [_make_match("bob@ffcs.cn", online=True)]
    chosen, reason = expert_consult._select_target(matches, "dave@ffcs.cn", "alice@ffcs.cn")
    assert chosen is None
    assert "dave@ffcs.cn" in reason
    assert "候选" in reason


def test_select_preferred_offline_still_routes():
    """指定 preferred 即使离线也强转 (员工可能稍后会答)."""
    matches = [_make_match("bob@ffcs.cn", online=False)]
    chosen, reason = expert_consult._select_target(matches, "bob@ffcs.cn", "alice@ffcs.cn")
    assert chosen["sub"] == "bob@ffcs.cn"
    assert "⚠️" in reason or "离线" in reason


# ─────────────────────────────────────────────────
# query_by_expertise — 调中央 endpoint
# ─────────────────────────────────────────────────


def test_query_by_expertise_basic():
    captured: dict[str, str] = {}

    def fake_get(url: str) -> dict[str, Any]:
        captured["url"] = url
        return {"tag": "资质", "matched_count": 1, "online_count": 1, "matches": []}

    resp = expert_consult.query_by_expertise("资质", http_get=fake_get)
    assert "tag=" in captured["url"]
    assert "%E8%B5%84%E8%B4%A8" in captured["url"]  # 资质 URL-encoded
    assert resp["matched_count"] == 1


def test_query_by_expertise_online_only_passed():
    captured: dict[str, str] = {}

    def fake_get(url: str) -> dict[str, Any]:
        captured["url"] = url
        return {"matched_count": 0, "matches": []}

    expert_consult.query_by_expertise("X", online_only=True, http_get=fake_get)
    assert "online_only=true" in captured["url"]


def test_query_by_expertise_empty_tag():
    with pytest.raises(ValueError):
        expert_consult.query_by_expertise("", http_get=lambda u: {})


def test_query_by_expertise_http_raises_propagates():
    def fake_get(url: str) -> dict[str, Any]:
        raise RuntimeError("HTTP 500")

    with pytest.raises(RuntimeError):
        expert_consult.query_by_expertise("X", http_get=fake_get)


# ─────────────────────────────────────────────────
# call_a2a_ask — 调本机 gateway
# ─────────────────────────────────────────────────


def test_call_a2a_ask_basic():
    captured: dict[str, Any] = {}

    def fake_post(url: str, body: dict[str, Any]) -> dict[str, Any]:
        captured["url"] = url
        captured["body"] = body
        return {"ok": True, "answer": "hi", "chunks_count": 3}

    resp = expert_consult.call_a2a_ask(
        "bob@ffcs.cn", "what?", purpose="x", context_hint="y", http_post=fake_post
    )
    assert resp["ok"] is True
    assert captured["body"]["from_sub"] == "alice@ffcs.cn"
    assert captured["body"]["to_sub"] == "bob@ffcs.cn"
    assert captured["body"]["question"] == "what?"
    assert captured["body"]["purpose"] == "x"
    assert captured["body"]["context_hint"] == "y"
    assert "/a2a/internal/ask" in captured["url"]


def test_call_a2a_ask_no_from_sub(monkeypatch):
    monkeypatch.delenv("CATFISH_USER_SUB", raising=False)
    resp = expert_consult.call_a2a_ask("bob@ffcs.cn", "q", http_post=lambda u, b: {})
    assert resp["ok"] is False
    assert resp["error_type"] == "config"


def test_call_a2a_ask_transport_error():
    def fake_post(url: str, body: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("connection refused")

    resp = expert_consult.call_a2a_ask("bob@ffcs.cn", "q", http_post=fake_post)
    assert resp["ok"] is False
    assert resp["error_type"] == "transport"
    assert "connection refused" in resp["error"]


# ─────────────────────────────────────────────────
# tool_expert_consult — 顶层入口 E2E
# ─────────────────────────────────────────────────


def _mock_registry(matches: list[dict[str, Any]]):
    """构造 mock http_get 返 by-expertise response."""
    def fake_get(url: str) -> dict[str, Any]:
        return {
            "tag": "资质",
            "matched_count": len(matches),
            "online_count": sum(1 for m in matches if m.get("online")),
            "matches": matches,
        }
    return fake_get


def _mock_a2a_ok(answer: str = "答案", chunks: int = 5):
    def fake_post(url: str, body: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "answer": answer, "chunks_count": chunks}
    return fake_post


def _mock_a2a_denied(reason: str = "ALLOW.md 拦"):
    def fake_post(url: str, body: dict[str, Any]) -> dict[str, Any]:
        return {"ok": False, "error_type": "denied", "error": reason}
    return fake_post


def test_tool_basic_e2e_success():
    matches = [_make_match("bob@ffcs.cn", online=True, dept="法务部")]
    resp = expert_consult.tool_expert_consult(
        {"expertise_tag": "资质", "question": "怎么审?"},
        http_get=_mock_registry(matches),
        http_post=_mock_a2a_ok(answer="走 OA 申请", chunks=3),
    )
    assert resp["ok"] is True
    assert resp["routed_to"] == "bob@ffcs.cn"
    assert resp["routed_department"] == "法务部"
    assert resp["answer"] == "走 OA 申请"
    assert resp["chunks_count"] == 3
    assert "📞" in resp["summary"]


def test_tool_required_args():
    """缺 expertise_tag 或 question → 友好错误."""
    r1 = expert_consult.tool_expert_consult({"question": "q"})
    assert r1["ok"] is False
    assert "expertise_tag" in r1["error"]
    r2 = expert_consult.tool_expert_consult({"expertise_tag": "X"})
    assert r2["ok"] is False
    assert "question" in r2["error"]


def test_tool_no_from_sub(monkeypatch):
    monkeypatch.delenv("CATFISH_USER_SUB", raising=False)
    r = expert_consult.tool_expert_consult({"expertise_tag": "X", "question": "q"})
    assert r["ok"] is False
    assert "CATFISH_USER_SUB" in r["error"]


def test_tool_no_match_returns_friendly():
    resp = expert_consult.tool_expert_consult(
        {"expertise_tag": "完全没人懂", "question": "q"},
        http_get=_mock_registry([]),
        http_post=_mock_a2a_ok(),
    )
    assert resp["ok"] is False
    assert resp["matched_count"] == 0
    assert "没人" in resp["error"]
    assert "catfish_extract_expertise" in resp["error"]


def test_tool_all_offline():
    matches = [
        _make_match("bob@ffcs.cn", online=False),
        _make_match("charlie@ffcs.cn", online=False),
    ]
    resp = expert_consult.tool_expert_consult(
        {"expertise_tag": "资质", "question": "q"},
        http_get=_mock_registry(matches),
        http_post=_mock_a2a_ok(),
    )
    assert resp["ok"] is False
    assert "都不在线" in resp["error"]
    assert resp["matched_count"] == 2
    assert resp["online_count"] == 0
    assert set(resp["candidates"]) == {"bob@ffcs.cn", "charlie@ffcs.cn"}


def test_tool_only_self_in_matches():
    """黄页只查到 alice 自己 — 不能路由."""
    matches = [_make_match("alice@ffcs.cn", online=True)]
    resp = expert_consult.tool_expert_consult(
        {"expertise_tag": "资质", "question": "q"},
        http_get=_mock_registry(matches),
        http_post=_mock_a2a_ok(),
    )
    assert resp["ok"] is False
    assert "你自己" in resp["error"]


def test_tool_preferred_sub_used():
    matches = [
        _make_match("bob@ffcs.cn", online=True),
        _make_match("charlie@ffcs.cn", online=True),
    ]
    captured: dict[str, Any] = {}

    def fake_post(url: str, body: dict[str, Any]) -> dict[str, Any]:
        captured["to_sub"] = body["to_sub"]
        return {"ok": True, "answer": "ok", "chunks_count": 1}

    resp = expert_consult.tool_expert_consult(
        {
            "expertise_tag": "资质",
            "question": "q",
            "preferred_sub": "charlie@ffcs.cn",
        },
        http_get=_mock_registry(matches),
        http_post=fake_post,
    )
    assert resp["ok"] is True
    assert captured["to_sub"] == "charlie@ffcs.cn"


def test_tool_preferred_sub_not_in_matches():
    matches = [_make_match("bob@ffcs.cn", online=True)]
    resp = expert_consult.tool_expert_consult(
        {"expertise_tag": "资质", "question": "q", "preferred_sub": "dave@ffcs.cn"},
        http_get=_mock_registry(matches),
        http_post=_mock_a2a_ok(),
    )
    assert resp["ok"] is False
    assert "dave@ffcs.cn" in resp["error"]


def test_tool_a2a_denied_friendly():
    matches = [_make_match("bob@ffcs.cn", online=True)]
    resp = expert_consult.tool_expert_consult(
        {"expertise_tag": "资质", "question": "q"},
        http_get=_mock_registry(matches),
        http_post=_mock_a2a_denied(reason="政策不允许"),
    )
    assert resp["ok"] is False
    assert "拒绝" in resp["error"]
    assert "政策不允许" in resp["error"]
    assert resp["routed_to"] == "bob@ffcs.cn"


def test_tool_a2a_transport_failure():
    matches = [_make_match("bob@ffcs.cn", online=True)]

    def fake_post(url: str, body: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("connection refused")

    resp = expert_consult.tool_expert_consult(
        {"expertise_tag": "资质", "question": "q"},
        http_get=_mock_registry(matches),
        http_post=fake_post,
    )
    assert resp["ok"] is False
    assert resp["routed_to"] == "bob@ffcs.cn"
    assert "失败" in resp["error"]


def test_tool_registry_unreachable():
    def fake_get(url: str) -> dict[str, Any]:
        raise RuntimeError("HTTP 502")

    resp = expert_consult.tool_expert_consult(
        {"expertise_tag": "资质", "question": "q"},
        http_get=fake_get,
        http_post=_mock_a2a_ok(),
    )
    assert resp["ok"] is False
    assert "黄页" in resp["error"]


def test_tool_default_purpose_includes_tag():
    """没传 purpose 时, 自动用 expert_consult:<tag> 作为 purpose (ALLOW.md 友好)."""
    matches = [_make_match("bob@ffcs.cn", online=True)]
    captured: dict[str, Any] = {}

    def fake_post(url: str, body: dict[str, Any]) -> dict[str, Any]:
        captured.update(body)
        return {"ok": True, "answer": "x", "chunks_count": 1}

    expert_consult.tool_expert_consult(
        {"expertise_tag": "资质审核", "question": "q"},
        http_get=_mock_registry(matches),
        http_post=fake_post,
    )
    assert captured["purpose"] == "expert_consult:资质审核"


def test_tool_explicit_purpose_passthrough():
    """显式传 purpose 时透传, 不被覆盖."""
    matches = [_make_match("bob@ffcs.cn", online=True)]
    captured: dict[str, Any] = {}

    def fake_post(url: str, body: dict[str, Any]) -> dict[str, Any]:
        captured.update(body)
        return {"ok": True, "answer": "x", "chunks_count": 1}

    expert_consult.tool_expert_consult(
        {"expertise_tag": "资质", "question": "q", "purpose": "compliance_check"},
        http_get=_mock_registry(matches),
        http_post=fake_post,
    )
    assert captured["purpose"] == "compliance_check"


def test_tool_context_hint_passthrough():
    matches = [_make_match("bob@ffcs.cn", online=True)]
    captured: dict[str, Any] = {}

    def fake_post(url: str, body: dict[str, Any]) -> dict[str, Any]:
        captured.update(body)
        return {"ok": True, "answer": "x", "chunks_count": 1}

    expert_consult.tool_expert_consult(
        {
            "expertise_tag": "资质",
            "question": "q",
            "context_hint": "客户周三要交",
        },
        http_get=_mock_registry(matches),
        http_post=fake_post,
    )
    assert captured["context_hint"] == "客户周三要交"


def test_tool_returns_routing_metadata_on_success():
    """成功返参必须含 expertise_tag / routed_to / routing_reason / matched_count."""
    matches = [_make_match("bob@ffcs.cn", online=True, dept="法务")]
    resp = expert_consult.tool_expert_consult(
        {"expertise_tag": "资质", "question": "q"},
        http_get=_mock_registry(matches),
        http_post=_mock_a2a_ok(),
    )
    assert resp["ok"] is True
    for k in ["expertise_tag", "routed_to", "routing_reason", "matched_count",
              "online_count", "routed_department", "answer", "chunks_count", "summary"]:
        assert k in resp, f"missing {k}"
