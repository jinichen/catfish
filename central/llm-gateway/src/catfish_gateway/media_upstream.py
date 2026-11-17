"""出图 / 出视频的上游适配 (11/17)。

走哪家看 upstream.model 的前缀, 跟对话模型一个规矩:

  dashscope/<型号>   阿里百炼原生接口 (LiteLLM 只支持它的文生图, 改图和视频都不支持, 所以自己接):
                     · 图片 (文生图 + 改图): POST {base}/services/aigc/multimodal-generation/generation, 同步
                     · 视频: POST {base}/services/aigc/video-generation/video-synthesis (异步) → GET {base}/tasks/{id}
  其它 (openai/…, gemini/…, vertex_ai/…)
                     LiteLLM 的通用接口: aimage_generation / aimage_edit / avideo_generation / avideo_status / avideo_content

base 地址: 模型 (或它引用的供应商) 的 api_base。百炼供应商行一般配的是对话用的 OpenAI 兼容地址
`…/compatible-mode/v1`, 原生接口在同一个域名的 `/api/v1` 下, 这里自动换; 新版按业务空间的地址
(https://<空间>.cn-beijing.maas.aliyuncs.com) 同理补 /api/v1。

上游给的图片 / 视频地址都是临时的 (百炼 24 小时过期), 网关取回字节交给员工端, 不把上游地址交出去。
"""
from __future__ import annotations

import base64
import binascii
import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

from .config import ModelConfig
from .media_config import MediaConfig

logger = logging.getLogger("catfish.gateway.media")

IMAGE_MAX_BYTES = 30 * 1024 * 1024
VIDEO_MAX_BYTES = 300 * 1024 * 1024
#: 员工端传来的单张原图上限 (百炼 Qwen-Image 是 10MB)
INPUT_IMAGE_MAX_BYTES = 10 * 1024 * 1024

#: 图片画幅 → 尺寸。管理员可在模型的 media.sizes 里覆盖 (key 是 landscape / square / portrait)。
DASHSCOPE_IMAGE_SIZES = {"landscape": "1664*928", "square": "1328*1328", "portrait": "928*1664"}
OPENAI_IMAGE_SIZES = {"landscape": "1536x1024", "square": "1024x1024", "portrait": "1024x1536"}

#: 百炼文生视频 (size 写 "宽*高") 各档位各画幅的尺寸, 抄自万相文生视频 API 参考。
#: media.sizes 里可用 "720P/16:9" 这样的 key 覆盖或补充。
DASHSCOPE_VIDEO_SIZES = {
    "480P": {"16:9": "832*480", "9:16": "480*832", "1:1": "624*624"},
    "720P": {"16:9": "1280*720", "9:16": "720*1280", "1:1": "960*960", "4:3": "1088*832", "3:4": "832*1088"},
    "1080P": {"16:9": "1920*1080", "9:16": "1080*1920", "1:1": "1440*1440", "4:3": "1632*1248", "3:4": "1248*1632"},
}
#: 表里没有的画幅取最接近的
_RATIO_NEAREST = {"21:9": "16:9", "3:2": "4:3", "2:3": "3:4"}
#: wan3.0-video 的 ratio 认这些 (resolution_ratio 方式)
_VIDEO_RATIOS = ("21:9", "16:9", "4:3", "1:1", "3:4", "9:16")

_DATA_URI = re.compile(r"^data:(image/[a-z0-9.+-]+);base64,(.*)$", re.I | re.S)


def _client(**kw: Any) -> httpx.AsyncClient:
    """所有上游请求都从这里建客户端 (测试换成假的上游)。"""
    return httpx.AsyncClient(**kw)


class MediaUpstreamError(Exception):
    """给员工看的人话 + 回给员工端的 HTTP 状态。"""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class ImageOut:
    data: bytes
    mime: str


@dataclass
class VideoStatus:
    status: str                 # running | succeeded | failed
    url: str = ""               # 成功时的临时下载地址 (litellm 那条路是 "litellm:<上游任务号>")
    seconds: float = 0.0        # 成功时实际时长 (计费)
    error: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


# ── 公共 ─────────────────────────────────────────────────────────────

