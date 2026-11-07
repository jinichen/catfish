"""公司级设置 (11/6, docs/GROUP-CHAT-PLAN.md §4.2) —— 中央下发给员工 Companion 的那几样。

  room_retention_days  群聊记录在员工电脑上最多保留几天 (空 = Companion 用默认 180 天)
  org_mail_domains     本单位邮箱域名 (11/07, 可多个, 如 chinatelecom.cn / ffcs.cn) —— 员工电脑上钓鱼识别判
                       「是不是本单位发来的」以它为准 (空 = 各自按员工自己的工作邮箱算)。公共邮箱 (gmail.com /
                       qq.com …) 不收: 填进去等于把全世界的那个邮箱都当成本单位, 冒充「信息安全中心」就放过了。

员工 Companion 定时来取 (任何登录员工都能读); 只有管理员能改。存的是设置, 不是员工内容。

    GET /api/org/policy        → {room_retention_days, org_mail_domains, updated_at, updated_by}
    PUT /api/admin/org/policy  {room_retention_days?, org_mail_domains?}   (admin; 只改传了的那项)
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .auth import User, get_current_user

logger = logging.getLogger("catfish.gateway.org_policy")

router = APIRouter(prefix="/api", tags=["org-policy"])

RETENTION_MIN, RETENTION_MAX = 7, 3650
MAX_DOMAINS = 50
_DOMAIN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")

# 公共邮箱: 谁都能注册, 不能当「本单位」。跟 Companion 的 src-tauri/src/services/email_own_domains.rs PUBLIC_MAIL 同一份。
PUBLIC_MAIL = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com", "yahoo.com", "yahoo.com.cn",
    "icloud.com", "me.com", "mac.com", "aol.com", "proton.me", "protonmail.com", "gmx.com", "mail.com", "yandex.com",
    "qq.com", "foxmail.com", "163.com", "126.com", "yeah.net", "sina.com", "sina.cn", "sohu.com", "aliyun.com",
    "139.com", "189.cn", "wo.cn", "tom.com", "21cn.com", "263.net",
})

KEYS = ("room_retention_days", "org_mail_domains")


def normalize_domains(raw: list[str]) -> list[str]:
    """「@ChinaTelecom.cn」「 ffcs.cn 」→ ["chinatelecom.cn", "ffcs.cn"]; 去重保序。格式不对 / 公共邮箱报错。"""
    out: list[str] = []
    for item in raw:
        d = str(item or "").strip().lower().lstrip("@").rstrip(".")
        if not d:
            continue
        if not _DOMAIN.fullmatch(d):
            raise ValueError(f"「{item}」不像邮箱域名 (要像 chinatelecom.cn 这样)")
        if d in PUBLIC_MAIL:
            raise ValueError(f"{d} 是公共邮箱, 谁都能注册, 不能当本单位域名")
        if d not in out:
            out.append(d)
    if len(out) > MAX_DOMAINS:
        raise ValueError(f"最多 {MAX_DOMAINS} 个域名")
    return out


def _pg():
    url = os.environ.get("CATFISH_DB_URL", "").strip()
    if not url:
        raise HTTPException(503, "公司设置需要 PostgreSQL (CATFISH_DB_URL 未配)")
    import psycopg  # noqa: PLC0415
    return psycopg.connect(url)


def read_policy() -> dict[str, Any]:
    with _pg() as conn, conn.cursor() as cur:
        cur.execute("SELECT key, value, updated_at, updated_by FROM org_policy WHERE key = ANY(%s)", (list(KEYS),))
        rows = cur.fetchall()
    out: dict[str, Any] = {"room_retention_days": None, "org_mail_domains": [], "updated_at": None, "updated_by": None}
    latest = None
    for key, value, at, by in rows:
        if key == "room_retention_days":
            out[key] = int(value)
        elif key == "org_mail_domains":
            try:
                out[key] = [str(x) for x in json.loads(value)]
            except (ValueError, TypeError):
                out[key] = []
        if latest is None or at > latest:
            latest, out["updated_by"] = at, by
    out["updated_at"] = latest.isoformat() if latest else None
    return out


def write_policy(changes: dict[str, Any], who: str) -> None:
    """changes 里有的才改; 值为 None / 空列表 = 删掉这项 (回到默认)。"""
    with _pg() as conn, conn.cursor() as cur:
        for key, value in changes.items():
            if value is None or value == []:
                cur.execute("DELETE FROM org_policy WHERE key = %s", (key,))
                continue
            stored = json.dumps(value, ensure_ascii=False) if key == "org_mail_domains" else str(value)
            cur.execute(
                """
                INSERT INTO org_policy (key, value, updated_by) VALUES (%s, %s, %s)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now(), updated_by = EXCLUDED.updated_by
                """,
                (key, stored, who),
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
    org_mail_domains: list[str] | None = Field(default=None, max_length=MAX_DOMAINS * 2)


@router.put("/admin/org/policy")
async def put_policy(req: PolicyPutReq, user: User = Depends(get_current_user)) -> dict[str, Any]:
    if not user.is_admin():
        raise HTTPException(403, f"role={user.role} 不能改公司设置 (admin only)")
    changes = {k: getattr(req, k) for k in KEYS if k in req.model_fields_set}
    if not changes:
        raise HTTPException(422, "没有要改的设置")
    if changes.get("org_mail_domains") is not None:
        try:   # 在这里校验而不是 pydantic validator: 管理页显示的就是这一句, 不带「body.xxx: Value error」前缀
            changes["org_mail_domains"] = normalize_domains(changes["org_mail_domains"])
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
    try:
        write_policy(changes, user.sub)
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("写公司设置失败: %s", e)
        raise HTTPException(503, "公司设置暂不可用") from None
    logger.info("公司设置: %s (by %s)", changes, user.sub)
    return read_policy()
