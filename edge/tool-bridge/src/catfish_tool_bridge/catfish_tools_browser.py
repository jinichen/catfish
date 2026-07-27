"""Catfish 浏览器全栈 tools (catfish_browser_*) — 抽自 catfish_tools.py (5/20 拆分).

7 个 browser tool: goto / click / fill / snapshot / screenshot / find_by_text (主要)
+ Playwright 连接 / SSRF check / DOM a11y 抽取 / screenshot 压缩 helper.

走 Playwright connect_over_cdp(http://127.0.0.1:9222) — 复用员工已登录 Chrome,
不装 chromium binary. 全 sync API (sync_playwright), tool_bridge 工具调用是
await asyncio.to_thread 包装.

历史:
  v1 (4/27): hermes browser_navigate 直 CDP 撞页面没真换
  v2 (4/28): 直 CDP 撞 Chrome 138+ --remote-allow-origins 限制
  v3 (4/28-): Playwright connect_over_cdp, 失败率 30% → 5%
  5/20: 从 catfish_tools.py 抽出 (拆分 6454 → ~3000 行)
"""
from __future__ import annotations

import base64  # BL-CI-TOOLBRIDGE-MOCK-DRIFT (6/3): 5/20 拆分漏 import, browser_screenshot 真用 base64.b64encode
import json
import os
import re
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple

# BL-CI-TOOLBRIDGE-MOCK-DRIFT (6/3): 5/20 拆分时漏 import — catfish_tools_today 真 helper
# 真生产 catfish_browser_screenshot 真撞 NameError:
#   - _MAX_SCREENSHOT_BYTES: line 1124, 1129 size 检查
#   - _unix_to_iso: line 1147 captured_at meta
from .catfish_tools_today import _MAX_SCREENSHOT_BYTES, _unix_to_iso  # noqa: F401

# ============================================================
# 浏览器全栈 (catfish_browser_*) — 走 Playwright connect_over_cdp
# ============================================================
#
# 历史:
#   v1 (2026-04-27): hermes browser_navigate 直 CDP, 在 Companion 隔离 Chrome 上
#      ✓ 调用成功但页面没真换, 模型幻觉 "已打开". 自己写直 CDP 绕开 — catfish_browser_goto.
#   v2 (2026-04-28): 直 CDP 撞 Chrome 138+ --remote-allow-origins 限制, 没自动等待 / iframe
#      处理代码量大, 失败率仍高.
#   v3 (2026-04-28, 当前): 全栈换 Playwright connect_over_cdp(http://127.0.0.1:9222)
#      复用员工已登录 Chrome, 但 API 用 Playwright 的稳健版 (auto-waiting / retry / iframe).
#      失败率从 ~30% → ~5%.
#
# 设计要点:
#   - 不装 chromium binary (Playwright 默认会装 ~150MB), 用 connect_over_cdp 复用员工 Chrome
#   - 每次操作开新 Playwright instance + connect → 操作 → close. 性能够用 (人在等)
#   - 全 sync API (sync_playwright), 因为 tool_bridge 工具调用是 await asyncio.to_thread

import os as _os


def _chrome_base() -> str:
    """从 hermes config 或环境变量拿 chrome 调试端口 base url."""
    return _os.environ.get("CATFISH_CHROME_BASE", "http://127.0.0.1:9222")


# BL-FIX10 (5/8): Playwright sync API 卡死时 ignore 自带 timeout 参数, 拖死整个
# tool-bridge daemon (单线程). 用 concurrent.futures ThreadPoolExecutor + 硬
# timeout 兜底, 卡了直接返 error 给 LLM, daemon 继续服务别的请求.
#
# 副作用: 卡死的线程没法真杀 (Python 没有"杀线程"原语), 会泄漏直到下次 daemon 重启.
# 接受这个代价 — Companion watchdog 5s 检 tool-bridge 死活, 累计太多线程时
# Tauri restart_tool_bridge 命令一刀切. 以后真要根治得改 multiprocessing pool.
import concurrent.futures as _futures  # noqa: E402

_BROWSER_HARD_TIMEOUT_SEC = 30.0


def _run_with_hard_timeout(fn: Any, args: Dict[str, Any], hard_timeout_sec: float = _BROWSER_HARD_TIMEOUT_SEC) -> Dict[str, Any]:
    """在线程池里跑 fn(args), 硬超时直接返 error. 不真杀线程 (Python 限制).

    用法 — 在 browser_* 入口套一层:
        def browser_xxx(args):
            return _run_with_hard_timeout(_browser_xxx_impl, args)

    BL-FIX11 (5/8): 不用 ``with ThreadPoolExecutor()`` —— 那个的 ``__exit__``
    默认 ``shutdown(wait=True)`` 会**等卡死线程结束才返回**, timeout 等于没用.
    改成裸 executor + finally ``shutdown(wait=False)`` 让卡死线程后台跑去 (mac
    重启 GC), daemon 立刻继续服务别的请求.
    """
    pool = _futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="catfish_pw")
    try:
        future = pool.submit(fn, args)
        try:
            return future.result(timeout=hard_timeout_sec)
        except _futures.TimeoutError:
            # 硬超时 — 返 error, 线程仍在跑 (没法真杀), 让 daemon 继续服务别的请求
            tool_name = getattr(fn, "__name__", "browser_tool")
            return {
                "type": "error",
                "error": (
                    f"{tool_name} 硬超时 ({hard_timeout_sec}s) — Playwright 卡住没响应. "
                    f"page 状态可能不稳定 (navigation 中 / iframe 重载 / Chrome 没响应). "
                    f"建议: catfish_browser_snapshot 看页面当前结构, 或者改 selector "
                    f"用 'text=...' / 'role=...' 文字匹配, 或者 Companion 控制台重启 Chrome."
                ),
            }
    finally:
        # BL-FIX11: wait=False 关键, 不等卡死线程结束 — 否则 shutdown 自己卡, daemon 死
        pool.shutdown(wait=False)


def _connect_playwright_browser(playwright):
    """connect_over_cdp 复用 Companion 起的 Chrome.

    Returns:
        (browser, context, page) — context 是第一个 BrowserContext, page 是第一个 page.
        失败抛 RuntimeError, 上层 catch 转 friendly error.
    """
    chrome_base = _chrome_base()
    try:
        browser = playwright.chromium.connect_over_cdp(chrome_base)
    except Exception as e:
        raise RuntimeError(
            f"连不上 Chrome CDP {chrome_base}: {e}. "
            "Chrome 没起? Companion 控制台点'启动 Catfish Chrome'."
        ) from e

    contexts = browser.contexts
    if not contexts:
        # 极少见 — Chrome 没任何 context (新启动), 创建一个
        context = browser.new_context()
    else:
        context = contexts[0]

    pages = context.pages
    if not pages:
        page = context.new_page()
    else:
        page = pages[0]  # 第一个 page (about:blank 或员工正在用的 tab)

    return browser, context, page


def _import_playwright():
    """lazy import playwright, 失败友好提示装."""
    try:
        from playwright.sync_api import sync_playwright  # type: ignore  # noqa: PLC0415
        return sync_playwright
    except ImportError as e:
        raise RuntimeError(
            "缺 playwright 包. 装一下 (在 hermes venv): "
            "HTTPS_PROXY= HTTP_PROXY= ~/.hermes/hermes-agent/venv/bin/pip install "
            "--proxy '' playwright"
        ) from e


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


def browser_goto(args: Dict[str, Any]) -> Dict[str, Any]:
    """硬 timeout 兜底 wrapper, 调 _impl. 防 Playwright 卡死锁住整个 daemon."""
    return _run_with_hard_timeout(_browser_goto_impl, args)


