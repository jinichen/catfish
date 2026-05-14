#!/usr/bin/env python3
"""catfish — 鲶鱼员工自助登录 CLI (BL-CATFISH-LOGIN, 5/14 凌晨 ship).

# 目的

新机部署时不再需要 IT 手粘 token. 员工首次开 catfish 自己浏览器登录公司
SSO (catfish-identity), token 自动存 + 自动同步到 hermes config.

学 hermes 的 "Qwen OAuth (reuses local Qwen CLI login)" 模式 — 我们写一个
独立 CLI 管登录, hermes 通过 Custom endpoint 复用我们维护的 token.

# 用法

  catfish login                  # 浏览器登录, 拿 token, 写盘
  catfish status                 # 看登录状态 (是谁 / 还有多久过期)
  catfish logout                 # 清 token
  catfish token                  # 输出当前 access_token (供 shell 用, e.g. curl Bearer)
  catfish refresh                # 续 token (现在重做 login, 5/16+ 加 refresh_token 后真 refresh)

# 跑法 (无包装, 单文件直接跑)

  python3 ~/person_task/catfish/edge/catfish-cli/catfish.py login

  推荐加 alias 到 ~/.zshrc:
    alias catfish='python3 ~/person_task/catfish/edge/catfish-cli/catfish.py'

# 文件布局

  ~/.catfish/auth/token.json      catfish 自己的 token store (本 CLI 写)
  ~/.hermes/config.yaml           hermes 配置 (本 CLI 同步 api_key 进去)

# 设计决策

- 用 OAuth 2.0 authorization_code flow (RFC 6749 §4.1) + 浏览器 (RFC 8252 §7.3 native app)
- redirect_uri = http://localhost:RANDOM_PORT/callback (loopback 防中间人)
- state CSRF 防御 (随机 32 字节)
- **MVP 不上 PKCE** — catfish-identity 还没支持. 5/16 加上 PKCE + refresh_token 一起.
- **不存 client_secret** — public client (RFC 6749 §2.1). 装机包不应该含任何 secret.
- token 存盘格式: JSON, chmod 600. 含 expires_at 让 status / token 命令快判.

# 配套要做 (跟其他 task 协同)

- BL-IDENTITY-REFRESH-TOKEN (#79): catfish-identity 加 refresh_token grant
- 5/16 加 PKCE 支持后这文件加 PKCE 实现 (~30 行 hashlib + secrets)
- 5/22+ install.sh (#80) 把这文件加到装机流程
"""
from __future__ import annotations

import argparse
import http.server
import json
import logging
import os
import secrets
import socket
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger("catfish.cli")

# ─── 配置 ──────────────────────────────────────────────────

DEFAULT_IDENTITY_URL = "http://localhost:8998"
DEFAULT_GATEWAY_URL = "http://localhost:8999"
DEFAULT_CLIENT_ID = "hermes-cli"
DEFAULT_SCOPES = "openid email profile chat.completions audit.write tools.invoke skills.run"
HERMES_PROVIDER_NAME = "Local (localhost:8999)"  # 跟用户 hermes setup 时填的 display name 对齐

#: token 过期前多少秒视为"快过期" (要重新拿)
EXPIRY_BUFFER_SECS = 60

#: 本地 callback 监听超时 (秒)
CALLBACK_TIMEOUT_SECS = 300


def _auth_dir() -> Path:
    """token 存哪. env CATFISH_AUTH_DIR 覆盖 (测试用)."""
    if env := os.environ.get("CATFISH_AUTH_DIR"):
        return Path(env).expanduser()
    return Path.home() / ".catfish" / "auth"


def _token_path() -> Path:
    return _auth_dir() / "token.json"


def _hermes_config_path() -> Path:
    if env := os.environ.get("HERMES_CONFIG"):
        return Path(env).expanduser()
    return Path.home() / ".hermes" / "config.yaml"


def _identity_url() -> str:
    return os.environ.get("CATFISH_IDENTITY_URL", DEFAULT_IDENTITY_URL).rstrip("/")


def _client_id() -> str:
    return os.environ.get("CATFISH_CLIENT_ID", DEFAULT_CLIENT_ID)


def _scopes() -> str:
    return os.environ.get("CATFISH_SCOPES", DEFAULT_SCOPES)


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


# ─── OAuth flow ────────────────────────────────────────────


