"""PUBLIC_HOST 的解析 —— envgen / certgen / certcheck 共用这一份 (setup.sh、setup.ps1、
renew-cert 都只是把原样的字符串传进来)。

# 是什么

中央服务器只有一个内网 IP (SERVER_IP), 内网员工照旧用 https://<IP>。PUBLIC_HOST 是
**另外**的对外地址, 逗号分隔, 每项:

    主机[:对外 https 端口]

  · 主机 = 域名 (catfish.example.com) 或公网 IP (203.0.113.7)
  · 端口 = 外面的人实际打开的 https 端口。公网映射常常换端口 (公网 203.0.113.7:8443 →
    内网 192.168.31.199:443), 所以要单独写; 不写 = 跟内部 HTTPS 端口 (CATFISH_HTTPS_PORT) 一样

每项都会变成一个 OIDC issuer / 门户来源: https://主机[:端口] (443 不写)。

# 为什么不让 shell 自己切

三个调用方 (bash / PowerShell / renew 的 cron) 各切一遍, 迟早有一边不认 ":8443" 或者大小写。
"""
from __future__ import annotations

import ipaddress
import re

_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


class PublicHostError(ValueError):
    """给现场看的一句话 (中文, 说清楚哪一项、怎么改)。"""


def _is_ipv4(host: str) -> bool:
    try:
        return isinstance(ipaddress.ip_address(host), ipaddress.IPv4Address)
    except ValueError:
        return False


def parse_entry(raw: str) -> tuple[str, int | None]:
    """一项 → (主机, 对外端口 或 None)。不合法抛 PublicHostError。"""
    e = raw.strip().lower()
    for scheme in ("https://", "http://"):
        if e.startswith(scheme):
            e = e[len(scheme):]
    e = e.rstrip("/")
    if not e:
        raise PublicHostError("PUBLIC_HOST 里有一项是空的 (多了一个逗号?)")
    if "/" in e or "?" in e or "#" in e:
        raise PublicHostError(f"PUBLIC_HOST 的 {raw.strip()!r} 带了路径; 只写 主机 或 主机:端口, 例 catfish.example.com")
    if e.startswith("[") or e.count(":") > 1:
        raise PublicHostError(f"PUBLIC_HOST 的 {raw.strip()!r}: 暂不支持 IPv6 地址, 请用域名或 IPv4")
    host, port = e, None
    if ":" in e:
        host, p = e.rsplit(":", 1)
        if not p.isdigit() or not 0 < int(p) < 65536:
            raise PublicHostError(f"PUBLIC_HOST 的 {raw.strip()!r}: 端口 {p!r} 不对, 应是 1-65535 的数字")
        port = int(p)
    if _is_ipv4(host):
        ip = ipaddress.ip_address(host)
        if ip.is_loopback or ip.is_unspecified or ip.is_link_local:
            raise PublicHostError(f"PUBLIC_HOST 的 {host} 不能用: 只有服务器自己访问得到")
        return host, port
    if "*" in host:
        raise PublicHostError(f"PUBLIC_HOST 的 {host}: 不支持通配符, 把要用的域名逐个写出来 (逗号分隔)")
    labels = host.split(".")
    if len(host) > 253 or not all(_LABEL.match(lb) for lb in labels) or host.replace(".", "").isdigit():
        raise PublicHostError(
            f"PUBLIC_HOST 的 {raw.strip()!r} 不是合法的域名或 IPv4 (只能有字母、数字、点和减号)"
        )
    return host, port


def parse_list(raw: str) -> list[tuple[str, int | None]]:
    """整串 → [(主机, 端口|None)], 去重 (同一主机只留第一项)。空串 → []。"""
    out: list[tuple[str, int | None]] = []
    seen: set[str] = set()
    if (raw or "").strip().lower() == "none":  # PUBLIC_HOST=none / setup.ps1 占位: 没有对外地址
        return out
    for part in (raw or "").split(","):
        if not part.strip():
            continue
        host, port = parse_entry(part)
        if host in seen:
            continue
        seen.add(host)
        out.append((host, port))
    return out


def normalize(entries: list[tuple[str, int | None]]) -> str:
    """写回 .env 的形式: 端口只在显式给了时才写 (不写的跟着 CATFISH_HTTPS_PORT 走)。"""
    return ",".join(h if p is None else f"{h}:{p}" for h, p in entries)


def origin(host: str, port: int | None, https_port: int) -> str:
    """https://主机[:端口] —— issuer / CORS 来源 / 员工打开的地址。"""
    p = port or https_port
    return f"https://{host}" + ("" if p == 443 else f":{p}")


def is_ip(host: str) -> bool:
    return _is_ipv4(host)


def domains(entries: list[tuple[str, int | None]]) -> list[str]:
    return [h for h, _ in entries if not _is_ipv4(h)]


def ips(entries: list[tuple[str, int | None]]) -> list[str]:
    return [h for h, _ in entries if _is_ipv4(h)]
