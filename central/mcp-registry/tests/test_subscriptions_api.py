"""Phase 2 订阅 endpoints e2e 单测 (BL-D3, 5/9).

mock secret-broker httpx, 验证整套 flow:
  POST /v1/mcp/subscribe → status pending_oauth (jira) / active (filesystem)
  POST /v1/mcp/oauth/start → 返 mock authorize_url + state
  POST /v1/mcp/oauth/callback → 写 secret-broker + mark active
  GET  /v1/mcp/subscribed → 返 active 列表
  DELETE /v1/mcp/subscribe/{id} → revoked + 删 secret
"""
from __future__ import annotations

import httpx
from fastapi.testclient import TestClient


def test_subscribe_filesystem_active_immediately(client: TestClient, auth_headers):
    """filesystem auth_type=path_allowlist → 直接 active, next_step=ready."""
    r = client.post(
        "/v1/mcp/subscribe",
        json={"connector_id": "filesystem"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["next_step"] == "ready"
    assert data["subscription"]["status"] == "active"
    assert data["subscription"]["connector_id"] == "filesystem"
    assert data["subscription"]["connector_name"] == "本地文件"
    assert data["oauth_start_url"] is None


def test_subscribe_jira_pending_oauth(client: TestClient, auth_headers):
    """jira auth_type=oauth2 → pending_oauth, next_step=oauth."""
    r = client.post(
        "/v1/mcp/subscribe",
        json={"connector_id": "jira"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["next_step"] == "oauth"
    assert data["subscription"]["status"] == "pending_oauth"
    assert data["oauth_start_url"] is not None


def test_subscribe_idempotent(client: TestClient, auth_headers):
    """重复订阅 → 返同 id, 不创建第二行."""
    r1 = client.post(
        "/v1/mcp/subscribe",
        json={"connector_id": "filesystem"},
        headers=auth_headers,
    )
    r2 = client.post(
        "/v1/mcp/subscribe",
        json={"connector_id": "filesystem"},
        headers=auth_headers,
    )
    assert r1.json()["subscription"]["id"] == r2.json()["subscription"]["id"]


def test_subscribe_404_unknown_connector(client: TestClient, auth_headers):
    r = client.post(
        "/v1/mcp/subscribe",
        json={"connector_id": "nonexistent"},
        headers=auth_headers,
    )
    assert r.status_code == 404


def test_subscribe_403_wrong_dept(client: TestClient):
    """finance 部门订阅 jira (engineering only) → 403."""
    r = client.post(
        "/v1/mcp/subscribe",
        json={"connector_id": "jira"},
        headers={
            "X-Catfish-User-Sub": "carol@x",
            "X-Catfish-User-Dept": "finance",
        },
    )
    assert r.status_code == 403


def test_subscribe_401_no_user(client: TestClient):
    """没传 X-Catfish-User-Sub → 401."""
    r = client.post(
        "/v1/mcp/subscribe",
        json={"connector_id": "filesystem"},
    )
    assert r.status_code == 401


def test_oauth_start_then_callback_flow(client: TestClient, auth_headers):
    """完整 OAuth flow: subscribe → oauth/start → oauth/callback → active.

    P3.4.1 (6/13 hb): 中央不再 POST secret-broker. callback response 直接返
    access_token 给 Companion 落本机, oauth_token_ref 改成本机文件 ref.
    """
    # subscribe (pending_oauth)
    r = client.post(
        "/v1/mcp/subscribe",
        json={"connector_id": "jira"},
        headers=auth_headers,
    )
    sub = r.json()["subscription"]
    assert sub["status"] == "pending_oauth"

    # oauth start
    r = client.post(
        "/v1/mcp/oauth/start",
        json={"subscription_id": sub["id"]},
        headers=auth_headers,
    )
    assert r.status_code == 200
    data = r.json()
    assert "mock-callback" in data["authorize_url"]  # mock 模式
    state = data["state"]

    # oauth callback (mock 模式: 直传 mock_token)
    r = client.post(
        "/v1/mcp/oauth/callback",
        json={"state": state, "code": "fake-code", "mock_token": "real-jira-token-xyz"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    after = body["subscription"]
    assert after["status"] == "active"
    # oauth_token_ref 改成本机文件 ref (P3.4.1)
    assert after["oauth_token_ref"] == "jira-alice-at-catfish.dev.token"

    # P3.4.1: token 应该直接出现在 callback response, 不再走中央
    assert body["access_token"] == "real-jira-token-xyz"
    assert body["token_ref_local"] == "jira-alice-at-catfish.dev.token"


def test_oauth_callback_replay_rejected(client: TestClient, auth_headers):
    """同 state 二次回调 → 400 (mark_active 后 state 清掉, find_by_state 找不到)."""
    r = client.post(
        "/v1/mcp/subscribe", json={"connector_id": "jira"}, headers=auth_headers,
    )
    sub_id = r.json()["subscription"]["id"]
    r = client.post(
        "/v1/mcp/oauth/start",
        json={"subscription_id": sub_id},
        headers=auth_headers,
    )
    state = r.json()["state"]

    # 第一次成功
    r = client.post(
        "/v1/mcp/oauth/callback",
        json={"state": state, "code": "x", "mock_token": "t1"},
        headers=auth_headers,
    )
    assert r.status_code == 200

    # 第二次同 state → 400
    r = client.post(
        "/v1/mcp/oauth/callback",
        json={"state": state, "code": "x", "mock_token": "t2"},
        headers=auth_headers,
    )
    assert r.status_code == 400


def test_oauth_callback_wrong_user_403(client: TestClient, auth_headers):
    """alice 起的 state, bob 来 callback → 403."""
    r = client.post(
        "/v1/mcp/subscribe", json={"connector_id": "jira"}, headers=auth_headers,
    )
    r = client.post(
        "/v1/mcp/oauth/start",
        json={"subscription_id": r.json()["subscription"]["id"]},
        headers=auth_headers,
    )
    state = r.json()["state"]

    r = client.post(
        "/v1/mcp/oauth/callback",
        json={"state": state, "code": "x"},
        headers={
            "X-Catfish-User-Sub": "bob@x",
            "X-Catfish-User-Dept": "engineering",
        },
    )
    assert r.status_code == 403


def test_subscribed_list(client: TestClient, auth_headers):
    """订阅 2 个 → GET /v1/mcp/subscribed 返 2."""
    client.post(
        "/v1/mcp/subscribe", json={"connector_id": "filesystem"}, headers=auth_headers,
    )
    client.post(
        "/v1/mcp/subscribe", json={"connector_id": "time"}, headers=auth_headers,
    )

    r = client.get("/v1/mcp/subscribed", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 2
    ids = sorted(s["connector_id"] for s in data["subscriptions"])
    assert ids == ["filesystem", "time"]


def test_subscribed_filter_active(client: TestClient, auth_headers):
    """?status_filter=active → 只返 active."""
    r1 = client.post(
        "/v1/mcp/subscribe", json={"connector_id": "filesystem"}, headers=auth_headers,
    )
    r2 = client.post(
        "/v1/mcp/subscribe", json={"connector_id": "jira"}, headers=auth_headers,
    )
    # filesystem 直接 active, jira pending_oauth
    r = client.get("/v1/mcp/subscribed?status_filter=active", headers=auth_headers)
    assert r.json()["total"] == 1
    assert r.json()["subscriptions"][0]["connector_id"] == "filesystem"


def test_unsubscribe(client: TestClient, auth_headers):
    """订阅 + 完整 OAuth → unsubscribe → status revoked.

    P3.4.1 (6/13 hb): 中央 unsubscribe 不再调 secret-broker. Companion 自己
    根据 oauth_token_ref 删本机 ~/.catfish/mcp/oauth-tokens/<ref>.
    """
    # 跑完整 flow
    r = client.post(
        "/v1/mcp/subscribe", json={"connector_id": "jira"}, headers=auth_headers,
    )
    sub_id = r.json()["subscription"]["id"]
    r = client.post(
        "/v1/mcp/oauth/start",
        json={"subscription_id": sub_id},
        headers=auth_headers,
    )
    state = r.json()["state"]
    client.post(
        "/v1/mcp/oauth/callback",
        json={"state": state, "code": "x", "mock_token": "t"},
        headers=auth_headers,
    )

    # unsubscribe
    r = client.delete(f"/v1/mcp/subscribe/{sub_id}", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["status"] == "revoked"


def test_unsubscribe_other_user_403(client: TestClient, auth_headers):
    """alice 的 sub, bob 来 unsubscribe → 403."""
    r = client.post(
        "/v1/mcp/subscribe", json={"connector_id": "filesystem"}, headers=auth_headers,
    )
    sub_id = r.json()["subscription"]["id"]

    r = client.delete(
        f"/v1/mcp/subscribe/{sub_id}",
        headers={
            "X-Catfish-User-Sub": "bob@x",
            "X-Catfish-User-Dept": "engineering",
        },
    )
    assert r.status_code == 403


def test_unsubscribe_404_unknown(client: TestClient, auth_headers):
    r = client.delete("/v1/mcp/subscribe/sub_nonexistent", headers=auth_headers)
    assert r.status_code == 404


def test_registry_shows_subscribed_status(client: TestClient, auth_headers):
    """订阅 filesystem 后 GET /v1/mcp/registry filesystem.subscribed=True."""
    client.post(
        "/v1/mcp/subscribe", json={"connector_id": "filesystem"}, headers=auth_headers,
    )
    r = client.get("/v1/mcp/registry", headers=auth_headers)
    fs = next(c for c in r.json()["connectors"] if c["id"] == "filesystem")
    assert fs["subscribed"] is True
    assert fs["subscriber_count"] == 1
