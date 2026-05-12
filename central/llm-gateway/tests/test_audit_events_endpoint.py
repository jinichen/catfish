"""BL-ADMIN-AUDIT (5/12) 测试 — /api/audit/events 分页查询.

覆盖:
- count_events / read_events 新参数 (dept_filter / offset)
- API endpoint RBAC (employee → 403, manager → 403, admin → 200, sysadmin → 200)
- 分页 limit + offset
- 各维度筛选 (since_ms / dept / user / model / status)
- 默认 since_ms = 24h 前
- limit 边界 (≤200), offset ≥0
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from catfish_gateway import metrics
from catfish_gateway.app import app


@pytest.fixture(autouse=True)
def _isolate_audit_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("CATFISH_AUDIT_PATH", raising=False)
    monkeypatch.delenv("CATFISH_DB_URL", raising=False)
    monkeypatch.setattr(metrics, "_audit_path", tmp_path / "audit.jsonl")
    yield


def _seed(events: list[dict]):
    """直接写一批 audit 行到 jsonl (不走 log_request_metadata)."""
    p = metrics.audit_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


# ── count_events ──────────────────────────────────────────


def test_count_events_no_filter():
    _seed([
        {"ts": 100, "user": "a@x", "model": "m1", "status": "ok", "department": "dev"},
        {"ts": 200, "user": "b@x", "model": "m1", "status": "ok", "department": "dev"},
        {"ts": 300, "user": "c@x", "model": "m2", "status": "error", "department": "ops"},
    ])
    assert metrics.count_events() == 3


def test_count_events_dept_filter():
    _seed([
        {"ts": 100, "user": "a@x", "department": "dev"},
        {"ts": 200, "user": "b@x", "department": "dev"},
        {"ts": 300, "user": "c@x", "department": "ops"},
    ])
    assert metrics.count_events(dept_filter="dev") == 2
    assert metrics.count_events(dept_filter="ops") == 1
    assert metrics.count_events(dept_filter="hr") == 0


def test_count_events_combined_filters():
    _seed([
        {"ts": 100, "user": "a@x", "model": "m1", "status": "ok", "department": "dev"},
        {"ts": 200, "user": "a@x", "model": "m1", "status": "error", "department": "dev"},
        {"ts": 300, "user": "b@x", "model": "m2", "status": "ok", "department": "dev"},
    ])
    assert metrics.count_events(user_filter="a@x", status_filter="ok") == 1
    assert metrics.count_events(dept_filter="dev", status_filter="ok") == 2


def test_count_events_since_unix():
    now = int(time.time())
    _seed([
        {"ts": now - 7200, "user": "old@x"},   # 2 小时前
        {"ts": now - 60, "user": "recent@x"},  # 1 分钟前
    ])
    assert metrics.count_events(since_unix=now - 3600) == 1


def test_count_events_no_file_returns_zero():
    assert metrics.count_events() == 0


# ── read_events offset ────────────────────────────────────


def test_read_events_offset_skips_first_n():
    _seed([
        {"ts": 100, "user": "a@x"},
        {"ts": 200, "user": "b@x"},
        {"ts": 300, "user": "c@x"},
    ])
    # 倒序: c, b, a
    p1 = metrics.read_events(limit=2, offset=0)
    p2 = metrics.read_events(limit=2, offset=2)
    assert [e["user"] for e in p1] == ["c@x", "b@x"]
    assert [e["user"] for e in p2] == ["a@x"]


def test_read_events_offset_with_dept_filter():
    _seed([
        {"ts": 100, "user": "a@x", "department": "dev"},
        {"ts": 200, "user": "b@x", "department": "ops"},
        {"ts": 300, "user": "c@x", "department": "dev"},
        {"ts": 400, "user": "d@x", "department": "dev"},
    ])
    # filter dev, 倒序: d, c, a (skip b 因 ops). offset=1, limit=1 → c
    out = metrics.read_events(dept_filter="dev", limit=1, offset=1)
    assert len(out) == 1
    assert out[0]["user"] == "c@x"


# ── API endpoint RBAC ─────────────────────────────────────


def _client_as(role: str, monkeypatch) -> TestClient:
    """mock get_current_user 返指定 role 的 User."""
    from catfish_gateway.auth.base import User
    fake = User(
        sub=f"{role}@ffcs.cn",
        department="engineering",
        tier="admin" if role in ("admin", "sysadmin") else "employee",
        role=role,
        managed_departments=[],
        auth_method="test",
    )

    async def fake_get_current_user():
        return fake

    # FastAPI dependency overrides 拦实际 endpoint 用的 dep (从 app 模块 import 的)
    from catfish_gateway.app import get_current_user as real_dep
    app.dependency_overrides[real_dep] = fake_get_current_user
    return TestClient(app)


def _cleanup_overrides():
    app.dependency_overrides.clear()


def test_endpoint_employee_403(monkeypatch):
    c = _client_as("employee", monkeypatch)
    r = c.get("/api/audit/events")
    _cleanup_overrides()
    assert r.status_code == 403


def test_endpoint_manager_403(monkeypatch):
    """manager 不能看全员历史 (走部门 endpoint /api/audit/department/{dept})."""
    c = _client_as("manager", monkeypatch)
    r = c.get("/api/audit/events")
    _cleanup_overrides()
    assert r.status_code == 403


def test_endpoint_admin_200(monkeypatch):
    c = _client_as("admin", monkeypatch)
    r = c.get("/api/audit/events")
    _cleanup_overrides()
    assert r.status_code == 200
    data = r.json()
    assert "events" in data
    assert "total" in data
    assert "viewer_role" in data
    assert data["viewer_role"] == "admin"


def test_endpoint_sysadmin_200(monkeypatch):
    """sysadmin 走 is_admin() 通过 (auth/base.py:73)."""
    c = _client_as("sysadmin", monkeypatch)
    r = c.get("/api/audit/events")
    _cleanup_overrides()
    assert r.status_code == 200
    assert r.json()["viewer_role"] == "sysadmin"


# ── API 行为 ──────────────────────────────────────────────


def test_endpoint_returns_paginated_result(monkeypatch):
    now = int(time.time())
    _seed([
        {"ts": now - 60, "user": f"u{i}@x", "model": "m1", "status": "ok",
         "department": "dev", "prompt_tokens": 100, "completion_tokens": 50}
        for i in range(10)
    ])
    c = _client_as("admin", monkeypatch)
    r = c.get("/api/audit/events?limit=3&offset=0")
    _cleanup_overrides()
    data = r.json()
    assert len(data["events"]) == 3
    assert data["total"] == 10
    assert data["limit"] == 3
    assert data["offset"] == 0


def test_endpoint_dept_filter(monkeypatch):
    now = int(time.time())
    _seed([
        {"ts": now - 60, "user": "alice@x", "department": "dev"},
        {"ts": now - 60, "user": "bob@x", "department": "ops"},
    ])
    c = _client_as("admin", monkeypatch)
    r = c.get("/api/audit/events?dept=dev")
    _cleanup_overrides()
    data = r.json()
    assert data["total"] == 1
    assert data["events"][0]["user"] == "alice@x"


def test_endpoint_user_filter(monkeypatch):
    now = int(time.time())
    _seed([
        {"ts": now - 60, "user": "alice@x"},
        {"ts": now - 60, "user": "bob@x"},
    ])
    c = _client_as("admin", monkeypatch)
    r = c.get("/api/audit/events?user_filter=alice@x")
    _cleanup_overrides()
    data = r.json()
    assert data["total"] == 1


def test_endpoint_status_filter(monkeypatch):
    now = int(time.time())
    _seed([
        {"ts": now - 60, "user": "a@x", "status": "ok"},
        {"ts": now - 60, "user": "b@x", "status": "error"},
        {"ts": now - 60, "user": "c@x", "status": "interrupted_resumed"},
    ])
    c = _client_as("admin", monkeypatch)
    r = c.get("/api/audit/events?status=interrupted_resumed")
    _cleanup_overrides()
    assert r.json()["total"] == 1


def test_endpoint_default_since_24h(monkeypatch):
    """不传 since_ms 默认 24h 前."""
    now = int(time.time())
    _seed([
        {"ts": now - 86400 - 60, "user": "old@x"},  # 24h+ 前
        {"ts": now - 60, "user": "recent@x"},  # 1 分钟前
    ])
    c = _client_as("admin", monkeypatch)
    r = c.get("/api/audit/events")
    _cleanup_overrides()
    data = r.json()
    assert data["total"] == 1
    assert data["events"][0]["user"] == "recent@x"


def test_endpoint_explicit_since_ms(monkeypatch):
    now = int(time.time())
    _seed([
        {"ts": now - 7200, "user": "old@x"},
        {"ts": now - 60, "user": "recent@x"},
    ])
    c = _client_as("admin", monkeypatch)
    # since_ms = 1h 前 → 老的不进
    since = (now - 3600) * 1000
    r = c.get(f"/api/audit/events?since_ms={since}")
    _cleanup_overrides()
    assert r.json()["total"] == 1


def test_endpoint_limit_clamped_to_200(monkeypatch):
    c = _client_as("admin", monkeypatch)
    r = c.get("/api/audit/events?limit=999")
    _cleanup_overrides()
    assert r.json()["limit"] == 200


def test_endpoint_offset_floor_zero(monkeypatch):
    c = _client_as("admin", monkeypatch)
    r = c.get("/api/audit/events?offset=-5")
    _cleanup_overrides()
    assert r.json()["offset"] == 0
