"""token 数据模型 / 存盘 / JWT 解 / refresh grant。

8/15 从 catfish.py 搬出来 (1875 行过了 CLAUDE.md §1 的 800 红线)。

# 这个文件的边界

装的是"一份 token 的生命周期": 造 (TokenStore) → 存盘 (save/load/delete) →
看里面是谁 (_decode_jwt_payload) → 快过期了续一下 (_do_refresh)。

`_do_refresh` 放这儿而不是放命令层, 是因为它只依赖 TokenStore 和
_decode_jwt_payload 两个本模块的东西 —— 放命令层的话这两个得反向 import。

# ⚠ 关于 monkeypatch

test_catfish.py 有两条测试 patch `catfish._do_refresh` 然后调 `catfish.cmd_token`。
那是**成立的**: cmd_token 还在 catfish.py 里, 它查 `_do_refresh` 查的是
catfish.py 的 globals, 也就是下面 re-export 过去的那个绑定。

但如果哪天 cmd_token 也搬走了, 那条 patch 就会静默失效 —— 同一天
catfish_proxy.py 上就栽过这一下 (见那个文件的 docstring)。判据是:
**被 patch 的名字必须和调用它的那个函数同模块**。
tests/test_split_layering.py 有守卫。
"""
from __future__ import annotations

import json
import os
import time
# ⚠ `import urllib.error` 是这次拆分**新加**的一行, 不是原样搬过来的。
#
# 原来 catfish.py 顶部只 import 了 urllib.parse / urllib.request, 却在 10 处
# 写 `except urllib.error.HTTPError`。能跑是因为 urllib.request 自己 import 了
# urllib.error, 于是 `urllib` 这个包对象上恰好挂着 `.error` 属性 —— 靠的是
# 上游的实现细节, 不是自己声明的依赖。
#
# 这一行是补上真实依赖, 不改行为 (urllib.error 本来就已经在 sys.modules 里)。
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional

from catfish_config import EXPIRY_BUFFER_SECS, _token_path, logger

# ─── token 数据模型 + 存盘 ────────────────────────────────


@dataclass
class TokenStore:
    """~/.catfish/auth/token.json 的 schema. 加字段时不破坏老格式."""

    access_token: str
    expires_at: int  # unix seconds
    issuer: str
    client_id: str
    scope: str = ""
    id_token: Optional[str] = None        # authorization_code 才有
    refresh_token: Optional[str] = None   # Phase 1C (#79) 加
    user_email: str = ""                   # 从 id_token 解出来 (有的话)
    user_sub: str = ""                     # access_token 的 sub claim
    saved_at: int = 0

    def is_expired(self, buffer: int = EXPIRY_BUFFER_SECS) -> bool:
        """快过期 (buffer 秒内) 视为过期, 让 caller 提前 refresh."""
        return time.time() >= (self.expires_at - buffer)

    def expires_in_secs(self) -> int:
        return max(0, int(self.expires_at - time.time()))

    def to_dict(self) -> dict:
        d = {
            "access_token": self.access_token,
            "expires_at": self.expires_at,
            "issuer": self.issuer,
            "client_id": self.client_id,
            "scope": self.scope,
            "user_email": self.user_email,
            "user_sub": self.user_sub,
            "saved_at": self.saved_at,
        }
        if self.id_token:
            d["id_token"] = self.id_token
        if self.refresh_token:
            d["refresh_token"] = self.refresh_token
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "TokenStore":
        return cls(
            access_token=d["access_token"],
            expires_at=int(d.get("expires_at", 0)),
            issuer=d.get("issuer", ""),
            client_id=d.get("client_id", ""),
            scope=d.get("scope", ""),
            id_token=d.get("id_token"),
            refresh_token=d.get("refresh_token"),
            user_email=d.get("user_email", ""),
            user_sub=d.get("user_sub", ""),
            saved_at=int(d.get("saved_at", 0)),
        )


def save_token(store: TokenStore) -> None:
    """写 ~/.catfish/auth/token.json. chmod 600 防别的用户偷."""
    p = _token_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    # 先写临时文件再 rename — 防写到一半 crash 留半截
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(store.to_dict(), indent=2, ensure_ascii=False))
    os.chmod(tmp, 0o600)
    tmp.replace(p)


def load_token() -> Optional[TokenStore]:
    """读 ~/.catfish/auth/token.json. 不存在 / 损坏返 None."""
    p = _token_path()
    if not p.exists():
        return None
    try:
        return TokenStore.from_dict(json.loads(p.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        logger.warning("token.json 损坏 (%s), 视为未登录", e)
        return None


def delete_token() -> bool:
    """logout: 删 token 文件. 返 True 如果删了, False 如果本来就没."""
    p = _token_path()
    if not p.exists():
        return False
    p.unlink()
    return True


# ─── JWT 解 (不验签, 只解 payload 拿 claim) ──────────────


def _decode_jwt_payload(token: str) -> dict:
    """从 JWT token 字符串解 payload (中间段). 不验签 — 只拿 claim 看用户身份.

    验签留给 catfish-gateway 做 (它有 jwks_uri). 我们这里只是显示用.
    """
    import base64
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    try:
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception:
        return {}



def _do_refresh(store: TokenStore) -> TokenStore:
    """调 catfish-identity /token grant_type=refresh_token, 拿新 access + 新 refresh.

    raise RuntimeError 如果 refresh 失败 (token 已 revoke / 过期 / network).
    """
    if not store.refresh_token:
        raise RuntimeError("当前 token 没 refresh_token, 必须重新登录")

    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": store.refresh_token,
        "client_id": store.client_id,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{store.issuer}/token",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_str = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"refresh 失败 (HTTP {e.code}): {body_str}") from e

    new_access = data.get("access_token")
    if not new_access:
        raise RuntimeError(f"identity 没返新 access_token: {data}")

    payload = _decode_jwt_payload(new_access)
    expires_at = int(payload.get("exp") or (time.time() + data.get("expires_in", 3600)))

    return TokenStore(
        access_token=new_access,
        # access_token RFC 9068 后含 user claims, 复用旧 store 的 email/sub 也对
        id_token=store.id_token,
        refresh_token=data.get("refresh_token", store.refresh_token),  # rotation 后是新的
        expires_at=expires_at,
        issuer=store.issuer,
        client_id=store.client_id,
        scope=data.get("scope", store.scope),
        user_email=store.user_email or payload.get("email", ""),
        user_sub=store.user_sub or payload.get("sub", ""),
        saved_at=int(time.time()),
    )


def _fmt_expires(ts: int) -> str:
    """格式化 unix 时间戳成可读"""
    import datetime
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
