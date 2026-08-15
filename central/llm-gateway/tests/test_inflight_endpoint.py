"""GET /api/inflight —— 卡住时看"哪条卡着、卡了多久" (8/15).

# 为什么有这个接口

inflight_streams 从 5/12 起就在记每条流, docstring 写着 "cancel UI / ops 调试用",
但从来没接出来。8/15 现场一条 deepseek 流卡在首 chunk 之前, 这份数据就在进程
内存里躺着, 排查的人拿不到 —— 只能从日志里数"哪条请求没有收尾行"。
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from catfish_gateway import inflight_streams
from catfish_gateway.app import app
from catfish_gateway.auth import User, get_current_user


def _as(role: str, sub: str = "u@x"):
    return lambda: User(sub=sub, role=role, department="d")


@pytest.fixture(autouse=True)
def _clean():
    inflight_streams.clear()
    yield
    inflight_streams.clear()
    app.dependency_overrides.pop(get_current_user, None)


def test_普通员工不能看():
    """记录里带别人的邮箱和模型, 是跨员工的运行状态。"""
    app.dependency_overrides[get_current_user] = _as("employee")
    assert TestClient(app).get("/api/inflight").status_code == 403


def test_空的时候返回_0_条():
    app.dependency_overrides[get_current_user] = _as("sysadmin")
    body = TestClient(app).get("/api/inflight").json()
    assert body["count"] == 0 and body["inflight"] == []


def test_列出在途的流并算出等了多久():
    inflight_streams.mark_started("req-a", user="a@x", model="m1", message_count=3)
    app.dependency_overrides[get_current_user] = _as("sysadmin")
    body = TestClient(app).get("/api/inflight").json()
    assert body["count"] == 1
    row = body["inflight"][0]
    assert row["request_id"] == "req-a"
    assert row["user"] == "a@x" and row["model"] == "m1" and row["message_count"] == 3
    # elapsed_secs 是这个接口唯一新算的字段, 也正是卡住时最想知道的那个
    assert isinstance(row["elapsed_secs"], float) and row["elapsed_secs"] >= 0


def test_按等待时长倒序_最久的排最前():
    """卡住时第一眼要看到最久的那条, 不是最新那条。"""
    inflight_streams.mark_started("new", user="a@x", model="m")
    with inflight_streams._lock:
        inflight_streams._inflight["old"] = {
            "request_id": "old", "user": "b@x", "model": "m",
            "message_count": 0, "started_at": time.time() - 600,
        }
    app.dependency_overrides[get_current_user] = _as("sysadmin")
    rows = TestClient(app).get("/api/inflight").json()["inflight"]
    assert [r["request_id"] for r in rows] == ["old", "new"]
    assert rows[0]["elapsed_secs"] > 500


def test_收尾之后就不再列出():
    inflight_streams.mark_started("req-b", user="a@x", model="m")
    inflight_streams.mark_finished("req-b")
    app.dependency_overrides[get_current_user] = _as("sysadmin")
    assert TestClient(app).get("/api/inflight").json()["count"] == 0