def family(model: ModelConfig) -> str:
    prefix, _, _ = model.upstream.model.partition("/")
    return "dashscope" if prefix.lower() == "dashscope" else "litellm"


def upstream_name(model: ModelConfig) -> str:
    return model.upstream.model.partition("/")[2] or model.upstream.model


def media_of(model: ModelConfig) -> MediaConfig:
    return model.media or MediaConfig()


def dashscope_base(model: ModelConfig) -> str:
    base = (model.upstream.api_base or "https://dashscope.aliyuncs.com/api/v1").strip().rstrip("/")
    if base.endswith("/compatible-mode/v1"):
        return base[: -len("/compatible-mode/v1")] + "/api/v1"
    if base.endswith("/api/v1"):
        return base
    if urlsplit(base).path in ("", "/"):
        return base + "/api/v1"
    return base  # 管理员配了别的路径 (代理等), 照用


def merged_params(model: ModelConfig, request_params: dict[str, Any]) -> dict[str, Any]:
    """上游参数 = 模型默认 (media.params) < 员工请求 < 强制 (upstream.param_overrides)。"""
    out = dict(media_of(model).params)
    out.update({k: v for k, v in request_params.items() if v is not None})
    out.update(model.upstream.param_overrides or {})
    return out


def image_size(model: ModelConfig, aspect_ratio: str) -> str | None:
    sizes = media_of(model).sizes
    if aspect_ratio in sizes:
        return sizes[aspect_ratio]
    table = DASHSCOPE_IMAGE_SIZES if family(model) == "dashscope" else OPENAI_IMAGE_SIZES
    return table.get(aspect_ratio)


def video_size(model: ModelConfig, resolution: str, aspect_ratio: str) -> str | None:
    """文生视频 "宽*高": media.sizes["720P/16:9"] > 内置表 > 最接近的画幅。"""
    res = resolution.upper()
    ratio = aspect_ratio if aspect_ratio else "16:9"
    sizes = media_of(model).sizes
    if f"{res}/{ratio}" in sizes:
        return sizes[f"{res}/{ratio}"]
    row = DASHSCOPE_VIDEO_SIZES.get(res) or {}
    return row.get(ratio) or row.get(_RATIO_NEAREST.get(ratio, "16:9")) or row.get("16:9")


def decode_input_image(value: str) -> tuple[bytes, str]:
    """员工端传来的原图 (data: URI) → (字节, MIME)。超限 / 格式不对抛 400。"""
    m = _DATA_URI.match(value.strip())
    if not m:
        raise MediaUpstreamError(400, "原图要用 data:image/...;base64,... 的形式传")
    try:
        raw = base64.b64decode(m.group(2), validate=False)
    except (binascii.Error, ValueError) as e:
        raise MediaUpstreamError(400, f"原图的 base64 解不开: {e}") from e
    if not raw:
        raise MediaUpstreamError(400, "原图是空的")
    if len(raw) > INPUT_IMAGE_MAX_BYTES:
        raise MediaUpstreamError(413, f"原图太大 ({len(raw) // 1024 // 1024}MB), 一张最多 {INPUT_IMAGE_MAX_BYTES // 1024 // 1024}MB")
    return raw, m.group(1).lower()


def _upstream_error(status: int, body: Any, what: str) -> MediaUpstreamError:
    code = msg = ""
    if isinstance(body, dict):
        err = body.get("error") if isinstance(body.get("error"), dict) else {}
        code = str(body.get("code") or err.get("code") or "")
        msg = str(body.get("message") or err.get("message") or "")
    detail = f"{code}: {msg}".strip(": ") or (str(body)[:300] if body else f"HTTP {status}")
    # 内容审核没过 / 参数不对 → 员工能改的, 原样告诉他; 限流 → 429; 鉴权 / 上游故障 → 502
    if "DataInspection" in code or code in ("InvalidParameter", "BadRequest") or status == 400:
        return MediaUpstreamError(400, f"{what}被上游拒绝 ({detail})")
    if status == 429 or "Throttl" in code:
        return MediaUpstreamError(429, f"{what}的上游太忙 / 超过限额, 稍后再试 ({detail})")
    return MediaUpstreamError(502, f"{what}的上游出错 ({detail})")


