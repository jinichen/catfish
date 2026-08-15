"""浏览器动作 —— evaluate / click / fill。

BL-TOOL-SPLIT 8/15: 从 catfish_tools_browser.py 抽出来 (1575 行超限), 沿用
5/20 那次 (browser 从 catfish_tools 抽出来) 的同一套做法。纯搬迁, 逻辑一行未改。

⚠ _import_playwright / _connect_playwright_browser 是**在函数体里 import** 的。

测试对它们打桩打的是 catfish_tools_browser 那份 (tests/test_browser_*.py 共 9 处
`monkeypatch.setattr(catfish_tools_browser, "_import_playwright", ...)`)。
`from X import name` 建的是**新绑定不是别名** —— 写在顶层的话这里查的是本模块的
绑定, 打桩完全打不到, 测试会真的去连 Chrome。延迟到调用时从 catfish_tools_browser
取, 打桩语义原样保留, 测试一行没动。

(另一种做法见 tests/test_propose_skill.py: 对两个模块各打一次。也可行, 但要动
测试, 且以后每加一条测试都得记得打两处。)
"""
from __future__ import annotations

import json
from typing import Any, Dict

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
    # 延迟 import —— 见文件头。打桩打的是 catfish_tools_browser 那份,
    # 顶层 import 会建新绑定, 打不到。
    from .catfish_tools_browser import _run_with_hard_timeout  # noqa: PLC0415

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
    # 延迟 import —— 见文件头。打桩打的是 catfish_tools_browser 那份,
    # 顶层 import 会建新绑定, 打不到。
    from .catfish_tools_browser import _connect_playwright_browser, _import_playwright  # noqa: PLC0415

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
    # 延迟 import —— 见文件头。打桩打的是 catfish_tools_browser 那份,
    # 顶层 import 会建新绑定, 打不到。
    from .catfish_tools_browser import _run_with_hard_timeout  # noqa: PLC0415

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
    # 延迟 import —— 见文件头。打桩打的是 catfish_tools_browser 那份,
    # 顶层 import 会建新绑定, 打不到。
    from .catfish_tools_browser import _connect_playwright_browser, _import_playwright  # noqa: PLC0415

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
    # 延迟 import —— 见文件头。打桩打的是 catfish_tools_browser 那份,
    # 顶层 import 会建新绑定, 打不到。
    from .catfish_tools_browser import _run_with_hard_timeout  # noqa: PLC0415

    return _run_with_hard_timeout(_browser_fill_impl, args)


def _browser_fill_impl(args: Dict[str, Any]) -> Dict[str, Any]:
    """走 Playwright `page.fill()`. 自动清空原值再填.

    历史:
      v1 (2026-04-28 早): 拒填 password 字段 → 实测员工需要登录场景, 拒了核心废.
      v2 (2026-04-28 中): 允许填 + 加 security_audit 标记 → 但密码仍在 LLM 上下文.
      v3 (2026-04-28 当前): 加 secret_ref 字段, 密码从 keychain / env 拉, **永不进 LLM 上下文**.
        text 字段保留 (用户名 / 邮箱 / 内容用), secret_ref 跟 text 二选一.
    """
    # 延迟 import —— 见文件头。打桩打的是 catfish_tools_browser 那份,
    # 顶层 import 会建新绑定, 打不到。
    from .catfish_tools_browser import _connect_playwright_browser, _import_playwright  # noqa: PLC0415

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
