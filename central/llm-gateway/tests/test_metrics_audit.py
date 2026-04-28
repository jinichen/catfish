"""metrics.py audit JSONL 持久化单测.

覆盖:
  - log_request_metadata 同时写 stderr + JSONL
  - 持久化字段格式 (metadata-only, 没有 prompt 内容)
  - read_events 时间 / user / model / status 过滤 + limit
  - 倒序 (最新在前)
  - 写失败永远不抛 (audit 不能影响 LLM 主流程)
  - CATFISH_AUDIT_PATH env var 支持
  - 多线程并发写不串
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from catfish_gateway import metrics


@pytest.fixture(autouse=True)
def _isolate_audit_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """每个测试用独立 audit 文件."""
    monkeypatch.delenv("CATFISH_AUDIT_PATH", raising=False)
    monkeypatch.setattr(metrics, "_audit_path", tmp_path / "gateway_audit.jsonl")
    yield


# ============================================================
# log_request_metadata 持久化
# ============================================================


def test_log_creates_file() -> None:
    """第一次 log 自动建文件 + 写一行"""
    metrics.log_request_metadata(
        user="alice", model="catfish-private-main",
        prompt_tokens=100, completion_tokens=50, latency_ms=234.5,
    )
    p = metrics.audit_path()
    assert p.is_file()
    lines = p.read_text().strip().split("\n")
    assert len(lines) == 1


def test_log_record_fields() -> None:
    """字段完整 + 类型对 + total_tokens 自动算"""
    metrics.log_request_metadata(
        user="alice", model="m1",
        prompt_tokens=100, completion_tokens=50, latency_ms=234.5,
        status="ok",
    )
    events = metrics.read_events()
    assert len(events) == 1
    e = events[0]
    assert e["user"] == "alice"
    assert e["model"] == "m1"
    assert e["prompt_tokens"] == 100
    assert e["completion_tokens"] == 50
    assert e["total_tokens"] == 150  # 自动 sum
    assert e["latency_ms"] == 234.5
    assert e["status"] == "ok"
    assert e["type"] == "llm_request"
    assert isinstance(e["ts"], int)


def test_log_excludes_prompt_content_strict() -> None:
    """合同承诺 — 永远不能记 prompt / completion / args.
    record 字段白名单, 没有任何字段叫 prompt / completion / messages / args."""
    metrics.log_request_metadata(user="x", model="y", status="ok")
    events = metrics.read_events()
    e = events[0]
    forbidden_keys = {"prompt", "completion", "messages", "args", "headers", "content"}
    assert not (set(e.keys()) & forbidden_keys), \
        f"audit record 不能含敏感字段: {set(e.keys()) & forbidden_keys}"


def test_log_error_truncated() -> None:
    """error 字段截 200 字, 防 prompt 回显泄漏"""
    long_err = "echo back of prompt: " + "x" * 1000
    metrics.log_request_metadata(
        user="x", model="y", status="error", error=long_err,
    )
    events = metrics.read_events()
    assert len(events[0]["error"]) <= 200


def test_log_no_error_field_when_ok() -> None:
    """status=ok 时 error 字段不出现 (干净 schema)"""
    metrics.log_request_metadata(user="x", model="y", status="ok")
    events = metrics.read_events()
    assert "error" not in events[0]


# ============================================================
# 写失败不抛 (audit 不能影响 LLM 主流程)
# ============================================================


def test_log_write_failure_does_not_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """audit 文件不可写 → log warning 不抛"""
    real_open = Path.open

    def fake_open(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if str(self).endswith("gateway_audit.jsonl"):
            raise OSError("disk full")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fake_open)
    # 不应该抛
    metrics.log_request_metadata(user="x", model="y")


def test_log_creates_parent_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """父目录不存在 → 自动建"""
    deep = tmp_path / "very" / "deep" / "audit.jsonl"
    monkeypatch.setattr(metrics, "_audit_path", deep)
    metrics.log_request_metadata(user="x", model="y")
    assert deep.is_file()


# ============================================================
# CATFISH_AUDIT_PATH env var 支持
# ============================================================


def test_env_var_overrides_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CATFISH_AUDIT_PATH env 设了就用这个路径"""
    custom = tmp_path / "custom_path.jsonl"
    monkeypatch.setenv("CATFISH_AUDIT_PATH", str(custom))
    monkeypatch.setattr(metrics, "_audit_path", None)  # 强制重 lazy resolve

    p = metrics.audit_path()
    assert p == custom