async def fetch_bytes(url: str, *, max_bytes: int, timeout: float = 180.0) -> tuple[bytes, str]:
    """取回上游给的临时地址。返回 (字节, Content-Type)。"""
    async with _client(timeout=timeout, follow_redirects=True) as client:
        async with client.stream("GET", url) as resp:
            if resp.status_code != 200:
                raise MediaUpstreamError(502, f"取生成结果失败: HTTP {resp.status_code}")
            buf = bytearray()
            async for chunk in resp.aiter_bytes():
                buf.extend(chunk)
                if len(buf) > max_bytes:
                    raise MediaUpstreamError(502, f"生成结果超过 {max_bytes // 1024 // 1024}MB, 不取了")
            return bytes(buf), resp.headers.get("content-type", "").split(";")[0].strip()


def _guess_mime(data: bytes, header: str, default: str) -> str:
    if header.startswith(("image/", "video/")):
        return header
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return default


# ── 图片 ─────────────────────────────────────────────────────────────

async def generate_images(
    model: ModelConfig, *, prompt: str, images: list[str], aspect_ratio: str,
    n: int | None = None, negative_prompt: str | None = None, seed: int | None = None,
) -> list[ImageOut]:
    """文生图 (images 为空) 或改图 (images 是 data: URI 列表)。"""
    if family(model) == "dashscope":
        return await _dashscope_images(model, prompt=prompt, images=images, aspect_ratio=aspect_ratio,
                                       n=n, negative_prompt=negative_prompt, seed=seed)
    return await _litellm_images(model, prompt=prompt, images=images, aspect_ratio=aspect_ratio, n=n)


async def _dashscope_images(model, *, prompt, images, aspect_ratio, n, negative_prompt, seed) -> list[ImageOut]:
    for img in images:
        decode_input_image(img)  # 只校验大小 / 格式, 原样 data: URI 发给百炼
    params = merged_params(model, {"size": image_size(model, aspect_ratio), "n": n,
                                   "negative_prompt": negative_prompt, "seed": seed})
    content = [{"image": img} for img in images] + [{"text": prompt}]
    body = {"model": upstream_name(model), "input": {"messages": [{"role": "user", "content": content}]},
            "parameters": params}
    url = dashscope_base(model) + "/services/aigc/multimodal-generation/generation"
    async with _client(timeout=float(model.upstream.timeout or 180)) as client:
        try:
            resp = await client.post(url, json=body, headers={"Authorization": f"Bearer {model.upstream.api_key}"})
        except httpx.TimeoutException as e:
            raise MediaUpstreamError(504, f"出图超时 ({model.upstream.timeout} 秒没回)") from e
        except httpx.HTTPError as e:
            raise MediaUpstreamError(502, f"连不上出图的上游: {e}") from e
    data = _json(resp)
    if resp.status_code != 200 or (isinstance(data, dict) and data.get("code") and not data.get("output")):
        raise _upstream_error(resp.status_code, data, "出图")
    urls: list[str] = []
    for choice in ((data.get("output") or {}).get("choices") or []):
        for part in ((choice.get("message") or {}).get("content") or []):
            if isinstance(part, dict) and part.get("image"):
                urls.append(str(part["image"]))
    if not urls:
        raise MediaUpstreamError(502, f"上游没给出图片: {str(data)[:300]}")
    out = []
    for u in urls:
        raw, ctype = await fetch_bytes(u, max_bytes=IMAGE_MAX_BYTES)
        out.append(ImageOut(raw, _guess_mime(raw, ctype, "image/png")))
    return out


