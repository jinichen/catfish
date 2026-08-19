"""catfish_recognize_captcha — 验证码 OCR 子 LLM 工具 (BL-Q3-WEBSKILL, 5/11).

# 为啥要这个

LLM 截图 #captchaImg → 自己 OCR → 翻车 (122b 视觉 OCR 不稳, 经常错位 / 漏字符).
真该走专门 vision LLM (catfish-private-vision) 做 OCR, 返**纯文本结果**.

LLM agent 路径或 skill 脚本调它都行:
  catfish_recognize_captcha(selector='#captchaImg', hint='alphanumeric_4')
  → {"text": "2fW2", "confidence": 0.9}

# 实现

1. 用 Playwright 截 selector (locator.screenshot) 拿 PNG bytes
2. base64 + 调 gateway loopback POST /v1/chat/completions
3. use_case='captcha_ocr' → pick_internal_model 选 catfish-private-vision
4. prompt 极简 + 强约束: "识别图中验证码, 只返回字符, 不要解释"
5. 解析返回, strip whitespace, 估算 confidence (基于长度匹配 hint)

Token 来源: 跟 catfish_read_tool_archive / catfish_skill_publish 同款 ——
从 ~/.catfish/oauth/id_token 读 Companion 写的 OAuth id_token.

# vs 直接让 LLM OCR

LLM 自己 OCR:
  - 走主对话, 占员工 quota
  - LLM 122b 视觉能力一般, 容易翻
  - 错了不知道, prompt 链路被污染

catfish_recognize_captcha:
  - 走 internal call, 不占员工 quota
  - 专用 vision 模型 (大概率比 122b 强)
  - 返结构化结果 (text + confidence), skill 可重试
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("catfish.tool_bridge.recognize_captcha")

OAUTH_ID_TOKEN_PATH = Path.home() / ".catfish" / "oauth" / "id_token"

_GATEWAY_PORT = os.environ.get("CATFISH_GATEWAY_PORT", "8999")
_GATEWAY_HOST = os.environ.get("CATFISH_GATEWAY_HOST", "127.0.0.1")
GATEWAY_URL = os.environ.get(
    "CATFISH_GATEWAY_URL",
    f"http://{_GATEWAY_HOST}:{_GATEWAY_PORT}",
)

#: 喂给 vision 模型的 prompt (强约束 + 反幻觉)
_OCR_SYSTEM_PROMPT = """你是验证码 OCR 工具. 输入是一张验证码图片, 输出**只**返回\
图中的字符串本身. 严格遵守:

✅ 必须:
1. 只返回验证码字符, 不要任何解释 / 标点 / 前缀
2. 大小写跟图片一致 (e.g. '2fW2' 不是 '2FW2')
3. 看不清就返 '?' 表示无法识别 (单字符), 不要瞎猜

❌ 严禁:
- 说 '验证码是 X' / '我看到 X' / 加引号
- 返回多行
- 加解释 / 注释
- 数字猜字母 / 字母猜数字

