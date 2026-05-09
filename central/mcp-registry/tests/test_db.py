"""SubscriptionDB 单测 (BL-D3 Phase 2)."""
from __future__ import annotations

from catfish_mcp_registry.db import SubscriptionDB


def test_create_active_no_oauth(db: SubscriptionDB):
    """auth_required=False → status='active', oauth_state=None."""
    sub = db.create_subscription("alice@x", "filesystem", auth_required=False)
    assert sub["status"] == "active"
    assert sub["oauth_state"] is None
    assert sub["id"].startswith("sub_")


def test_create_pending_oauth(db: SubscriptionDB):
    """auth_required=True → status='pending_oauth', 有 oauth_state."""
    sub = db.create_subscription("alice@x", "jira", auth_required=True)
    assert sub["status"] == "pending_oauth"
    assert sub["oauth_state"] is not None
    assert len(sub["oauth_state"]) > 10


def test_create_idempotent(db: SubscriptionDB):
    """同 (user, connector) 重复订阅 → 返原值, 不创建新行."""
    s1 = db.create_subscription("alice@x", "jira", auth_required=True)
    s2 = db.create_subscription("alice@x", "jira", auth_required=True)
    assert s1["id"] == s2["id"]


def test_mark_active(db: SubscriptionDB):
    sub = db.create_subscription("alice@x", "jira", auth_required=True)
    after = db.mark_active(sub["id"], oauth_token_ref="jira-alice-token-ref")
    assert after["status"] == "active"
    assert after["oauth_token_ref"] == "jira-alice-token-ref"
    assert after["oauth_state"] is None  # 清掉防 replay


def test_revoke(db: SubscriptionDB):
    sub = db.create_subscription("alice@x", "filesystem", auth_required=False)
    assert db.revoke(sub["id"]) is True
    after = db.get_subscription_by_id(sub["id"])
    assert after["status"] == "revoked"


def test_revoke_unknown(db: SubscriptionDB):
    assert db.revoke("sub_nonexistent") is False


def test_list_user_subscriptions(db: SubscriptionDB):
    db.create_subscription("alice@x", "jira", auth_required=True)
    db.create_subscription("alice@x", "filesystem", auth_required=False)
    db.create_subscription("bob@x", "gitlab", auth_required=True)

    alice = db.list_user_subscriptions("alice@x")
    assert len(alice) == 2
    ids = sorted(s["connector_id"] for s in alice)
    assert ids == ["filesystem", "jira"]


def test_list_user_subscriptions_filter_status(db: SubscriptionDB):
    s1 = db.create_subscription("alice@x", "jira", auth_required=True)
    db.create_subscription("alice@x", "filesystem", auth_required=False)
    db.revoke(s1["id"])

    active = db.list_user_subscriptions("alice@x", status="active")
    assert len(active) == 1
    assert active[0]["connector_id"] == "filesystem"


def test_count_subscribers(db: SubscriptionDB):
    db.create_subscription("alice@x", "jira", auth_required=True)
    s2 = db.create_subscription("bob@x", "jira", auth_required=True)
    # alice pending_oauth 不算 active 订阅, bob 也是 pending → 0
    assert db.count_subscribers("jira") == 0
    db.mark_active(s2["id"], "jira-bob-ref")
    assert db.count_subscribers("jira") == 1


def test_find_by_oauth_state(db: SubscriptionDB):
    sub = db.create_subscription("alice@x", "jira", auth_required=True)
    found = db.find_by_oauth_state(sub["oauth_state"])
    assert found is not None
    assert found["id"] == sub["id"]
    # mark_active 后 state 清掉
    db.mark_active(sub["id"], "x")
    assert db.find_by_oauth_state(sub["oauth_state"]) is None


def test_audit(db: SubscriptionDB):
    db.write_audit("alice@x", "subscribe", connector_id="jira", meta={"k": "v"})
    db.write_audit("alice@x", "oauth_complete", connector_id="jira")
    rows = db.list_audit("alice@x")
    assert len(rows) == 2
    # 倒序 (最新在前)
    assert rows[0]["action"] == "oauth_complete"
    # meta 解析回 dict
    assert rows[1]["meta"] == {"k": "v"}
