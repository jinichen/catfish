"""截图压缩 —— 尺寸 / quality / 目标体积。

BL-TOOL-SPLIT 8/15: 从 catfish_tools_browser.py 抽出来 (1575 行超限), 沿用
5/20 那次 (browser 从 catfish_tools 抽出来) 的同一套做法。纯搬迁, 逻辑一行未改。
"""
from __future__ import annotations

import base64
import io
from typing import Any, Dict

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