async def _litellm_images(model, *, prompt, images, aspect_ratio, n) -> list[ImageOut]:
    import litellm  # noqa: PLC0415

    extra = merged_params(model, {})
    kwargs: dict[str, Any] = {
        "model": model.upstream.model, "prompt": prompt, "api_key": model.upstream.api_key,
        "timeout": float(model.upstream.timeout or 180), **extra,
    }
    if model.upstream.api_base:
        kwargs["api_base"] = model.upstream.api_base
    size = image_size(model, aspect_ratio)
    if size and "size" not in extra:
        kwargs["size"] = size
    if n:
        kwargs["n"] = n
    try:
        if images:
            files = []
            for i, img in enumerate(images):
                raw, mime = decode_input_image(img)
                files.append((f"input{i}.{mime.split('/')[-1]}", raw, mime))
            resp = await litellm.aimage_edit(image=files if len(files) > 1 else files[0], **kwargs)
        else:
            resp = await litellm.aimage_generation(**kwargs)
    except MediaUpstreamError:
        raise
    except Exception as e:  # noqa: BLE001 —— litellm 各家异常类型不一, 统一转人话
        status = int(getattr(e, "status_code", 502) or 502)
        raise _upstream_error(status, {"message": str(e)[:500]}, "出图") from e
    out = []
    for item in (getattr(resp, "data", None) or []):
        b64 = getattr(item, "b64_json", None) or (item.get("b64_json") if isinstance(item, dict) else None)
        url = getattr(item, "url", None) or (item.get("url") if isinstance(item, dict) else None)
        if b64:
            raw = base64.b64decode(b64)
            out.append(ImageOut(raw, _guess_mime(raw, "", "image/png")))
        elif url:
            raw, ctype = await fetch_bytes(url, max_bytes=IMAGE_MAX_BYTES)
            out.append(ImageOut(raw, _guess_mime(raw, ctype, "image/png")))
    if not out:
        raise MediaUpstreamError(502, "上游没给出图片")
    return out


# ── 视频 ─────────────────────────────────────────────────────────────

def video_request(model: ModelConfig, *, image: str | None, duration: int | None, aspect_ratio: str,
                  resolution: str | None, negative_prompt: str | None, seed: int | None,
                  audio: bool | None) -> tuple[dict[str, Any], int]:
    """视频的上游参数 + 预计秒数 (时长夹在模型配置的范围里)。"""
    media = media_of(model)
    dur = duration or media.default_duration or media.params.get("duration")
    if dur is not None:
        dur = int(dur)
        if media.min_duration:
            dur = max(dur, media.min_duration)
        if media.max_duration:
            dur = min(dur, media.max_duration)
    res = (resolution or "").upper() or (media.resolutions[0].upper() if media.resolutions else "")
    if res and media.resolutions and res not in [r.upper() for r in media.resolutions]:
        res = media.resolutions[0].upper()
    req: dict[str, Any] = {"duration": dur, "negative_prompt": negative_prompt, "seed": seed}
    if media.audio is not None and audio is not None:
        req["audio"] = audio
    style = media.size_param or ("resolution" if image else "size")
    if res and "size" not in media.params and "resolution" not in media.params:
        if style == "size":
            req["size"] = video_size(model, res, aspect_ratio)
        else:
            req["resolution"] = res
    if style == "resolution_ratio" and "ratio" not in media.params:
        # 带首帧: 画幅跟原图走; 文生: 员工要的画幅, 不在表里的取最接近的
        ratio = aspect_ratio if aspect_ratio in _VIDEO_RATIOS else _RATIO_NEAREST.get(aspect_ratio, "16:9")
        req["ratio"] = "adaptive" if image else ratio
    params = merged_params(model, req)
    return params, int(params.get("duration") or dur or 0)


