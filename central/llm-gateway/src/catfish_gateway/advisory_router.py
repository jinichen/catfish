"""Advisory Feed Router — Phase 1 + 2 (6/7 ship).

Spec: docs/ADVISORY-FEED-SPEC.md

# 跟 catfish-central-manifesto 公理 4 关系

中央服务**只 publish, 不 push**. 这个 router 只提供:
  - GET /api/advisory/feed.json — 客户端 pull
  - POST /api/admin/advisory — admin publish (sysadmin only)
  - DELETE /api/admin/advisory/:id — admin revoke (sysadmin only)
  - GET /api/admin/advisory — admin list (admin / sysadmin)

没有 "push to device" 类 API. 客户端拿到 feed 自己决定怎么处理.

# Phase 1 (6/7 早): yaml-based feed

config/advisories.yaml 静态源. 适合 dev / 演示 / 没 PG 的部署.

# Phase 2 (6/7 晚, 本次): PG 持久化 + admin CRUD

- DB schema: alembic/versions/20260607_005_advisories.py
- CRUD: advisory_db.py
- 读优先级:
  1. CATFISH_DB_URL 配了 + PG 有数据 → 用 PG
  2. PG 没数据 / 没配 → fallback yaml (Phase 1 兼容 + seed)
- 写: 必须 PG (无 PG → 拒绝, fail loud)

# Phase 3 (BL): 员工自愿 ack 上报 + 聚合统计

- POST /v1/advisory/ack (员工自愿, 默认关)
- GET /admin/advisory/:id/stats (聚合)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from . import advisory_db
from .auth import User, get_current_user

logger = logging.getLogger("catfish.gateway.advisory")

router = APIRouter(prefix="/api", tags=["advisory"])

# ── yaml seed fallback (Phase 1 兼容) ─────────────────────────


def _advisories_yaml_path() -> Path:
    """yaml 文件路径. env 可覆盖给测试用."""
    if env := os.environ.get("CATFISH_ADVISORIES_YAML"):
        return Path(env)
    here = Path(__file__).parent.parent.parent  # catfish_gateway/.../src/catfish_gateway
    return here / "config" / "advisories.yaml"


def _load_yaml_advisories() -> list[dict[str, Any]]:
    """加载 yaml fallback (Phase 1 兼容)."""
    path = _advisories_yaml_path()
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data.get("advisories", []) or []
    except Exception as e:  # noqa: BLE001
        logger.warning("advisories.yaml 解析失败 (返空 list): %s", e)
        return []


def _is_active(advisory: dict[str, Any], now: datetime) -> bool:
    """advisory 没过期 (expires 未设或 > now)."""
    exp_str = advisory.get("expires")
    if not exp_str:
        return True
    if isinstance(exp_str, datetime):
        return exp_str > now
    try:
        exp = datetime.fromisoformat(str(exp_str).replace("Z", "+00:00"))
        return exp > now
    except Exception:  # noqa: BLE001
        return True


def _compute_etag(advisories: list[dict[str, Any]]) -> str:
    """对 advisory 列表 hash. 同一 set 返同 etag, 客户端 If-None-Match 304."""
    sorted_ids = sorted(a.get("id", "") for a in advisories)
    payload = json.dumps(sorted_ids, sort_keys=True).encode("utf-8")
    return f'W/"{hashlib.sha256(payload).hexdigest()[:16]}"'


def _load_active_advisories() -> list[dict[str, Any]]:
    """读优先级: PG > yaml fallback.

    PG 配了 + 有数据 → 用 PG
    PG 没配 / PG 没数据 → fallback yaml
    """
    if advisory_db.use_pg():
        pg_advisories = advisory_db.pg_list_active()
        if pg_advisories:
            return pg_advisories
        # PG 配了但表空 → 仍 fallback yaml (seed / demo / 平滑迁移)
        logger.info("PG 配了但 advisories 表空, fallback yaml seed")

    # Phase 1 yaml mode: 加载 + 过滤 expired
    yaml_advisories = _load_yaml_advisories()
    now = datetime.now(timezone.utc)
    return [a for a in yaml_advisories if _is_active(a, now)]


# ── 公开 endpoint: GET /api/advisory/feed.json ──────────────


@router.get("/advisory/feed.json")
async def get_feed(
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),  # noqa: ARG001 (鉴权用)
) -> dict[str, Any]:
    """公开 advisory feed — pull-based, no push.

    跟 catfish-central-manifesto 公理 4 一致 — 中央不知道员工设备状态.
    任何 SSO 鉴权过的员工都能拉同一份 feed.

    ## Headers

    - Response: `ETag`, `Cache-Control: public, max-age=3600`
    - Request (optional): `If-None-Match: <etag>` → 304 Not Modified
    """
    active = _load_active_advisories()
    now = datetime.now(timezone.utc)

    etag = _compute_etag(active)
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "public, max-age=3600"

    if_none_match = request.headers.get("if-none-match")
    if if_none_match and if_none_match == etag:
        raise HTTPException(status_code=304)

    return {
        "version": "1.0",
        "generated_at": now.isoformat(),
        "advisories": active,
        "metadata": {
            "total": len(active),
            "active_count": len(active),
            "expired_count": 0,  # 已过滤
        },
    }


# ── admin endpoints ─────────────────────────────────────────


def _require_sysadmin(user: User) -> None:
    """advisory publish/revoke 只能 sysadmin (跟 catfish-tools / quota 同级权限).

    admin / manager / employee 拒绝. 跟 manifesto 公理 4 一致 — 哪怕是普通
    admin 也不能随便 publish advisory (这是给所有员工看的, 类似 release manager).
    """
    if user.role != "sysadmin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"role={user.role} 不能 publish/revoke advisory (需 sysadmin)",
        )


_ADVISORY_ID_PATTERN = re.compile(r"^CATFISH-ADV-\d{4}-\d{3,}$")
_VALID_SEVERITY = {"critical", "high", "medium", "low", "info"}
_VALID_CATEGORY = {
    "skill_vulnerability",
    "mcp_vulnerability",
    "catfish_update",
    "policy_recommendation",
    "external_status",
    "deprecation_notice",
}


class AdvisoryTargetReq(BaseModel):
    skill: str | None = None
    skill_version_pattern: str | None = None
    mcp_name: str | None = None
    catfish_version_pattern: str | None = None


class RemediationActionReq(BaseModel):
    label: str
    kind: str  # uninstall_skill | install_skill | uninstall_and_install_skill | update_catfish | open_settings | open_url
    params: dict[str, Any] | None = None


class AdvisoryPublishReq(BaseModel):
    id: str = Field(..., description="格式 CATFISH-ADV-YYYY-NNN")
    severity: str
    category: str
    title: str = Field(..., max_length=120)
    description: str | None = None
    recommendation: str | None = Field(None, max_length=500)
    target: AdvisoryTargetReq | None = None
    references: list[str] | None = None
    tags: list[str] | None = None
    remediation_actions: list[RemediationActionReq] | None = None
    published: str = Field(..., description="ISO 8601")
    expires: str | None = Field(None, description="ISO 8601, 可空")


@router.post("/admin/advisory", status_code=201)
async def publish_advisory(
    req: AdvisoryPublishReq,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """publish 新 advisory. sysadmin only.

    跟 manifesto 公理 4 一致 — 这是 admin **publish** API, 不是 push. 客户端
    自己 pull 拿到 advisory 后, **员工自己决定**怎么处理.
    """
    _require_sysadmin(user)

    # 校验字段
    if not _ADVISORY_ID_PATTERN.match(req.id):
        raise HTTPException(400, f"id 格式错: 需 CATFISH-ADV-YYYY-NNN, 实际 {req.id}")
    if req.severity not in _VALID_SEVERITY:
        raise HTTPException(400, f"severity={req.severity} 不在 {_VALID_SEVERITY}")
    if req.category not in _VALID_CATEGORY:
        raise HTTPException(400, f"category={req.category} 不在 {_VALID_CATEGORY}")

    advisory = {
        "id": req.id,
        "severity": req.severity,
        "category": req.category,
        "title": req.title,
        "description": req.description,
        "recommendation": req.recommendation,
        "published": req.published,
        "expires": req.expires,
    }
    if req.target:
        advisory["target"] = req.target.model_dump(exclude_none=True)
    if req.references:
        advisory["references"] = req.references
    if req.tags:
        advisory["tags"] = req.tags
    if req.remediation_actions:
        advisory["remediation_actions"] = [
            a.model_dump(exclude_none=True) for a in req.remediation_actions
        ]

    try:
        advisory_db.pg_insert(advisory, published_by=user.sub)
    except RuntimeError as e:
        # PG 未配
        raise HTTPException(503, f"中央服务未配 DB: {e}") from e
    except Exception as e:
        # PG 错 (e.g. id 重复, constraint 违反)
        msg = str(e)
        if "duplicate key" in msg.lower() or "unique constraint" in msg.lower():
            raise HTTPException(409, f"advisory id 已存在: {req.id}") from e
        logger.exception("publish advisory 失败: id=%s", req.id)
        raise HTTPException(500, f"内部错误: {e}") from e

    logger.info(
        "advisory published id=%s severity=%s by=%s",
        req.id, req.severity, user.sub,
    )
    return {"ok": True, "id": req.id}


@router.delete("/admin/advisory/{advisory_id}")
async def revoke_advisory(
    advisory_id: str,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """revoke advisory — 改 revoked_at, 不真删 (审计历史保留). sysadmin only."""
    _require_sysadmin(user)

    try:
        ok = advisory_db.pg_revoke(advisory_id, revoked_by=user.sub)
    except RuntimeError as e:
        raise HTTPException(503, f"中央服务未配 DB: {e}") from e

    if not ok:
        raise HTTPException(404, f"advisory 不存在或已 revoke: {advisory_id}")

    logger.info("advisory revoked id=%s by=%s", advisory_id, user.sub)
    return {"ok": True, "id": advisory_id}


@router.get("/admin/advisory")
async def list_advisories_admin(
    include_revoked: bool = True,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """admin 看全部 advisory 含 revoked. admin / sysadmin 都行 (audit 用)."""
    if not user.is_admin():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"role={user.role} 不能查 advisory 列表 (需 admin / sysadmin)",
        )

    advisories = advisory_db.pg_list_all(include_revoked=include_revoked)
    return {
        "advisories": advisories,
        "count": len(advisories),
    }


__all__ = ["router"]