def _browser_goto_impl(args: Dict[str, Any]) -> Dict[str, Any]:
    """走 Playwright `page.goto()`. connect_over_cdp 复用员工已登录 Chrome.

    https→http 自动 fallback (踩过坑 2026-04-28):
      模型默认补 https, 但中国电信内网很多老系统 (.ffcs.cn / .10086.cn) 只监听 80.
      https 过去直接 ERR_CONNECTION_REFUSED. 这里检测到网络层 error + url 是 https
      时, 自动用同一个 page 切 http 重试 1 次. 成功就加 fallback_hint 让模型记住.
      双保险: SOUL.md 也有"内网默认 http" 纪律, 这是工程层兜底.
    """
    url = (args.get("url") or "").strip()
    if not url:
        return {"type": "error", "error": "url 必填"}
    # BL-HERMES013-2 (5/11): SSRF deny — 拦云 metadata 端点防 IAM 泄露
    ssrf_err = _check_ssrf_safe(url)
    if ssrf_err:
        return {"type": "error", "error": ssrf_err}
    wait_until = (args.get("wait_until") or "load").lower()
    if wait_until not in {"load", "domcontentloaded", "networkidle"}:
        wait_until = "load"
    timeout_ms = int(float(args.get("timeout_seconds") or 30.0) * 1000)
    timeout_ms = max(1000, min(timeout_ms, 120_000))

    try:
        sync_playwright = _import_playwright()
    except RuntimeError as e:
        return {"type": "error", "error": str(e)}

    def _try_goto(page, target_url: str, wait: Optional[str] = None) -> Dict[str, Any]:
        """单次 goto 尝试, 包装成 result dict (不抛). wait 不给则用外层 wait_until."""
        wait = wait or wait_until
        try:
            response = page.goto(target_url, wait_until=wait, timeout=timeout_ms)
            actual_title = page.title()
            actual_url = page.url
            http_status = response.status if response else None
            matched = target_url in actual_url or actual_url.startswith(target_url[:20])
            return {
                "type": "ok",
                "navigated_to": target_url,
                "actual_title": actual_title,
                "actual_url": actual_url,
                "http_status": http_status,
                "wait_until": wait,
                "summary": (
                    f"已 navigate 到 {target_url}. 真实 title='{actual_title}', "
                    f"url='{actual_url}', http={http_status}. "
                    f"({'✓ 加载成功' if matched else '⚠ url 跟请求不一致, 可能重定向'})"
                ),
            }
        except Exception as e:
            return {"type": "error", "error": f"playwright goto 异常: {type(e).__name__}: {e}"}

    try:
        with sync_playwright() as p:
            try:
                browser, context, page = _connect_playwright_browser(p)
            except RuntimeError as e:
                return {"type": "error", "error": str(e)}

            result = _try_goto(page, url)

            # BL-BROWSER-LOAD-NEVER-FIRES (7/27 鸿波实盘): load 超时 → 自动降级
            # domcontentloaded 重试一次.
            #
            # 实测数据 (鸿波 Mac, agent-browser 直接调, 绕开 LLM):
            #   about:blank   → success   (CDP 链路没问题)
            #   example.com   → success   (真实网络导航没问题)
            #   sohu.com      → 25s 超时
            #   sohu.com + AGENT_BROWSER_DEFAULT_TIMEOUT=60000 → **仍然超时**
            #
            # 60 秒都等不到, 说明不是"慢", 是 `load` 事件**永远不会触发**.
            # 搜狐首页挂着一堆第三方域名 (广告 / 统计 / CDN), 员工网络环境下有些
            # 根本连不上, 那些请求一直挂着 → load 永不完成. 而页面 DOM 早就渲染
            # 出来了 —— 员工手动打开看着完全正常, 工具却报超时, 最难排查的那种.
            #
            # 为什么不干脆把默认改成 domcontentloaded: `load` 语义更强 (资源齐全,
            # 截图 / 取全文更准), 能等到就该等. 只在等不到时降级, 两头的好处都要.
            #
            # 降级成功会带 degraded_wait_until 字段 + summary 前缀, 让 LLM 知道
            # "页面能用但资源可能没齐", 后续要截图/取全文时自己判断要不要再等.
            if (
                result.get("type") == "error"
                and wait_until == "load"
                and _is_timeout_error(result.get("error", ""))
            ):
                fb = _try_goto(page, url, wait="domcontentloaded")
                if fb.get("type") == "ok":
                    fb["degraded_wait_until"] = "domcontentloaded"
                    fb["degraded_reason"] = (
                        f"等 'load' 超时 ({timeout_ms}ms) —— 页面有第三方资源一直加载不完 "
                        f"(广告 / 统计 / 被墙的 CDN 都会这样). 已降级到 'domcontentloaded' "
                        f"重试成功: DOM 已就绪, 页面可以正常操作, 但部分资源可能还没到位."
                    )
                    fb["summary"] = "[load 超时 → domcontentloaded 降级] " + fb["summary"]
                    return fb
                # 降级也失败 → 保留原始 error, 补一句说明避免 LLM 以为没试过
                result["error"] = (
                    f"{result['error']}\n"
                    f"注: 已自动降级 wait_until='domcontentloaded' 重试, 仍然失败 "
                    f"({fb.get('error', '')[:100]}). 这次多半是真连不上, 不是等 load 的问题."
                )

            # https → http fallback (网络层撞墙 + url 是 https 才触发)
            if (
                result.get("type") == "error"
                and url.lower().startswith("https://")
                and any(err in result.get("error", "") for err in _NET_LAYER_ERRORS)
            ):
                fallback_url = "http://" + url[len("https://"):]
                fb_result = _try_goto(page, fallback_url)
                if fb_result.get("type") == "ok":
                    fb_result["fallback_hint"] = (
                        f"⚠ {url} (https) 不通 ({result['error'][:80]}…), 自动 fallback "
                        f"到 {fallback_url} (http) 成功. 内网老系统常见 "
                        f"(.ffcs.cn / .10086.cn / .chinatelecom.cn 等). "
                        f"以后**直接用 http://**, 不要补 https://."
                    )
                    fb_result["summary"] = "[https→http fallback] " + fb_result["summary"]
                    return fb_result
                # fallback 也失败 → 增强原始 error 给员工更多线索
                result["error"] = (
                    f"{result['error']}\n"
                    f"注: 已自动尝试 http fallback ({fallback_url}) 也失败 "
                    f"({fb_result.get('error', '')[:80]}). 可能员工不在公司内网, 或服务器临时挂了."
                )
            return result
            # 不关 browser (员工日常 Chrome) / 不关 page (后续 tool 复用)
    except Exception as e:
        return {"type": "error", "error": f"playwright goto 异常: {type(e).__name__}: {e}"}


# ============================================================
# BL-TOOLBRIDGE-CONSOLE-TOOL (5/27 鸿波): browser_evaluate — 跑 JS 拿值
# ============================================================
#
# 起源: 5/26 晚那次 LLM 死循环 — 模型一直调 `catfish_browser_goto(expression=
# "document.querySelector(...)")`, 想跑 JS 但 catfish-tool-bridge 之前**根本没暴露
# JS eval 工具**. 模型从 prior 假设有 `browser_console`, schema 不让它选,
# fallback 到 goto 误用 expression 字段, error 拿到也学不会, 卡死循环烧 token.
#
# 修法:
#   1. 加 `catfish_browser_evaluate(expression)` — 走 Playwright `page.evaluate()`,
#      返序列化值 (字符串/数字/object/null), 加错误 friendly text.
#   2. 同函数注册两个 schema 名: `catfish_browser_evaluate` (规范名) +
#      `catfish_browser_console` (LLM 习惯叫法的 alias, 模型抓哪个都行).
#   3. browser_goto schema 收紧 additionalProperties: false, expression 字段直接
#      被 schema 拒, error message 显式提示 "你想跑 JS 用 catfish_browser_evaluate".

def browser_evaluate(args: Dict[str, Any]) -> Dict[str, Any]:
    """硬 timeout 兜底 wrapper, 走 _impl."""
    return _run_with_hard_timeout(_browser_evaluate_impl, args)


