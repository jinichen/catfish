"""一个 identity, 多个对外地址 (IP + 域名 / 公网映射) —— 按请求挑 issuer。

# 为什么

OIDC 的 issuer 必须等于浏览器 / App **实际打开的那个地址**: 浏览器从
`<issuer>/.well-known/openid-configuration` 拿到的 issuer 跟它请求的 authority
对不上, oidc-client 直接报错; token 的 `iss` 又是网关验签的依据。

装机以前只认一个地址 (https://<服务器 IP>)。现在内网员工照旧走 IP, 外面的人
(和外网手机) 走域名 / 公网映射地址, 两条路同时要能登录, 所以:

  · CATFISH_IDENTITY_ISSUERS = 逗号分隔的列表, 第一个是"主地址" (canonical)
  · 老的 CATFISH_IDENTITY_ISSUER 照旧可用 (= 只有一个元素的列表)
  · 每个请求按它的**外部来源** (scheme + host + 端口) 在列表里找, 找到就用那个,
    找不到用主地址 —— 跟以前的行为一样

外部来源怎么算: web 容器的 nginx 反代时带 `X-Forwarded-Proto` 和
`X-Forwarded-Host: $http_host` (浏览器发来的原样 Host, **带端口**; nginx 的 $host
会把端口去掉, 公网 203.0.113.7:8443 → 内网 443 这种映射就认不出来了)。
没有反代时 (HTTP 模式直连 8998) 就看请求自己的 Host 和 scheme。

Host 头是客户端可控的, 但这里只会在**配置好的列表**里选, 选不中就是主地址,
不会把请求里的任意字符串当 issuer 发出去。
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit

DEFAULT_PORTS = {"http": 80, "https": 443}


def parse_issuer_list(raw: str) -> list[str]:
    """逗号分隔 → 去空、去尾斜杠、去重 (保序)。"""
    out: list[str] = []
    for part in (raw or "").split(","):
        v = part.strip().rstrip("/")
        if v and v not in out:
            out.append(v)
    return out


def configured_issuers(default: str) -> list[str]:
    """CATFISH_IDENTITY_ISSUERS > CATFISH_IDENTITY_ISSUER > default。第一个是主地址。

    两个都设了时, 单值那个排第一 (它就是 .env 里的主地址), 列表里的其余跟在后面。
    """
    single = parse_issuer_list(os.environ.get("CATFISH_IDENTITY_ISSUER", ""))
    many = parse_issuer_list(os.environ.get("CATFISH_IDENTITY_ISSUERS", ""))
    merged: list[str] = []
    for v in single[:1] + many + single[1:]:
        if v not in merged:
            merged.append(v)
    return merged or [default.rstrip("/")]


def origin_key(scheme: str, host: str, port: int | None) -> tuple[str, str, int]:
    scheme = (scheme or "http").lower()
    return scheme, (host or "").lower().strip("[]"), port or DEFAULT_PORTS.get(scheme, 0)


def _split_hostport(hostport: str) -> tuple[str, int | None]:
    hostport = (hostport or "").strip()
    if not hostport:
        return "", None
    try:
        u = urlsplit(f"//{hostport}")
        return u.hostname or "", u.port
    except ValueError:  # 端口不是数字之类
        return "", None


def issuer_origin(issuer: str) -> tuple[str, str, int]:
    u = urlsplit(issuer)
    try:
        port = u.port
    except ValueError:
        port = None
    return origin_key(u.scheme, u.hostname or "", port)


def request_origin(headers, url_scheme: str, url_netloc: str) -> tuple[str, str, int]:
    """请求的外部来源。headers 是大小写不敏感的映射 (starlette Headers)。"""
    proto = (headers.get("x-forwarded-proto") or "").split(",")[0].strip() or url_scheme
    raw_host = (headers.get("x-forwarded-host") or "").split(",")[0].strip()
    raw_host = raw_host or headers.get("host") or url_netloc
    host, port = _split_hostport(raw_host)
    return origin_key(proto, host, port)


def pick_issuer(issuers: list[str], origin: tuple[str, str, int]) -> str:
    """在 issuers 里找跟 origin 对得上的; 对不上返回第一个 (主地址)。

    1. scheme + host + 端口完全一致
    2. 只看 host, 且列表里**只有一个**这个 host 的 issuer —— 兜住前面有一层只转
       `$host` (不带端口) 的反代: 公网映射了非 443 端口, 端口信息在路上丢了
    """
    if not issuers:
        raise ValueError("issuers 为空")
    for iss in issuers:
        if issuer_origin(iss) == origin:
            return iss
    same_host = [iss for iss in issuers if issuer_origin(iss)[1] == origin[1] and origin[1]]
    if len(same_host) == 1:
        return same_host[0]
    return issuers[0]


def issuer_for_request(issuers: list[str], request) -> str:
    """FastAPI Request → issuer。只有一个地址时不看请求 (跟以前完全一样)。"""
    if len(issuers) == 1:
        return issuers[0]
    return pick_issuer(
        issuers, request_origin(request.headers, request.url.scheme, request.url.netloc)
    )
