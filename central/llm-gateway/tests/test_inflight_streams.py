"""BL-HERMES013-4 + BL-INFLIGHT-MEM (5/26) — gateway in-flight stream tracking
in-memory 实现的测试.

# 5/26 改造背景

老逻辑用 ~/.catfish/inflight_streams/<req>.json 文件存 (gateway 跑员工 mac 写  # noqa: BOUNDARY
本机). SaaS 化后 gateway 跑客户机房, 写不到员工本机 → 改 in-memory dict.

# 覆盖

  - mark_started / mark_finished 增减 in-memory 项
  - list_inflight 返当前 snapshot
  - reap_interrupted 永远返 0 (重启后 dict 已空, 不再走 fs 残留 audit)
  - InflightCleanupTransform: ok / error 都从 dict 移除 / 没 request_id 跳

老 fs-based 测试 (atomic write / fsync / corrupt json / 路径 CATFISH_HOME 联动)
跟着 fs 实现一起 sunset — in-memory 没有这些概念.
"""
from __future__ import annotations

import pytest

from catfish_gateway import inflight_streams as ifs
from catfish_gateway import output_transforms as ot


@pytest.fixture(autouse=True)
def clear_inflight_dict():
    """每个测试用独立 in-memory 状态 (清空 + run + 清空)."""
    ifs.clear()
    yield
    ifs.clear()


# ── mark_started ──────────────────────────────────────────


def test_mark_started_adds_to_dict():
    ok = ifs.mark_started(
        "req-abc",
        user="alice@x",
        model="catfish-private-main",
        message_count=5,
    )
    assert ok is True
    items = ifs.list_inflight()
    assert len(items) == 1
    rec = items[0]
    assert rec["request_id"] == "req-abc"
    assert rec["user"] == "alice@x"
    assert rec["model"] == "catfish-private-main"
    assert rec["message_count"] == 5
    assert "started_at" in rec
    assert "started_iso" in rec


def test_mark_started_includes_extra():
    ifs.mark_started("req-1", user="a@x", extra={"is_internal": True, "trace_id": "xyz"})
    items = ifs.list_inflight()
    assert len(items) == 1
    rec = items[0]
    assert rec["is_internal"] is True
    assert rec["trace_id"] == "xyz"


def test_mark_started_empty_request_id_returns_false():
    assert ifs.mark_started("") is False
    assert ifs.list_inflight() == []


def test_mark_started_same_id_overwrites():
    """同 request_id 调两次, 后调的 record 覆盖前一个 (业务上是 retry / 改 model)."""
    ifs.mark_started("req-1", user="a@x", model="m1")
    ifs.mark_started("req-1", user="a@x", model="m2")
    items = ifs.list_inflight()
    assert len(items) == 1
    assert items[0]["model"] == "m2"


# ── mark_finished ─────────────────────────────────────────


def test_mark_finished_removes_from_dict():
    ifs.mark_started("req-1")
    assert len(ifs.list_inflight()) == 1
    ok = ifs.mark_finished("req-1")
    assert ok is True
    assert ifs.list_inflight() == []


def test_mark_finished_missing_id_idempotent():
    """没 record 也不抛, 返 True (mark_started 失败过 / 已被别处清掉都正常)."""
    assert ifs.mark_finished("never-existed") is True


def test_mark_finished_empty_request_id():
    assert ifs.mark_finished("") is False


# ── mark_aborted (BL-ABORT-PROPAGATE 7/23 达华 POC) ──────────


def test_mark_aborted_removes_from_dict():
    """abort 也从 dict 清 (跟 finished 语义一致) · 差异只在 log 侧."""
    ifs.mark_started("req-x", user="a@x", model="qwen")
    assert len(ifs.list_inflight()) == 1
    ok = ifs.mark_aborted("req-x", reason="client_disconnect")
    assert ok is True
    assert len(ifs.list_inflight()) == 0


def test_mark_aborted_missing_id_idempotent():
    """跟 mark_finished 一致 · 不存在也不抛 · 返 True."""
    assert ifs.mark_aborted("never-existed") is True


def test_mark_aborted_empty_request_id():
    assert ifs.mark_aborted("") is False


