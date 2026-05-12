"""BL-HERMES013-5 测试 — output_transforms ABC chain.

覆盖:
- OutputCtx frozen 不可改
- 3 个内置 transform 在不同 status / is_internal / token 组合下的语义
- TransformChain 运行: 顺序 / 失败静默 / on_complete vs on_error 分发
- build_default_chain 顺序保护
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from catfish_gateway import output_transforms as ot


def _ctx(status: str = "ok", is_internal: bool = False, prompt_tokens: int = 100,
         completion_tokens: int = 50, used_model: Any = None,
         user: str = "alice@x") -> ot.OutputCtx:
    return ot.OutputCtx(
        user=user,
        department="研发部",
        model="catfish-private-main",
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_ms=1234.5,
        ttft_ms=200.0,
        status=status,
        error="" if status == "ok" else "boom",
        security_concern="",
        is_internal=is_internal,
        used_model=used_model,
    )


# ── OutputCtx ─────────────────────────────────────────────


def test_ctx_is_frozen():
    """ctx 必须 frozen — transform 不准改, 防顺序依赖."""
    c = _ctx()
    with pytest.raises(FrozenInstanceError):
        c.status = "error"  # type: ignore


# ── ContextUsageTransform ─────────────────────────────────


def test_context_usage_skips_when_no_model(monkeypatch):
    called = []
    monkeypatch.setattr(
        "catfish_gateway.app._check_context_usage",
        lambda *a, **kw: called.append((a, kw)),
    )
    ot.ContextUsageTransform().on_complete(_ctx(used_model=None))
    assert called == []


def test_context_usage_skips_when_no_tokens(monkeypatch):
    called = []
    monkeypatch.setattr(
        "catfish_gateway.app._check_context_usage",
        lambda *a, **kw: called.append((a, kw)),
    )
    fake_model = type("M", (), {"name": "x"})()
    ot.ContextUsageTransform().on_complete(_ctx(used_model=fake_model, prompt_tokens=0))
    assert called == []


def test_context_usage_called_when_model_and_tokens(monkeypatch):
    called = []
    monkeypatch.setattr(
        "catfish_gateway.app._check_context_usage",
        lambda model, tokens, user: called.append((model, tokens, user)),
    )
    fake_model = type("M", (), {"name": "x"})()
    ot.ContextUsageTransform().on_complete(_ctx(used_model=fake_model, prompt_tokens=500))
    assert len(called) == 1
    assert called[0] == (fake_model, 500, "alice@x")


def test_context_usage_on_error_also_runs(monkeypatch):
    """error 路径也跑 — context overflow 错误 (BL-FIX23-L4) 走这个 hook."""
    called = []
    monkeypatch.setattr(
        "catfish_gateway.app._check_context_usage",
        lambda *a, **kw: called.append(a),
    )
    fake_model = type("M", (), {"name": "x"})()
    ot.ContextUsageTransform().on_error(_ctx(status="error", used_model=fake_model, prompt_tokens=500))
    assert len(called) == 1


# ── AuditTransform ────────────────────────────────────────


def test_audit_calls_log_request_metadata_with_full_ctx(monkeypatch):
    captured: dict = {}

    def fake_log(**kw):
        captured.update(kw)

    monkeypatch.setattr("catfish_gateway.metrics.log_request_metadata", fake_log)
    c = _ctx()
    ot.AuditTransform().on_complete(c)
    assert captured["user"] == "alice@x"
    assert captured["model"] == "catfish-private-main"
    assert captured["prompt_tokens"] == 100
    assert captured["completion_tokens"] == 50
    assert captured["status"] == "ok"


def test_audit_called_on_error_too(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(
        "catfish_gateway.metrics.log_request_metadata",
        lambda **kw: captured.update(kw),
    )
    ot.AuditTransform().on_error(_ctx(status="error"))
    assert captured["status"] == "error"
    assert captured["error"] == "boom"


# ── QuotaTransform ────────────────────────────────────────


def test_quota_skipped_when_internal(monkeypatch):
    called = []
    monkeypatch.setattr(
        "catfish_gateway.quota.record_usage",
        lambda **kw: called.append(kw),
    )
    ot.QuotaTransform().on_complete(_ctx(is_internal=True))
    assert called == []


def test_quota_skipped_when_zero_tokens(monkeypatch):
    called = []
    monkeypatch.setattr(
        "catfish_gateway.quota.record_usage",
        lambda **kw: called.append(kw),
    )
    ot.QuotaTransform().on_complete(_ctx(prompt_tokens=0, completion_tokens=0))
    assert called == []


def test_quota_records_when_ok_external_with_tokens(monkeypatch):
    called: dict = {}
    monkeypatch.setattr(
        "catfish_gateway.quota.record_usage",
        lambda **kw: called.update(kw),
    )
    ot.QuotaTransform().on_complete(_ctx())
    assert called["user_email"] == "alice@x"
    assert called["department"] == "研发部"
    assert called["model"] == "catfish-private-main"
    assert called["tokens_in"] == 100
    assert called["tokens_out"] == 50


def test_quota_skipped_on_error(monkeypatch):
    """error 不计 quota (5/2 sprint 收尾原行为)."""
    called = []
    monkeypatch.setattr(
        "catfish_gateway.quota.record_usage",
        lambda **kw: called.append(kw),
    )
    ot.QuotaTransform().on_error(_ctx(status="error"))
    assert called == []


# ── TransformChain ────────────────────────────────────────


def test_chain_runs_all_in_order():
    order = []

    class T:
        def __init__(self, name):
            self.name = name

        def on_complete(self, ctx):
            order.append(("c", self.name))

        def on_error(self, ctx):
            order.append(("e", self.name))

    chain = ot.TransformChain([T("a"), T("b"), T("c")])
    chain.run(_ctx())
    assert order == [("c", "a"), ("c", "b"), ("c", "c")]


def test_chain_dispatches_to_on_error_when_status_error():
    order = []

    class T:
        name = "t"

        def on_complete(self, ctx):
            order.append("complete")

        def on_error(self, ctx):
            order.append("error")

    ot.TransformChain([T()]).run(_ctx(status="error"))
    assert order == ["error"]


def test_chain_failure_does_not_block_other_transforms(caplog):
    """一个 transform 抛异常 → log warning + 跳到下一个, 不传染."""
    runs = []

    class Boom:
        name = "boom"

        def on_complete(self, ctx):
            raise RuntimeError("disk full")

        def on_error(self, ctx):
            raise RuntimeError("disk full")

    class Good:
        name = "good"

        def on_complete(self, ctx):
            runs.append("good")

        def on_error(self, ctx):
            runs.append("good_err")

    chain = ot.TransformChain([Boom(), Good()])
    with caplog.at_level("WARNING", logger="catfish.gateway.output_transforms"):
        chain.run(_ctx())
    assert runs == ["good"]
    assert any("transform 'boom' 失败" in r.message for r in caplog.records)


def test_chain_add_returns_self_for_fluent():
    chain = ot.TransformChain([])
    assert chain.add(ot.AuditTransform()) is chain
    assert len(chain.transforms) == 1


# ── build_default_chain 顺序保护 ──────────────────────────


def test_default_chain_has_four_in_correct_order():
    """ContextUsage → Audit → Quota → InflightCleanup:
      BL-FIX23-L4 警告进 audit, audit 始终落盘才能让 quota 失败也有审计依据,
      InflightCleanup 最后 (audit + quota 都跑完才 unlink — 崩在 audit 之
      前文件留下, 重启 reap 写 'interrupted' audit 替补).
    """
    chain = ot.build_default_chain()
    names = [t.name for t in chain.transforms]
    assert names == ["context_usage", "audit", "quota", "inflight_cleanup"]


def test_default_chain_module_singleton_exists():
    assert ot.DEFAULT_CHAIN is not None
    assert isinstance(ot.DEFAULT_CHAIN, ot.TransformChain)


def test_default_chain_e2e_smoke(monkeypatch):
    """跑一遍默认 chain, 看 audit/quota 都被调到, context (无 model) 跳过."""
    audit_called = []
    quota_called = []
    monkeypatch.setattr(
        "catfish_gateway.metrics.log_request_metadata",
        lambda **kw: audit_called.append(kw),
    )
    monkeypatch.setattr(
        "catfish_gateway.quota.record_usage",
        lambda **kw: quota_called.append(kw),
    )
    ot.build_default_chain().run(_ctx(used_model=None))
    assert len(audit_called) == 1
    assert len(quota_called) == 1
    assert audit_called[0]["status"] == "ok"
    assert quota_called[0]["user_email"] == "alice@x"
