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
    "10.0.0.0/8",       # 大型企业内网 (含 INTERNAL_LLM_BASE_* 指向的内网 LLM)
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
    except (TimeoutError, OSError):
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


def _check_upstream(
    api_base: str | None,
    api_key: str | None = None,
    timeout: float = 1.5,
) -> tuple[bool, str]:
    """探一个上游 LLM 是否可达。返回 (ok, reason)。

    策略分两类:

    **外网模型 (api_base = None)**: 走 LiteLLM 内置 endpoint (Google / OpenAI / Anthropic)。
        LiteLLM 用各 provider 自己的 SDK,不读 HTTPS_PROXY 也不读我们的 NO_PROXY。
        所以靠代理状态推断不可靠 — 之前这里报的"无代理 不可达"对 Gemini 直接错位。
        现在策略: 信任 api_key_configured。配了 key 就标 reachable,实际能不能用
        以第一次业务调用反馈为准 (LiteLLM 自带 retry 机制)。

    **内网 / 自定义 endpoint**: TCP probe + 一次 HTTP GET /models 验证 server 应用层活。
        之前只 TCP probe → server 层 ConnectionReset 时还显示绿,误导员工。
        现在 TCP 通后再做 HTTP HEAD 等价的 GET /models, server 真返回(2xx/4xx)才算 reachable。
    """
    if not api_base:
        # 外网模型: 信任 LiteLLM SDK 自己处理网络层
        return True, "外网模型(LiteLLM SDK 直调,以实际请求为准)"

    try:
        from urllib.parse import urlparse  # noqa: PLC0415
        parsed = urlparse(api_base)
    except ValueError:
        return False, f"非法 URL:{api_base}"
    if not parsed.hostname:
        return False, "无 hostname"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    # ── 第一层: TCP probe ──
    if not _check_tcp_port(parsed.hostname, port, timeout=timeout):
        return False, f"超时 {parsed.hostname}:{port}(VPN/网络?)"

    # ── 第二层: HTTP GET /models 验证 server 应用层活 ──
    # 跳过这一步: TCP 通但 server RST 时还会显示绿 → 失真
    return _check_http_models(api_base, api_key, timeout=timeout, host_port=f"{parsed.hostname}:{port}")


def _check_http_models(
    api_base: str,
    api_key: str | None,
    timeout: float,
    host_port: str,
) -> tuple[bool, str]:
    """对内网 endpoint 做一次 HTTP GET <api_base>/models 验证应用层活着。

    成功标准:
        - 2xx: server 完全活
        - 4xx: server 在响应,只是请求格式 / 鉴权问题(对 reachability 而言 OK)
        - 5xx: server 异常
        - Connection reset / refused: server 拒绝连接(TCP 层通但应用层挂)

    用 stdlib urllib.request 同步发请求,不引入 httpx 依赖且跟 _check_tcp_port 同进程同步逻辑。
    """
    import urllib.error  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415

    url = f"{api_base.rstrip('/')}/models"
    req = urllib.request.Request(url, method="GET")
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, f"直连 {host_port} (HTTP {resp.status})"
    except urllib.error.HTTPError as e:
        # 4xx: server 在响应,只是这个请求被拒(鉴权 / 路径) — 对可达性而言算活
        if 400 <= e.code < 500:
            return True, f"直连 {host_port} (HTTP {e.code},server 活)"
        return False, f"server {e.code}"
    except urllib.error.URLError as e:
        # URLError 包了底层异常 (ConnectionResetError / ConnectionRefusedError 等)
        reason = e.reason
        if isinstance(reason, ConnectionResetError):
            return False, f"{host_port} TCP 通但 HTTP RST(server 拒绝)"
        if isinstance(reason, ConnectionRefusedError):
            return False, f"{host_port} 拒绝连接"
        if isinstance(reason, TimeoutError):
            return False, f"{host_port} HTTP 超时"
        return False, f"{host_port} 网络异常: {type(reason).__name__}"
    except (ConnectionResetError, ConnectionRefusedError) as e:
        return False, f"{host_port} {type(e).__name__}"
    except Exception as e:  # 兜底,任何其他异常都算 reachability 失败
        return False, f"{host_port} 异常: {type(e).__name__}"


def report_upstream_reachability(
    models, silent: bool = False
) -> dict[str, dict[str, object]]:
    """探每个 chat 模型上游是否可达,返回缓存。

    串行探测，每个 timeout 1.5s。5 个模型最多 ~7.5s 启动延迟，
    换来员工启动后立刻知道现在能调哪些 LLM。

    silent=False (默认): 打印 banner —— 启动时用
    silent=True: 不打印任何东西 —— 后台定期刷新时用,免得日志被 banner 刷屏

    返回 {model_name: {"reachable": bool, "reason": str}} 给 app.state 缓存，
    /v1/catalog 直接读这个，避免每次列模型都做 TCP 探测。
    """
    results: dict[str, dict[str, object]] = {}
    if not silent:
        print("─" * 70, flush=True)
        print("🐟 上游 LLM 可达性自检", flush=True)
    any_available = False
    for m in models:
        if m.mode == "embedding":
            continue
        if not m.upstream.is_available:
            results[m.name] = {"reachable": False, "reason": "API key 未配"}
            if not silent:
                print(f"   ✗ {m.name:32s} N/A（API key 未配）", flush=True)
            continue

        ok, reason = _check_upstream(
            m.upstream.api_base,
            api_key=m.upstream.api_key,
        )
        results[m.name] = {"reachable": ok, "reason": reason}
        if not silent:
            mark = "✓" if ok else "✗"
            print(f"   {mark} {m.name:32s} {reason}", flush=True)
        if ok:
            any_available = True

    if not silent:
        if not any_available:
            print("", flush=True)
            print("   ⚠ 当前没有可调用的 LLM。常见原因：", flush=True)
            print("       - 内网模型不可达 → 检查 VPN / 内网连接", flush=True)
            print("       - 外网模型不可达 → 启动 Clash / 配代理", flush=True)
        print("─" * 70, flush=True)
    return results