def test_mark_aborted_logs_warning(caplog):
    """有 record 时打 warn log · 含 request_id / user / model / reason.
    ops 靠这个统计 abort 频率. 军规 · 无 record 时 silent (不该噪).
    """
    import logging
    ifs.mark_started("req-log", user="alice@x", model="qwen-plus")
    with caplog.at_level(logging.WARNING, logger="catfish.gateway.inflight_streams"):
        ifs.mark_aborted("req-log", reason="cancelled")
    # 至少一条 warn · 含 request_id + reason
    warn_msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("req-log" in m and "cancelled" in m for m in warn_msgs)


def test_mark_aborted_no_log_when_missing():
    """没 record 时 silent · 别噪音刷 log."""
    import logging
    with caplog_context() as cap:
        ifs.mark_aborted("never-existed", reason="client_disconnect")
    # 应该 no warn (record 不存在 · 没数据可打)
    warns = [r for r in cap.records if r.levelno >= logging.WARNING]
    assert warns == []


# caplog fixture 简化包装 (pytest 内建 · 直接用避 fixture 依赖 chain)
from contextlib import contextmanager  # noqa: E402


@contextmanager
def caplog_context():
    import logging
    logger = logging.getLogger("catfish.gateway.inflight_streams")
    records: list = []

    class _H(logging.Handler):
        def emit(self, r):
            records.append(r)

    h = _H()
    logger.addHandler(h)
    try:
        yield type("Cap", (), {"records": records})()
    finally:
        logger.removeHandler(h)


# ── list_inflight ─────────────────────────────────────────


def test_list_inflight_empty():
    assert ifs.list_inflight() == []


def test_list_inflight_returns_all_in_dict():
    ifs.mark_started("req-a", user="alice@x")
    ifs.mark_started("req-b", user="bob@x")
    items = ifs.list_inflight()
    assert len(items) == 2
    subs = {i["user"] for i in items}
    assert subs == {"alice@x", "bob@x"}


def test_list_inflight_returns_snapshot_not_live():
    """list_inflight 应该返新 list, 不能让 caller 修改影响内部 dict."""
    ifs.mark_started("req-1")
    items = ifs.list_inflight()
    items.append({"injected": "external"})
    # 内部仍只 1 项
    assert len(ifs.list_inflight()) == 1


# ── reap_interrupted (5/26 后简化) ───────────────────────────


def test_reap_interrupted_always_returns_zero():
    """5/26 改 in-memory 后, reap 永远返 0 (启动时 dict 自然为空).

    防回归: 不再恢复 fs scan + audit "interrupted_resumed" 老路径.
    """
    # 即使现在有 in-flight 也不该被 reap 清 (留 active 给当前 process)
    ifs.mark_started("req-active")
    assert ifs.reap_interrupted() == 0
    # 这条 active 没动
    assert len(ifs.list_inflight()) == 1


def test_reap_interrupted_ignores_audit_writer():
    """audit_writer 参数保留兼容, 但 5/26 后不再调."""
    called = []
    ifs.mark_started("req-1")
    ifs.reap_interrupted(audit_writer=lambda r: called.append(r))
    assert called == []


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


def test_cleanup_transform_removes_on_complete():
    ifs.mark_started("req-test", user="alice@x")
    assert len(ifs.list_inflight()) == 1
    ot.InflightCleanupTransform().on_complete(_ctx("req-test"))
    assert ifs.list_inflight() == []


def test_cleanup_transform_removes_on_error():
    """error 路径也清 — 上游 LLM 报错不是 'interrupted' (Python 跑到 finally 说明
    generator 至少正常 yielded). 真崩才是断电 finally 不跑."""
    ifs.mark_started("req-test")
    ot.InflightCleanupTransform().on_error(_ctx("req-test", status="error"))
    assert ifs.list_inflight() == []


def test_cleanup_transform_skips_when_no_request_id():
    """没 request_id 不动其他记录 (向后兼容)."""
    ifs.mark_started("req-other")
    ot.InflightCleanupTransform().on_complete(_ctx(""))
    items = ifs.list_inflight()
    assert len(items) == 1
    assert items[0]["request_id"] == "req-other"


def test_cleanup_transform_in_default_chain(monkeypatch):
    """默认 chain 跑一遍真清掉 inflight 记录."""
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
    assert ifs.list_inflight() == []
