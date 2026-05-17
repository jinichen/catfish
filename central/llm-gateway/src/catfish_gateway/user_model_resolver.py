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


def get_user_last_session_model(user_email: str | None = None) -> str | None:
    """读最近 1 个 session 的 model.

    给 proactive / facts / a2a / memory_distill 等 **后台任务** 用 — 它们没员工
    current chat, 但仍有员工身份, 用最近 session 的 model 保证体验一致.

    BL-RESOLVER-SOURCE-FIX (5/17 鸿波实盘暴露):
      老版本 SQL 用 `WHERE source = ?` 匹配 user_email, 但 Companion
      session_write.rs:127 写的是字面量 `source='companion'` (不是员工 email).
      这 SQL 在生产永远 match 不上 → memory_distill 一直 skip → 7th use case
      实际没工作. 修: 镜像 identity.rs:124 同模式, 不过滤 source, 直接拿最近
      started_at 的 session.

      为啥安全: state.db 是 **per-Mac per-user** 文件, 单机只有一个员工的
      session 写进来. user_email 参数现在保留只为 log 上下文, 不用于查询.

    fallback 顺序:
      1. 最近 started_at 的 session, model 非空
      2. 没 session / model 字段空 → None
    """
    if not STATE_DB.exists():
        return None
    try:
        conn = sqlite3.connect(
            f"file:{STATE_DB}?mode=ro", uri=True, timeout=1.0
        )
        # 跟 Companion identity.rs:124 同模式: ORDER BY started_at DESC LIMIT 1.
        # 不过滤 source — Companion 写死 'companion', 跟 user_email 无关.
        # 加 model NOT NULL 过滤防早期未写 model 的老 session.
        row = conn.execute(
            """
            SELECT model FROM sessions
            WHERE model IS NOT NULL AND model <> ''
            ORDER BY started_at DESC
            LIMIT 1
            """,
        ).fetchone()
        conn.close()
        if row and row[0]:
            logger.debug(
                "get_user_last_session_model (user=%s): %s",
                user_email or "<no-email>", row[0],
            )
            return str(row[0])
    except sqlite3.Error as e:
        logger.debug(
            "get_user_last_session_model: sqlite 错 (sessions schema 老 / model 字段缺): %s",
            e,
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
