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


# ── BL-TOOL-SPLIT 8/15: 五块抽出去了, 这里 import 回来 ──
from .catfish_tools_browser_actions import (  # noqa: E402,F401
    browser_click, browser_evaluate, browser_fill,
    _browser_click_impl, _browser_evaluate_impl, _browser_fill_impl,
)
from .catfish_tools_browser_dom import (  # noqa: E402,F401
    _DOM_SNAPSHOT_JS, _evaluate_dom_snapshot, _flatten_a11y,
)
from .catfish_tools_browser_findtext import _FIND_BY_TEXT_JS  # noqa: E402,F401
from .catfish_tools_browser_guard import (  # noqa: E402,F401
    _NET_LAYER_ERRORS, _SSRF_DENY_HOSTS, _TIMEOUT_MARKERS,
    _check_ssrf_safe, _is_timeout_error,
)
from .catfish_tools_browser_shot import (  # noqa: E402,F401
    _SCREENSHOT_FALLBACK_QUALITY, _SCREENSHOT_FULLPAGE_MAX_PX,
    _SCREENSHOT_FULLPAGE_QUALITY, _SCREENSHOT_TARGET_KB,
    _SCREENSHOT_VIEWPORT_MAX_PX, _SCREENSHOT_VIEWPORT_QUALITY,
    _compress_screenshot,
)


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


def browser_screenshot(args: Dict[str, Any]) -> Dict[str, Any]:
    """硬 timeout 兜底 wrapper, 调 _impl. 防 Playwright 卡死锁住整个 daemon."""
    return _run_with_hard_timeout(_browser_screenshot_impl, args)


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