def _browser_evaluate_impl(args: Dict[str, Any]) -> Dict[str, Any]:
    """在 Chrome 当前 page 跑一段 JS, 返表达式值.

    用法:
      browser_evaluate(expression="document.title")
      browser_evaluate(expression="document.querySelectorAll('a').length")
      browser_evaluate(expression="document.querySelector('li[data-id=42]')?.textContent")

    返回 {type: ok, value, value_type, summary} 或 {type: error, error}.

    安全: Playwright `page.evaluate` 是合法 JS eval, 跟 DevTools Console 一样
    能力 — 员工本机自己用没 sandbox 顾虑. 网络隔离层之上 (员工已经登录的页面)
    跑什么 JS 都是员工权限. **不**做 SSRF / 内容白名单 — 这是 dev tool.
    """
    expression = args.get("expression")
    if not isinstance(expression, str) or not expression.strip():
        return {
            "type": "error",
            "error": "expression 必填, 且必须是 JS 表达式字符串. 例: 'document.title'",
        }
    expression = expression.strip()

    # 用户的 expression 可能是多行函数 / IIFE / 直接表达式 — Playwright
    # page.evaluate 接受任一形式 (会自动包成 async). 但如果是裸语句 (`let x = 1`)
    # 而非表达式, page.evaluate 会抛 SyntaxError, 让 caller 自己看.
    timeout_ms = int(float(args.get("timeout_seconds") or 10.0) * 1000)
    timeout_ms = max(1000, min(timeout_ms, 30_000))

    try:
        sync_playwright = _import_playwright()
    except RuntimeError as e:
        return {"type": "error", "error": str(e)}

    try:
        with sync_playwright() as p:
            try:
                browser, context, page = _connect_playwright_browser(p)
            except RuntimeError as e:
                return {"type": "error", "error": str(e)}

            page.set_default_timeout(timeout_ms)
            try:
                value = page.evaluate(expression)
            except Exception as e:
                # Playwright 把 JS 语法/运行错包成 Error, msg 里会带 stack
                # 给 LLM 看时截短, 防 stack 太长污染 context
                msg = f"{type(e).__name__}: {e}"
                if len(msg) > 800:
                    msg = msg[:800] + "…"
                return {
                    "type": "error",
                    "error": f"JS 执行失败: {msg}",
                    "expression": expression[:200],
                }

            # 序列化 value — 已是 JSON-safe (Playwright evaluate 走 CDP 序列化协议)
            # 但保险起见, 不可 JSON 的类型 (DOM Node 等) Playwright 自己已经
            # 转 None / 字符串, 这里只截长字符串
            value_type = type(value).__name__
            display: Any = value
            if isinstance(display, str) and len(display) > 4000:
                display = display[:4000] + f"…(truncated, full {len(value)} chars)"
            elif isinstance(display, (list, dict)):
                try:
                    serialized = json.dumps(display, ensure_ascii=False)
                    if len(serialized) > 4000:
                        display = (
                            json.loads(serialized[:4000]) if False
                            else f"<{value_type} 大对象, {len(serialized)} chars, 截断>"
                        )
                except Exception:
                    display = str(display)[:4000]

            actual_url = page.url
            page_title = ""
            try:
                page_title = page.title()
            except Exception:
                pass

            return {
                "type": "ok",
                "value": display,
                "value_type": value_type,
                "page_url": actual_url,
                "page_title": page_title,
                "summary": f"在 {actual_url} 跑 JS, 返 {value_type}",
            }
    except Exception as e:
        return {
            "type": "error",
            "error": f"playwright evaluate 异常: {type(e).__name__}: {e}",
        }


def browser_click(args: Dict[str, Any]) -> Dict[str, Any]:
    """硬 timeout 兜底 wrapper, 调 _impl. 防 Playwright 卡死锁住整个 daemon."""
    return _run_with_hard_timeout(_browser_click_impl, args)


def _browser_click_impl(args: Dict[str, Any]) -> Dict[str, Any]:
    """走 Playwright `page.click()`. auto-waiting 等元素出现 + visible + clickable.

    BL-FIX44 (5/11) 加 coordinates 路径: LLM 截图看到位置直接传 [x,y], 不依赖 selector.
    走 page.mouse.click(x, y), 完全绕开 selector 歧义.

    Args:
      selector: Playwright selector (优先, 老路径不变)
      coordinates: [x, y] 整数像素坐标. 跟 selector 二选一.
                   LLM 拿截图看到按钮位置时用这个 — 没有 selector 歧义.
      timeout_seconds: selector 等待超时, coordinates 模式不用 (鼠标点立即触发).
    """
    selector = (args.get("selector") or "").strip()
    coordinates = args.get("coordinates")
    timeout_ms = int(float(args.get("timeout_seconds") or 30.0) * 1000)
    timeout_ms = max(1000, min(timeout_ms, 120_000))

    # 校验: 二选一
    if not selector and not coordinates:
        return {
            "type": "error",
            "error": "selector 跟 coordinates 至少传一个. "
                     "看到截图直接传 coordinates=[x,y]; 有可靠 selector 传 selector.",
        }

    # coordinates 校验
    coord_xy = None
    if coordinates:
        try:
            if isinstance(coordinates, dict):
                cx, cy = int(coordinates.get("x")), int(coordinates.get("y"))
            elif isinstance(coordinates, (list, tuple)) and len(coordinates) == 2:
                cx, cy = int(coordinates[0]), int(coordinates[1])
            else:
                raise ValueError("不是 [x,y] 列表或 {x,y} 字典")
            if cx < 0 or cy < 0 or cx > 10000 or cy > 10000:
                return {
                    "type": "error",
                    "error": f"坐标超合理范围 ({cx},{cy}). 应该是页面 pixel 坐标, 0-3000 量级.",
                }
            coord_xy = (cx, cy)
        except Exception as e:  # noqa: BLE001
            return {
                "type": "error",
                "error": f"coordinates 格式错: {type(e).__name__}: {e}. "
                         "应该是 [x, y] 像素整数, 例 [450, 380].",
            }

    try:
        sync_playwright = _import_playwright()
    except RuntimeError as e:
        return {"type": "error", "error": str(e)}

    try:
        with sync_playwright() as p:
            try:
                browser, context, page = _connect_playwright_browser(p)
            except RuntimeError as e:
                return {"type": "error", "error": str(e)}

            # 优先级: selector 传了 → 走 selector (老路径); 没传 → 走 coordinates
            try:
                if selector:
                    page.click(selector, timeout=timeout_ms)
                    page.wait_for_load_state("domcontentloaded", timeout=5000)
                    return {
                        "type": "ok",
                        "mode": "selector",
                        "selector": selector,
                        "current_url": page.url,
                        "current_title": page.title(),
                        "summary": f"✓ 点击 '{selector}' 成功. 当前页面: {page.title()}",
                    }
                else:
                    # coordinates 模式: page.mouse.click(x, y)
                    cx, cy = coord_xy
                    page.mouse.click(cx, cy)
                    page.wait_for_load_state("domcontentloaded", timeout=5000)
                    return {
                        "type": "ok",
                        "mode": "coordinates",
                        "coordinates": [cx, cy],
                        "current_url": page.url,
                        "current_title": page.title(),
                        "summary": (
                            f"✓ 点击坐标 ({cx},{cy}) 成功. 当前页面: {page.title()}. "
                            "注: 坐标点击没 auto-waiting, 如果页面没反应可能是点空了 — "
                            "重新截图确认位置."
                        ),
                    }
            except Exception as e:
                err_str = str(e)
                if "Timeout" in err_str or "timeout" in err_str:
                    return {
                        "type": "error",
                        "error": (
                            f"等不到元素 '{selector}' 可点击 (超时 {timeout_ms}ms). "
                            "selector 写错? 元素被 modal 遮住? 先 catfish_browser_snapshot 看 DOM, "
                            "或者 screenshot 看视觉 + 用 coordinates 直点."
                        ),
                    }
                return {"type": "error", "error": f"click 失败: {type(e).__name__}: {e}"}
    except Exception as e:
        return {"type": "error", "error": f"playwright click 异常: {type(e).__name__}: {e}"}


