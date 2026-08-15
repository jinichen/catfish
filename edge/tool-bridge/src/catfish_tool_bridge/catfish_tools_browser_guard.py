"""SSRF 白名单与错误分类 —— 纯判断, 不碰浏览器。

BL-TOOL-SPLIT 8/15: 从 catfish_tools_browser.py 抽出来 (1575 行超限), 沿用
5/20 那次 (browser 从 catfish_tools 抽出来) 的同一套做法。纯搬迁, 逻辑一行未改。
"""
from __future__ import annotations

from urllib.parse import urlparse

# ── BL-HERMES013-2 (5/11): cloud-metadata SSRF deny ──────────────────
#
# 借鉴 Hermes 0.13 `Browser — enforce cloud-metadata SSRF floor in hybrid routing`.
# 防 LLM 被 prompt injection 引导去访问云厂商 metadata 服务 (AWS IMDS / GCP /
# Azure / 阿里云 / ECS) 泄露 IAM credentials.
#
# **不拦内网 IP**: catfish 主客户央企内网 (10.10.40.102 EIS / 192.168 / 172.16),
# 这些是日常正常 URL, 不该拦. 只拦真正的 metadata 端点 + link-local 段.
_SSRF_DENY_HOSTS = frozenset({
    # AWS IMDSv1/v2, GCP, Azure metadata (link-local 段)
    "169.254.169.254",
    # ECS task metadata
    "169.254.170.2",
    # GCP metadata (DNS 名)
    "metadata.google.internal",
    "metadata",
    # 阿里云 metadata
    "100.100.100.200",
    # AWS IPv6 IMDS
    "fd00:ec2::254",
})

# link-local 段整段拦 (IPv4 169.254.0.0/16, IPv6 fe80::/10).
# 内网常用 169.254 是 link-local autoconf, 不该有正经服务.
_SSRF_DENY_IPV4_PREFIXES = ("169.254.",)
_SSRF_DENY_IPV6_PREFIXES = ("fe80::", "fd00:ec2:")


def _check_ssrf_safe(url: str) -> str | None:
    """检测 url 是否撞 cloud-metadata SSRF deny list.

    返 None = 安全可访问, 返 str = 拒绝原因.

    Args:
      url: 完整 URL (http:// / https:// 前缀)
    """
    if not url:
        return None
    try:
        from urllib.parse import urlparse  # noqa: PLC0415
        host = urlparse(url).hostname
        if not host:
            return None
        h = host.lower()
        # 名字精确匹配
        if h in _SSRF_DENY_HOSTS:
            return (
                f"SSRF deny: '{h}' 是云厂商 metadata 端点 (IAM credentials 泄露风险). "
                "catfish 默认拦截. 如果是误判 (内网巧合同名), 跟 IT 报."
            )
        # IPv4 link-local
        for prefix in _SSRF_DENY_IPV4_PREFIXES:
            if h.startswith(prefix):
                return (
                    f"SSRF deny: '{h}' 在 169.254.0.0/16 link-local 段 "
                    "(AWS/GCP/Azure metadata 标准位置). catfish 默认拦截."
                )
        # IPv6 link-local
        for prefix in _SSRF_DENY_IPV6_PREFIXES:
            if h.startswith(prefix):
                return (
                    f"SSRF deny: '{h}' 在 IPv6 link-local 段. catfish 默认拦截."
                )
        return None
    except Exception:  # noqa: BLE001
        # 解析失败不阻塞业务, 兜底放过 (上层有 timeout / network err 各种兜底)
        return None


# BL-BROWSER-LOAD-NEVER-FIRES (7/27): Playwright 超时错误的识别.
# page.goto 超时抛 TimeoutError, message 形如
#   "Timeout 30000ms exceeded." / "page.goto: Timeout 30000ms exceeded."
# 注意跟 _NET_LAYER_ERRORS 区分: 那些是"连不上服务器", 这个是"连上了但等不到
# 完成信号" —— 两种要走完全不同的 fallback.
_TIMEOUT_MARKERS = ("timeout", "timederror", "timed out")


def _is_timeout_error(err: str) -> bool:
    """错误信息是不是超时类. 大小写不敏感."""
    low = (err or "").lower()
    return any(m in low for m in _TIMEOUT_MARKERS)


# Chrome 网络层 net_error 关键字 (区别于 404/500 这种 server 层 error).
# 撞这些 = 根本连不上服务器 → 适合走 https→http fallback.
_NET_LAYER_ERRORS = (
    "ERR_CONNECTION_REFUSED",
    "ERR_CONNECTION_RESET",
    "ERR_CONNECTION_CLOSED",
    "ERR_SSL_PROTOCOL_ERROR",
    "ERR_CERT_",
    "ERR_TIMED_OUT",
)
