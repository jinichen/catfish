"""Advisory Feed Router — Phase 1 (6/7 ship).

Spec: docs/ADVISORY-FEED-SPEC.md

# 跟 catfish-central-manifesto 公理 4 关系

中央服务**只 publish, 不 push**. 这个 router 只提供一个 `GET /advisory/feed.json`
endpoint, 客户端自己 pull 决定怎么处理. 没有 "push to device" 类 API.

# Phase 1 范围

- 从 `config/advisories.yaml` 读静态 advisory 数据
- 公开 GET endpoint (有效 SSO token 即可拉)
- ETag-based caching (1h max-age, etag 304)

# Phase 2 (BL) 加

- DB 持久化 (advisories 表)
- Admin publish UI (POST /admin/advisory)
- 员工自愿 ack 上报 (POST /v1/advisory/ack)
- 聚合统计 dashboard (GET /admin/advisory/:id/stats)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from .auth import User, get_current_user

logger = logging.getLogger("catfish.gateway.advisory")

router = APIRouter(prefix="/api/advisory", tags=["advisory"])


def _advisories_yaml_path() -> Path:
    """yaml 文件路径. env 可覆盖给测试用."""
    if env := os.environ.get("CATFISH_ADVISORIES_YAML"):
        return Path(env)
    # 跟 facts_router / models.yaml 同模式: central/llm-gateway/config/
    here = Path(__file__).parent.parent.parent  # catfish_gateway/.../src/catfish_gateway
    return here / "config" / "advisories.yaml"


def _load_advisories() -> list[dict[str, Any]]:
    """加载 advisories.yaml. 文件不存在 / 解析失败 → 空 list (不 panic, 不阻塞 chat)."""
    path = _advisories_yaml_path()
    if not path.exists():
        logger.warning("advisories.yaml 不存在: %s", path)
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
    try:
        exp = datetime.fromisoformat(exp_str.replace("Z", "+00:00"))
        return exp > now
    except Exception:  # noqa: BLE001
        return True  # 解析失败 → 当 active (保守)


def _compute_etag(advisories: list[dict[str, Any]]) -> str:
    """对 advisory 列表 hash. 同一 set 返同 etag, 客户端 If-None-Match 304."""
    # 排序 id 让顺序无关
    sorted_ids = sorted(a.get("id", "") for a in advisories)
    payload = json.dumps(sorted_ids, sort_keys=True).encode("utf-8")
    return f'W/"{hashlib.sha256(payload).hexdigest()[:16]}"'


@router.get("/feed.json")
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
    all_advisories = _load_advisories()
    now = datetime.now(timezone.utc)
    active = [a for a in all_advisories if _is_active(a, now)]

    etag = _compute_etag(active)
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "public, max-age=3600"

    # 304 fast path
    if_none_match = request.headers.get("if-none-match")
    if if_none_match and if_none_match == etag:
        # 注意: FastAPI 不太支持直接返 304 + body, 走 HTTPException 模式
        raise HTTPException(status_code=304)

    return {
        "version": "1.0",
        "generated_at": now.isoformat(),
        "advisories": active,
        "metadata": {
            "total": len(all_advisories),
            "active_count": len(active),
            "expired_count": len(all_advisories) - len(active),
        },
    }


__all__ = ["router"]
