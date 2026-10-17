"""一起填表 · 跟中央邮筒打交道 (docs/FORM-COLLECT-PLAN.md §6)。

中央只当邮筒: 投一封 / 取走自己的。payload 是这里拼的 JSON 字符串, 中央不解析、取走即清空。
填表的 4 种 kind 必须显式要 (`?kinds=`), 老版 Companion 不带 kinds 就拿不到, 不会被它取走丢掉。
"""
from __future__ import annotations

import base64
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any

from . import skill_publish

FORM_KINDS = ("form_invite", "form_response", "form_remind", "form_close")
MAILBOX_PATH = "/api/room-link/mailbox"
MAX_TTL_SECONDS = 30 * 24 * 3600
MIN_RESPONSE_TTL_SECONDS = 3 * 24 * 3600   # 回填至少留 3 天: 发起人可能出差关机
AFTER_DEADLINE_DAYS = 7
MAX_PAYLOAD_BYTES = 64 * 1024


def me() -> str:
    """当前登录员工的邮箱 (id_token 里的 email / sub, 只读本机, 不验签 —— 验签是中央的事)。"""
    token = skill_publish._read_id_token()
    if not token:
        raise ValueError("还没登录鲶鱼账号 (Companion 里先登录), 填表要用你的身份")
    try:
        part = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (IndexError, ValueError) as e:
        raise ValueError(f"登录凭据读不出身份: {e}") from None
    who = str(claims.get("email") or claims.get("sub") or "").strip().lower()
    if not who:
        raise ValueError("登录凭据里没有邮箱")
    return who


def ttl_seconds(deadline: str | None, *, minimum: int = 0, now: datetime | None = None) -> int:
    """中央留多久: 截止日后 7 天, 夹在 [minimum, 30 天]; 没截止日就 30 天。"""
    now = now or datetime.now(timezone.utc)
    if not deadline:
        return MAX_TTL_SECONDS
    try:
        d = date.fromisoformat(deadline)
    except ValueError:
        return MAX_TTL_SECONDS
    until = datetime(d.year, d.month, d.day, tzinfo=timezone.utc) + timedelta(days=AFTER_DEADLINE_DAYS + 1)
    secs = int((until - now).total_seconds())
    return max(minimum, 600, min(secs, MAX_TTL_SECONDS))


def _client_call(method: str, path: str, *, params: dict | None = None, body: dict | None = None) -> tuple[int, Any]:
    import httpx  # noqa: PLC0415

    token = skill_publish._read_id_token()
    if not token:
        raise ValueError("还没登录鲶鱼账号 (Companion 里先登录), 填表要用你的身份")
    try:
        with httpx.Client(timeout=20.0) as client:
            r = client.request(method, f"{skill_publish.GATEWAY_URL}{path}", params=params, json=body,
                               headers={"Authorization": f"Bearer {token}"})
    except httpx.RequestError as e:
        raise ValueError(f"连不上中央服务: {e}") from None
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {"detail": r.text[:300]}


def post(to: str, kind: str, envelope: dict[str, Any], ttl: int) -> int:
    """投一封。envelope 序列化成 payload; 超 64KB 直接拒 (中央也会拒, 这里先给句人话)。"""
    if kind not in FORM_KINDS:
        raise ValueError(f"不认识的种类 {kind}")
    payload = json.dumps(envelope, ensure_ascii=False)
    if len(payload.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ValueError("内容太长 (超过 64KB), 发不出去: 把长文本精简一下")
    code, data = _client_call("POST", MAILBOX_PATH, body={"to": to, "kind": kind, "payload": payload, "ttl_seconds": ttl})
    if code >= 400:
        detail = data.get("detail") if isinstance(data, dict) else data
        raise ValueError(f"投递给 {to} 失败 ({code}): {detail}")
    return int(data.get("id") or 0)


def take() -> list[dict[str, Any]]:
    """取走发给我的填表邮件。payload 解析失败的丢掉 (中央已清空, 不会重复来)。"""
    code, data = _client_call("GET", MAILBOX_PATH, params={"kinds": ",".join(FORM_KINDS)})
    if code >= 400:
        detail = data.get("detail") if isinstance(data, dict) else data
        raise ValueError(f"取填表邮件失败 ({code}): {detail}")
    out = []
    for m in (data.get("messages") or []) if isinstance(data, dict) else []:
        try:
            env = json.loads(m.get("payload") or "")
        except ValueError:
            continue
        if isinstance(env, dict):
            out.append({"from": str(m.get("from") or "").lower(), "kind": m.get("kind"), "env": env,
                        "created_at": m.get("created_at")})
    return out


def directory(q: str = "", limit: int = 50) -> dict[str, Any]:
    """同事通讯录 (中央 /api/directory, 只有 邮箱 / 姓名 / 部门)。"""
    code, data = _client_call("GET", "/api/directory", params={"q": q, "limit": limit})
    if code >= 400:
        detail = data.get("detail") if isinstance(data, dict) else data
        raise ValueError(f"查同事名单失败 ({code}): {detail}")
    return data if isinstance(data, dict) else {"people": [], "total": 0}
