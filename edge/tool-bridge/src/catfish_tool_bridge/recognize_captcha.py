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


def _capture_via_playwright(selector: str, timeout_ms: int = 8000) -> bytes | None:
    """Playwright locator.screenshot(selector) — 拿 PNG bytes."""
    try:
        from . import catfish_tools  # noqa: PLC0415
        sync_playwright = catfish_tools._import_playwright()
    except Exception as e:  # noqa: BLE001
        logger.warning("playwright 不可用: %s", e)
        return None

    try:
        with sync_playwright() as p:
            try:
                browser, context, page = catfish_tools._connect_playwright_browser(p)
            except RuntimeError as e:
                logger.warning("connect browser 失败: %s", e)
                return None
            try:
                # locator.screenshot 直接拿 element 截图, 不需要 full page
                loc = page.locator(selector).first
                png = loc.screenshot(timeout=timeout_ms)
                return png
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "截 captcha selector=%s 失败: %s", selector, e
                )
                return None
    except Exception as e:  # noqa: BLE001
        logger.warning("playwright 上下文异常: %s", e)
        return None


def _estimate_confidence(text: str, hint: str | None) -> float:
    """简单 confidence 估算: hint 给的话长度对得上 +, '?' 大降, 含解释词大降.

    返 0.0 - 1.0. 仅启发式, 不是真概率.
    """
    if not text or text == "?":
        return 0.0

    # 包含解释词 → 模型没遵守 prompt, 低信
    bad_words = ("验证码", "是", "看到", "图中", "无法", "I see", "the code")
    if any(w in text for w in bad_words):
        return 0.2

    # hint 长度校验
    expected_len = None
    if hint:
        h = hint.lower()
        if "4" in h:
            expected_len = 4
        elif "5" in h:
            expected_len = 5
        elif "6" in h:
            expected_len = 6

    base = 0.85
    if expected_len and len(text) != expected_len:
        return max(0.3, base - 0.3)

    # 字符集校验
    if hint and "numeric" in hint.lower() and not text.isdigit():
        return max(0.3, base - 0.2)
    if hint and "alpha" in hint.lower() and not all(c.isalnum() for c in text):
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
        png_bytes = _capture_via_playwright(selector)
        if png_bytes is None:
            return {
                "ok": False,
                "error": (
                    f"无法截 selector={selector!r} 的图. selector 写错? "
                    "页面没开 browser? Playwright 死了? 先 catfish_browser_snapshot 查."
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
                        "max_tokens": 30,    # 验证码很短
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
            raw = (
                data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                or ""
            )
            last_raw = raw
        except Exception as e:  # noqa: BLE001
            last_error = f"解析 gateway 响应失败: {type(e).__name__}: {e}"
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
