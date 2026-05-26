"""STUB — DEPRECATED 5/26 (BL-SESSION-META-PLUGIN-TAKEOVER).

# 砍的真原因

老 session_meta 在 gateway 端 tick() 写员工本机 ~/.catfish/session_meta.json,    # noqa: BOUNDARY
跟踪员工今天第几次跟鲶鱼聊 + last_chat_iso. SaaS 化后 gateway 跑客户机房, 写
不到员工 mac, 字段永远不更新 → catfish-memory plugin 渲染"🕒 时间感"段永远空.

# 替代方案

catfish-memory hermes plugin (跑员工 mac, 写自己 fs 合规) 在 sync_turn hook 里
接管 _tick_session_meta(). 字段格式跟老 gateway tick 一模一样, plugin 自己渲染
也读自己写的, 闭环.

参见: edge/hermes-plugins/catfish-memory/catfish_memory.py:_tick_session_meta

# 兼容

老 gateway `meta_path()` / `_now()` / `_read()` / `_write()` 这些 helper 都
没人外部调, 同批砍. `tick()` 改 no-op (app.py 老 caller 5/26 已删, 留 stub
防回归).
"""
from __future__ import annotations

import logging

logger = logging.getLogger("catfish.gateway.session_meta")

_DEPRECATED_NOTICE = (
    "session_meta.tick 5/26 砍 — gateway 不再写员工本机 session_meta.json. "
    "catfish-memory hermes plugin _tick_session_meta() 接管 (跑员工 mac, 写自己 fs 合规)."
)

_warned = False


def tick() -> None:
    """no-op stub. 调一次日志警告一次. 不抛错防回归 (老 caller 调到不能挂)."""
    global _warned
    if not _warned:
        logger.warning("[DEPRECATED 5/26] %s", _DEPRECATED_NOTICE)
        _warned = True


def __getattr__(name: str):  # noqa: D401
    """任何其他 attr (meta_path / _now / _read / _write / build_meta_block /
    _humanize_delta 等) 直接抛 — 不该有人调."""
    raise RuntimeError(f"[DEPRECATED 5/26] {_DEPRECATED_NOTICE} (attr: {name})")
