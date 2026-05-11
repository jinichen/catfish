"""BL-Q3-ARCHIVE — HTTP 路由 (5/11).

/api/tool-archives/read   POST  LLM 调 catfish_read_tool_archive 走这条
/api/tool-archives/{ref}  GET   admin / 自查 拿全量 (鉴权)
/api/tool-archives        GET   列我的 archive (admin UI P1 用)
/api/tool-archives/gc     POST  手动 GC (sysadmin only)

LLM 调路径: Companion 收到 catfish_read_tool_archive tool_call → 看 tool-bridge
没本地 handler → 走 gateway loopback POST /api/tool-archives/read → 这里处理.

跟 catfish_save_skill / catfish_user_profile_propose 走同款 loopback 模式.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from ..auth import User, get_current_user
from . import db, reader

logger = logging.getLogger("catfish.gateway.tool_archive.router")

router = APIRouter(prefix="/api/tool-archives", tags=["tool-archives"])


class ReadArchiveReq(BaseModel):
    ref: str = Field(..., description="archive 引用, 16 字 sha256")
    line_range: str | None = Field(
        None, description="行号范围, 'N-M' 或单行 'N' (1-indexed)"
    )
    grep: str | None = Field(
        None, description="关键字子串, 召回匹配行 ± 5 行上下文"
    )
    max_bytes: int | None = Field(
        None, description="返回上限, 默认 8000, 硬上限 32K"
    )


class ReadArchiveResp(BaseModel):
    ref: str
    content: str
    total_lines: int
    total_bytes: int
    tool_name: str | None = None
    summary: str | None = None


def _check_access(row: dict, user: User) -> None:
    """archive 只能 owner 读. admin/sysadmin 全公司可读 (审计)."""
    if user.is_admin():  # admin / sysadmin
        return
    if row.get("user_email") != user.email:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="不能读别人的 archive",
        )


@router.post("/read", response_model=ReadArchiveResp)
async def read_archive(
    req: ReadArchiveReq,
    user: User = Depends(get_current_user),
):
    """LLM 调 catfish_read_tool_archive 走这条.

    返按 grep / line_range / 全文 的内容片段.
    """
    row = db.get_archive(req.ref)
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"archive {req.ref} 不存在或已过期 (14 天保留)",
        )
    _check_access(row, user)

    body = reader.read_archive_content(
        content=row["content"],
        line_range=req.line_range,
        grep=req.grep,
        max_bytes=req.max_bytes,
    )

    logger.info(
        "read_archive ref=%s user=%s mode=%s out_bytes=%d",
        req.ref, user.email,
        "grep" if req.grep else ("line_range" if req.line_range else "full"),
        len(body.encode("utf-8")),
    )

    return ReadArchiveResp(
        ref=row["ref"],
        content=body,
        total_lines=int(row["lines"]),
        total_bytes=int(row["content_bytes"]),
        tool_name=row.get("tool_name"),
        summary=row.get("summary"),
    )


@router.get("/{ref}")
async def get_archive_meta(
    ref: str,
    user: User = Depends(get_current_user),
):
    """admin UI / 自查拿 archive 完整元信息 + 全文 (不切片).

    内部 debug 用. LLM 不该调这个 (拉全文撑 context), LLM 该调 /read.
    """
    row = db.get_archive(ref)
    if not row:
        raise HTTPException(404, f"archive {ref} 不存在或已过期")
    _check_access(row, user)
    # 不返 content (太大), 只返元信息 + 摘要
    safe = {k: v for k, v in row.items() if k != "content"}
    safe["content_preview"] = row["content"][:500]
    return safe


@router.post("/gc")
async def trigger_gc(user: User = Depends(get_current_user)):
    """手动触发 GC (sysadmin only). 正常每天 cron 自动跑."""
    if user.role != "sysadmin":
        raise HTTPException(403, "GC 仅 sysadmin")
    n = db.gc_expired()
    return {"deleted": n}


__all__ = ["router"]
