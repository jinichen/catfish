"""BL-INTERNAL-MODEL-FOLLOW-USER (5/17 鸿波拍板): 员工最近用的 model 解析器.

# 规则

员工选哪个 model, 所有 LLM 调用 (含 chat 内 summarize / compress, 后台
proactive starter / a2a federation / admin facts 等) 都用同款. 解析方式:

  1. 给 session_id → 查 sessions.model
  2. 给 user_email → 查 sessions 表 last_active (started_at DESC) 的 model
  3. 拿不到 → None, caller 跳过 (不 fallback)

# 跟 pick_internal_models_ordered 的关系

本 helper 完全替代 'pick_internal_model("summarizer" / etc) 选 model' 的逻辑.
internal_models.py 保留只给 captcha_ocr (必须 vision) 这种特殊 use case 用.

# Sources

- state.db schema: catfish/edge/companion-app/src-tauri/src/commands/session_write.rs
  CREATE TABLE sessions (..., model TEXT, ..., started_at REAL, ...)
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger("catfish.gateway.user_model_resolver")

STATE_DB = Path.home() / ".hermes" / "state.db"


def get_session_model(session_id: str) -> str | None:
    """读 sessions.model. 拿不到返 None."""
    if not STATE_DB.exists():
        return None
    try:
        conn = sqlite3.connect(
            f"file:{STATE_DB}?mode=ro", uri=True, timeout=1.0
        )
        row = conn.execute(
            "SELECT model FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        conn.close()
        if row and row[0]:
            return str(row[0])
    except sqlite3.Error as e:
        logger.debug("get_session_model: sqlite 错 (sessions 表 schema 老?): %s", e)
    return None


def get_user_last_session_model(user_email: str) -> str | None:
    """读员工最近 1 个 session 的 model.

    给 proactive / facts / a2a 等 **后台任务** 用 — 它们没员工 current chat,
    但仍有员工身份, 用最近 session 的 model 保证体验一致.

    fallback 顺序:
      1. 最近 started_at 的 session, model 非空
      2. 没 session / 没 model 字段 → None
    """
    if not STATE_DB.exists() or not user_email:
        return None
    try:
        conn = sqlite3.connect(
            f"file:{STATE_DB}?mode=ro", uri=True, timeout=1.0
        )
        # sessions 表 source 字段含 user_email (Companion session_write 5/15 起写)
        # 老 schema 没 source / user_email → SQL 错, caller 跳过
        row = conn.execute(
            """
            SELECT model FROM sessions
            WHERE source = ? AND model IS NOT NULL AND model <> ''
            ORDER BY started_at DESC NULLS LAST
            LIMIT 1
            """,
            (user_email,),
        ).fetchone()
        conn.close()
        if row and row[0]:
            return str(row[0])
    except sqlite3.Error as e:
        logger.debug(
            "get_user_last_session_model: sqlite 错 (sessions schema 老 / source 字段缺): %s", e,
        )
    return None


def resolve_model_obj(model_name: str | None, config) -> "ModelConfig | None":  # type: ignore[name-defined]
    """从 model name 拿 catalog 里的 ModelConfig (要可达 + chat).

    None / 不在 catalog / 不可达 → 返 None. caller 跳过 LLM 调用.
    """
    if not model_name:
        return None
    for m in config.models:
        if m.name == model_name and m.mode == "chat" and m.upstream.is_available:
            return m
    return None


__all__ = [
    "get_session_model",
    "get_user_last_session_model",
    "resolve_model_obj",
]