输出格式: 直接一行验证码字符, 例 '2fW2' 或 'aXY8' 或 '?'."""


def _read_id_token() -> str | None:
    if not OAUTH_ID_TOKEN_PATH.exists():
        return None
    try:
        return OAUTH_ID_TOKEN_PATH.read_text(encoding="utf-8").strip() or None
    except Exception as e:  # noqa: BLE001
        logger.warning("读 id_token 失败: %s", e)
        return None


def _capture_via_playwright(selector: str, timeout_ms: int = 8000) -> tuple[bytes | None, str]:
    """Playwright locator.screenshot(selector) — 返 (PNG bytes, 失败原因)。

    # 为什么返两个值 (8/18)

    原来只返 `bytes | None`, 上层看到 None 就报固定的一句:

        "无法截 selector=... 的图. selector 写错? 页面没开 browser? Playwright 死了?"

    三个猜测**全是错的方向**, 而真因根本不在里面 —— 见下面那条 import。
    真异常只进了 logger.warning, 员工和 LLM 都看不到。所以现在把原因带上去。

    # 那条 import bug (这个函数曾经 100% 失败)

    老代码写的是:

        from . import catfish_tools
        catfish_tools._import_playwright()            ← 不存在
        catfish_tools._connect_playwright_browser(p)  ← 不存在

    `catfish_tools.py` 只从 catfish_tools_browser re-export 了 7 个 `browser_*`
    公开函数, 这两个下划线私有的**一个都没导**。于是必然 AttributeError。

    而 AttributeError 不是 RuntimeError, 穿过 `except RuntimeError` 那层, 被
    最外面的 `except Exception` 吞成 warning → 返 None → 上层报"selector 写错?"。

    实测对照: 同一个页面同一个 selector, `catfish_browser_screenshot` 每次都成功
    (它直接用本模块的函数), 这里每次都失败。也就是说 catfish_recognize_captcha
    从写下这行起就没成功过一次。

    ⚠ browser_locate.py 有一模一样的两行, 8/18 一起修了。
    """
    try:
        # 这两个函数在 catfish_tools_browser 里, 不在 catfish_tools。
        # 直接从定义它们的模块导 —— 别再绕 re-export, 那正是老 bug 的成因。
        try:
            from .catfish_tools_browser import (  # noqa: PLC0415
                _connect_playwright_browser,
                _import_playwright,
            )
        except ImportError:  # 独立脚本模式 (无父包)
            from catfish_tools_browser import (  # type: ignore  # noqa: PLC0415
                _connect_playwright_browser,
                _import_playwright,
            )
        sync_playwright = _import_playwright()
    except Exception as e:  # noqa: BLE001
        logger.warning("playwright 不可用: %s", e)
        return None, f"playwright 不可用: {type(e).__name__}: {e}"

    try:
        with sync_playwright() as p:
            try:
                # ⚠ 这里**不能**只 catch RuntimeError。老代码只 catch 它, 于是
                #   AttributeError / ImportError 这类"代码写错了"的异常会伪装成
                #   "浏览器连不上", 把排查方向带偏。
                browser, context, page = _connect_playwright_browser(p)
            except Exception as e:  # noqa: BLE001
                logger.warning("connect browser 失败: %s: %s", type(e).__name__, e)
                return None, f"连不上浏览器: {type(e).__name__}: {e}"
            try:
                # locator.screenshot 直接拿 element 截图, 不需要 full page
                loc = page.locator(selector).first
                png = loc.screenshot(timeout=timeout_ms)
                return png, ""
            except Exception as e:  # noqa: BLE001
                logger.warning("截 captcha selector=%s 失败: %s", selector, e)
                return None, f"截 {selector!r} 失败: {type(e).__name__}: {e}"
    except Exception as e:  # noqa: BLE001
        logger.warning("playwright 上下文异常: %s", e)
        return None, f"playwright 上下文异常: {type(e).__name__}: {e}"


#: `<think>…</think>` 内联形态。有的上游把思考塞在 content 里而不是单独字段。
_THINK_BLOCK = re.compile(r"<think\b[^>]*>.*?</think\s*>", re.S | re.I)


def _extract_answer(message: dict[str, Any]) -> tuple[str, str]:
    """从一条 assistant message 里取 OCR 结果。返 (答案, 诊断说明)。

    # 为什么不能只读 content —— 8/19 实撞, 查了两轮

    `catfish-private-vision` 是自建 Qwen3-VL, **默认开思考**。而这里原来发的是
    `max_tokens: 30`, 注释写着"验证码很短" —— 短的是**答案**, 不是生成过程。
    思考把 30 个 token 吃光, `content` 回来是空串, 于是:

        text=''  confidence=0.00  →  "识别置信度低"

    员工看到的是"验证码识别不了", 而模型其实一个字都还没开始写答案。

    鸿波 8/10 打内网端点实测过同一件事 (记在 gateway 的 thinking_guard.py 里):
    `max_tokens=100` 问 "1+1=?" , 思考开着时 `content` 就是 `''`。

    所以这里两手都要:
      · max_tokens 给够 (见调用处)
      · content 空时看 reasoning_content —— 有些上游把话说在那儿
      · content 里内联 `<think>` 的, 剥掉再用

    第二个返回值是**给人看的诊断**。原来失败只说"置信度低 text=''", 那句话
    把人往"图片太糊"上带, 而真相是"根本没返回内容" —— 今天就是被它带偏的。
    """
    raw = message.get("content")
    raw = raw if isinstance(raw, str) else ""
    if raw.strip():
        cleaned = _THINK_BLOCK.sub("", raw).strip()
        if cleaned:
            return cleaned, ""
        # 整段 content 就是一个 <think> 块, 剥完什么都不剩
        return "", "content 里只有 <think> 块, 没有答案 (思考没写完就截断了?)"

    rc = message.get("reasoning_content")
    rc = rc if isinstance(rc, str) else ""
    if rc.strip():
        # 只到这一步说明 content 是空的 —— 思考占满了输出预算。
        # reasoning 里**通常没有**干净的答案 (它被截断了), 所以不拿它当结果,
        # 只用来把诊断说清楚: 是"没写答案", 不是"图片认不出"。
        return "", (
            f"上游只返了思考没返答案 (reasoning {len(rc)} 字, content 空) —— "
            "多半是 max_tokens 不够, 被思考吃光了"
        )
    return "", "上游返回里 content 和 reasoning_content 都是空的"


def _expected_charset(hint: str | None) -> str | None:
    """hint 说的是哪种字符集。返 'digits' / 'alnum' / 'letters' / 'chinese' / None。

    ⚠ **顺序是判据本身, 不能调**。`alphanumeric` 里含有 `numeric` 这个子串 ——
      原来那句 `if "numeric" in hint.lower()` 会把 `alphanumeric_4` 也判成"纯数字",
      于是传**对**的 hint 反而扣分 (8/19 实测: hint=alphanumeric_4 认对 '5KBz' → 0.65,
      而 hint=numeric_4 被带偏认错成 '5482' → 0.85, **错的比对的分高**)。
      判据比真事宽一格, 结果整条置信度就反了。
    """
    h = (hint or "").strip().lower()
    if not h:
        return None
    if "alphanumeric" in h or "alnum" in h or "字母数字" in h:
        return "alnum"
    if "chinese" in h or "中文" in h or "汉字" in h:
        return "chinese"
    if "numeric" in h or "digit" in h or "数字" in h:
        return "digits"
    if "alpha" in h or "letter" in h or "字母" in h:
        return "letters"
    return None


def _expected_len(hint: str | None) -> int | None:
    """hint 里说的位数。取不出来返 None。

    原来是 `if "4" in h: 4 elif "5" in h: 5 elif "6" in h: 6` —— 只认这三个数,
    而且 "4-6位" 这种会被判成 4。

    现在: 只接受 3..8 (验证码不会只有 1-2 位, 也不会有 9 位 —— 超出范围多半是
    hint 里别的数字, 例 "hint_v2" 的 2), 并且**必须只有一个** —— "4-6位" 这种
    范围说明调用方自己也不确定位数, 那就别拿它去扣分。取第一个还是最后一个都是
    瞎定, 而扣错分的代价是把一个认对了的验证码判成低置信度。
    """
    h = (hint or "").lower()
    nums = {int(n) for n in re.findall(r"\d+", h) if 3 <= int(n) <= 8}
    return nums.pop() if len(nums) == 1 else None


def _estimate_confidence(text: str, hint: str | None) -> float:
    """简单 confidence 估算: hint 给的话长度 / 字符集对得上 +, '?' 大降, 含解释词大降.

    返 0.0 - 1.0. 仅启发式, 不是真概率.

    # 它挡不住的那件事

    hint 是**调用方给的**, 可能本身就是错的。hint 错的时候模型会照着错的读
    (numeric_4 → 把 S 读成 5), 返回一个跟错 hint 完全自洽的错答案, 这里给它满分。
    这个函数没有任何办法识破 —— 它看不见图片。

    所以真正的防线在**别传错 hint**: 工具 schema 里写了"不确定就别传"(不传反而
    是最高分), skill 里把验证码字符集写死。这里只保证一件事: **对的 hint 不扣分**。
    """
    if not text or text == "?":
        return 0.0

    # 包含解释词 → 模型没遵守 prompt, 低信
    bad_words = ("验证码", "是", "看到", "图中", "无法", "I see", "the code")
    if any(w in text for w in bad_words):
        return 0.2

    base = 0.85
    want_len = _expected_len(hint)
    if want_len and len(text) != want_len:
        return max(0.3, base - 0.3)

    charset = _expected_charset(hint)
    ok = {
        "digits": text.isdigit(),
        "alnum": text.isalnum(),
        "letters": text.isalpha(),
        # 中文验证码: 只要不是纯 ASCII 就当对得上, 不再细判
        "chinese": not text.isascii(),
        None: True,
    }[charset]
    if not ok:
        return max(0.3, base - 0.2)

    return base


def recognize_captcha(args: dict[str, Any]) -> dict[str, Any]:
    """LLM 调过来或 skill 直接 import.

    Args:
      selector: CSS selector for captcha image (e.g. '#captchaImg'). 必填**或** image_b64.
      image_b64: base64 PNG/JPG bytes (不含 data:image/... prefix). 跟 selector 二选一.
      hint: 'numeric_4' / 'alphanumeric_4' / 'numeric_5' / 'numeric_6' / 'alphanumeric_5' /
             'alphanumeric_6' / 'chinese' / 任意自然语言. 帮 vision 模型 + confidence 估算.
      max_retry: 失败重试 (LLM 自身偶发), 默认 1, 最大 3.

    Returns:
      {"ok": bool, "text": str, "confidence": float, "model": str,
       "error": str | None, "raw_response": str | None}
    """
    selector = (args.get("selector") or "").strip()
    image_b64 = (args.get("image_b64") or "").strip()
    hint = (args.get("hint") or "").strip()
    max_retry = max(1, min(int(args.get("max_retry") or 1), 3))

    if not selector and not image_b64:
        return {
            "ok": False,
            "error": "selector 或 image_b64 必须传一个",
        }

    # 1. 拿图
    png_bytes: bytes | None = None
    if image_b64:
        try:
            png_bytes = base64.b64decode(image_b64, validate=True)
        except Exception as e:  # noqa: BLE001
            return {
                "ok": False,
                "error": f"image_b64 解码失败: {type(e).__name__}: {e}",
            }
    else:
        png_bytes, why = _capture_via_playwright(selector)
        if png_bytes is None:
            # 把**真原因**带出去。老版只给三个猜测 (selector 写错 / 没开浏览器 /
            # Playwright 死了), 而 8/18 那次真因是代码里 import 拿错模块 ——
            # 三个猜测一个都没沾边, 反而把排查带偏了一整轮。
            return {
                "ok": False,
                "error": f"无法截 selector={selector!r} 的图 — {why}",
                "hint": (
                    "先用 catfish_browser_screenshot 拿同一个 selector 试一下: "
                    "它成功而这里失败, 就不是 selector 的问题。"
                ),
            }

    # 太小的图大概率不是验证码 (e.g. 1x1 像素 fallback)
    if len(png_bytes) < 200:
        return {
            "ok": False,
            "error": f"截到的图太小 ({len(png_bytes)} 字节), 可能 selector 错了.",
        }

    # 2. base64 + data URL
    data_url = f"data:image/png;base64,{base64.b64encode(png_bytes).decode()}"

    # 3. 调 gateway loopback
    token = _read_id_token()
    if not token:
        return {
            "ok": False,
            "error": "未登录 catfish (~/.catfish/oauth/id_token 没找到)",
        }

    try:
        import httpx  # noqa: PLC0415
    except ImportError:
        return {"ok": False, "error": "tool-bridge 缺 httpx 依赖"}

    user_prompt = f"识别这个验证码{f' (提示: {hint})' if hint else ''}:"

    # P3.5.42.1 (6/18 鸿波拍 '所有遵循 picker, 不乱改'): vision model 走 role_resolver
    # chain, 不再 hardcode 'catfish-private-vision'. 兜底仍是 'catfish-private-vision'
    # (gateway 挂时用). 这是注释 line 234-236 留的 TODO: '后续 catalog 加 captcha_ocr tag
    # 走 pick_internal_model 选'.
    try:
        from . import role_resolver  # noqa: PLC0415
        _vision_model = role_resolver.resolve("vision") or "catfish-private-vision"
    except Exception:  # noqa: BLE001
        _vision_model = "catfish-private-vision"

    last_error = None
    last_raw = None
    for attempt in range(max_retry):
        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(
                    f"{GATEWAY_URL}/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    json={
                        # P3.5.42.1: role_resolver("vision") chain, 不再 hardcode.
                        "model": _vision_model,
                        "messages": [
                            {"role": "system", "content": _OCR_SYSTEM_PROMPT},
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": user_prompt},
                                    {"type": "image_url", "image_url": {"url": data_url}},
                                ],
                            },
                        ],
                        "temperature": 0.0,  # OCR 不要创造性
                        # ⚠ 这里原来是 30, 注释写着"验证码很短" —— **短的是答案,
                        #   不是生成过程**。vision 角色现在指向自建 Qwen3-VL,
                        #   它默认开思考, 30 个 token 全被思考吃掉, content 回来
                        #   是空串。8/19 员工登 EIS 时连撞三次, 界面只说"识别不了"。
                        #
                        #   鸿波 8/10 在 gateway/thinking_guard.py 里记过同一件事:
                        #   max_tokens=100 问 "1+1=?", 思考开着 content 就是 ''。
                        #   所以 100 也不够, 得给思考留出真实空间。
                        #
                        #   4096 相对模型的 122880 输出上限微不足道, 而验证码答案
                        #   本身仍然只有几个字符 —— 多出来的额度只在"思考写了很长"
                        #   时才真的花掉。
                        "max_tokens": 4096,
                        "stream": False,
                    },
                )
        except Exception as e:  # noqa: BLE001
            last_error = f"HTTP 异常: {type(e).__name__}: {e}"
            continue

        if resp.status_code != 200:
            last_error = f"gateway {resp.status_code}: {resp.text[:200]}"
            continue

        try:
            data = resp.json()
            msg = data.get("choices", [{}])[0].get("message", {}) or {}
            raw, why_empty = _extract_answer(msg)
            last_raw = raw
        except Exception as e:  # noqa: BLE001
            last_error = f"解析 gateway 响应失败: {type(e).__name__}: {e}"
            continue

        if not raw:
            # ★ 空返回**不是**"图片认不出", 别混进置信度那条路 —— 混了之后错误
            #   消息会说"识别置信度低 (text='')", 把人往"图片太糊"上带。
            #   8/19 就是被这句话带着查了两轮。
            finish = (data.get("choices", [{}])[0] or {}).get("finish_reason")
            last_error = f"{why_empty} (finish_reason={finish!r})"
            continue

        # 清洗结果: strip / 去引号 / 取第一行
        text = raw.strip()
        for q in ("'", '"', '"', '"', '`'):
            text = text.strip(q)
        text = text.splitlines()[0].strip() if text else ""

        # 验证码偶尔模型加 '验证码: XXX' 之类前缀, 提取最后一段连续字母数字
        if ":" in text and len(text) > 15:
            text = text.split(":")[-1].strip()
        if "：" in text:
            text = text.split("：")[-1].strip()

        confidence = _estimate_confidence(text, hint)
        if confidence < 0.4:
            last_error = f"识别置信度低 (text={text!r} confidence={confidence:.2f})"
            continue

        # 成功
        return {
            "ok": True,
            "text": text,
            "confidence": confidence,
            "model": _vision_model,  # P3.5.42.1: 返实际用的 model, 不 hardcode
            "attempts": attempt + 1,
            "raw_response": raw,
        }

    # 全失败
    return {
        "ok": False,
        "text": "",
        "confidence": 0.0,
        "error": f"{max_retry} 次识别全失败. 最后错: {last_error}",
        "raw_response": last_raw,
    }


__all__ = ["recognize_captcha"]
