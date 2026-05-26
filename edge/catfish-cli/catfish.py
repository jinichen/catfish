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

# BL-HERMES-SERVICE-TOKEN (5/24 鸿波"hermes 内存缓存过期 user token 撞 401"):
# 之前 _patch_hermes_config(store.access_token) 把用户 OAuth access_token (1h TTL)
# 写进 hermes config api_key. hermes 启动后读这个, 1 小时后过期 → gateway 验签
# 拒 → 401 storm 直到 `hermes gateway restart` 手动续.
#
# 真正的修法 (D 方案, identity-server 5/14 BL-RBAC P0 就为此预留了基础设施):
# hermes 用 client_credentials grant 拿"服务身份" token (sub=client:hermes-cli,
# TTL 30 天), 不依赖任何用户的 OAuth 周期. 0002 patch 已经实现 X-Catfish-User
# 转发, gateway 看 service token + header 一起识别真实员工身份做 audit/quota.
#
# client_secret 来源:
#   1. env CATFISH_HERMES_CLI_SECRET (生产推荐, 跟 identity-server clients.yaml
#      里 hermes-cli 的 bcrypt hash 对应的明文 secret)
#   2. 默认 dev demo secret (跟 identity-server/config/clients.yaml line 30 注释
#      里写的 "hermes-dev-secret-2026-please-change" 一致). 生产部署一定要改.
DEFAULT_HERMES_CLI_CLIENT_ID = "hermes-cli"
DEFAULT_HERMES_CLI_DEV_SECRET = "hermes-dev-secret-2026-please-change"
HERMES_SERVICE_SCOPES = "chat.completions tools.invoke skills.run audit.write"

#: token 过期前多少秒视为"快过期" (要重新拿)
EXPIRY_BUFFER_SECS = 60

#: 本地 callback 监听超时 (秒)
CALLBACK_TIMEOUT_SECS = 300

#: hermes-cli service token 剩余 < 这天数时, opportunistic 重新 mint (login/refresh 自动触发)
HERMES_TOKEN_MIN_REMAINING_DAYS = 7


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


def _hermes_env_path() -> Path:
    """~/.hermes/.env — hermes 0.14 读 backend API key 的文件 (TAVILY_API_KEY 等).

    hermes 启动时自动 load 这个文件. 我们 (catfish) 写 per-key update, 保留无关行.
    见 docs/DEPLOYMENT-RUNBOOK.md §15.
    """
    if env := os.environ.get("HERMES_DOTENV"):
        return Path(env).expanduser()
    return Path.home() / ".hermes" / ".env"


def _gateway_url() -> str:
    return os.environ.get("CATFISH_GATEWAY_URL", DEFAULT_GATEWAY_URL).rstrip("/")


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
    custom_providers = cfg.get("custom_providers")
    if not custom_providers:
        logger.info("hermes config 没 custom_providers 段, 跳过")
        return None

    # hermes 0.13 实际存 list-of-dict 格式 (5/15 鸿波端到端测发现, 之前我误判 dict).
    # 兼容两种格式: list[dict] (hermes 0.13) 和 dict[name → config] (老版本可能).
    target = None
    if isinstance(custom_providers, list):
        # list-of-dict 格式 — 每个 dict 含 name + base_url + api_key + model
        for entry in custom_providers:
            if not isinstance(entry, dict):
                continue
            entry_name = entry.get("name", "") or entry.get("display_name", "")
            base_url = entry.get("base_url", "") or ""
            if entry_name == HERMES_PROVIDER_NAME or "localhost:8999" in base_url or "127.0.0.1:8999" in base_url:
                entry["api_key"] = token
                target = entry_name or "<unnamed>"
                break
    elif isinstance(custom_providers, dict):
        # dict 格式 (老版本)
        if HERMES_PROVIDER_NAME in custom_providers:
            target = HERMES_PROVIDER_NAME
        else:
            for name, p in custom_providers.items():
                base_url = (p or {}).get("base_url", "") if isinstance(p, dict) else ""
                if "localhost:8999" in base_url or "127.0.0.1:8999" in base_url:
                    target = name
                    break
        if target:
            custom_providers[target] = custom_providers[target] or {}
            custom_providers[target]["api_key"] = token

    if not target:
        logger.info(
            "hermes config 没找到 catfish provider (期望 '%s' 或 base_url 含 localhost:8999), 跳过",
            HERMES_PROVIDER_NAME,
        )
        return None

    cfg["custom_providers"] = custom_providers

    # 5/15 鸿波端到端测发现: hermes 0.13 active 配置在顶层 'model:' 段, 不在
    # custom_providers (那是配置库). 选 Custom endpoint 时 hermes 把配置复制到 model:
    # 之后启动只读 model:. 我们必须**同时** patch model.api_key 才真生效.
    model_section = cfg.get("model")
    if isinstance(model_section, dict):
        model_base_url = model_section.get("base_url", "") or ""
        if "localhost:8999" in model_base_url or "127.0.0.1:8999" in model_base_url:
            model_section["api_key"] = token
            logger.info("同步 model.api_key (hermes 真用的 active 配置)")

    # 备份原文件 + 原子写
    backup = cfg_path.with_suffix(".yaml.bak")
    backup.write_text(cfg_path.read_text(encoding="utf-8"))
    tmp = cfg_path.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False))
    tmp.replace(cfg_path)
    return target


# ─── BL-HERMES-SERVICE-TOKEN (5/24): hermes service token mint + 同步 ──


def _hermes_cli_client_secret() -> str:
    """读 hermes-cli 的 client_secret.

    生产: env CATFISH_HERMES_CLI_SECRET (跟 identity-server clients.yaml hermes-cli
    那条 bcrypt hash 对应的明文).
    Dev: fallback 到 identity-server clients.yaml 注释里写的 demo secret.

    返空 → 抛错由 caller 处理.
    """
    return os.environ.get("CATFISH_HERMES_CLI_SECRET", DEFAULT_HERMES_CLI_DEV_SECRET)


