"""Advisory feed contract test — Phase 1 (6/7 BL-MANIFESTO-ADVISORY).

Spec: docs/ADVISORY-FEED-SPEC.md

Phase 1 scope:
  - yaml-based static feed
  - public GET /advisory/feed.json (SSO 鉴权)
  - ETag-based caching (304 fast path)

不测的 (留 Phase 2):
  - Admin POST /advisory (DB 持久化)
  - 员工 ack POST /v1/advisory/ack
  - 聚合统计 dashboard
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml


@pytest.fixture
def yaml_with_2_advisories(monkeypatch):
    """临时 yaml, 1 active + 1 expired."""
    tmp = tempfile.mkdtemp(prefix="test_advisory_")
    yaml_path = Path(tmp) / "advisories.yaml"
    data = {
        "advisories": [
            {
                "id": "CATFISH-ADV-TEST-001",
                "severity": "high",
                "category": "skill_vulnerability",
                "title": "Test active advisory",
                "published": "2026-06-07T10:00:00Z",
                "target": {"skill": "test-skill"},
            },
            {
                "id": "CATFISH-ADV-TEST-002",
                "severity": "low",
                "category": "policy_recommendation",
                "title": "Test expired advisory",
                "published": "2025-01-01T10:00:00Z",
                "expires": "2025-12-31T23:59:59Z",  # 已过期
            },
        ]
    }
    yaml_path.write_text(yaml.dump(data), encoding="utf-8")
    monkeypatch.setenv("CATFISH_ADVISORIES_YAML", str(yaml_path))
    yield yaml_path


def test_load_advisories_filters_expired(yaml_with_2_advisories):
    """active 2 个写到 yaml, 但 1 个已过期 → loader 返 2 个 (loader 不 filter),
    /feed.json 才 filter expired."""
    from catfish_gateway.advisory_router import _is_active, _load_advisories
    from datetime import datetime, timezone

    advisories = _load_advisories()
    assert len(advisories) == 2

    now = datetime.now(timezone.utc)
    active = [a for a in advisories if _is_active(a, now)]
    assert len(active) == 1
    assert active[0]["id"] == "CATFISH-ADV-TEST-001"


def test_advisory_yaml_missing_returns_empty(monkeypatch):
    """yaml 不存在 → 空 list, 不 panic (manifest 公理 4 — 中央 publish 优雅降级)."""
    monkeypatch.setenv("CATFISH_ADVISORIES_YAML", "/nonexistent/path.yaml")
    from catfish_gateway.advisory_router import _load_advisories

    assert _load_advisories() == []


def test_advisory_yaml_malformed_returns_empty(monkeypatch):
    """yaml 解析失败 → 空 list (不 fail open 阻塞 chat)."""
    tmp = tempfile.mkdtemp(prefix="test_malformed_")
    yaml_path = Path(tmp) / "advisories.yaml"
    yaml_path.write_text("not: valid: yaml: [", encoding="utf-8")
    monkeypatch.setenv("CATFISH_ADVISORIES_YAML", str(yaml_path))

    from catfish_gateway.advisory_router import _load_advisories
    assert _load_advisories() == []


def test_compute_etag_stable_for_same_set():
    """同一 advisory 集合返同一 etag, 顺序无关 (304 fast path 基础)."""
    from catfish_gateway.advisory_router import _compute_etag

    set_a = [
        {"id": "CATFISH-ADV-001"},
        {"id": "CATFISH-ADV-002"},
    ]
    set_b = [
        {"id": "CATFISH-ADV-002"},  # 顺序反
        {"id": "CATFISH-ADV-001"},
    ]
    assert _compute_etag(set_a) == _compute_etag(set_b)


def test_compute_etag_different_for_different_set():
    """advisory 集合不同 → etag 不同 (强制客户端 refresh)."""
    from catfish_gateway.advisory_router import _compute_etag

    set_a = [{"id": "CATFISH-ADV-001"}]
    set_b = [{"id": "CATFISH-ADV-001"}, {"id": "CATFISH-ADV-002"}]
    assert _compute_etag(set_a) != _compute_etag(set_b)


def test_is_active_no_expires():
    """advisory 没设 expires → 永远 active."""
    from catfish_gateway.advisory_router import _is_active
    from datetime import datetime, timezone

    advisory = {"id": "x"}
    now = datetime.now(timezone.utc)
    assert _is_active(advisory, now)


def test_is_active_future_expires():
    """expires 在未来 → active."""
    from catfish_gateway.advisory_router import _is_active
    from datetime import datetime, timezone

    advisory = {"id": "x", "expires": "2099-12-31T23:59:59Z"}
    now = datetime.now(timezone.utc)
    assert _is_active(advisory, now)


def test_is_active_past_expires():
    """expires 在过去 → 不 active."""
    from catfish_gateway.advisory_router import _is_active
    from datetime import datetime, timezone

    advisory = {"id": "x", "expires": "2020-01-01T00:00:00Z"}
    now = datetime.now(timezone.utc)
    assert not _is_active(advisory, now)