def browser_fill(args: Dict[str, Any]) -> Dict[str, Any]:
    """硬 timeout 兜底 wrapper, 调 _impl. 防 Playwright 卡死锁住整个 daemon."""
    return _run_with_hard_timeout(_browser_fill_impl, args)


def _browser_fill_impl(args: Dict[str, Any]) -> Dict[str, Any]:
    """走 Playwright `page.fill()`. 自动清空原值再填.

    历史:
      v1 (2026-04-28 早): 拒填 password 字段 → 实测员工需要登录场景, 拒了核心废.
      v2 (2026-04-28 中): 允许填 + 加 security_audit 标记 → 但密码仍在 LLM 上下文.
      v3 (2026-04-28 当前): 加 secret_ref 字段, 密码从 keychain / env 拉, **永不进 LLM 上下文**.
        text 字段保留 (用户名 / 邮箱 / 内容用), secret_ref 跟 text 二选一.
    """
    selector = (args.get("selector") or "").strip()
    text = args.get("text")
    secret_ref = (args.get("secret_ref") or "").strip()

    if not selector:
        return {"type": "error", "error": "selector 必填"}

    # secret_ref 跟 text 二选一. 都没给 → error. 都给 → 优先 secret_ref + warning.
    if not secret_ref and (text is None or text == ""):
        return {
            "type": "error",
            "error": "必须给 'text' 或 'secret_ref' 之一. 密码场景用 secret_ref",
        }

    timeout_ms = int(float(args.get("timeout_seconds") or 10.0) * 1000)
    timeout_ms = max(1000, min(timeout_ms, 60_000))

    # 检测密码 / 凭据字段
    selector_lower = selector.lower()
    is_credential_field = (
        "password" in selector_lower
        or "pwd" in selector_lower
        or "passwd" in selector_lower
    )

    # 解析 secret_ref (如果有), 拿到真实密码值
    actual_text: str
    used_secret_ref = False
    if secret_ref:
        try:
            from . import secret_resolver  # noqa: PLC0415
            actual_text = secret_resolver.resolve_secret(secret_ref)
            used_secret_ref = True
        except Exception as e:  # SecretResolveError 或其他
            return {
                "type": "error",
                "error": f"secret_ref 解析失败: {e}",
            }
    else:
        actual_text = str(text)
        # 检测员工是不是把 secret_ref 写错位置 (写到 text 字段了)
        try:
            from . import secret_resolver  # noqa: PLC0415
            if secret_resolver.is_secret_ref(actual_text):
                return {
                    "type": "error",
                    "error": (
                        f"text='{actual_text[:30]}...' 看起来是 secret_ref. "
                        "应该传到 secret_ref 字段, 不是 text 字段."
                    ),
                }
        except ImportError:
            pass

    try:
        sync_playwright = _import_playwright()
    except RuntimeError as e:
        return {"type": "error", "error": str(e)}

    try:
        with sync_playwright() as p:
            try:
                browser, context, page = _connect_playwright_browser(p)
            except RuntimeError as e:
                return {"type": "error", "error": str(e)}

            try:
                page.fill(selector, actual_text, timeout=timeout_ms)
                result: Dict[str, Any] = {
                    "type": "ok",
                    "selector": selector,
                    "filled_chars": len(actual_text),
                    "summary": f"✓ 在 '{selector}' 填了 {len(actual_text)} 个字符",
                }
                # 标 audit:
                #   - 用了 secret_ref → "credential_via_secret_ref" (好的实践)
                #   - 直接 text + 是密码字段 → "credential_field_filled" (不好的实践, 提醒)
                if used_secret_ref:
                    result["security_audit"] = "credential_via_secret_ref"
                    result["secret_ref_used"] = secret_ref  # 记 ref 不记值
                    result["summary"] += f" (从 {secret_ref} 拉值, 密码不进 LLM 上下文)"
                elif is_credential_field:
                    result["security_audit"] = "credential_field_filled"
                    result["security_note"] = (
                        "selector 看起来是密码 / 凭据字段, 但 text 是明文 (已经在 LLM 上下文了). "
                        "下次推荐用 secret_ref='keychain://<name>' 或 'env://<NAME>' "
                        "让密码从安全源拉, 不进 LLM."
                    )
                return result
            except Exception as e:
                err_str = str(e)
                if "Timeout" in err_str or "timeout" in err_str:
                    return {
                        "type": "error",
                        "error": (
                            f"等不到 '{selector}' 可写 (超时 {timeout_ms}ms). "
                            "selector 错? 输入框被 disabled? 用 catfish_browser_snapshot 看一下"
                        ),
                    }
                return {"type": "error", "error": f"fill 失败: {type(e).__name__}: {e}"}
    except Exception as e:
        return {"type": "error", "error": f"playwright fill 异常: {type(e).__name__}: {e}"}


def browser_snapshot(args: Dict[str, Any]) -> Dict[str, Any]:
    """硬 timeout 兜底 wrapper, 调 _impl. 防 Playwright 卡死锁住整个 daemon."""
    return _run_with_hard_timeout(_browser_snapshot_impl, args)


def _browser_snapshot_impl(args: Dict[str, Any]) -> Dict[str, Any]:
    """拿当前页面结构化 DOM. 优先 Playwright accessibility, 失败 fallback 到 DOM evaluate.

    BL-FIX3 (5/8): EIS 登录 demo 撞 ``page.accessibility`` 在新版 Playwright 上 None,
    LLM 看到 ``AttributeError`` 直接放弃, 跟员工说"工具坏了". 修法 — 双路径:
      1. 先试 ``page.accessibility.snapshot()`` (老版 Playwright, 数据最干净)
      2. 失败 (None / AttributeError / 抛异常) → fallback ``page.evaluate()`` 走 JS
         扫 button/input/a/[role] 拿可见可交互元素列表, 跟 a11y 输出格式兼容

    返回里多个 ``snapshot_method`` 字段标明走哪条路径, 方便 audit / debug.

    BL-FIX9 (5/8): default 200 → 500, cap 500 → 1000. 鸿波 5/8 点的真因 — LLM 偷
    懒主动选 max_elements=50, CAS 登录页 nav / footer link 把登录 button 挤出 50,
    LLM 看不到只能截图找 → 撞 Playwright sync 卡死. 默认大点 + truncated 时返
    hint_for_llm 引导加大不是减小, 这条链上无解.
    """
    max_elements = int(args.get("max_elements") or 500)
    max_elements = max(10, min(max_elements, 1000))

    try:
        sync_playwright = _import_playwright()
    except RuntimeError as e:
        return {"type": "error", "error": str(e)}

    try:
        with sync_playwright() as p:
            try:
                browser, context, page = _connect_playwright_browser(p)
            except RuntimeError as e:
                return {"type": "error", "error": str(e)}

            # 先拿 title / url — 这俩失败说明 page 本身坏了, 直接退出
            try:
                title = page.title()
                url = page.url
            except Exception as e:
                return {
                    "type": "error",
                    "error": (
                        f"读 page.title/url 失败 (page 不可用): "
                        f"{type(e).__name__}: {e}. "
                        f"Chrome 标签页是不是被员工关了? Companion 重启 Chrome 再试."
                    ),
                }

            elements: List[Dict[str, Any]] = []
            snapshot_method = "unknown"
            a11y_error: Optional[str] = None

            # 路径 1: accessibility tree (老版 Playwright, 输出最干净)
            try:
                a11y_module = getattr(page, "accessibility", None)
                if a11y_module is not None:
                    a11y = a11y_module.snapshot()
                    if a11y:
                        _flatten_a11y(a11y, elements, max_count=max_elements)
                        if elements:
                            snapshot_method = "accessibility"
                else:
                    a11y_error = "page.accessibility 属性不存在 (Playwright >=1.50 已移除)"
            except Exception as e:
                a11y_error = f"{type(e).__name__}: {e}"
                import logging as _logging  # noqa: PLC0415
                _logging.getLogger("catfish.tool_bridge").warning(
                    "BL-FIX3 accessibility.snapshot 失败 → fallback DOM evaluate: %s",
                    a11y_error,
                )

            # 路径 2: DOM evaluate fallback (新版 Playwright 走这, 也是 a11y 拿不到时兜底)
            if not elements:
                try:
                    elements = _evaluate_dom_snapshot(page, max_count=max_elements)
                    snapshot_method = "dom_evaluate"
                except Exception as e:
                    return {
                        "type": "error",
                        "error": (
                            f"snapshot 失败 — accessibility ({a11y_error}) 和 "
                            f"DOM evaluate ({type(e).__name__}: {e}) 都不可用. "
                            f"页面 title={title!r} url={url!r}"
                        ),
                    }

            truncated = len(elements) >= max_elements
            summary = (
                f"页面 '{title}' ({url}) 有 {len(elements)} 个可见元素 "
                f"[via {snapshot_method}]"
            )
            if truncated:
                summary += f" — 截断到 {max_elements}, 加大 max_elements 看全部"

            result: Dict[str, Any] = {
                "type": "ok",
                "title": title,
                "url": url,
                "elements": elements[:max_elements],
                "element_count": len(elements),
                "truncated": truncated,
                "snapshot_method": snapshot_method,
                "summary": summary,
            }
            if truncated:
                # BL-FIX9: 显式给 LLM 下一步建议, 防它偷懒减小或者去截图找
                next_max = min(max_elements * 2, 1000)
                result["hint_for_llm"] = (
                    f"⚠️ elements 被截断了 (实际 >={max_elements}). 找不到要点"
                    f"的按钮 / link 时, **重调本工具加大 max_elements 到 {next_max}** "
                    f"(不是减小, 不是去截图). 如果 max_elements 已经到 1000, 改用 "
                    f"selector='text=登录' 这种文字匹配直接 click, 不用先看 element."
                )
            if a11y_error and snapshot_method == "dom_evaluate":
                result["accessibility_fallback_reason"] = a11y_error
            return result
    except Exception as e:
        return {"type": "error", "error": f"playwright snapshot 异常: {type(e).__name__}: {e}"}