def _mint_hermes_service_token(
    identity_url: str,
    client_id: str = DEFAULT_HERMES_CLI_CLIENT_ID,
    scopes: str = HERMES_SERVICE_SCOPES,
) -> str:
    """调 catfish-identity /token grant_type=client_credentials 拿 service token.

    实施 BL-RBAC P0 (5/14) 设计的 RFC 6749 §4.4 client_credentials grant. 返
    sub=client:hermes-cli, aud=catfish-gateway, TTL 30 天的 access_token.

    跟用户 OAuth 完全独立 — 哪怕用户没 login / OAuth token 过期, hermes 也照常跑.

    Returns: JWT 字符串 (~600-800 bytes).
    Raises: RuntimeError on 网络 / HTTP / parse 失败.
    """
    secret = _hermes_cli_client_secret()
    if not secret:
        raise RuntimeError(
            "hermes-cli client_secret 没配 (env CATFISH_HERMES_CLI_SECRET 空 + dev "
            "fallback 也被清). 看 identity-server/config/clients.yaml hermes-cli 段."
        )
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": secret,
        "scope": scopes,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{identity_url.rstrip('/')}/token",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_str = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"client_credentials mint 失败 (HTTP {e.code}): {body_str}. "
            f"检查 CATFISH_HERMES_CLI_SECRET 是否跟 identity-server 配的 hash 对得上."
        ) from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise RuntimeError(
            f"catfish-identity ({identity_url}) 不可达: {e}. "
            f"检查 identity-server 是否在跑."
        ) from e

    tok = data.get("access_token")
    if not tok:
        raise RuntimeError(f"identity 没返 access_token: {data}")
    return tok


# ─── BL-EDGE-TOOL-PROXY (5/25): 死代理检测 + 清理 hermes 进程 env ──────
#
# 背景 (5/24 凌晨鸿波 web_search demo 暴露):
#   员工 shell 经常有 HTTPS_PROXY=http://127.0.0.1:7890 指向 Clash / Mihomo /
#   v2ray, 但代理常常没开. hermes gateway restart 起的 hermes 进程**继承**这条
#   死代理, 之后所有外网调用 (Tavily web_search / Firecrawl 等) 全撞墙 timeout,
#   模型学会"web_search 不能用", 退化用 catfish_browser_* 抓页面 (慢 30 倍).
#
# Gateway 自己启动时跑过这个检测 (network.py:precheck_and_setup), 清掉了自己
# 进程的死代理. 但 hermes 是单独进程, 没人帮它清. 现在 catfish refresh-hermes
# 顺手帮 hermes 的 restart 把 env 清干净.
#
# 复用 gateway/network.py 的检测思路, 但不依赖那个模块 (catfish-cli 是单文件,
# 不想加 import). 50 行抄过来 + 个性化 logging.


_PROXY_ENV_VARS = (
    "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY",
    "https_proxy", "http_proxy", "all_proxy",
)


def _check_proxy_alive(proxy_url: str, timeout: float = 2.0) -> bool:
    """TCP probe 代理端口是否能连上.

    抄 gateway network.py:_check_tcp_port 同款. 不实际跑 HTTP, 只看 TCP 通不通
    (端口接受连接 = 代理至少在跑, 哪怕配错也算"活着", 不在我们 scope).
    """
    if not proxy_url:
        return False
    from urllib.parse import urlparse  # noqa: PLC0415
    if not proxy_url.startswith(("http://", "https://")):
        proxy_url = "http://" + proxy_url
    try:
        parsed = urlparse(proxy_url)
    except ValueError:
        return False
    host = parsed.hostname
    if not host:
        return False
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    import socket  # noqa: PLC0415
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (TimeoutError, OSError):
        return False


def _detect_dead_proxy_vars() -> list[tuple[str, str]]:
    """检测当前 shell env 里哪些 proxy var 指向死端口.

    返 [(var_name, value), ...]. 全活或全没设返 []. 多个 var 指同一个 URL 算多条.

    幂等 — 只读 env, 不修改.
    """
    seen_urls: dict[str, bool] = {}  # url → alive cache, 不重复 TCP probe
    dead: list[tuple[str, str]] = []
    for var in _PROXY_ENV_VARS:
        val = os.environ.get(var, "").strip()
        if not val:
            continue
        if val not in seen_urls:
            seen_urls[val] = _check_proxy_alive(val)
        if not seen_urls[val]:
            dead.append((var, val))
    return dead


def _build_clean_env(unset_vars: list[str]) -> dict[str, str]:
    """copy os.environ 然后删指定 var, 给 subprocess 用. 不动当前进程 env."""
    env = os.environ.copy()
    for v in unset_vars:
        env.pop(v, None)
    return env