async def submit_video(model: ModelConfig, *, prompt: str, image: str | None, params: dict[str, Any]) -> str:
    """向上游提交视频任务, 返回上游任务号。"""
    if family(model) == "dashscope":
        if image:
            decode_input_image(image)
        inp: dict[str, Any] = {"prompt": prompt}
        if image:
            if media_of(model).image_input == "first_frame":
                inp["media"] = [{"type": "first_frame", "url": image}]
            else:
                inp["img_url"] = image
        params = dict(params)
        neg = params.pop("negative_prompt", None)  # 百炼视频的反向提示词在 input 里
        if neg:
            inp["negative_prompt"] = neg
        body = {"model": upstream_name(model), "input": inp, "parameters": params}
        url = dashscope_base(model) + "/services/aigc/video-generation/video-synthesis"
        async with _client(timeout=60.0) as client:
            try:
                resp = await client.post(url, json=body, headers={
                    "Authorization": f"Bearer {model.upstream.api_key}", "X-DashScope-Async": "enable"})
            except httpx.HTTPError as e:
                raise MediaUpstreamError(502, f"连不上出视频的上游: {e}") from e
        data = _json(resp)
        task = ((data or {}).get("output") or {}).get("task_id") if isinstance(data, dict) else None
        if resp.status_code != 200 or not task:
            raise _upstream_error(resp.status_code, data, "出视频")
        return str(task)

    import litellm  # noqa: PLC0415

    params = dict(params)
    kwargs: dict[str, Any] = {"model": model.upstream.model, "prompt": prompt, "api_key": model.upstream.api_key}
    if model.upstream.api_base:
        kwargs["api_base"] = model.upstream.api_base
    if params.get("duration"):
        kwargs["seconds"] = str(params.pop("duration"))
    if params.get("size"):
        kwargs["size"] = str(params.pop("size")).replace("*", "x")
    if image:
        raw, mime = decode_input_image(image)
        kwargs["input_reference"] = (f"input.{mime.split('/')[-1]}", raw, mime)
    extra = {k: v for k, v in params.items() if v is not None}
    if extra:
        kwargs["extra_body"] = extra
    try:
        video = await litellm.avideo_generation(**kwargs)
    except Exception as e:  # noqa: BLE001
        raise _upstream_error(int(getattr(e, "status_code", 502) or 502), {"message": str(e)[:500]}, "出视频") from e
    vid = getattr(video, "id", None)
    if not vid:
        raise MediaUpstreamError(502, "上游没给视频任务号")
    return str(vid)


async def poll_video(model: ModelConfig, task: str, *, expected_seconds: float = 0) -> VideoStatus:
    if family(model) == "dashscope":
        url = dashscope_base(model) + f"/tasks/{task}"
        async with _client(timeout=30.0) as client:
            try:
                resp = await client.get(url, headers={"Authorization": f"Bearer {model.upstream.api_key}"})
            except httpx.HTTPError as e:
                return VideoStatus("running", error=f"这次没查到进度: {e}")
        data = _json(resp)
        if resp.status_code != 200 or not isinstance(data, dict):
            return VideoStatus("running", error=f"这次没查到进度: HTTP {resp.status_code}")
        out = data.get("output") or {}
        st = str(out.get("task_status") or "").upper()
        if st == "SUCCEEDED":
            usage = data.get("usage") or {}
            secs = (usage.get("output_video_duration") or usage.get("duration")
                    or usage.get("video_duration") or expected_seconds or 0)
            return VideoStatus("succeeded", url=str(out.get("video_url") or ""), seconds=float(secs))
        if st in ("FAILED", "CANCELED", "UNKNOWN"):
            msg = f"{out.get('code') or st}: {out.get('message') or ('任务已过期或不存在' if st == 'UNKNOWN' else '')}"
            return VideoStatus("failed", error=msg.strip(": "))
        return VideoStatus("running")

    import litellm  # noqa: PLC0415

    try:
        v = await litellm.avideo_status(video_id=task, model=model.upstream.model, api_key=model.upstream.api_key,
                                        **({"api_base": model.upstream.api_base} if model.upstream.api_base else {}))
    except Exception as e:  # noqa: BLE001
        return VideoStatus("running", error=f"这次没查到进度: {e}")
    st = str(getattr(v, "status", "") or "").lower()
    if st in ("completed", "succeeded"):
        secs = float(getattr(v, "seconds", 0) or expected_seconds or 0)
        return VideoStatus("succeeded", url=f"litellm:{task}", seconds=secs)
    if st in ("failed", "error", "cancelled", "canceled"):
        err = getattr(v, "error", None)
        return VideoStatus("failed", error=str(getattr(err, "message", err) or st))
    return VideoStatus("running")


async def fetch_video(model: ModelConfig, url: str) -> tuple[bytes, str]:
    if url.startswith("litellm:"):
        import litellm  # noqa: PLC0415

        raw = await litellm.avideo_content(video_id=url[len("litellm:"):], model=model.upstream.model,
                                           api_key=model.upstream.api_key,
                                           **({"api_base": model.upstream.api_base} if model.upstream.api_base else {}))
        return bytes(raw), "video/mp4"
    raw, ctype = await fetch_bytes(url, max_bytes=VIDEO_MAX_BYTES, timeout=300.0)
    return raw, ctype if ctype.startswith("video/") else "video/mp4"


def _json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return {"message": resp.text[:500]}