def test_default_path_when_no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """没设 env 时走默认 ~/.catfish/gateway_audit.jsonl"""
    monkeypatch.delenv("CATFISH_AUDIT_PATH", raising=False)
    monkeypatch.setenv("HOME", "/tmp/fakehome")
    monkeypatch.setattr(metrics, "_audit_path", None)

    p = metrics.audit_path()
    assert str(p).endswith(".catfish/gateway_audit.jsonl")


# ============================================================
# read_events 过滤 + 排序
# ============================================================


def test_read_events_returns_newest_first() -> None:
    """倒序 (最新在前)"""
    for i in range(5):
        metrics.log_request_metadata(user=f"u{i}", model="m1")
        time.sleep(0.001)
    events = metrics.read_events()
    users = [e["user"] for e in events]
    assert users == ["u4", "u3", "u2", "u1", "u0"]


def test_read_events_limit() -> None:
    for i in range(20):
        metrics.log_request_metadata(user=f"u{i}", model="m1")
    events = metrics.read_events(limit=5)
    assert len(events) == 5


def test_read_events_filter_by_user() -> None:
    metrics.log_request_metadata(user="alice", model="m1")
    metrics.log_request_metadata(user="bob", model="m1")
    metrics.log_request_metadata(user="alice", model="m2")

    events = metrics.read_events(user_filter="alice")
    assert len(events) == 2
    assert all(e["user"] == "alice" for e in events)


def test_read_events_filter_by_model() -> None:
    metrics.log_request_metadata(user="x", model="catfish-private-main")
    metrics.log_request_metadata(user="x", model="catfish-public-qwen-flash")
    metrics.log_request_metadata(user="x", model="catfish-private-main")

    events = metrics.read_events(model_filter="catfish-private-main")
    assert len(events) == 2


def test_read_events_filter_by_status() -> None:
    metrics.log_request_metadata(user="x", model="m", status="ok")
    metrics.log_request_metadata(user="x", model="m", status="error", error="boom")
    metrics.log_request_metadata(user="x", model="m", status="ok")

    err_events = metrics.read_events(status_filter="error")
    assert len(err_events) == 1
    assert err_events[0]["error"] == "boom"


def test_read_events_filter_by_time() -> None:
    """since_unix 过滤"""
    metrics.log_request_metadata(user="old", model="m")
    cutoff = int(time.time()) + 100  # 未来时间
    metrics.log_request_metadata(user="new", model="m")

    events = metrics.read_events(since_unix=cutoff)
    assert len(events) == 0  # 都在未来之前

    events_all = metrics.read_events(since_unix=0)
    assert len(events_all) == 2


def test_read_events_empty_file() -> None:
    events = metrics.read_events()
    assert events == []


def test_read_events_skips_malformed() -> None:
    """audit 文件里偶尔损坏行 → skip 不抛"""
    p = metrics._audit_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        '{"ts":1000,"user":"a","model":"m","status":"ok"}\n'
        'CORRUPTED LINE\n'
        '{"ts":2000,"user":"b","model":"m","status":"ok"}\n'
    )
    events = metrics.read_events()
    assert len(events) == 2  # 损坏的跳过


# ============================================================
# 多线程并发写
# ============================================================


def test_concurrent_writes_no_partial_lines() -> None:
    """5 线程并发写 100 次 → 500 行, 每行合法 json"""
    def hammer(user: str) -> None:
        for _ in range(100):
            metrics.log_request_metadata(user=user, model="m1")

    threads = [threading.Thread(target=hammer, args=(f"u{i}",)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    p = metrics.audit_path()
    lines = p.read_text().strip().split("\n")
    assert len(lines) == 500
    parsed = [json.loads(line) for line in lines]
    from collections import Counter
    counts = Counter(e["user"] for e in parsed)
    for i in range(5):
        assert counts[f"u{i}"] == 100