def _restart_hermes_with_clean_env(unset_vars: list[str]) -> int:
    """spawn `hermes gateway restart` with proxy env vars removed.

    返 hermes 命令的 exit code. 找不到 hermes 命令返 127.
    不挂任何异常 — caller 应当吞掉 (这是 best-effort 帮员工省事).
    """
    import shutil  # noqa: PLC0415
    import subprocess  # noqa: PLC0415

    hermes_bin = shutil.which("hermes")
    if not hermes_bin:
        print("  proxy-clean: ⚠ 找不到 hermes 命令 (PATH 没设?), 跳过自动 restart")
        return 127

    env = _build_clean_env(unset_vars)
    try:
        result = subprocess.run(
            [hermes_bin, "gateway", "restart"],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        print("  proxy-clean: ⚠ hermes gateway restart 60s 没返, 放弃")
        return 124
    except Exception as e:  # pragma: no cover
        print(f"  proxy-clean: ⚠ hermes gateway restart 出错: {e}")
        return 1

    # hermes "✓ Service restarted" 这种行打到 stdout
    if result.stdout:
        for line in result.stdout.rstrip().split("\n"):
            print(f"  hermes: {line}")
    if result.returncode != 0 and result.stderr:
        for line in result.stderr.rstrip().split("\n")[:5]:
            print(f"  hermes(err): {line}")
    return result.returncode


def _handle_proxy_cleanup(auto_restart: bool) -> None:
    """打 banner + 可选 auto restart hermes. 永远 best-effort, 不挂主流程.

    auto_restart=True (传 --restart-hermes flag): 死代理 → unset + 重启 hermes.
    auto_restart=False: 只警告, 给员工具体命令, 不动 hermes.

    无死代理 → 静默不打字 (避免 banner 噪音).
    """
    dead = _detect_dead_proxy_vars()
    if not dead:
        return

    # 打 banner
    print()
    print("⚠ 检测到死代理 (TCP 端口连不上):")
    for var, val in dead:
        print(f"    {var}={val}")
    print("  hermes 进程继承这条会让 web_search / Firecrawl 等外网工具撞墙 timeout.")

    var_names = sorted({v for v, _ in dead})
    if auto_restart:
        print(f"  → 自动用 clean env 重启 hermes (unset: {' '.join(var_names)})")
        rc = _restart_hermes_with_clean_env(var_names)
        if rc == 0:
            print("  ✓ hermes 已用 clean env 重启, web_search 等工具应可正常调")
        else:
            print(f"  ✗ hermes restart 失败 (rc={rc}), 手动跑下面命令:")
            print(f"    unset {' '.join(var_names)}")
            print(f"    hermes gateway restart")
    else:
        print("  → 推荐用这两条手动重启 (跳过死代理):")
        print(f"    unset {' '.join(var_names)}")
        print(f"    hermes gateway restart")
        print("  → 或下次 `catfish refresh-hermes --restart-hermes` 自动帮你做.")


# ─── BL-EDGE-TOOL-KEY (5/24): 中央派发 hermes 边缘 backend key ─────
#
# 后续会扩到 image_generate / x_search / 等其他外部 key 工具. 不在 scope:
# Tavily quota 不在 catfish 跟 (Tavily 自家 dashboard 看).
# 详见 central/llm-gateway/src/catfish_gateway/edge_tool_config.py.


def _fetch_edge_tool_list(gateway_url: str, token: str) -> list[str]:
    """GET /v1/edge/tool-config → 拿支持的 tool 列表.

    返空 list = gateway 不支持这接口 (老版本) 或网络挂. caller 应当跳过, 不挂.
    """
    req = urllib.request.Request(
        f"{gateway_url.rstrip('/')}/v1/edge/tool-config",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        # 404 → gateway 老版本没这接口, 静默跳过.
        if e.code == 404:
            logger.info("[edge-tool] gateway %s 没 /v1/edge/tool-config (老版本?), 跳过", gateway_url)
            return []
        logger.warning("[edge-tool] list endpoint HTTP %d: %s", e.code, e.read()[:200])
        return []
    except (urllib.error.URLError, TimeoutError) as e:
        logger.warning("[edge-tool] gateway %s 不可达: %s", gateway_url, e)
        return []
    return list(data.get("supported", []))


def _fetch_edge_tool_config(
    gateway_url: str, token: str, tool_name: str,
) -> Optional[dict]:
    """GET /v1/edge/tool-config/{tool_name} → 拿 env_vars + yaml_block.

    返 None: 任何失败 (403 RBAC 拦 / 503 admin 没配 key / 网络).
    caller 应当 print warning 跳过这个 tool, 不挂.
    """
    req = urllib.request.Request(
        f"{gateway_url.rstrip('/')}/v1/edge/tool-config/{tool_name}",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300]
        if e.code == 403:
            print(f"  edge-tool: ⏭ {tool_name} — 部门 RBAC 没批 (admin 加白名单后重试)")
        elif e.code == 503:
            print(f"  edge-tool: ⏭ {tool_name} — gateway 中央 .env 没配 key (admin 配后重启 gateway)")
        elif e.code == 404:
            # 接口存在但 tool 不在 registry — 不该发生 (我们从 list 拿的), log 一下
            logger.warning("[edge-tool] %s 404: %s", tool_name, body)
        else:
            print(f"  edge-tool: ⏭ {tool_name} — gateway HTTP {e.code}")
            logger.warning("[edge-tool] %s HTTP %d: %s", tool_name, e.code, body)
        return None
    except (urllib.error.URLError, TimeoutError) as e:
        logger.warning("[edge-tool] %s 网络: %s", tool_name, e)
        return None


def _patch_hermes_env_file(env_vars: dict[str, str]) -> int:
    """合并写 ~/.hermes/.env, per-key update, 保留无关行 + 注释.

    格式: 每行 KEY=VALUE 或注释. 已存在的 key 就地覆盖, 不存在的追加在尾部.
    我们的写入行带 catfish marker 注释让员工知道是 catfish 同步进来的.

    返 patched 的 key 数 (0 = env_vars 空 / 全部没变化).

    示例:
        old .env:
            FIRECRAWL_API_KEY=fc-old   # 员工手贴的
        env_vars: {TAVILY_API_KEY: "tvly-new"}
        new .env:
            FIRECRAWL_API_KEY=fc-old   # 员工手贴的
            # ── catfish-cli 同步 (BL-EDGE-TOOL-KEY) ──
            TAVILY_API_KEY=tvly-new
    """
    if not env_vars:
        return 0

    env_path = _hermes_env_path()
    env_path.parent.mkdir(parents=True, exist_ok=True)

    # 读现有内容 (不存在视为空文件)
    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()

    # per-key update
    seen: set[str] = set()
    out_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        # 注释 / 空白 / 不含 = 的行: 原样保留
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            out_lines.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in env_vars:
            out_lines.append(f"{key}={env_vars[key]}")
            seen.add(key)
        else:
            out_lines.append(line)

    # 没出现过的 key → 追加 (带 marker, 一次写一组)
    new_keys = [k for k in env_vars if k not in seen]
    if new_keys:
        if out_lines and out_lines[-1].strip() != "":
            out_lines.append("")
        out_lines.append("# ── catfish-cli 同步 (BL-EDGE-TOOL-KEY 中央派发) ──")
        for k in new_keys:
            out_lines.append(f"{k}={env_vars[k]}")

    # 备份 + 原子写
    if env_path.exists():
        backup = env_path.with_suffix(".env.bak")
        backup.write_text(env_path.read_text(encoding="utf-8"), encoding="utf-8")
    tmp = env_path.with_suffix(".env.tmp")
    tmp.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    tmp.replace(env_path)
    # chmod 600 — env 含 secret, 跟 token store 一个标准
    try:
        env_path.chmod(0o600)
    except OSError:
        pass

    return len(env_vars)


def _patch_hermes_config_yaml_blocks(yaml_blocks: list[dict]) -> int:
    """合并写 ~/.hermes/config.yaml 的顶层段 (web/image/...), preserve sibling keys.

    yaml_blocks: [{"web": {"backend": "tavily"}}, {"image": {...}}]
      → 把每个 dict 的顶层 key 合并进 config.yaml 顶层.
      已有的 sibling key (model / custom_providers / 等) 不动.

    返 patched 的 top-level key 数. config.yaml 不存在则跳过 (返 0).
    """
    if not yaml_blocks:
        return 0

    try:
        import yaml  # noqa: PLC0415
    except ImportError:
        logger.warning("PyYAML 没装, 跳过 ~/.hermes/config.yaml yaml block 同步")
        return 0

    cfg_path = _hermes_config_path()
    if not cfg_path.exists():
        # 没 config.yaml → 仅靠 .env 的 auto-detect 也能让 hermes web_search 跑
        # (TAVILY_API_KEY 存在 → 自动选 Tavily). 跳过 yaml 不是错.
        logger.info("hermes config (%s) 不存在, 跳过 yaml block 同步 (env 已写够用)", cfg_path)
        return 0

    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    patched_keys: list[str] = []
    for block in yaml_blocks:
        if not isinstance(block, dict):
            continue
        for top_key, top_val in block.items():
            existing = cfg.get(top_key)
            if isinstance(existing, dict) and isinstance(top_val, dict):
                # 浅合并 — sibling sub-key 保留, 同名 sub-key 覆盖
                existing.update(top_val)
                cfg[top_key] = existing
            else:
                cfg[top_key] = top_val
            patched_keys.append(top_key)

    if not patched_keys:
        return 0

    # 备份 + 原子写 (跟 _patch_hermes_config 同款)
    backup = cfg_path.with_suffix(".yaml.bak")
    backup.write_text(cfg_path.read_text(encoding="utf-8"), encoding="utf-8")
    tmp = cfg_path.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    tmp.replace(cfg_path)

    return len(set(patched_keys))


def _sync_hermes_edge_tool_configs(gateway_url: str, token: str) -> tuple[int, int]:
    """把中央派发的 backend key 同步到 ~/.hermes/.env + config.yaml.

    返 (env_key_count, yaml_top_key_count) 让 caller 打 summary.
    任一步失败 (网络 / RBAC 拦 / 没配 key) 都不挂, 返 (0, 0).

    幂等: 重跑只覆盖差异行, .env / yaml 里别的内容不动.
    """
    tools = _fetch_edge_tool_list(gateway_url, token)
    if not tools:
        return 0, 0

    # RBAC 是 per-tool, 必须每个 tool 单独打 endpoint 让 gateway 判权
    # (理论上 admin 可以放 web_search 但不放 web_extract 给某部门).
    # 但**写盘 dedupe by tool_group** — 3 个 web tool 共用 TAVILY_API_KEY,
    # 拉 3 次后只往 .env 写 1 行, yaml 也只更新 1 段.
    env_vars: dict[str, str] = {}
    yaml_blocks: list[dict] = []
    seen_groups: set[str] = set()
    for name in tools:
        cfg = _fetch_edge_tool_config(gateway_url, token, name)
        if cfg is None:
            continue
        group = cfg.get("tool_group") or name
        if group in seen_groups:
            continue
        seen_groups.add(group)
        env_vars.update(cfg.get("env_vars") or {})
        yb = cfg.get("yaml_block")
        if isinstance(yb, dict) and yb:
            yaml_blocks.append(yb)

    env_n = _patch_hermes_env_file(env_vars)
    yaml_n = _patch_hermes_config_yaml_blocks(yaml_blocks)
    return env_n, yaml_n


def _sync_hermes_with_service_token(
    identity_url: str,
    user_fallback_token: Optional[str] = None,
) -> Optional[str]:
    """Mint hermes-cli service token + 写进 hermes config api_key.

    主路径: client_credentials → 30 天 token → 写盘.
    Fallback: 如果 mint 失败但 caller 给了 user_fallback_token, 退化到老行为
    (用 user OAuth access_token, 1 小时后会撞 401, 但至少能立刻用).

    返 _patch_hermes_config 的结果 (patched provider name 或 None).
    """
    try:
        tok = _mint_hermes_service_token(identity_url)
    except Exception as e:
        logger.warning("[hermes-svc] mint 失败: %s", e)
        if user_fallback_token:
            logger.warning(
                "[hermes-svc] fallback 写 user access_token (1h TTL, 之后会 401, "
                "请检查 CATFISH_HERMES_CLI_SECRET / identity-server)"
            )
            return _patch_hermes_config(user_fallback_token)
        return None

    # 友好打印: service token 的 sub / exp 让员工知道发生了啥
    payload = _decode_jwt_payload(tok)
    sub = payload.get("sub", "?")
    exp = payload.get("exp", 0)
    if exp:
        remaining_days = (exp - time.time()) / 86400.0
        print(
            f"  hermes-svc: ✓ 已 mint service token "
            f"(sub={sub}, 还有 {remaining_days:.1f} 天有效)"
        )
    else:
        print(f"  hermes-svc: ✓ 已 mint service token (sub={sub})")

    patched = _patch_hermes_config(tok)

    # BL-EDGE-TOOL-KEY (5/24): 顺手拉中央派发的 backend key (Tavily 等), 写进
    # ~/.hermes/.env + config.yaml. 失败 (网络 / RBAC / admin 没配) 不挂主流程,
    # hermes 自己 token 写完才是 critical path, 工具 key 是 nice-to-have.
    try:
        env_n, yaml_n = _sync_hermes_edge_tool_configs(_gateway_url(), tok)
        if env_n or yaml_n:
            print(
                f"  edge-tool: ✓ 同步 {env_n} 个 env key + {yaml_n} 个 yaml 段 "
                f"(~/.hermes/.env, ~/.hermes/config.yaml)"
            )
    except Exception as e:  # pragma: no cover — defensive
        logger.warning("[edge-tool] 同步出错 (不影响 hermes token): %s", e)

    return patched


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
    # BL-HERMES-SERVICE-TOKEN (5/24): 不再写 store.access_token (user OAuth, 1h TTL)
    # 改 mint hermes-cli service token (sub=client:hermes-cli, 30 天 TTL) 写进去.
    # 这样 hermes 30 天不用动 token, 不再撞 "缓存 user token 1h 后过期" 那个 bug.
    # user OAuth token 仍然存在 ~/.catfish/auth/token.json (catfish 自己用 +
    # Companion 通过 `catfish token` 拿) — 两条 token 互不影响.
    patched = _sync_hermes_with_service_token(
        identity_url=identity_url,
        user_fallback_token=store.access_token,
    )
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
                # BL-HERMES-SERVICE-TOKEN (5/24): user OAuth refresh 顺便 opportunistic
                # refresh hermes service token. 不影响主 cmd_token 返 user token 的语义,
                # 只是让 hermes 那条独立链也跟上节奏. mint 失败不阻塞 cmd_token (caller
                # 调 catfish token 是为了 user token, hermes 同步是副作用).
                try:
                    _sync_hermes_with_service_token(
                        identity_url=store.issuer,
                        user_fallback_token=store.access_token,
                    )
                except Exception as e:
                    logger.warning("hermes service token 同步失败 (non-fatal): %s", e)
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
    """续 token. 有 refresh_token 走 refresh grant 无感续, 没 refresh_token 退化到重 login.

    BL-HERMES-SERVICE-TOKEN (5/24): 顺手 refresh hermes service token (独立 client_credentials
    grant, 跟 user OAuth refresh 完全解耦). 想纯 refresh hermes 不动 user 用 `catfish refresh-hermes`.
    """
    store = load_token()
    if store and store.refresh_token:
        try:
            new_store = _do_refresh(store)
            save_token(new_store)
            # BL-HERMES-SERVICE-TOKEN (5/24): mint 新 service token 写 hermes config
            _sync_hermes_with_service_token(
                identity_url=new_store.issuer,
                user_fallback_token=new_store.access_token,
            )
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


def cmd_refresh_hermes(args) -> int:
    """BL-HERMES-SERVICE-TOKEN (5/24): 独立 refresh hermes service token, 不动 user OAuth.

    使用场景:
      - hermes service token 快到 30 天上限, 手动 rotate
      - launchd / cron 每月跑一次防 token 过期
      - identity-server 配置改了 (client_secret 轮换), 强制重新 mint

    不需要先 catfish login — 完全 server-to-server, 不依赖任何 user 状态.
    """
    identity_url = (args.identity_url if hasattr(args, 'identity_url') else None) \
        or _identity_url()
    print(f"📡 catfish-identity:  {identity_url}")
    print(f"🆔 client_id:         {DEFAULT_HERMES_CLI_CLIENT_ID}")
    print(f"🎫 scopes:            {HERMES_SERVICE_SCOPES}")
    print()

    try:
        patched = _sync_hermes_with_service_token(identity_url=identity_url)
    except Exception as e:
        print(f"\n❌ refresh-hermes 失败: {e}", file=sys.stderr)
        return 1

    if patched:
        print(f"\n✓ hermes config 已更新 provider '{patched}'")
        print(f"  接下来: hermes gateway restart  # 让 hermes 读新 token")
    else:
        print(f"\n⚠ hermes config 没更新 (~/.hermes/config.yaml 不存在 / 没匹配 provider).")
        print(f"  先跑 hermes model 配 Custom endpoint (localhost:8999) 再来一次.")

    # BL-EDGE-TOOL-PROXY (5/25): 死代理检测 + 可选 auto restart hermes.
    # 解决 5/24 凌晨发现的 web_search 走 browser 后路问题 — hermes 进程继承
    # 死 HTTPS_PROXY → Tavily HTTP 撞墙 → 模型退化用浏览器抓页面.
    _handle_proxy_cleanup(auto_restart=getattr(args, "restart_hermes", False))

    return 0 if patched else 1


# ─── BL-EMPLOYEE-PRIVACY-VERIFICATION (#76, 5/25) ─────────────────
#
# `catfish privacy-audit`: 员工自己跑一发, 输出 "我本机存了啥 + 中央存了我啥"
# 报告. 目的不是给运维诊断, 是给员工"我能验证, 不需要信任公司说辞".
#
# 报告分 3 段:
#   1. 本机数据 (员工电脑上的): catfish 目录 / hermes 目录 / token / 配置.
#      列文件路径 + 大小 + 权限 + 内容类别 (token / 对话 / 日志 / 缓存).
#      员工看完知道 "哦, 对话历史在我电脑这, 不在中央" / "中央拿不到我 prompt".
#   2. 中央存了我啥 (调 /api/audit/me): 全是 metadata (count / token / model / 时间戳).
#      schema_note 让员工知道"中央只看 metadata, 不看 prompt/response 文本".
#   3. 上传记录 (本机 audit jsonl): 我电脑往中央发了啥 (按模型分布 / 今日量).
#
# 防误判设计:
#   - 调中央失败不 fail-hard (本机段照样输出, 中央段标"未连通", 让离线员工也能审)
#   - --json 给机器 (CI / 软著合规检查脚本接), 默认人类可读 (员工平常用)
#   - 路径全用 expanduser, 写绝对 (~/.catfish/... → /Users/xxx/.catfish/...), 复制粘贴可验
#   - 权限位单独列, 600 / 644 / 755 一眼看 "token 文件是 600 ✓ 私有 / 还是 644 ✗ 漏权限"
#
# 不做:
#   - 不读对话文件内容打印 (那是 privacy nightmare, 员工想看自己开 ~/.catfish 看)
#   - 不删任何东西 (审计 ≠ 清理. 清理用 catfish logout / 手动 rm)
#   - 不联网下载任何"判定规则" (全本地逻辑, 防中央偷偷改判定)


# 本机数据扫描的目录清单 (按"员工最该知道的"排序).
#
# (相对 home, 是路径, 描述, 是否敏感) — 敏感=含 prompt 内容 / token.
_PRIVACY_SCAN_TARGETS = [
    (".catfish/auth/token.json",
     "我的 OAuth token (中央认证用, 不含对话)",
     True),
    (".catfish/gateway_audit.jsonl",
     "本机 audit log 历史归档 (PG 化前的镜像, 当前 PG-only 模式不再写)",
     False),
    (".catfish/",
     "catfish 数据目录 (token / 配置 / 边缘缓存)",
     False),
    (".hermes/sessions/",
     "hermes 对话会话 (含 prompt + response 全文 — 本机, 不上传)",
     True),
    (".hermes/memories/",
     "hermes 长期记忆 (USER.md / MEMORY.md, LLM 学的事实 — 本机, 不上传)",
     True),
    (".hermes/config.yaml",
     "hermes 配置 (含 service token, 不含对话)",
     True),
    (".hermes/.env",
     "hermes 第三方 API key (Tavily / 等, 本机调外网用)",
     True),
]


def _stat_path(p: Path) -> dict:
    """收 path 的 size / mode / 文件数 / 最新 mtime. 不存在返 exists=False."""
    if not p.exists():
        return {"exists": False, "path": str(p)}

    out: dict = {"exists": True, "path": str(p)}
    if p.is_file():
        st = p.stat()
        out.update({
            "kind": "file",
            "size_bytes": st.st_size,
            "mode_oct": oct(st.st_mode & 0o777),
            "mtime": int(st.st_mtime),
        })
    elif p.is_dir():
        # 目录: 算总大小 + 文件数, 不递归打印每个
        total = 0
        count = 0
        latest_mtime = 0
        try:
            for f in p.rglob("*"):
                if f.is_file():
                    try:
                        s = f.stat()
                        total += s.st_size
                        latest_mtime = max(latest_mtime, int(s.st_mtime))
                        count += 1
                    except OSError:
                        # symlink 断 / 权限不够 跳
                        continue
        except OSError as e:
            out["scan_error"] = str(e)
        st = p.stat()
        out.update({
            "kind": "dir",
            "file_count": count,
            "total_bytes": total,
            "mode_oct": oct(st.st_mode & 0o777),
            "mtime": int(latest_mtime or st.st_mtime),
        })
    return out


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024  # type: ignore[assignment]
    return f"{n:.1f}TB"


def _fetch_audit_me(token: str, gateway: str) -> dict:
    """调 GET /api/audit/me. 任何失败抛 RuntimeError (让 caller 决定 fail-soft)."""
    url = f"{gateway}/api/audit/me"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:200]
        raise RuntimeError(f"HTTP {e.code}: {body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"连不上 gateway ({url}): {e.reason}") from e


def _scan_local_audit_jsonl(p: Path, since_ts: int) -> dict:
    """扫本机 ~/.catfish/gateway_audit.jsonl, 算今天往中央发了啥.

    返:
      - request_count: 今天总请求数
      - total_tokens:  今天 token 总数
      - by_model:      [{model, count}]
      - earliest_ts / latest_ts: 文件内最早 / 最新一条 (反映"我电脑上 audit 留多久")
    """
    empty = {
        "exists": False,
        "request_count": 0,
        "total_tokens": 0,
        "by_model": [],
        "earliest_ts": None,
        "latest_ts": None,
    }
    if not p.exists():
        return empty

    count_today = 0
    tokens_today = 0
    by_model_today: dict = {}
    earliest = None
    latest = None
    try:
        with p.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = int(r.get("ts") or 0)
                if not ts:
                    continue
                earliest = ts if earliest is None else min(earliest, ts)
                latest = ts if latest is None else max(latest, ts)
                if ts < since_ts:
                    continue
                count_today += 1
                tokens_today += int(r.get("prompt_tokens", 0) or 0) + int(r.get("completion_tokens", 0) or 0)
                m = r.get("model", "")
                by_model_today[m] = by_model_today.get(m, 0) + 1
    except OSError as e:
        logger.warning("scan audit jsonl: %s", e)
        return empty

    return {
        "exists": True,
        "path": str(p),
        "request_count": count_today,
        "total_tokens": tokens_today,
        "by_model": [{"model": m, "count": c} for m, c in sorted(by_model_today.items(), key=lambda x: -x[1])],
        "earliest_ts": earliest,
        "latest_ts": latest,
    }


def cmd_privacy_audit(args) -> int:
    """BL-EMPLOYEE-PRIVACY-VERIFICATION (#76, 5/25): 员工自查 "本机存了啥 + 中央存了我啥".

    给员工"我能验证, 不需要纯信任公司"的工具. 跟 #77 (Dashboard 隐私 tab) /
    #79 (gateway /api/audit/me) / #78 (员工 doc) 配套.

    退码:
      0: 全段成功 (含中央段)
      1: 中央段失败 (离线 / 未登录 / 中央挂), 本机段照样输出
    """
    json_mode = getattr(args, "json", False)
    home = Path.home()
    today_start = int(time.time()) - 86400  # 本机 audit jsonl ts 用秒, 跟 audit.rs 一致

    # ── 段 1: 本机数据 ────────────────────────────
    local_items = []
    for rel, desc, sensitive in _PRIVACY_SCAN_TARGETS:
        p = home / rel
        info = _stat_path(p)
        info["description"] = desc
        info["sensitive"] = sensitive
        local_items.append(info)

    local_audit_jsonl_path = home / ".catfish" / "gateway_audit.jsonl"
    local_audit_summary = _scan_local_audit_jsonl(local_audit_jsonl_path, today_start)

    # ── 段 2: 中央 (/api/audit/me) ────────────────
    central_section: dict = {"reachable": False, "reason": "", "data": None}
    store = load_token()
    if not store:
        central_section["reason"] = "未登录 (没 token, 跑: catfish login)"
    elif store.is_expired(buffer=0) and not store.refresh_token:
        central_section["reason"] = "token 过期且无 refresh_token (跑: catfish login)"
    else:
        # 过期但有 refresh, 自动 refresh 一次
        if store.is_expired():
            try:
                store = _do_refresh(store)
                save_token(store)
            except Exception as e:
                central_section["reason"] = f"refresh 失败: {e}"
                store = None  # 不再尝试

        if store is not None:
            try:
                gw = _gateway_url()
                data = _fetch_audit_me(store.access_token, gw)
                central_section["reachable"] = True
                central_section["data"] = data
                central_section["gateway"] = gw
            except RuntimeError as e:
                central_section["reason"] = str(e)

    report = {
        "version": 1,
        "generated_at": int(time.time()),
        "user_email": (store.user_email if store else None) or "(未登录)",
        "local": {
            "scanned_paths": local_items,
            "local_audit_jsonl_summary": local_audit_summary,
        },
        "central": central_section,
        "privacy_contract": [
            "中央只存 metadata (count / tokens / model / 时间戳), 不存 prompt / response 文本.",
            "对话 / 长期记忆 / 第三方 API key 全在本机 (~/.hermes/, ~/.catfish/), 不上传.",
            "本机 audit jsonl 是边缘 gateway 自己写的副本, 跟中央存的内容一致.",
            "中央 /api/audit/me 跟本机 audit jsonl 数字对得上 → 没偷偷上传额外字段.",
        ],
    }

    if json_mode:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if central_section["reachable"] else 1

    # ── 人类可读输出 ──────────────────────────────
    print("═" * 60)
    print("  catfish 隐私自查报告")
    print(f"  员工: {report['user_email']}")
    print(f"  时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}")
    print("═" * 60)

    print("\n── 段 1 · 本机数据 (在你电脑上, 不上传) ──")
    for item in local_items:
        marker = "🔒" if item.get("sensitive") else "📄"
        if not item["exists"]:
            print(f"  {marker} (不存在) {item['path']}")
            print(f"      {item['description']}")
            continue
        if item["kind"] == "file":
            size = _fmt_bytes(item["size_bytes"])
            print(f"  {marker} {item['path']}")
            print(f"      {item['description']}")
            print(f"      大小: {size}  权限: {item['mode_oct']}  改动: {time.strftime('%Y-%m-%d %H:%M', time.localtime(item['mtime']))}")
            # token 文件权限不是 600 → 警告
            if item["path"].endswith("token.json") and item["mode_oct"] != "0o600":
                print(f"      ⚠ token 文件权限不是 600! 任何同机器其他用户可读. 建议: chmod 600 {item['path']}")
        else:  # dir
            size = _fmt_bytes(item["total_bytes"])
            print(f"  {marker} {item['path']}/  ({item['file_count']} 个文件, {size})")
            print(f"      {item['description']}")
            if item["file_count"] > 0:
                print(f"      最近改动: {time.strftime('%Y-%m-%d %H:%M', time.localtime(item['mtime']))}")

    # ── PG-only 模式检测 ──
    # 5/9 之前 gateway 写 ~/.catfish/gateway_audit.jsonl (jsonl backend).
    # 5/9 之后配 CATFISH_DB_URL → 切 PG-only, metrics.py 不再写 jsonl.
    # 判定: 本机 latest_ts (jsonl 最后一条) < 中央 first_seen (PG 最早记录) → PG-only.
    a = local_audit_summary
    pg_only_mode = False
    if (a["exists"] and a.get("latest_ts")
        and central_section["reachable"]
        and central_section["data"]
        and central_section["data"].get("first_seen_ts")):
        local_latest_s = int(a["latest_ts"])
        central_earliest_s = int(central_section["data"]["first_seen_ts"] / 1000)
        if local_latest_s < central_earliest_s:
            pg_only_mode = True

    print("\n── 段 2 · 本机 audit log (历史镜像) ──")
    if not a["exists"]:
        print(f"  (无 jsonl: {local_audit_jsonl_path} 不存在 — 当前 PG-only 模式, 此为预期)")
    else:
        print(f"  路径: {a['path']}")
        if pg_only_mode:
            print(f"  📦 PG-only 模式: gateway 5/9 后切 PG backend, 本机 jsonl 是历史归档不再更新.")
            print(f"  历史范围: {time.strftime('%Y-%m-%d', time.localtime(a['earliest_ts']))} ~ {time.strftime('%Y-%m-%d', time.localtime(a['latest_ts']))}")
            print(f"  (中央 PG 是当前唯一真相, 见段 3)")
        else:
            print(f"  今日: {a['request_count']} 请求, {a['total_tokens']} tokens")
            if a["by_model"]:
                print(f"  今日按模型:")
                for r in a["by_model"][:10]:
                    print(f"    - {r['model']}: {r['count']} 次")
            if a["earliest_ts"]:
                print(f"  全量记录: {time.strftime('%Y-%m-%d', time.localtime(a['earliest_ts']))} ~ {time.strftime('%Y-%m-%d', time.localtime(a['latest_ts']))}")

    print("\n── 段 3 · 中央存了我啥 (调 /api/audit/me 验) ──")
    if not central_section["reachable"]:
        print(f"  ⚠ 中央段未连通: {central_section['reason']}")
        print(f"  (本机段照样有效, 离线员工也能审本机数据)")
    else:
        d = central_section["data"]
        print(f"  gateway: {central_section.get('gateway', '?')}")
        print(f"  user_email: {d.get('user_email')}")
        print(f"  department: {d.get('department') or '(未配置)'}")
        print(f"  今日: {d.get('request_count', 0)} 请求, {d.get('total_tokens', 0)} tokens")
        bm = d.get("by_model") or []
        if bm:
            print(f"  今日按模型:")
            for r in bm[:10]:
                print(f"    - {r['model']}: {r['count']} 次, {r['total_tokens']} tokens")
        if d.get("first_seen_ts"):
            f_str = time.strftime('%Y-%m-%d', time.localtime(d["first_seen_ts"] / 1000))
            l_str = time.strftime('%Y-%m-%d', time.localtime((d.get("last_seen_ts") or d["first_seen_ts"]) / 1000))
            print(f"  中央对我的最早记录: {f_str}  最新: {l_str}")
        print(f"  schema_note: {d.get('schema_note', '')}")

        # 自洽性检查: 只在 jsonl backend 模式 (非 PG-only) 才对照
        # PG-only 时本机 jsonl 不更新, 跟中央对比永远差一截 — 不该报警
        if not pg_only_mode and a["exists"] and d.get("request_count", 0) > 0:
            diff = abs(d.get("request_count", 0) - a["request_count"])
            if diff > 5:
                print(f"  ⚠ 本机今日 {a['request_count']} ≠ 中央 {d['request_count']} (差 {diff}), 可能漏统计 / 边缘gateway 没刷新")

    # 契约文案按 backend 动态调整 — PG-only 跟 jsonl-mirror 时代说法不同
    print("\n── 隐私契约 (审计判定依据) ──")
    if pg_only_mode:
        contracts = [
            "中央只存 metadata (count / tokens / model / 时间戳), 不存 prompt / response 文本.",
            "对话 / 长期记忆 / 第三方 API key 全在本机 (~/.hermes/, ~/.catfish/), 不上传.",
            "中央当前走 PG-only backend (gateway metrics.py 直写 PG, 不再镜像本机 jsonl).",
            "中央 /api/audit/me 返的就是 schema_note 写的字段, 多一个少一个就是契约违反.",
        ]
    else:
        contracts = report["privacy_contract"]
    for line in contracts:
        print(f"  · {line}")

    print(f"\n报告完成. 想给机器 / CI 看: 加 --json")
    return 0 if central_section["reachable"] else 1


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

    # BL-HERMES-SERVICE-TOKEN (5/24): 独立 refresh hermes service token, 不动 user OAuth.
    # 适合 cron / 30 天前手动跑. 跟 `refresh` 命令的区别: refresh 续 user OAuth (顺手
    # 也续 hermes); refresh-hermes 只续 hermes, 不需要 user 登录态.
    refresh_hermes_p = sub.add_parser(
        "refresh-hermes",
        parents=[common],
        help="Mint 新 hermes service token + 写 hermes config (30 天 TTL, 跟 user OAuth 解耦)",
    )
    # BL-EDGE-TOOL-PROXY (5/25): 默认只检测+警告死代理. 加 flag 才真自动 restart hermes
    # 跟 clean env (unset 死的 HTTPS_PROXY/HTTP_PROXY/ALL_PROXY). 推荐 demo / cron 用.
    refresh_hermes_p.add_argument(
        "--restart-hermes",
        action="store_true",
        help="检测到死代理时, 用 clean env (unset HTTPS_PROXY 等) 自动 hermes gateway restart",
    )
    refresh_hermes_p.set_defaults(func=cmd_refresh_hermes)

    # BL-EMPLOYEE-PRIVACY-VERIFICATION (#76, 5/25): 员工自查 "本机存了啥 + 中央存了我啥"
    privacy_p = sub.add_parser(
        "privacy-audit",
        help="员工自查: 本机存了啥 + 中央存了我啥 (透明性卖点兑现)",
    )
    privacy_p.add_argument(
        "--json",
        action="store_true",
        help="输出 JSON (供 CI / 软著合规脚本机器读)",
    )
    privacy_p.set_defaults(func=cmd_privacy_audit)

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
