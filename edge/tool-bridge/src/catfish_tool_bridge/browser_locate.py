"""catfish_browser_locate — 视觉找元素位置 (BL-Q3-WEBSKILL, 5/11).

# 为啥

LLM 看截图大概知道"登录按钮在那个蓝色区域", 但 122b 视觉估坐标常常偏 (50-100 像素).
专门 vision 模型 (catfish-private-vision) 更精准. 用法:

  catfish_browser_locate(query="蓝色登录按钮") →
  {ok, found, x, y, w, h, center, confidence, reasoning}

→ 直接喂 catfish_browser_click(coordinates=[center.x, center.y]).

# 跟 recognize_captcha 关系

| 工具 | 输出 | 任务      |
|------|------|----------|
| recognize_captcha | 字符串 | 字符识别 (OCR)  |
| browser_locate    | 坐标   | 空间定位 (找元素) |

共用 vision 模型, prompt + 输出 schema 完全不同, 拆成两条工具线清晰.

# 实现

1. Playwright page.screenshot 截 viewport 或 full_page
2. 读 PNG header 拿图片宽高 (不依赖 PIL, 8 字节就能解)
3. base64 + 调 gateway loopback POST /v1/chat/completions
4. system prompt: '返 JSON, 含 x/y/width/height/confidence/reasoning'
5. 解析 JSON + 校验坐标在图内 + confidence 0-1
6. 返结构化结果
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import struct
from pathlib import Path
from typing import Any

logger = logging.getLogger("catfish.tool_bridge.browser_locate")

OAUTH_ID_TOKEN_PATH = Path.home() / ".catfish" / "oauth" / "id_token"

_GATEWAY_PORT = os.environ.get("CATFISH_GATEWAY_PORT", "8999")
_GATEWAY_HOST = os.environ.get("CATFISH_GATEWAY_HOST", "127.0.0.1")
GATEWAY_URL = os.environ.get(
    "CATFISH_GATEWAY_URL",
    f"http://{_GATEWAY_HOST}:{_GATEWAY_PORT}",
)

#: 视觉定位 system prompt — 强约束 JSON 输出 + 反幻觉
_LOCATE_SYSTEM_PROMPT = """你是网页元素视觉定位工具. 输入一张网页截图 + 自然语言\
描述要找的元素, 输出元素位置 JSON. 严格遵守:

输出格式 (必须**只**返这个 JSON, 不要 markdown / 解释 / 引号包裹):

{
  "found": true,
  "x": 450,
  "y": 380,
  "width": 120,
  "height": 40,
  "confidence": 0.85,
  "reasoning": "蓝色矩形按钮, 位于表单下方居中, 文字'登 录'"
}

字段说明:
- found: 找到 true / 没找到 false. 没找到时 x/y/w/h 全 0, confidence 0
- x, y: 元素**左上角**像素坐标 (整数, 0 到图片宽/高)
- width, height: 元素尺寸 (整数, 像素)
- confidence: 0.0-1.0, 你对识别准确性的自评.
  ≥0.8 = 很确定; 0.5-0.7 = 大致看到但位置可能偏; <0.4 = 不确定/没看到
- reasoning: 一句话描述你定位的依据 (颜色 / 文字 / 位置), 帮 debug

✅ 必须:
1. 只返一个 JSON 对象, **不要**用 ```json``` 包裹
2. 坐标必须在图片范围内 (0 ≤ x < image_width, 0 ≤ y < image_height)
3. 元素中心 (x + width/2, y + height/2) 应该是点击的最佳位置
4. 多个候选时返**最可能符合 query 的那个**

