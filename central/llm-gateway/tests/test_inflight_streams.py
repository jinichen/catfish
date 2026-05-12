"""BL-HERMES013-4 测试 — gateway atomic in-flight stream tracking.

覆盖:
- mark_started 写文件 atomic (tmp + rename + fsync)
- mark_finished unlink
- list_inflight 扫目录返残留
- reap_interrupted 写 audit + 清文件
- InflightCleanupTransform: ok / error 都清 / 没 request_id 跳
- 隐私: 路径走 CATFISH_HOME 联动 (跟 employee_journal 一致, 多 agent 不撞)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from catfish_gateway import inflight_streams as ifs
from catfish_gateway import output_transforms as ot


@pytest.fixture(autouse=True)
def isolated_catfish_home(tmp_path: Path, monkeypatch):
    home = tmp_path / "fake_catfish"
    home.mkdir()
    monkeypatch.setenv("CATFISH_HOME", str(home))
    yield home


# ── mark_started ──────────────────────────────────────────


def test_mark_started_writes_file(isolated_catfish_home: Path):
    ok = ifs.mark_started(
        "req-abc",
        user="alice@x",
        model="catfish-private-main",
        message_count=5,
    )
    assert ok is True
    f = isolated_catfish_home / "inflight_streams" / "req-abc.json"
    assert f.exists()
    data = json.loads(f.read_text(encoding="utf-8"))
    assert data["request_id"] == "req-abc"
    assert data["user"] == "alice@x"
    assert data["model"] == "catfish-private-main"
    assert data["message_count"] == 5
    assert "started_at" in data
    assert "started_iso" in data


def test_mark_started_includes_extra(isolated_catfish_home: Path):
    ifs.mark_started("req-1", user="a@x", extra={"is_internal": True, "trace_id": "xyz"})
    f = isolated_catfish_home / "inflight_streams" / "req-1.json"
    data = json.loads(f.read_text(encoding="utf-8"))
    assert data["is_internal"] is True
    assert data["trace_id"] == "xyz"


def test_mark_started_empty_request_id_returns_false(isolated_catfish_home: Path):
    assert ifs.mark_started("") is False


def test_mark_started_no_tmp_residue(isolated_catfish_home: Path):
    """atomic write — tmp 文件应该在 rename 后消失."""
    ifs.mark_started("req-1")
    d = isolated_catfish_home / "inflight_streams"
    files = sorted(p.name for p in d.iterdir())
    assert files == ["req-1.json"]


# ── mark_finished ─────────────────────────────────────────


def test_mark_finished_unlinks(isolated_catfish_home: Path):
    ifs.mark_started("req-1")
    assert (isolated_catfish_home / "inflight_streams" / "req-1.json").exists()
    ok = ifs.mark_finished("req-1")
    assert ok is True
    assert not (isolated_catfish_home / "inflight_streams" / "req-1.json").exists()


def test_mark_finished_missing_file_idempotent(isolated_catfish_home: Path):
    """没文件也不抛."""
    assert ifs.mark_finished("never-existed") is True


def test_mark_finished_empty_request_id(isolated_catfish_home: Path):
    assert ifs.mark_finished("") is False


# ── list_inflight ─────────────────────────────────────────


def test_list_inflight_empty_when_no_dir(isolated_catfish_home: Path):
    assert ifs.list_inflight() == []


def test_list_inflight_returns_residue(isolated_catfish_home: Path):
    ifs.mark_started("req-a", user="alice@x")
    ifs.mark_started("req-b", user="bob@x")
    items = ifs.list_inflight()
    assert len(items) == 2
    subs = {i["user"] for i in items}
    assert subs == {"alice@x", "bob@x"}
    # 每条都有 _path 让 reap 知道删哪
    for i in items:
        assert "_path" in i


def test_list_inflight_skips_corrupt_json(isolated_catfish_home: Path):
    """损坏 json 跳过, 不抛."""
    d = isolated_catfish_home / "inflight_streams"
    d.mkdir()
    (d / "good.json").write_text('{"request_id":"good","user":"a@x"}', encoding="utf-8")
    (d / "bad.json").write_text("not json", encoding="utf-8")
    items = ifs.list_inflight()
    assert len(items) == 1
    assert items[0]["request_id"] == "good"


def test_list_inflight_skips_non_json_files(isolated_catfish_home: Path):
    d = isolated_catfish_home / "inflight_streams"
    d.mkdir()
    (d / "good.json").write_text('{"request_id":"good"}', encoding="utf-8")
    (d / "stray.txt").write_text("ignored", encoding="utf-8")
    items = ifs.list_inflight()
    assert len(items) == 1


# ── reap_interrupted ──────────────────────────────────────


def test_reap_writes_audit_and_unlinks(isolated_catfish_home: Path):
    """reap 写 audit + 清文件."""
    ifs.mark_started("req-1", user="alice@x", model="m1")
    ifs.mark_started("req-2", user="bob@x", model="m2")

    audited = []
    cleaned = ifs.reap_interrupted(audit_writer=lambda r: audited.append(r))
    assert cleaned == 2
    # 文件被清
    assert ifs.list_inflight() == []
    # audit 被调
    assert len(audited) == 2
    subs = {a["user"] for a in audited}
    assert subs == {"alice@x", "bob@x"}


def test_reap_audit_failure_still_unlinks(isolated_catfish_home: Path):
    """audit 写失败也 unlink (防文件累积)."""
    ifs.mark_started("req-1")

    def boom(r):
        raise RuntimeError("audit DB down")

    cleaned = ifs.reap_interrupted(audit_writer=boom)
    assert cleaned == 1
    assert ifs.list_inflight() == []


def test_reap_empty_returns_zero(isolated_catfish_home: Path):
    assert ifs.reap_interrupted() == 0


def test_reap_default_writer_calls_log_request_metadata(monkeypatch, isolated_catfish_home: Path):
    """默认 audit_writer 走 metrics.log_request_metadata."""
    captured: dict = {}
    monkeypatch.setattr(
        "catfish_gateway.metrics.log_request_metadata",
        lambda **kw: captured.update(kw),
    )
    ifs.mark_started("req-1", user="alice@x", model="m1")
    ifs.reap_interrupted()  # 不传 writer, 走默认
    assert captured["status"] == "interrupted_resumed"
    assert captured["user"] == "alice@x"
    assert "BL-HERMES013-4" in captured["error"]


# ── InflightCleanupTransform ──────────────────────────────


def _ctx(request_id: str = "req-test", status: str = "ok") -> ot.OutputCtx:
    return ot.OutputCtx(
        user="alice@x",
        department="研发部",
        model="m",
        prompt_tokens=100,
        completion_tokens=50,
        latency_ms=1234.0,
        ttft_ms=200.0,
        status=status,
        error="" if status == "ok" else "boom",
        security_concern="",
        is_internal=False,
        used_model=None,
        request_id=request_id,
    )


def test_cleanup_transform_unlinks_on_complete(isolated_catfish_home: Path):
    ifs.mark_started("req-test", user="alice@x")
    assert (isolated_catfish_home / "inflight_streams" / "req-test.json").exists()
    ot.InflightCleanupTransform().on_complete(_ctx("req-test"))
    assert not (isolated_catfish_home / "inflight_streams" / "req-test.json").exists()


def test_cleanup_transform_unlinks_on_error(isolated_catfish_home: Path):
    """error 路径也清 — Python 跑到 finally 说明 generator 至少正常 yielded
    (上游 LLM 报错不算 'interrupted', 只是失败). 真崩才是断电 finally 不跑."""
    ifs.mark_started("req-test")
    ot.InflightCleanupTransform().on_error(_ctx("req-test", status="error"))
    assert not (isolated_catfish_home / "inflight_streams" / "req-test.json").exists()


def test_cleanup_transform_skips_when_no_request_id(isolated_catfish_home: Path):
    """没 request_id 不动文件 (向后兼容老代码路径)."""
    ifs.mark_started("req-other")
    ot.InflightCleanupTransform().on_complete(_ctx(""))
    assert (isolated_catfish_home / "inflight_streams" / "req-other.json").exists()


def test_cleanup_transform_in_default_chain(isolated_catfish_home: Path, monkeypatch):
    """默认 chain 跑一遍真清掉 inflight 文件."""
    monkeypatch.setattr(
        "catfish_gateway.metrics.log_request_metadata",
        lambda **kw: None,
    )
    monkeypatch.setattr(
        "catfish_gateway.quota.record_usage",
        lambda **kw: None,
    )
    ifs.mark_started("req-chain")
    ot.build_default_chain().run(_ctx("req-chain"))
    assert not (isolated_catfish_home / "inflight_streams" / "req-chain.json").exists()
