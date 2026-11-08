"""连中央的 HTTPS: 默认信任库 + 公司内部 CA (11/08, IP + 域名同时用)。

⚠ 同一份文件在两处, 一字不差 (tests/test_central_tls.py 比对):
    edge/tool-bridge/src/catfish_tool_bridge/central_tls.py
    edge/hermes-plugins/catfish-xcatfish-user/central_tls.py
hermes 的 venv 里不一定装了 tool-bridge, 插件目录又是单独部署的, 只能各带一份。

# 为什么

员工在公司外面用域名 / 公网映射连中央时, 网关也走门户的 https 地址 (外面只开了这一个端口)。
那个地址的证书有两种:
  · 正式证书 (公网 CA 签的): httpx / requests 默认的信任库 (certifi) 本来就认 —— 什么都不用做
  · 内部证书 (setup 生成的内部 CA 签的): 默认信任库不认, hermes / tool-bridge 连网关全部
    SSL 失败。Companion (Rust) 那边是在系统根证书**之上**加 ~/.catfish/server-ca.pem
    (接入码导入的那张), Python 这边以前没有对应的东西 —— 因为以前网关只走 http://IP:8999

**不能**写成 verify="~/.catfish/server-ca.pem": 那会**替换**掉默认信任库, 正式证书的域名
和所有公网服务 (模型供应商等) 反而全验不过。要的是「默认的 + 内部 CA」。

# 怎么做

进程一启动 (hermes 插件 register / tool-bridge main) 调 ensure_central_ca_env():
  · ~/.catfish/server-ca.pem 不在 → 什么都不做 (跟以前完全一样)
  · 在 → 把 certifi 的根证书 + 系统 OpenSSL 默认 cafile + server-ca.pem 拼成
    ~/.catfish/ca-bundle.pem, 设 SSL_CERT_FILE / REQUESTS_CA_BUNDLE 指向它。
    httpx (含 openai SDK)、requests、urllib 都认这两个变量, 之后建的客户端 / 子进程全生效。
  · 别人已经设了 SSL_CERT_FILE (IT 统一配的公司根证书) → 不覆盖, 只提醒
需要自己建 SSLContext 的地方用 central_ssl_context()。
"""
from __future__ import annotations

import logging
import os
import ssl
from pathlib import Path

logger = logging.getLogger("catfish.central_tls")

BUNDLE_NAME = "ca-bundle.pem"
_OURS_MARK = "# catfish ca-bundle: default roots + ~/.catfish/server-ca.pem\n"


def server_ca_path() -> Path:
    return Path(os.environ.get("CATFISH_HOME") or (Path.home() / ".catfish")) / "server-ca.pem"


def _default_roots() -> list[Path]:
    out: list[Path] = []
    try:
        import certifi  # noqa: PLC0415

        out.append(Path(certifi.where()))
    except Exception:  # noqa: BLE001 —— 没装 certifi 就只用系统的
        pass
    # openssl_cafile = 编译时的系统默认 (Linux 上就是发行版的根证书)。不用 .cafile ——
    # 那个会跟着 SSL_CERT_FILE 走, 设过一次之后就指向我们自己拼的 bundle, 再拼就套娃了。
    p = ssl.get_default_verify_paths().openssl_cafile
    bundle = server_ca_path().parent / BUNDLE_NAME
    if p and Path(p).is_file() and Path(p) not in out and Path(p) != bundle:
        out.append(Path(p))
    return out


def central_ssl_context() -> ssl.SSLContext:
    """默认信任库 + 内部 CA (有的话)。给自己建连接的地方用。"""
    ctx = ssl.create_default_context()
    for root in _default_roots():
        try:
            ctx.load_verify_locations(cafile=str(root))
        except (OSError, ssl.SSLError):
            pass
    ca = server_ca_path()
    if ca.is_file():
        try:
            ctx.load_verify_locations(cafile=str(ca))
        except (OSError, ssl.SSLError) as e:
            logger.error("公司证书 %s 读不了, 没加进信任库: %s", ca, e)
    return ctx


def ensure_central_ca_env() -> str | None:
    """有内部 CA 就拼合并信任库并设环境变量; 返回生效的 bundle 路径 (没动返回 None)。"""
    ca = server_ca_path()
    if not ca.is_file():
        return None
    bundle = ca.parent / BUNDLE_NAME
    current = os.environ.get("SSL_CERT_FILE", "")
    if current and Path(current) != bundle:
        logger.warning(
            "SSL_CERT_FILE 已经被设成 %s, 不覆盖; 公司内部 CA (%s) 要自己加进那个文件, "
            "否则用内部证书的中央地址会 SSL 失败", current, ca,
        )
        return None
    try:
        ca_pem = ca.read_text(encoding="utf-8", errors="replace")
        if "BEGIN CERTIFICATE" not in ca_pem or "PRIVATE KEY" in ca_pem:
            logger.error("%s 不是公开证书 (PEM), 不用它", ca)
            return None
        parts = [_OURS_MARK]
        for root in _default_roots():
            parts.append(root.read_text(encoding="utf-8", errors="replace").rstrip("\n") + "\n")
        parts.append(f"# --- {ca} ---\n" + ca_pem.rstrip("\n") + "\n")
        text = "".join(parts)
        if not bundle.is_file() or bundle.read_text(encoding="utf-8", errors="replace") != text:
            tmp = bundle.with_name(BUNDLE_NAME + ".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, bundle)
    except OSError as e:
        logger.error("拼合并信任库失败 (连用内部证书的中央地址会 SSL 失败): %s", e)
        return None
    os.environ["SSL_CERT_FILE"] = str(bundle)
    os.environ.setdefault("REQUESTS_CA_BUNDLE", str(bundle))
    logger.info("连中央: 默认信任库 + 公司内部 CA → %s", bundle)
    return str(bundle)
