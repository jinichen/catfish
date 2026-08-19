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


def _unreadable_result(
    *,
    exc: Exception,
    site: str,
    found: str,
    page_url: str,
    page_title: str,
    selector: str,
) -> Dict[str, Any]:
    """索引里有这个站点、但密码取不出来时返什么。

    # 为什么抽成一个函数

    它原来内联在 `_browser_fill_impl` 的 playwright 块里 —— 那块要真起浏览器才跑
    得到, 于是这段判据一条测试都没有。而它恰恰是最容易错的一处: 8/19 就是它把
    "取值通道断了"也当成"该再问一次", 让鸿波删了重存三次, 每次都存成功、每次都读
    不出来, 界面从头到尾没有一处说得出"存没用"。半小时耗在这个循环里。

    # 判据只有一条: 再存一次能不能解决

    由下层的 `SecretResolveError.ask_employee` 说了算, 这里不猜:

      True   钥匙串里没这条 → `needs_credential`, 前端弹密码框
      False  通道断了 (Companion 没在跑) → **不弹框**。弹框是有害的, 它让人相信
             问题在自己这边, 然后一遍遍白输密码。

    没有这个属性的异常 (老代码 / 别的错) 一律按 True —— 多问一次是安全的那一侧。
    """
    out: Dict[str, Any] = {
        "type": "error",
        # 只转述真正的原因, 不加"多半是钥匙串里那条被删了"这种猜测 —— 这句会进
        # 模型上下文, 猜错了模型就照着把人往错方向带 (8/19 就是)。
        "error": f"{site} 的密码在索引里有 ({found}), 但取不出来: {exc}",
        "site": site,
        "page_url": page_url,
        "page_title": page_title,
        "selector": selector,
    }
    if getattr(exc, "ask_employee", True):
        out["needs_credential"] = True
        # ★ 跟 missing 必须分开。索引说"存过了", 钥匙串里却没有 —— 前端要是按索引
        #   判断"已存过, 不用再问", 就会把输入框藏起来, 员工卡死在一句"应该已经
        #   处理完了"上 (8/18 实撞)。
        out["reason"] = "unreadable"
    else:
        # 给模型一句能照着说的话, 免得它自己发明"你在浏览器里手动输一下吧"
        # (SOUL 明令不许开口要密码)。
        out["summary"] = (
            f"⚠ 取不到 {site} 的密码, 但**不是没存过** —— 是取值通道断了。"
            "再存一次没有用。请员工确认鲶鱼 Companion 在跑, 然后重试这一步。"
        )
    return out


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
    # 8/18: 按**当前页站点**取密码, 调用方不用知道任何 ref。
    #
    # 老路 (secret_ref) 要人把一个字符串从"存密码"那个流程搬到"教学"这个流程,
    # 然后被 _infer_params 焊进冻结的 script.py —— 改密码就对不上了 (8/17 实撞)。
    # 这条不搬也不焊: 每次按 page.url 现查, 员工改完密码下一次跑就是新的。
    #
    # 两个都给时以 secret_ref 优先 —— 显式写死的意图更强, 而且冻结的老 skill
    # 全走那条, 不能被这条抢掉。
    secret_for_site = bool(args.get("secret_for_site"))

    if not selector:
        return {"type": "error", "error": "selector 必填"}

    # 三选一. 都没给 → error。
    # secret_for_site 要等拿到 page 才知道站点, 所以它在这儿是"合法的空手" ——
    # 真正的取值在下面 _connect_playwright_browser 之后。
    if not secret_for_site and not secret_ref and (text is None or text == ""):
        return {
            "type": "error",
            "error": "必须给 'text' / 'secret_ref' / 'secret_for_site' 之一. 密码场景用 secret_for_site",
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
    if secret_for_site and not secret_ref:
        # 站点要等 page 才知道 —— 真正的解析挪到下面拿到 page 之后。
        # 这里只占位, 别让后面的 text 校验把它当成"没给值"。
        actual_text = ""
    elif secret_ref:
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

            used_site_ref = ""
            if secret_for_site and not secret_ref:
                # ★ 只有到这儿才知道站点 —— 所以这段必须在 page 之后, 不能跟上面
                #   那段 secret_ref 解析并在一起。
                from . import credential_sites  # noqa: PLC0415
                page_url = page.url or ""
                site = credential_sites.host_of(page_url)
                if not site:
                    return {
                        "type": "error",
                        "error": f"当前页取不到站点 (url={page_url!r}), 没法按站点找密码",
                    }
                found = credential_sites.ref_for_url(page_url)
                if not found:
                    # 不是"出错了", 是"还没存过" —— 前端认这个标记, 就地弹密码框。
                    # 用 type=error 是为了模型别以为填成功了往下走。
                    known = credential_sites.known_sites()
                    return {
                        "type": "error",
                        "error": f"{site} 还没保存过登录密码",
                        "needs_credential": True,
                        # 索引里也没有 —— 前端可以按"已存过就别再问"来省一步
                        "reason": "missing",
                        "site": site,
                        "page_url": page_url,
                        "page_title": (page.title() or "")[:120],
                        "selector": selector,
                        "known_sites": known,
                        "summary": (
                            f"⚠ {site} 还没存过密码. 请在下面存一次, 我再接着填. "
                            + (f"(本机已存: {', '.join(known)})" if known else "")
                        ),
                    }
                try:
                    from . import secret_resolver  # noqa: PLC0415
                    actual_text = secret_resolver.resolve_secret(found)
                except Exception as e:
                    return _unreadable_result(
                        exc=e,
                        site=site,
                        found=found,
                        page_url=page_url,
                        page_title=(page.title() or "")[:120],
                        selector=selector,
                    )
                used_secret_ref = True
                used_site_ref = found

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
                if used_site_ref:
                    # ⚠ 这里**故意不回 ref**。回了模型就学会下次直接传
                    #   secret_ref='keychain://…', 那就退回老路 —— 一个人工搬运的
                    #   字符串, 会被 _infer_params 焊进冻结的 script.py, 改密码就失联。
                    #   只说"按站点拿到了", 站点是天然标识, 焊死了也仍然对。
                    result["security_audit"] = "credential_via_site"
                    result["site_used"] = credential_sites.host_of(page.url or "")
                    result["summary"] += (
                        f" (按站点 {result['site_used']} 取的密码, 不进 LLM 上下文)"
                    )
                elif used_secret_ref:
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