# DOM-based fallback. ``page.accessibility.snapshot()`` 在新版 Playwright (>=1.50)
# 已废弃 / 返 None, 这条路走 ``page.evaluate(JS)`` 直接扫 DOM 拿可见可交互元素.
# 输出 schema 跟 _flatten_a11y 兼容: {role, name, depth} + 多个 selector_hint
# 给模型抓 selector 用.
_DOM_SNAPSHOT_JS = r"""
(maxCount) => {
    const out = [];
    function visible(el) {
        const rect = el.getBoundingClientRect();
        if (rect.width < 1 || rect.height < 1) return false;
        const cs = getComputedStyle(el);
        return cs.display !== 'none' && cs.visibility !== 'hidden' && cs.opacity !== '0';
    }
    function role(el) {
        const r = el.getAttribute('role');
        if (r) return r;
        const tag = el.tagName.toUpperCase();
        if (tag === 'A') return 'link';
        if (tag === 'BUTTON') return 'button';
        if (tag === 'INPUT') {
            const t = (el.getAttribute('type') || 'text').toLowerCase();
            if (t === 'checkbox') return 'checkbox';
            if (t === 'radio') return 'radio';
            if (t === 'submit' || t === 'button') return 'button';
            if (t === 'password') return 'textbox';
            return 'textbox';
        }
        if (tag === 'TEXTAREA') return 'textbox';
        if (tag === 'SELECT') return 'combobox';
        if (tag === 'IMG') return 'img';
        if (tag === 'FORM') return 'form';
        if (tag === 'LABEL') return 'label';
        if (/^H[1-6]$/.test(tag)) return 'heading';
        return tag.toLowerCase();
    }
    function name(el) {
        const candidates = [
            el.getAttribute('aria-label'),
            el.getAttribute('placeholder'),
            el.getAttribute('name'),
            el.getAttribute('title'),
            el.getAttribute('alt'),
            (el.innerText || '').trim(),
            el.getAttribute('value'),
        ];
        for (const c of candidates) {
            if (c) return String(c).trim().slice(0, 100);
        }
        return '';
    }
    function selectorHint(el) {
        if (el.id) return '#' + el.id;
        const nm = el.getAttribute('name');
        if (nm) return el.tagName.toLowerCase() + '[name="' + nm + '"]';
        const cls = (el.className || '').toString().split(/\s+/).filter(Boolean).slice(0, 2).join('.');
        if (cls) return el.tagName.toLowerCase() + '.' + cls;
        return el.tagName.toLowerCase();
    }
    function depthOf(el) {
        let d = 0;
        let cur = el;
        while (cur.parentElement) { d += 1; cur = cur.parentElement; }
        return d;
    }
    const all = document.querySelectorAll(
        'button, a, input, textarea, select, [role], h1, h2, h3, h4, h5, h6, label, form, img'
    );
    for (const el of all) {
        if (out.length >= maxCount) break;
        if (!visible(el)) continue;
        const r = role(el);
        const n = name(el);
        // 没 name 的 generic / div / span 跳过, 跟 a11y 行为一致
        const interesting = ['button', 'link', 'textbox', 'checkbox', 'radio',
                             'combobox', 'menuitem', 'tab', 'heading', 'img', 'form'];
        if (!n && interesting.indexOf(r) === -1) continue;
        out.push({
            role: r,
            name: n,
            depth: depthOf(el),
            selector_hint: selectorHint(el),
        });
    }
    return out;
}
"""


def _evaluate_dom_snapshot(page: Any, max_count: int = 200) -> List[Dict[str, Any]]:
    """跑 JS 拿可见可交互元素列表. 跟 _flatten_a11y 输出格式兼容 + 多 selector_hint."""
    raw = page.evaluate(_DOM_SNAPSHOT_JS, max_count) or []
    # 防 JS 端塞了脏 / 非 dict
    out: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        out.append({
            "role": str(item.get("role", "")),
            "name": str(item.get("name", ""))[:100],
            "depth": int(item.get("depth", 0)),
            "selector_hint": str(item.get("selector_hint", ""))[:200],
        })
    return out


def _flatten_a11y(
    node: Optional[Dict[str, Any]],
    out: List[Dict[str, Any]],
    max_count: int = 200,
    depth: int = 0,
) -> None:
    """把 accessibility tree 递归平铺成 element 列表. 超 max_count 立刻停."""
    if not node or len(out) >= max_count:
        return
    role = node.get("role", "")
    name = node.get("name", "")
    # 只收 "有意义" 的元素 (有 name 或可交互 role)
    interesting_roles = {
        "button", "link", "textbox", "checkbox", "radio", "combobox",
        "menuitem", "tab", "heading", "img", "img-text", "form",
    }
    if name or role in interesting_roles:
        out.append({
            "role": role,
            "name": name[:100] if name else "",
            "depth": depth,
        })

    for child in node.get("children", []) or []:
        if len(out) >= max_count:
            break
        _flatten_a11y(child, out, max_count=max_count, depth=depth + 1)


def browser_screenshot(args: Dict[str, Any]) -> Dict[str, Any]:
    """硬 timeout 兜底 wrapper, 调 _impl. 防 Playwright 卡死锁住整个 daemon."""
    return _run_with_hard_timeout(_browser_screenshot_impl, args)


# BL-FIX17 (5/8): 截图智能压缩参数
_SCREENSHOT_TARGET_KB = 800           # 硬 cap, 超过自动降 quality 重压
_SCREENSHOT_VIEWPORT_MAX_PX = 1280    # viewport max 边
_SCREENSHOT_FULLPAGE_MAX_PX = 1600    # full_page max 边
_SCREENSHOT_VIEWPORT_QUALITY = 80     # JPEG quality (viewport)
_SCREENSHOT_FULLPAGE_QUALITY = 75     # JPEG quality (full_page)
_SCREENSHOT_FALLBACK_QUALITY = 60     # 重压 quality