def _find_free_port() -> int:
    """找一个空闲 localhost 端口给 OAuth callback 用."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """临时 HTTP server 接 OAuth callback. 只处理 GET /callback."""

    captured_code: Optional[str] = None
    captured_state: Optional[str] = None
    captured_error: Optional[str] = None

    def log_message(self, format, *args):
        # 静音 stderr 标准 log (BaseHTTPRequestHandler 默认会 print 每个 request)
        pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return
        qs = urllib.parse.parse_qs(parsed.query)
        type(self).captured_state = qs.get("state", [None])[0]
        if "error" in qs:
            type(self).captured_error = qs.get("error_description", qs.get("error", ["unknown"]))[0]
            self._render("登录失败", f"<p style='color:#cc0000'>{type(self).captured_error}</p>")
        elif "code" in qs:
            type(self).captured_code = qs["code"][0]
            self._render("登录成功 ✓", "<p>已拿到授权 code, 你现在可以关闭这个窗口回 terminal.</p>")
        else:
            self._render("登录失败", "<p style='color:#cc0000'>callback URL 缺 code 和 error 字段.</p>")

    def _render(self, title: str, body_html: str) -> None:
        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>{title} · 鲶鱼登录</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'PingFang SC', sans-serif;
           background: #fbfbfd; color: #1d1d1f; margin: 0;
           display: flex; min-height: 100vh; align-items: center; justify-content: center; }}
    .card {{ background: #fff; border: 1px solid #e5e5e7; border-radius: 12px;
            padding: 40px 36px; max-width: 420px; box-shadow: 0 4px 16px rgba(0,0,0,0.04); }}
    h1 {{ font-size: 22px; margin: 0 0 16px 0; font-weight: 500; }}
    p {{ font-size: 14px; line-height: 1.5; margin: 6px 0; color: #424245; }}
    .footer {{ color: #86868b; font-size: 11px; margin-top: 24px; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>{title}</h1>
    {body_html}
    <div class="footer">鲶鱼平台 · catfish CLI</div>
  </div>
</body>
</html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))


def _do_oauth_login(*, identity_url: str, client_id: str, scopes: str) -> TokenStore:
    """跑 OAuth authorization_code 流程. 返存好的 TokenStore.

    1. 生成 state (CSRF 防御)
    2. 起 localhost:RANDOM 接 callback
    3. 浏览器开 /authorize
    4. 等 callback 拿 code (timeout CALLBACK_TIMEOUT_SECS)
    5. POST /token 换 access_token
    """
    state = secrets.token_urlsafe(32)
    port = _find_free_port()
    redirect_uri = f"http://localhost:{port}/callback"

    # 起本地 callback server (后台线程)
    server = http.server.HTTPServer(("127.0.0.1", port), _CallbackHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    try:
        # 拼 /authorize URL
        authorize_url = (
            f"{identity_url}/authorize?"
            + urllib.parse.urlencode({
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "scope": scopes,
                "state": state,
            })
        )

        print(f"打开浏览器登录: {authorize_url}")
        print(f"如果没自动弹, 复制上面 URL 到浏览器.")
        print(f"(等待 callback, 最多 {CALLBACK_TIMEOUT_SECS} 秒...)")

        # 自动开浏览器
        try:
            webbrowser.open(authorize_url)
        except Exception as e:
            logger.debug("自动开浏览器失败 (%s), 用户复制 URL", e)

        # 轮询等 callback (handler 把 code 写到类属性)
        deadline = time.time() + CALLBACK_TIMEOUT_SECS
        while time.time() < deadline:
            if _CallbackHandler.captured_code or _CallbackHandler.captured_error:
                break
            time.sleep(0.2)

        if _CallbackHandler.captured_error:
            raise RuntimeError(f"OAuth 登录失败: {_CallbackHandler.captured_error}")
        if not _CallbackHandler.captured_code:
            raise RuntimeError(f"等 callback 超时 ({CALLBACK_TIMEOUT_SECS}s) — 浏览器没回来")
        if _CallbackHandler.captured_state != state:
            raise RuntimeError("OAuth state 不匹配 — 可能 CSRF 攻击, 拒绝")

        code = _CallbackHandler.captured_code
        # 重置类属性, 防下次调用时残留
        _CallbackHandler.captured_code = None
        _CallbackHandler.captured_state = None
        _CallbackHandler.captured_error = None
    finally:
        server.shutdown()
        server.server_close()

    # 换 token
    print("拿到 code, 换 access_token...")
    token_url = f"{identity_url}/token"
    body = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
    }).encode("utf-8")
    req = urllib.request.Request(
        token_url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_str = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"换 token 失败 (HTTP {e.code}): {body_str}") from e

    access_token = data.get("access_token")
    if not access_token:
        raise RuntimeError(f"identity 没返 access_token: {data}")

    # 解 access_token claim 拿用户信息 + 真 expires_at
    payload = _decode_jwt_payload(access_token)
    expires_at = int(payload.get("exp") or (time.time() + data.get("expires_in", 3600)))
    user_sub = payload.get("sub", "")

    # id_token 才含真 user 信息 (email / name / department)
    id_token = data.get("id_token", "")
    user_email = ""
    if id_token:
        id_payload = _decode_jwt_payload(id_token)
        user_email = id_payload.get("email", "")

    return TokenStore(
        access_token=access_token,
        id_token=id_token or None,
        refresh_token=data.get("refresh_token"),
        expires_at=expires_at,
        issuer=identity_url,
        client_id=client_id,
        scope=data.get("scope", scopes),
        user_email=user_email,
        user_sub=user_sub,
        saved_at=int(time.time()),
    )


# ─── hermes config 同步 ───────────────────────────────────


def _patch_hermes_config(token: str) -> Optional[str]:
    """把 token 写进 ~/.hermes/config.yaml 的 custom_providers."Local (localhost:8999)".api_key.

    返 None = 没找到 hermes config 或 provider (用户没 setup 过), 跳过.
    返 str = 改了哪个 provider 的 api_key.

    要求 yaml 库 (PyYAML). 没装 logger.warning 跳过 (不挂).
    """
    try:
        import yaml  # noqa: PLC0415
    except ImportError:
        logger.warning("PyYAML 没装 (pip install pyyaml), 跳过 hermes config 同步")
        return None

    cfg_path = _hermes_config_path()
    if not cfg_path.exists():
        logger.info("hermes config (%s) 不存在, 跳过 (员工先跑 hermes model 配 Custom endpoint)", cfg_path)
        return None

    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    custom_providers = cfg.get("custom_providers") or {}
    if not custom_providers:
        logger.info("hermes config 没 custom_providers 段, 跳过")
        return None

    # 找 catfish 那个 provider — 默认 name="Local (localhost:8999)", 也找 base_url 含 localhost:8999 的
    target = None
    if HERMES_PROVIDER_NAME in custom_providers:
        target = HERMES_PROVIDER_NAME
    else:
        for name, p in custom_providers.items():
            base_url = (p or {}).get("base_url", "") if isinstance(p, dict) else ""
            if "localhost:8999" in base_url or "127.0.0.1:8999" in base_url:
                target = name
                break

    if not target:
        logger.info(
            "hermes config 没找到 catfish provider (期望 '%s' 或 base_url 含 localhost:8999), 跳过",
            HERMES_PROVIDER_NAME,
        )
        return None

    # 更新 api_key
    custom_providers[target] = custom_providers[target] or {}
    custom_providers[target]["api_key"] = token
    cfg["custom_providers"] = custom_providers

    # 备份原文件 + 原子写
    backup = cfg_path.with_suffix(".yaml.bak")
    backup.write_text(cfg_path.read_text(encoding="utf-8"))
    tmp = cfg_path.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False))
    tmp.replace(cfg_path)
    return target


# ─── 命令实现 ───────────────────────────────────────────────


def cmd_login(args) -> int:
    identity_url = args.identity_url or _identity_url()
    client_id = args.client_id or _client_id()
    scopes = args.scopes or _scopes()

    print(f"📡 catfish-identity:  {identity_url}")
    print(f"🆔 client_id:         {client_id}")
    print(f"🎫 scopes:            {scopes}")
    print()

    try:
        store = _do_oauth_login(
            identity_url=identity_url,
            client_id=client_id,
            scopes=scopes,
        )
    except Exception as e:
        print(f"\n❌ 登录失败: {e}", file=sys.stderr)
        return 1

    save_token(store)
    print(f"\n✓ 登录成功!")
    if store.user_email:
        print(f"  员工:     {store.user_email}")
    elif store.user_sub:
        print(f"  sub:      {store.user_sub}")
    print(f"  scope:    {store.scope}")
    print(f"  到期:     {store.expires_in_secs()} 秒后 ({_fmt_expires(store.expires_at)})")
    print(f"  已存:     {_token_path()}")

    # 同步 hermes config
    patched = _patch_hermes_config(store.access_token)
    if patched:
        print(f"  hermes:   ✓ 已更新 ~/.hermes/config.yaml provider '{patched}'")
        print(f"            (备份: {_hermes_config_path().with_suffix('.yaml.bak')})")
    else:
        print(f"  hermes:   ⚠ 未同步 (跑过 'hermes model' setup 后再 catfish login 一次会自动同步)")

    return 0


def cmd_status(args) -> int:
    store = load_token()
    if not store:
        print("❌ 未登录. 跑: catfish login")
        return 1

    expired = store.is_expired(buffer=0)
    print(f"{'❌ 已过期' if expired else '✓ 已登录'}")
    if store.user_email:
        print(f"  员工:     {store.user_email}")
    elif store.user_sub:
        print(f"  sub:      {store.user_sub}")
    print(f"  client:   {store.client_id}")
    print(f"  issuer:   {store.issuer}")
    print(f"  scope:    {store.scope}")
    print(f"  到期:     {_fmt_expires(store.expires_at)} ({store.expires_in_secs()} 秒后)")
    if store.refresh_token:
        print(f"  refresh:  ✓ 有 (catfish refresh 续)")
    else:
        print(f"  refresh:  ✗ 没 (5/16+ 加 refresh_token 后会有, 现在过期要重 catfish login)")
    print(f"  文件:     {_token_path()}")
    return 0 if not expired else 1


def cmd_logout(args) -> int:
    deleted = delete_token()
    if deleted:
        print(f"✓ 已登出 (删了 {_token_path()})")
    else:
        print("(本来就没登录)")
    return 0


def cmd_token(args) -> int:
    """输出当前 access_token. 给 shell 用 (e.g. curl -H "Authorization: Bearer $(catfish token)").

    BL-IDENTITY-REFRESH-TOKEN (5/15): 如果 access_token 快过期 (60s 内) 且有 refresh_token,
    自动透明 refresh. 员工 / hermes 不感知.
    """
    store = load_token()
    if not store:
        print("ERROR: 未登录, 跑: catfish login", file=sys.stderr)
        return 1
    if store.is_expired():
        # 自动 refresh
        if store.refresh_token:
            try:
                store = _do_refresh(store)
                save_token(store)
                _patch_hermes_config(store.access_token)
            except Exception as e:
                print(f"ERROR: refresh 失败 ({e}), 跑: catfish login", file=sys.stderr)
                return 1
        else:
            print(
                f"ERROR: token 已过期 ({_fmt_expires(store.expires_at)}) 且无 refresh_token, "
                f"跑: catfish login",
                file=sys.stderr,
            )
            return 1
    print(store.access_token)
    return 0


def cmd_refresh(args) -> int:
    """续 token. 有 refresh_token 走 refresh grant 无感续, 没 refresh_token 退化到重 login."""
    store = load_token()
    if store and store.refresh_token:
        try:
            new_store = _do_refresh(store)
            save_token(new_store)
            _patch_hermes_config(new_store.access_token)
            print(f"✓ token 已续 ({new_store.expires_in_secs()} 秒后过期, "
                  f"{_fmt_expires(new_store.expires_at)})")
            if store.user_email:
                print(f"  员工: {store.user_email}")
            return 0
        except Exception as e:
            print(f"❌ refresh 失败: {e}", file=sys.stderr)
            print("退化到重做 login...", file=sys.stderr)
    else:
        print("(没 refresh_token, 重做 login — 浏览器会再开一次)")
    return cmd_login(args)


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


# ─── argparse 入口 ─────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="catfish",
        description="鲶鱼员工自助登录 CLI — 浏览器 OAuth + 自动同步 hermes",
    )
    sub = p.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--identity-url", help=f"catfish-identity URL (默认 {DEFAULT_IDENTITY_URL})")
    common.add_argument("--client-id", help=f"OAuth client_id (默认 {DEFAULT_CLIENT_ID})")
    common.add_argument("--scopes", help="OAuth scopes (空格分隔)")

    login_p = sub.add_parser("login", parents=[common], help="浏览器 OAuth 登录")
    login_p.set_defaults(func=cmd_login)

    status_p = sub.add_parser("status", help="查登录状态")
    status_p.set_defaults(func=cmd_status)

    logout_p = sub.add_parser("logout", help="登出 (删 token)")
    logout_p.set_defaults(func=cmd_logout)

    token_p = sub.add_parser("token", help="输出当前 access_token (供 shell)")
    token_p.set_defaults(func=cmd_token)

    refresh_p = sub.add_parser("refresh", parents=[common], help="续 token (现在重做 login)")
    refresh_p.set_defaults(func=cmd_refresh)

    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
