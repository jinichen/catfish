"""STUB 化 — DEPRECATED state.db read (5/26 BL-PROACTIVE-DECOUPLE).

# 5/26 砍的真原因

老 user_model_resolver 读员工本机 hermes state.db 拿"员工最近 session 用的
model 名". gateway 中央代码读员工本机 sqlite, SaaS 化即破. 5/26 audit 纠正:
Companion (在员工 mac, 合规读自己 state.db) 通过 header `X-Catfish-Last-Model`
传给 gateway. gateway 不再自己读.

# 保留的 API

- `resolve_model_obj(model_name, config)` — 字符串 model name → Config.Model
  对象. 纯查表, 无 fs/db read. 保留, 给 proactive + facts_pipeline 用.

# 砍的 API

- `get_session_model(session_id)` — 读 state.db. 5/26 砍 (从来无外部 caller).
- `get_user_last_session_model(user_email)` — 读 state.db. 5/26 砍 (proactive
  + facts_pipeline caller 已改成接 model_name 参数, 不再调).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config, ModelConfig

logger = logging.getLogger("catfish.gateway.user_model_resolver")


# ─── 砍的 API: stub fail-loud 防回归 ───────────────────────────


def get_session_model(session_id: str) -> str | None:  # noqa: ARG001
    """5/26 砍 — 读员工 state.db 违反边界. Companion header 化."""
    raise RuntimeError(
        "[DEPRECATED 5/26] get_session_model 砍 — 改用 Companion header "
        "X-Catfish-Last-Model 透传 (gateway 不读员工 hermes state.db)."
    )


def get_user_last_session_model(user_email: str | None = None) -> str | None:  # noqa: ARG001
    """5/26 砍 — 读员工 state.db 违反边界. caller 改接 model_name 参数."""
    raise RuntimeError(
        "[DEPRECATED 5/26] get_user_last_session_model 砍 — caller (proactive / "
        "facts_pipeline) 改成接 model_name 参数, 由 Companion 通过 header 透传."
    )


# ─── 保留的 API: 纯查表, 无 fs/db read ───────────────────────


def resolve_model_obj(model_name: str | None, config: "Config") -> "ModelConfig | None":
    """字符串 model name → Config.Model 对象. None / 找不到 / 不可用 → None."""
    if not model_name:
        return None
    m = config.get_model(model_name)
    if m is None:
        return None
    if not m.upstream.is_available:
        return None
    return m


__all__ = [
    "resolve_model_obj",
    # stub-only (raise on call):
    "get_session_model",
    "get_user_last_session_model",
]