def _compress_screenshot(
    png_bytes: bytes,
    *,
    is_element: bool,
    full_page: bool,
) -> tuple[bytes, str, Dict[str, Any]]:
    """智能压缩截图. 返 (压完 bytes, format 'png'/'jpeg', meta).

    BL-FIX17 (5/8): 4MB 截图卡死 LLM (IPC + context + vision 推理三连卡).
    自动 downscale + JPEG, vision 一样能识别但省 90% size.

    策略:
      - is_element=True (有 selector): 不压 PNG (元素本来就小, 保真重要)
      - viewport: max 1280px + JPEG q=80
      - full_page: max 1600px + JPEG q=75
      - 压完仍 >800KB → 降 q=60 重压
      - 仍 >800KB → 抛 ValueError, 让 caller 报 error 给 LLM 改策略
    """
    size_before = len(png_bytes)

    # 元素截图: 不压, 直接返
    if is_element:
        return png_bytes, "png", {
            "size_kb_before": round(size_before / 1024, 1),
            "size_kb_after": round(size_before / 1024, 1),
            "compression_ratio": 1.0,
            "downscaled": False,
            "compress_strategy": "element_keep_png",
        }

    try:
        from PIL import Image  # noqa: PLC0415
    except ImportError:
        # PIL 没装 (极少见, hermes venv 一般有), fallback 不压返原图
        return png_bytes, "png", {
            "size_kb_before": round(size_before / 1024, 1),
            "size_kb_after": round(size_before / 1024, 1),
            "compression_ratio": 1.0,
            "downscaled": False,
            "compress_strategy": "no_pil_fallback",
            "warning": "PIL 没装, 没压. pip install Pillow.",
        }

    import io  # noqa: PLC0415
    img = Image.open(io.BytesIO(png_bytes))
    orig_w, orig_h = img.size

    max_px = _SCREENSHOT_FULLPAGE_MAX_PX if full_page else _SCREENSHOT_VIEWPORT_MAX_PX
    quality = _SCREENSHOT_FULLPAGE_QUALITY if full_page else _SCREENSHOT_VIEWPORT_QUALITY

    # downscale (保比例, 长边到 max_px)
    long_edge = max(orig_w, orig_h)
    downscaled = False
    if long_edge > max_px:
        scale = max_px / long_edge
        new_w = round(orig_w * scale)
        new_h = round(orig_h * scale)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        downscaled = True

    # 转 RGB (JPEG 不支持 alpha)
    if img.mode in ("RGBA", "LA", "P"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1] if img.mode != "P" else None)
        img = bg

    # 编 JPEG
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    out_bytes = buf.getvalue()

    # 压完仍 >800KB → 降 quality 重压
    if len(out_bytes) > _SCREENSHOT_TARGET_KB * 1024:
        buf2 = io.BytesIO()
        img.save(buf2, format="JPEG", quality=_SCREENSHOT_FALLBACK_QUALITY, optimize=True)
        out_bytes = buf2.getvalue()
        quality = _SCREENSHOT_FALLBACK_QUALITY

    size_after = len(out_bytes)
    if size_after > _SCREENSHOT_TARGET_KB * 1024:
        # 第二次还不行 — 让 caller 报 error
        raise ValueError(
            f"截图压缩后仍 {size_after // 1024} KB > {_SCREENSHOT_TARGET_KB} KB. "
            f"原图 {orig_w}x{orig_h} {size_before // 1024} KB. "
            f"建议: 加 selector 截特定元素 (元素截图不压保真), "
            f"或 full_page=false 只截 viewport."
        )

    return out_bytes, "jpeg", {
        "size_kb_before": round(size_before / 1024, 1),
        "size_kb_after": round(size_after / 1024, 1),
        "compression_ratio": round(size_before / max(size_after, 1), 2),
        "downscaled": downscaled,
        "downscaled_to": f"{img.size[0]}x{img.size[1]}" if downscaled else None,
        "orig_size": f"{orig_w}x{orig_h}",
        "jpeg_quality": quality,
        "compress_strategy": "viewport_jpeg" if not full_page else "fullpage_jpeg",
    }


def _browser_screenshot_impl(args: Dict[str, Any]) -> Dict[str, Any]:
    """截浏览器当前 tab 的图. 走 Playwright `page.screenshot()`, 返 data:image base64.

    BL-FIX7 (5/8): 之前 hermes builtin browser_screenshot 被 BL-FIX4 dedupe 一刀切
    丢了, LLM 想看浏览器内容只剩 catfish_screenshot (mac screencapture), 不对路.
    这条直接拿 Playwright 的 page.screenshot, 跟 browser_goto / fill / click 同
    一个 connect_over_cdp 链路, **不需要 mac 截屏权限**.

    BL-FIX17 (5/8): 自动智能压缩 — viewport / full_page 自动 downscale + JPEG,
    省 90% size + token + 推理时间. element 截图保 PNG 不压.

    LLM 拿到 data_uri 之后, 经 gateway BL-FIX2 multimodal_tool_unwrap 重组到 user
    multipart, 上游 Qwen 主力直接看图回答.
    """
    selector = (args.get("selector") or "").strip()
    full_page = bool(args.get("full_page", False))
    timeout_ms = int(float(args.get("timeout_seconds") or 10.0) * 1000)
    timeout_ms = max(1000, min(timeout_ms, 60_000))
    compress_mode = (args.get("compress") or "auto").strip().lower()

    try:
        sync_playwright = _import_playwright()
    except RuntimeError as e:
        return {"type": "error", "error": str(e)}

    try:
        with sync_playwright() as p:
            try:
                browser, context, page = _connect_playwright_browser(p)
            except RuntimeError as e:
                return {"type": "error", "error": str(e)}

            try:
                title = page.title()
                url = page.url
            except Exception as e:
                return {
                    "type": "error",
                    "error": (
                        f"读 page.title/url 失败 (page 不可用): "
                        f"{type(e).__name__}: {e}. "
                        f"Chrome 标签页是不是被员工关了?"
                    ),
                }

            try:
                if selector:
                    locator = page.locator(selector)
                    locator.wait_for(state="visible", timeout=timeout_ms)
                    png_bytes = locator.screenshot(timeout=timeout_ms)
                    capture_kind = f"element[{selector}]"
                else:
                    png_bytes = page.screenshot(
                        full_page=full_page, timeout=timeout_ms
                    )
                    capture_kind = "full_page" if full_page else "viewport"
            except Exception as e:
                return {
                    "type": "error",
                    "error": (
                        f"截图失败: {type(e).__name__}: {e}. "
                        f"selector={selector!r} full_page={full_page}"
                    ),
                }

            # BL-FIX17: 智能压缩
            if compress_mode == "none":
                # 员工显式要原图
                final_bytes = png_bytes
                final_format = "png"
                compress_meta = {
                    "size_kb_before": round(len(png_bytes) / 1024, 1),
                    "size_kb_after": round(len(png_bytes) / 1024, 1),
                    "compression_ratio": 1.0,
                    "compress_strategy": "none_explicit",
                }
            else:
                # auto (默认): 智能压缩
                try:
                    final_bytes, final_format, compress_meta = _compress_screenshot(
                        png_bytes,
                        is_element=bool(selector),
                        full_page=full_page,
                    )
                except ValueError as e:
                    return {
                        "type": "error",
                        "error": str(e),
                    }

            size = len(final_bytes)
            # 12MB hard cap (防 IPC 撑爆 — 跟 catfish_screenshot 一致)
            if size > _MAX_SCREENSHOT_BYTES:
                return {
                    "type": "error",
                    "error": (
                        f"截图太大 ({size // (1024*1024)} MB > "
                        f"{_MAX_SCREENSHOT_BYTES // (1024*1024)} MB 上限). "
                        f"加 selector 截单个元素, 或 compress='auto'"
                    ),
                }

            b64 = base64.b64encode(final_bytes).decode("ascii")
            mime = "image/jpeg" if final_format == "jpeg" else "image/png"
            return {
                "type": "image",
                "format": final_format,
                "encoding": "base64",
                "data": b64,
                "data_uri": f"data:{mime};base64,{b64}",
                "size_bytes": size,
                "captured_at": _unix_to_iso(time.time()),
                "capture": capture_kind,
                "title": title,
                "url": url,
                # BL-FIX17: 压缩 meta
                "size_kb_before": compress_meta.get("size_kb_before"),
                "size_kb_after": compress_meta.get("size_kb_after"),
                "compression_ratio": compress_meta.get("compression_ratio"),
                "downscaled": compress_meta.get("downscaled", False),
                "downscaled_to": compress_meta.get("downscaled_to"),
                "orig_size": compress_meta.get("orig_size"),
                "compress_strategy": compress_meta.get("compress_strategy"),
                "summary": (
                    f"浏览器截图完成 ({capture_kind}, "
                    f"{compress_meta.get('size_kb_after', size // 1024)} KB "
                    f"{final_format.upper()}, "
                    f"压缩 {compress_meta.get('compression_ratio', 1)}x), "
                    f"页面: {title} ({url})"
                ),
            }
    except Exception as e:
        return {"type": "error", "error": f"playwright browser_screenshot 异常: {type(e).__name__}: {e}"}