❌ 严禁:
1. 凭空猜坐标 (没看到就 found=false + confidence=0)
2. 加 markdown / 解释 / 前缀 ("以下是结果:" / "```json")
3. 返多个对象或数组 (只一个)
4. width/height < 5 (太小不像 UI 元素)"""


def _read_id_token() -> str | None:
    if not OAUTH_ID_TOKEN_PATH.exists():
        return None
    try:
        return OAUTH_ID_TOKEN_PATH.read_text(encoding="utf-8").strip() or None
    except Exception as e:  # noqa: BLE001
        logger.warning("读 id_token 失败: %s", e)
        return None


def _png_dimensions(png_bytes: bytes) -> tuple[int, int] | None:
    """PNG 头解析拿宽高. 不依赖 PIL.

    PNG header: 8 字节 signature + 4 字节 length + 4 字节 'IHDR' + 4 字节 width + 4 字节 height
    """
    if len(png_bytes) < 24:
        return None
    if png_bytes[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    if png_bytes[12:16] != b"IHDR":
        return None
    try:
        width = struct.unpack(">I", png_bytes[16:20])[0]
        height = struct.unpack(">I", png_bytes[20:24])[0]
        return (width, height)
    except Exception:  # noqa: BLE001
        return None


def _capture_screenshot(
    selector: str | None,
    full_page: bool,
    timeout_ms: int = 8000,
) -> bytes | None:
    """Playwright 截图 — selector 截元素, 否则截 viewport / full_page."""
    # 8/18: 这两个函数在 catfish_tools_browser 里, **不在 catfish_tools**。
    #
    # 老代码写的是 catfish_tools._import_playwright() /
    # catfish_tools._connect_playwright_browser(p) —— 而 catfish_tools 只从
    # catfish_tools_browser re-export 了 7 个公开的 browser_* 函数, 这两个
    # 下划线私有的一个都没导, 必然 AttributeError。
    #
    # AttributeError 不是 RuntimeError, 穿过下面那层 except, 被外层
    # `except Exception` 吞成 warning → 返 None → 上层报"截图失败"。
    # 也就是说 catfish_browser_locate 从写下这行起就没成功过一次,
    # 而 SOUL.md 的浏览器铁律里它是 find_by_text 找不到时的视觉兜底。
    #
    # 同款 bug 在 recognize_captcha.py, 8/18 一起修。
    try:
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
        return None

    try:
        with sync_playwright() as p:
            try:
                # 不能只 catch RuntimeError —— 那会让 AttributeError / ImportError
                # 这类"代码写错了"伪装成"浏览器连不上"。
                browser, context, page = _connect_playwright_browser(p)
            except Exception as e:  # noqa: BLE001
                logger.warning("connect browser 失败: %s: %s", type(e).__name__, e)
                return None
            try:
                if selector:
                    loc = page.locator(selector).first
                    png = loc.screenshot(timeout=timeout_ms)
                else:
                    png = page.screenshot(
                        full_page=full_page,
                        timeout=timeout_ms,
                    )
                return png
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "截图失败 selector=%r full_page=%s: %s",
                    selector, full_page, e,
                )
                return None
    except Exception as e:  # noqa: BLE001
        logger.warning("playwright 上下文异常: %s", e)
        return None


def _parse_locate_json(raw: str) -> dict[str, Any] | None:
    """解析 vision 返的 JSON. 容忍 markdown 包裹 / 前后噪音."""
    if not raw:
        return None
    text = raw.strip()
    # 去 markdown code block
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()
    # 抓第一个 {...} 块 (容忍前缀 "以下是:" 之类)
    m = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.S)
    if m:
        text = m.group(0)
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        logger.warning("locate JSON 解析失败: %s raw=%r", e, raw[:200])
        return None


def _validate_and_normalize(
    data: dict, image_w: int | None, image_h: int | None,
) -> dict[str, Any]:
    """校验返回字段 + 算 center + clamp 到图片范围."""
    found = bool(data.get("found", False))
    try:
        x = int(data.get("x", 0))
        y = int(data.get("y", 0))
        w = int(data.get("width", 0))
        h = int(data.get("height", 0))
        conf = float(data.get("confidence", 0.0))
    except (TypeError, ValueError) as e:
        return {
            "ok": False,
            "error": f"字段类型错: {e}",
            "raw_data": data,
        }
    reasoning = str(data.get("reasoning", ""))[:300]

    conf = max(0.0, min(1.0, conf))

    if not found or conf < 0.1:
        return {
            "ok": True,
            "found": False,
            "confidence": conf,
            "reasoning": reasoning or "vision 模型说没找到",
        }

    # 校验尺寸合理
    if w < 5 or h < 5:
        return {
            "ok": True,
            "found": False,
            "confidence": 0.0,
            "reasoning": f"返尺寸太小 ({w}x{h}), 不像 UI 元素",
            "raw_data": data,
        }

    # 校验坐标在图片范围内
    if image_w and image_h:
        if x < 0 or y < 0 or x >= image_w or y >= image_h:
            return {
                "ok": True,
                "found": False,
                "confidence": 0.0,
                "reasoning": f"坐标 ({x},{y}) 超图片范围 ({image_w}x{image_h})",
                "raw_data": data,
            }
        # clamp width/height 防溢出
        w = min(w, image_w - x)
        h = min(h, image_h - y)

    center_x = x + w // 2
    center_y = y + h // 2

    return {
        "ok": True,
        "found": True,
        "x": x,
        "y": y,
        "width": w,
        "height": h,
        "center": {"x": center_x, "y": center_y},
        "confidence": conf,
        "reasoning": reasoning,
    }


def locate(args: dict[str, Any]) -> dict[str, Any]:
    """LLM 调入口或 skill import.

    Args:
      query: 自然语言描述 ('蓝色登录按钮' / '验证码输入框' / '关闭 X').  必填.
      selector: 可选, 只截某元素区域内找. 不传则截 viewport / full_page.
      image_b64: 可选, 直传 base64 (不含 data: prefix). 调试用.
      full_page: bool. selector / image_b64 都没传时, 决定截 viewport (默认 False) 还是 full_page.
      max_retry: 默认 1, 最大 3. 模型偶发返 garbage 时重试.

    Returns:
      {ok, found, x, y, width, height, center: {x, y}, confidence, reasoning,
       model, attempts, image_size: {w, h}, raw_response, error?}
    """
    query = (args.get("query") or "").strip()
    if not query:
        return {"ok": False, "error": "query 必填 (描述要找的元素)"}
    selector = (args.get("selector") or "").strip() or None
    image_b64 = (args.get("image_b64") or "").strip()
    full_page = bool(args.get("full_page", False))
    max_retry = max(1, min(int(args.get("max_retry") or 1), 3))

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
        png_bytes = _capture_screenshot(selector, full_page)
        if png_bytes is None:
            return {
                "ok": False,
                "error": (
                    "截图失败. 检查 Playwright 浏览器是否在跑, "
                    "或者先 catfish_browser_snapshot 看 DOM 状态."
                ),
            }

    if len(png_bytes) < 200:
        return {
            "ok": False,
            "error": f"截到的图太小 ({len(png_bytes)} 字节), 大概率截空了.",
        }

    # 2. 拿图片尺寸 (校验返回坐标用)
    img_dims = _png_dimensions(png_bytes)
    image_w, image_h = img_dims if img_dims else (None, None)

    # 3. base64 + 调 gateway loopback
    data_url = f"data:image/png;base64,{base64.b64encode(png_bytes).decode()}"
    token = _read_id_token()
    if not token:
        return {
            "ok": False,
            "error": "未登录 catfish (~/.catfish/oauth/id_token 不存在)",
        }

    try:
        import httpx  # noqa: PLC0415
    except ImportError:
        return {"ok": False, "error": "tool-bridge 缺 httpx 依赖"}

    user_prompt = f"图片尺寸 {image_w}x{image_h}. 找: {query}"
    if image_w is None:
        user_prompt = f"找: {query}"

    # P3.5.42.1 (6/18 鸿波拍 '所有遵循 picker, 不乱改'): vision model 走 role_resolver
    # chain. 兜底仍 'catfish-private-vision' (gateway 挂时用). 跟 recognize_captcha 同模式.
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
                            {"role": "system", "content": _LOCATE_SYSTEM_PROMPT},
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": user_prompt},
                                    {"type": "image_url", "image_url": {"url": data_url}},
                                ],
                            },
                        ],
                        "temperature": 0.0,
                        "max_tokens": 200,
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

        parsed = _parse_locate_json(raw)
        if not parsed:
            last_error = f"返回不是合法 JSON: {raw[:150]}"
            continue

        result = _validate_and_normalize(parsed, image_w, image_h)
        if not result.get("ok"):
            last_error = result.get("error", "validate 失败")
            continue

        # 成功 (包括 found=False 也算成功一次返)
        out = {
            "model": _vision_model,  # P3.5.42.1: 返实际用的 model
            "attempts": attempt + 1,
            "image_size": {"w": image_w, "h": image_h} if image_w else None,
            "query": query,
            "raw_response": raw,
        }
        out.update(result)
        return out

    return {
        "ok": False,
        "error": f"{max_retry} 次定位全失败. 最后错: {last_error}",
        "raw_response": last_raw,
    }


__all__ = ["locate"]
