"""网络层屏蔽 —— 让员工不用 care HTTPS_PROXY / Clash / NO_PROXY 这些系统状态。

启动 gateway 前做三件事：
    1. 检测当前 HTTPS_PROXY 端口是否可达（Clash / Mihomo 在不在跑）
    2. 不可达就 unset，避免 gateway 内部调外网时去连一个死端口
    3. 强制 NO_PROXY 包含本机 + 公司内网网段（无论员工 shell 怎么配）

输出一行人话状态行让员工知道"现在网络栈是什么状态"。

设计哲学：
    员工的代理 / VPN / Clash 状态是**实现细节**，不是员工要 care 的事。
    鲶鱼平台的责任是把这些复杂度藏在 gateway 启动里，
    员工启动时看到的应该是"OK 现在能调内网 LLM" / "OK 现在外网也能"。
"""
from __future__ import annotations

import logging
import os
import socket
from urllib.parse import urlparse

logger = logging.getLogger("catfish.gateway.network")

# 永远不走代理的目标 —— 本机 + 公司内网典型网段（RFC1918）
DEFAULT_NO_PROXY = [
    "localhost",
    "127.0.0.1",
    "::1",
    "10.0.0.0/8",       # 大型企业内网（含我们的 10.10.40.102 内网 LLM）
    "172.16.0.0/12",
    "192.168.0.0/16",
    ".internal",        # 一些企业 DNS 后缀
    ".corp",
    ".local",
]

PROXY_VARS = (
    "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY",
    "https_proxy", "http_proxy", "all_proxy",
)


def _check_tcp_port(host: str, port: int, timeout: float = 2.0) -> bool:
    """快速测 TCP 端口是否可连。代理活着的前置条件。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


def _parse_proxy_url(url: str) -> tuple[str, int] | None:
    """从 'http://1.2.3.4:7890' 提取 ('1.2.3.4', 7890)。"""
    if not url:
        return None
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if not parsed.hostname:
        return None
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return parsed.hostname, port


def _current_proxy_url() -> str:
    """优先级：HTTPS_PROXY > https_proxy > HTTP_PROXY > http_proxy。"""
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        val = os.environ.get(var)
        if val:
            return val
    return ""


def precheck_and_setup() -> dict[str, str]:
    """启动 gateway 前的网络栈准备：

    1. 看代理是否活着；死了就 unset
    2. 强制 NO_PROXY 包含本机 + 内网
    3. 返回人话状态供 banner 打印

    返回 {"proxy": ..., "no_proxy": ..., "verdict": "ok|fixed|none"}
    """
    status: dict[str, str] = {"proxy": "", "no_proxy": "", "verdict": ""}

    # ----- 步骤 1: 代理可达性检测 -----
    current_proxy = _current_proxy_url()
    if not current_proxy:
        status["proxy"] = "○ 无代理（直连模式）"
        status["verdict"] = "none"
    else:
        parsed = _parse_proxy_url(current_proxy)
        if parsed and _check_tcp_port(*parsed):
            status["proxy"] = f"✓ 代理可达：{current_proxy}"
            status["verdict"] = "ok"
        else:
            # 代理 down 或地址非法 → 主动 unset 避免 gateway 内部失败
            for var in PROXY_VARS:
                os.environ.pop(var, None)
            status["proxy"] = (
                f"⚠ 代理不可达：{current_proxy} → 已清除"
                f"（外网 LLM 暂不可调，内网照常）"
            )
            status["verdict"] = "fixed"

    # ----- 步骤 2: 强制 NO_PROXY 包含本机 + 内网 -----
    existing_parts: list[str] = []
    for var in ("NO_PROXY", "no_proxy"):
        val = os.environ.get(var, "")
        if val:
            existing_parts.extend(p.strip() for p in val.split(",") if p.strip())

    merged: list[str] = []
    seen: set[str] = set()
    for item in existing_parts + DEFAULT_NO_PROXY:
        if item not in seen:
            merged.append(item)
            seen.add(item)
    final_no_proxy = ",".join(merged)
    os.environ["NO_PROXY"] = final_no_proxy
    os.environ["no_proxy"] = final_no_proxy

    # 状态行只展示关键的内网网段，完整列表太长
    status["no_proxy"] = "本机 + 内网 (10.x / 192.168.x / 172.16-31.x) 永不走代理"
    return status


def print_banner(status: dict[str, str]) -> None:
    """打印一行人话的网络状态。"""
    print("─" * 70, flush=True)
    print("🐟 鲶鱼网关 · 网络层", flush=True)
    print(f"   代理：{status['proxy']}", flush=True)
    print(f"   规则：{status['no_proxy']}", flush=True)
    print("─" * 70, flush=True)


def _check_upstream(api_base: str | None, timeout: float = 1.5) -> tuple[bool, str]:
    """探一个上游 LLM 是否可达。返回 (ok, reason)。

    api_base = None 的模型（如 Gemini，走 LiteLLM 内置 endpoint）：
        靠当前代理状态推断 —— 有可达代理就认为可用，否则不可用
    """
    if not api_base:
        proxy = _current_proxy_url()
        if not proxy:
            return False, "外网模型，无代理（国内直连大概率不通）"
        parsed = _parse_proxy_url(proxy)
        if parsed and _check_tcp_port(*parsed, timeout=timeout):
            return True, f"经代理 {proxy}"
        return False, "外网模型，代理不可达"

    # 内网 / 自定义 endpoint：直接 TCP 探测
    try:
        from urllib.parse import urlparse  # noqa: PLC0415
        parsed = urlparse(api_base)
    except ValueError:
        return False, f"非法 URL：{api_base}"
    if not parsed.hostname:
        return False, "无 hostname"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if _check_tcp_port(parsed.hostname, port, timeout=timeout):
        return True, f"直连 {parsed.hostname}:{port}"
    return False, f"超时 {parsed.hostname}:{port}（VPN/网络？）"


def report_upstream_reachability(models) -> dict[str, dict[str, object]]:
    """启动后探每个 chat 模型上游是否可达，banner 打印 + 返回缓存。

    串行探测，每个 timeout 1.5s。5 个模型最多 ~7.5s 启动延迟，
    换来员工启动后立刻知道现在能调哪些 LLM。

    返回 {model_name: {"reachable": bool, "reason": str}} 给 app.state 缓存，
    /v1/catalog 直接读这个，避免每次列模型都做 TCP 探测。
    """
    results: dict[str, dict[str, object]] = {}
    print("─" * 70, flush=True)
    print("🐟 上游 LLM 可达性自检", flush=True)
    any_available = False
    for m in models:
        if m.mode == "embedding":
            continue
        if not m.upstream.is_available:
            results[m.name] = {"reachable": False, "reason": "API key 未配"}
            print(f"   ✗ {m.name:32s} N/A（API key 未配）", flush=True)
            continue

        ok, reason = _check_upstream(m.upstream.api_base)
        results[m.name] = {"reachable": ok, "reason": reason}
        mark = "✓" if ok else "✗"
        print(f"   {mark} {m.name:32s} {reason}", flush=True)
        if ok:
            any_available = True

    if not any_available:
        print("", flush=True)
        print("   ⚠ 当前没有可调用的 LLM。常见原因：", flush=True)
        print("       - 内网模型不可达 → 检查 VPN / 内网连接", flush=True)
        print("       - 外网模型不可达 → 启动 Clash / 配代理", flush=True)
    print("─" * 70, flush=True)
    return results
