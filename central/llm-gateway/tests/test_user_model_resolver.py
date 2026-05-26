"""STUB+API — 5/26 BL-PROACTIVE-DECOUPLE: get_session_model / get_user_last_session_model 砍.

# 原测试搬哪了

无搬迁. 老测试覆盖 get_user_last_session_model 读 state.db 的所有边界
(BL-RESOLVER-SOURCE-FIX 5/17). 5/26 这俩 API 整体砍 — gateway 不再读员工本机
hermes state.db, Companion 通过 header `X-Catfish-Last-Model` 透传给 gateway.

# 留的测试

- 验 get_session_model / get_user_last_session_model 是 fail-loud stub (调即抛)
- 验 resolve_model_obj (纯查表, 无 fs/db read) 保留正常工作

# 关联

- 5/17 BL-RESOLVER-SOURCE-FIX (老 bug 已通过砍函数彻底消除, 没源就没漂移)
- 5/26 BL-PROACTIVE-DECOUPLE
- Companion 改造 (header 透传) — separate ticket
"""
from __future__ import annotations

import pytest

from catfish_gateway import user_model_resolver
from catfish_gateway.user_model_resolver import (
    get_session_model,
    get_user_last_session_model,
    resolve_model_obj,
)


# ─── 砍的 API: fail-loud stub 防回归 ─────────────────────────


def test_get_session_model_is_fail_loud_stub():
    """5/26 兑现校验: get_session_model 调即抛 (gateway 不读员工 state.db)."""
    with pytest.raises(RuntimeError, match="DEPRECATED 5/26"):
        get_session_model("any_sid")


def test_get_user_last_session_model_is_fail_loud_stub():
    """5/26 兑现校验: get_user_last_session_model 调即抛."""
    with pytest.raises(RuntimeError, match="DEPRECATED 5/26"):
        get_user_last_session_model("x@y.com")


def test_state_db_constant_removed():
    """STATE_DB 常量也砍 (没人再用). 防回归: 不能复活."""
    assert not hasattr(user_model_resolver, "STATE_DB"), (
        "STATE_DB 5/26 砍, 不能复活. 改 caller 接 model_name 参数 + Companion header."
    )


# ─── 保留的 API: resolve_model_obj 纯查表 ─────────────────────


class _FakeUpstream:
    def __init__(self, available: bool):
        self.is_available = available


class _FakeModel:
    def __init__(self, name: str, available: bool = True):
        self.name = name
        self.upstream = _FakeUpstream(available)


class _FakeConfig:
    def __init__(self, models: dict[str, _FakeModel]):
        self._models = models

    def get_model(self, name: str) -> _FakeModel | None:
        return self._models.get(name)


def test_resolve_model_obj_returns_none_for_empty_name():
    cfg = _FakeConfig({})
    assert resolve_model_obj(None, cfg) is None  # type: ignore[arg-type]
    assert resolve_model_obj("", cfg) is None  # type: ignore[arg-type]


def test_resolve_model_obj_returns_none_when_not_found():
    cfg = _FakeConfig({})
    assert resolve_model_obj("no-such-model", cfg) is None  # type: ignore[arg-type]


def test_resolve_model_obj_returns_none_when_upstream_unavailable():
    """模型存在但 upstream 标 unavailable → None (不让 caller 用挂的模型)."""
    m = _FakeModel("catfish-private-main", available=False)
    cfg = _FakeConfig({"catfish-private-main": m})
    assert resolve_model_obj("catfish-private-main", cfg) is None  # type: ignore[arg-type]


def test_resolve_model_obj_returns_model_when_available():
    m = _FakeModel("catfish-private-main", available=True)
    cfg = _FakeConfig({"catfish-private-main": m})
    out = resolve_model_obj("catfish-private-main", cfg)  # type: ignore[arg-type]
    assert out is m