# ============================================================
# BL-FIX16 (5/8) — catfish_browser_find_by_text: 文字直接定位元素
# ============================================================
#
# CAS / 央企老页面常用非标准登录按钮 (`<a class="login-btn">登录</a>` / `<div onclick>` /
# `<input type="image">`), snapshot 里 DOM evaluate / accessibility tree 都找不到.
# 但**用户眼里就是个登录按钮**, 文字是 '登录'. 这条工具按文字找, 不挑 tag.
#
# 实现走 Playwright `page.get_by_text()` (内置部分匹配 + 优先 visible 元素), 失败
# fallback page.evaluate JS 全 DOM 扫. 返多个候选 (selector_hint + tag + bbox), LLM
# 拿第 1 个直接 click.

def browser_find_by_text(args: Dict[str, Any]) -> Dict[str, Any]:
    """硬 timeout 兜底 wrapper, 调 _impl. 防 Playwright 卡死锁住整个 daemon."""
    return _run_with_hard_timeout(_browser_find_by_text_impl, args)


_FIND_BY_TEXT_JS = r"""
(params) => {
    // BL-FIX44 (5/11) 重写: 返**全维度候选元数据 + 综合排序**, 让 LLM 自己判断挑哪个.
    // 之前版本只返 selector_hint, 撞 placeholder/label 同字符就翻车 (鸿波 EIS 登录场景).
    //
    // 新返字段:
    //   - selector: Playwright 最稳的 selector (优先 #id, 再 [name], 再 role/text 组合)
    //   - tag, role (ARIA), match_type (innerText/placeholder/aria-label/value/title/alt)
    //   - text (匹配到的文字), is_clickable (有 click handler 或 interactive role/tag)
    //   - bounds {x,y,w,h}, center {x,y} (供 catfish_browser_click coordinates 直点)
    //   - score (综合排序权重, 透明可解释)
    const wantedText = params.text;
    const exact = !!params.exact;
    const maxCount = params.maxCount || 10;
    const wantedRole = (params.role || '').toLowerCase();  // 'button' / 'link' / null

    function visible(el) {
        const rect = el.getBoundingClientRect();
        if (rect.width < 1 || rect.height < 1) return false;
        const cs = getComputedStyle(el);
        return cs.display !== 'none' && cs.visibility !== 'hidden' && cs.opacity !== '0';
    }

    // 返 [text, match_type] — 哪个属性匹配的, 优先级 innerText > value > aria-label > placeholder > title > alt
    //
    // BL-FIX44-fix (5/12): 鸿波 EIS 实测 — 登录按钮真实文字是 '登 录' (中间空格),
    // text='登录' 子串匹配失败. 改 normalize whitespace 比较 — 两侧 strip + 内部
    // \s+ 折成空 (中文场景两字之间空格通常无意义), 再 substring 比.
    function _norm(s) {
        return (s || '').replace(/\s+/g, '').toLowerCase();
    }
    function matchedText(el, wanted, exact) {
        const wantedNorm = _norm(wanted);
        const tries = [
            ['innerText', (el.innerText || '').trim()],
            ['value', (el.value || el.getAttribute('value') || '').trim()],
            ['aria-label', (el.getAttribute('aria-label') || '').trim()],
            ['placeholder', (el.getAttribute('placeholder') || '').trim()],
            ['title', (el.getAttribute('title') || '').trim()],
            ['alt', (el.getAttribute('alt') || '').trim()],
        ];
        for (const [mt, t] of tries) {
            if (!t) continue;
            // 先试原始匹配 (保兼容); 没中再 norm-whitespace 匹配 (修 '登 录' 类按钮)
            const m1 = exact ? (t === wanted) : t.includes(wanted);
            if (m1) return [t, mt];
            const tNorm = _norm(t);
            const m2 = exact ? (tNorm === wantedNorm) : tNorm.includes(wantedNorm);
            if (m2) return [t, mt];
        }
        return [null, null];
    }

    // 显式 ARIA role 或隐式 (button/a/input[submit]/...).
    function getRole(el) {
        const explicit = el.getAttribute('role');
        if (explicit) return explicit.toLowerCase();
        const tag = el.tagName.toUpperCase();
        if (tag === 'BUTTON') return 'button';
        if (tag === 'A' && el.hasAttribute('href')) return 'link';
        if (tag === 'INPUT') {
            const t = (el.type || 'text').toLowerCase();
            if (t === 'submit' || t === 'button' || t === 'reset' || t === 'image') return 'button';
            if (t === 'checkbox') return 'checkbox';
            if (t === 'radio') return 'radio';
            return 'textbox';
        }
        if (tag === 'TEXTAREA') return 'textbox';
        if (tag === 'SELECT') return 'combobox';
        return '';
    }

    // 是否真可点击 — 有原生 interactive 行为或显式 click handler.
    function isClickable(el) {
        const tag = el.tagName.toUpperCase();
        if (['BUTTON', 'A', 'INPUT', 'TEXTAREA', 'SELECT'].includes(tag)) return true;
        if (el.hasAttribute('onclick')) return true;
        const r = (el.getAttribute('role') || '').toLowerCase();
        if (['button', 'link', 'menuitem', 'tab'].includes(r)) return true;
        // cursor:pointer 也是 click 信号
        try {
            if (getComputedStyle(el).cursor === 'pointer') return true;
        } catch (e) {}
        return false;
    }

    // 构造最稳的 Playwright selector. 注意: 不用 'text=' (歧义), 优先 #id / [name] / role-name.
    function bestSelector(el, matchType, matchedTextVal) {
        if (el.id) return '#' + CSS.escape(el.id);
        const nm = el.getAttribute('name');
        if (nm) return el.tagName.toLowerCase() + '[name=' + JSON.stringify(nm) + ']';
        // role + name (Playwright 1.27+ 支持 'role=button[name="登录"]')
        const role = getRole(el);
        if (role && matchType === 'innerText' && matchedTextVal && matchedTextVal.length <= 50) {
            return 'role=' + role + '[name=' + JSON.stringify(matchedTextVal) + ']';
        }
        // tag + class 兜底
        const cls = (el.className || '').toString().split(/\s+/).filter(Boolean).slice(0, 2).join('.');
        if (cls) return el.tagName.toLowerCase() + '.' + cls;
        return el.tagName.toLowerCase();
    }

    function inViewport(rect) {
        return rect.top < window.innerHeight && rect.bottom > 0 &&
               rect.left < window.innerWidth && rect.right > 0;
    }

    // 扫所有元素 (限制只看可见 + 文字匹配的)
    const all = document.querySelectorAll('*');
    const out = [];
    for (const el of all) {
        if (out.length >= 200) break;  // 硬上限, 防大页面爆
        if (!visible(el)) continue;
        const [matched, matchType] = matchedText(el, wantedText, exact);
        if (!matched) continue;

        const rect = el.getBoundingClientRect();
        const role = getRole(el);
        const clickable = isClickable(el);

        // 综合排序: 透明可解释
        let score = 0;
        // 1. 用户显式指定 role → 同 role 大加分, 不同 -10 排到末尾 (但仍返, 不丢)
        if (wantedRole) {
            if (role === wantedRole) score += 50;
            else score -= 20;
        }
        // 2. clickable > 不可点
        if (clickable) score += 30;
        // 3. match_type 优先级 (innerText 最强, alt 最弱)
        const mtBonus = {
            'innerText': 20, 'value': 15, 'aria-label': 12,
            'placeholder': 3, 'title': 2, 'alt': 1,
        };
        score += mtBonus[matchType] || 0;
        // 4. exact match + 10
        if (matched === wantedText) score += 10;
        // 5. 元素大小: 登录按钮通常 ≥ 100×40, log scale
        const area = Math.max(1, rect.width * rect.height);
        score += Math.min(15, Math.log2(area) | 0);
        // 6. 在 viewport 内 +5
        if (inViewport(rect)) score += 5;
        // 7. 文字越长越可能是误匹配 (placeholder 长描述 vs 按钮短文字)
        if (matched.length > 20) score -= 5;
        if (matched.length > 50) score -= 10;

        out.push({
            selector: bestSelector(el, matchType, matched),
            tag: el.tagName.toLowerCase(),
            role: role || null,
            text: matched.slice(0, 100),
            match_type: matchType,
            is_clickable: clickable,
            bounds: {
                x: Math.round(rect.x),
                y: Math.round(rect.y),
                w: Math.round(rect.width),
                h: Math.round(rect.height),
            },
            center: {
                x: Math.round(rect.x + rect.width / 2),
                y: Math.round(rect.y + rect.height / 2),
            },
            in_viewport: inViewport(rect),
            score: score,
        });
    }

    // score 倒序
    out.sort((a, b) => b.score - a.score);
    return out.slice(0, maxCount);
}
"""


