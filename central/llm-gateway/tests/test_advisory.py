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


# ─── Phase 2: admin CRUD + RBAC (mock advisory_db, 不真连 PG) ───
#
# 6/7 BL-MANIFESTO-ADVISORY-PHASE2. 真 PG 集成 test 鸿波本地跑.


def test_phase2_require_sysadmin_rejects_non_sysadmin():
    """RBAC: admin / manager / employee 都不能 publish advisory, 必须 sysadmin.

    跟 manifesto 公理 4 一致 — advisory 是给全员看的, publish 权限是 release
    manager 级别, 不是普通 admin."""
    from catfish_gateway.advisory_router import _require_sysadmin
    from catfish_gateway.auth.base import User
    from fastapi import HTTPException

    for role in ["employee", "manager", "admin"]:
        user = User(sub="t@x.com", role=role)
        with pytest.raises(HTTPException) as exc_info:
            _require_sysadmin(user)
        assert exc_info.value.status_code == 403


def test_phase2_require_sysadmin_accepts_sysadmin():
    from catfish_gateway.advisory_router import _require_sysadmin
    from catfish_gateway.auth.base import User

    user = User(sub="t@x.com", role="sysadmin")
    # 不 raise = 通过
    _require_sysadmin(user)


def test_phase2_advisory_id_pattern_validation():
    """ID 必须 CATFISH-ADV-YYYY-NNN 格式. 防 admin 误传 free-form id."""
    from catfish_gateway.advisory_router import _ADVISORY_ID_PATTERN

    assert _ADVISORY_ID_PATTERN.match("CATFISH-ADV-2026-001")
    assert _ADVISORY_ID_PATTERN.match("CATFISH-ADV-2026-999")
    assert _ADVISORY_ID_PATTERN.match("CATFISH-ADV-2026-1000")  # 4 位也允许

    assert not _ADVISORY_ID_PATTERN.match("CATFISH-ADV-2026")  # 缺 NNN
    assert not _ADVISORY_ID_PATTERN.match("CATFISH-ADV-001")  # 缺 YYYY
    assert not _ADVISORY_ID_PATTERN.match("CVE-2026-001")  # 错 prefix
    assert not _ADVISORY_ID_PATTERN.match("CATFISH-ADV-2026-01")  # NNN < 3 位
    assert not _ADVISORY_ID_PATTERN.match("catfish-adv-2026-001")  # 小写


def test_phase2_use_pg_env_check(monkeypatch):
    """advisory_db.use_pg() 跟 facts_db 同 pattern."""
    from catfish_gateway import advisory_db

    monkeypatch.delenv("CATFISH_DB_URL", raising=False)
    assert not advisory_db.use_pg()

    monkeypatch.setenv("CATFISH_DB_URL", "postgresql://x")
    assert advisory_db.use_pg()


def test_phase2_pg_insert_without_db_url_raises(monkeypatch):
    """advisory publish 必须 PG 模式. 没配 CATFISH_DB_URL → 直接 raise RuntimeError.

    跟 manifesto 公理 4 一致 — 中央服务可以**优雅降级**到 yaml-only read mode,
    但 admin publish/revoke 必须有 DB (admin 看到错误自己 fix DB 配置)."""
    from catfish_gateway import advisory_db

    monkeypatch.delenv("CATFISH_DB_URL", raising=False)
    with pytest.raises(RuntimeError) as exc_info:
        advisory_db.pg_insert({"id": "x"}, published_by="admin@x.com")
    assert "CATFISH_DB_URL" in str(exc_info.value)


def test_phase2_pg_list_active_without_db_returns_empty(monkeypatch):
    """没 PG → list_active 返空 list (不 raise, feed.json 会 fallback yaml)."""
    from catfish_gateway import advisory_db

    monkeypatch.delenv("CATFISH_DB_URL", raising=False)
    assert advisory_db.pg_list_active() == []


def test_phase2_feed_fallback_yaml_when_pg_empty(yaml_with_2_advisories, monkeypatch):
    """PG 配了但表空 → feed 仍 fallback yaml (seed / demo).

    跟 spec 一致 — yaml 是 Phase 1 兼容路径, Phase 2 后 yaml 作 seed."""
    from catfish_gateway.advisory_router import _load_active_advisories
    from catfish_gateway import advisory_db

    # mock PG 返空 list
    monkeypatch.setattr(advisory_db, "use_pg", lambda: True)
    monkeypatch.setattr(advisory_db, "pg_list_active", lambda: [])

    advisories = _load_active_advisories()
    # 应 fallback yaml, yaml 含 1 active (TEST-001) + 1 expired (TEST-002), 过滤后 1
    assert len(advisories) == 1
    assert advisories[0]["id"] == "CATFISH-ADV-TEST-001"


def test_phase2_feed_prefers_pg_when_has_data(yaml_with_2_advisories, monkeypatch):
    """PG 有数据 → 用 PG, 不读 yaml (避免双数据源混乱)."""
    from catfish_gateway.advisory_router import _load_active_advisories
    from catfish_gateway import advisory_db

    pg_advisory = {
        "id": "CATFISH-ADV-FROM-PG-001",
        "severity": "high",
        "category": "skill_vulnerability",
        "title": "PG advisory",
        "published": "2026-06-07T10:00:00Z",
        "published_by": "admin@x.com",
    }
    monkeypatch.setattr(advisory_db, "use_pg", lambda: True)
    monkeypatch.setattr(advisory_db, "pg_list_active", lambda: [pg_advisory])

    advisories = _load_active_advisories()
    assert len(advisories) == 1
    assert advisories[0]["id"] == "CATFISH-ADV-FROM-PG-001"  # PG, 不是 yaml

