"""公司级设置 (11/6, docs/GROUP-CHAT-PLAN.md §4.2) —— 中央下发给员工 Companion 的那几个数。

先只有一个: `room_retention_days` 群聊记录在员工电脑上最多保留几天 (空 = Companion 用默认 180 天)。
员工 Companion 每次同步顺带取 (任何登录员工都能读); 只有管理员能改。存的是设置, 不是员工内容。

    GET /api/org/policy                         → {room_retention_days: int | null, updated_at, updated_by}
    PUT /api/admin/org/policy  {room_retention_days: int | null}   (admin)
"""
from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .auth import User, get_current_user

logger = logging.getLogger("catfish.gateway.org_policy")

router = APIRouter(prefix="/api", tags=["org-policy"])

RETENTION_MIN, RETENTION_MAX = 7, 3650


def _pg():
    url = os.environ.get("CATFISH_DB_URL", "").strip()
    if not url:
        raise HTTPException(503, "公司设置需要 PostgreSQL (CATFISH_DB_URL 未配)")
    import psycopg  # noqa: PLC0415
    return psycopg.connect(url)


def read_policy() -> dict[str, Any]:
    with _pg() as conn, conn.cursor() as cur:
        cur.execute("SELECT value, updated_at, updated_by FROM org_policy WHERE key = 'room_retention_days'")
        r = cur.fetchone()
    if not r:
        return {"room_retention_days": None, "updated_at": None, "updated_by": None}
    return {"room_retention_days": int(r[0]), "updated_at": r[1].isoformat(), "updated_by": r[2]}


def write_policy(days: int | None, who: str) -> None:
    with _pg() as conn, conn.cursor() as cur:
        if days is None:
            cur.execute("DELETE FROM org_policy WHERE key = 'room_retention_days'")
        else:
            cur.execute(
                """
                INSERT INTO org_policy (key, value, updated_by) VALUES ('room_retention_days', %s, %s)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now(), updated_by = EXCLUDED.updated_by
                """,
                (str(days), who),
            )
        conn.commit()


@router.get("/org/policy")
async def get_policy(user: User = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return read_policy()
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("读公司设置失败: %s", e)
        raise HTTPException(503, "公司设置暂不可用") from None


class PolicyPutReq(BaseModel):
    room_retention_days: int | None = Field(default=None, ge=RETENTION_MIN, le=RETENTION_MAX)


@router.put("/admin/org/policy")
async def put_policy(req: PolicyPutReq, user: User = Depends(get_current_user)) -> dict[str, Any]:
    if not user.is_admin():
        raise HTTPException(403, f"role={user.role} 不能改公司设置 (admin only)")
    try:
        write_policy(req.room_retention_days, user.sub)
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("写公司设置失败: %s", e)
        raise HTTPException(503, "公司设置暂不可用") from None
    logger.info("公司设置: 群聊记录保留天数 = %s (by %s)", req.room_retention_days, user.sub)
    return read_policy()