def _browser_find_by_text_impl(args: Dict[str, Any]) -> Dict[str, Any]:
    """文字直接定位元素. 返**排序候选 + 元数据**, LLM 看 role/match_type/clickable 挑.

    BL-FIX44 (5/11) 重写: 单元素 → 候选列表 + 元数据. 修鸿波 EIS 实测 —
    find_by_text 抓到密码框 placeholder 含 '登录' 翻车. 老版 selector_hint='text=登录'
    天然歧义, 新版返 role/match_type/is_clickable, LLM 自己判断.

    Args:
      text: 要找的文字 (必填)
      exact: 完全匹配 (默认 False, 子串匹配)
      role: ARIA role 过滤 ('button' / 'link' / 'textbox' / ...). 显式指定时
            同 role 大加分, 不同 -20. 不传则不过滤.
      max_results: 返回数量 (默认 10)

    BL-FIX16 (5/8) 历史: CAS 登录非标准, find_by_text 不挑 tag 找文字. L44 保留.
    """
    text = (args.get("text") or "").strip()
    if not text:
        return {"type": "error", "error": "text 必填 (要找的元素文字)"}
    exact = bool(args.get("exact", False))
    max_results = int(args.get("max_results") or 10)
    max_results = max(1, min(max_results, 30))
    role_filter = (args.get("role") or "").strip().lower()  # BL-FIX44 新加

    try:
        sync_playwright = _import_playwright()
    except RuntimeError as e:
        return {"type": "error", "error": str(e)}

    try:
        with sync_playwright() as p:
            try:
                browser, context, page = _connect_playwright_browser(p)
            except RuntimeError as e:
                return {"type": "error", "error": str(e)}

            try:
                title = page.title()
                url = page.url
            except Exception as e:
                return {
                    "type": "error",
                    "error": (
                        f"读 page.title/url 失败 (page 不可用): "
                        f"{type(e).__name__}: {e}"
                    ),
                }

            # 走 page.evaluate JS 全扫 (比 page.get_by_text 兼容性更好, 回退一招)
            try:
                raw = page.evaluate(
                    _FIND_BY_TEXT_JS,
                    {
                        "text": text,
                        "exact": exact,
                        "maxCount": max_results,
                        "role": role_filter,
                    },
                )
            except Exception as e:
                return {
                    "type": "error",
                    "error": (
                        f"page.evaluate 失败: {type(e).__name__}: {e}. "
                        f"页面 title={title!r}"
                    ),
                }

            elements: List[Dict[str, Any]] = []
            if isinstance(raw, list):
                for item in raw:
                    if not isinstance(item, dict):
                        continue
                    elements.append({
                        "selector": str(item.get("selector", ""))[:200],
                        "tag": str(item.get("tag", "")),
                        "role": item.get("role"),
                        "text": str(item.get("text", ""))[:100],
                        "match_type": str(item.get("match_type", "")),
                        "is_clickable": bool(item.get("is_clickable", False)),
                        "bounds": item.get("bounds") or {},
                        "center": item.get("center") or {},  # 供 coordinates click 用
                        "in_viewport": bool(item.get("in_viewport", False)),
                        "score": int(item.get("score", 0)),
                    })

            # 顶部推荐: score 最高 + clickable (如果有 clickable 的话)
            top = None
            if elements:
                clickables = [e for e in elements if e["is_clickable"]]
                top = clickables[0] if clickables else elements[0]

            # 构造给 LLM 的 summary — 解释 top 是怎么挑出来的
            summary_parts: list[str] = []
            if not elements:
                summary_parts.append(
                    f"页面 {title!r} 上没找到含 '{text}' 的元素."
                )
                if role_filter:
                    summary_parts.append(
                        f"过滤 role='{role_filter}' 可能太严, 去掉再试一次, "
                        "或者直接 catfish_browser_screenshot 看一眼页面真实结构."
                    )
                else:
                    summary_parts.append(
                        "试 exact=false / 改文字, 或 catfish_browser_screenshot 让员工看一眼."
                    )
            else:
                summary_parts.append(
                    f"找到 {len(elements)} 个含 '{text}' 的元素."
                )
                if top:
                    mt_zh = {
                        "innerText": "正文",
                        "value": "value 属性",
                        "aria-label": "aria-label",
                        "placeholder": "placeholder",
                        "title": "title",
                        "alt": "alt",
                    }.get(top.get("match_type", ""), top.get("match_type", ""))
                    summary_parts.append(
                        f"推荐: tag={top['tag']} role={top['role']} "
                        f"match={mt_zh} clickable={top['is_clickable']} "
                        f"size={top['bounds'].get('w')}x{top['bounds'].get('h')}."
                    )
                    summary_parts.append(
                        f"如果这是要的, 直接 catfish_browser_click(selector={top['selector']!r}) "
                        f"或 catfish_browser_click(coordinates=[{top['center'].get('x')}, "
                        f"{top['center'].get('y')}])."
                    )
                    # 检查 top 是不是 placeholder 匹配, 提醒可能不是真按钮
                    if top.get("match_type") == "placeholder":
                        summary_parts.append(
                            "⚠ top 候选匹配的是 placeholder (输入框提示文字), 不是真按钮. "
                            "想找按钮请传 role='button' 重试, 或看下面候选挑 clickable+role=button 的."
                        )

            return {
                "type": "ok",
                "title": title,
                "url": url,
                "search_text": text,
                "exact": exact,
                "role_filter": role_filter or None,
                "elements": elements,
                "element_count": len(elements),
                "top_recommendation": top,
                "summary": "\n".join(summary_parts),
            }
    except Exception as e:
        return {"type": "error", "error": f"playwright find_by_text 异常: {type(e).__name__}: {e}"}


