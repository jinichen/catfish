"""OAuth 2.0 authorization_code flow —— 开浏览器、收 loopback callback、换 token。

8/15 从 catfish.py 搬出来。

# 这一组的设计约束 (原 catfish.py 头部 docstring, 别丢)

- authorization_code flow (RFC 6749 §4.1) + 浏览器 (RFC 8252 §7.3 native app)
- redirect_uri = http://localhost:RANDOM_PORT/callback —— **loopback 防中间人**
- state 做 CSRF 防御 (secrets 随机 32 字节)
- **不存 client_secret** —— public client (RFC 6749 §2.1)。装机包里不该有任何
  secret。要改这一条之前先想清楚: 这个 CLI 是发到员工机器上的。
- MVP 没上 PKCE (catfish-identity 当时还不支持)

# 依赖方向

依赖 catfish_config (常量) 和 catfish_token (TokenStore / _decode_jwt_payload)。
不反向依赖 catfish.py —— 那会造成循环导入, 而且 catfish.py 直接跑的时候模块名
是 `__main__`, `import catfish` 会把同一份源码再执行一遍拿到第二个 module 对象。
"""
from __future__ import annotations

import http.server
import json
import secrets
import socket
import threading
import time
import urllib.error   # ← 见 catfish_token.py 顶部关于这一行的说明
import urllib.parse
import urllib.request
import webbrowser
from typing import Optional

from catfish_config import CALLBACK_TIMEOUT_SECS, logger
from catfish_token import TokenStore, _decode_jwt_payload

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

