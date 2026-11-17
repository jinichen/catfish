"""出图 / 出视频的网关接口 (11/17)。

  GET  /v1/media/models            每种用途 (文生图 / 图生图 / 文生视频 / 图生视频) 现在用哪个模型、能做什么
  POST /v1/images/generations      出图: 不带 images 是文生图, 带 images (data: URI) 是改图 —— 同步, 直接回图片
  POST /v1/videos                  出视频: 不带 image 是文生视频, 带 image 是图生视频 —— 异步, 回任务号
  GET  /v1/videos/{id}             查进度 (成功那一次记账)
  GET  /v1/videos/{id}/content     取视频文件

员工端 (Hermes 的 image_generate / video_generate, 由鲶鱼插件的供应商接过来) 不选型号, 只说要做什么,
网关按用途挑模型: 挂「默认」的优先, 员工没权限 / 没配 key 的跳过, 落到同用途的下一个。

身份 / 配额 / 审计跟对话一样: Hermes 拿服务凭证 + X-Catfish-User 代员工调, 记在员工名下; 员工当天
token 配额用完了也不能出图 (配额管的是花钱); 用量按张 / 秒记进 quota_events.media_units。
不存提示词和图片 / 视频内容 (跟对话审计同一条边界)。
"""
from __future__ import annotations

import base64
import logging
import time
from typing import Any

from fastapi import Depends, HTTPException, Request
from fastapi.responses import Response

from . import media_jobs
from . import quota as _quota_module
from .auth import User, X_CATFISH_USER_HEADER, get_current_user, resolve_effective_user_email
from .config import Config, ModelConfig, get_config
from .media_config import IMAGE_MODES, MEDIA_MODES, MODE_LABEL, MODE_UNIT, VIDEO_MODES
from .media_upstream import (
    MediaUpstreamError,
    fetch_video,
    generate_images,
    media_of,
    poll_video,
    submit_video,
    video_request,
)
from .metrics import log_request_metadata

logger = logging.getLogger("catfish.gateway.media")

MAX_PROMPT_CHARS = 4000
VALID_IMAGE_ASPECTS = ("landscape", "square", "portrait")


def pick_model(config: Config, user: User, mode: str, requested: str | None = None) -> ModelConfig:
    """按用途挑模型。requested 是员工端点名的 (Hermes 配置里 image_gen.model 之类), 必须是这个用途的。"""
    label = MODE_LABEL.get(mode, mode)
    if requested:
        m = config.get_model(requested)
        if m is None or m.mode != mode:
            raise HTTPException(400, f"{requested} 不是{label}模型")
        if not user.can_access(m):
            raise HTTPException(403, f"你没有权限用 {requested}")
        if not m.upstream.is_available:
            raise HTTPException(503, f"{requested} 的上游 key 还没配, 请联系管理员")
        return m
    for m in config.media_models(mode):
        if user.can_access(m) and m.upstream.is_available:
            return m
    raise HTTPException(
        503,
        f"中央还没配置可用的{label}模型 (或你的部门没有权限)。请管理员到控制台 → 模型, "
        f"新增一个用途为「{label}」的模型。",
    )


def model_entry(m: ModelConfig) -> dict[str, Any]:
    media = media_of(m)
    entry: dict[str, Any] = {
        "id": m.name, "display_name": m.display_name, "mode": m.mode, "default": m.default,
        "unit": MODE_UNIT.get(m.mode, ""), "price_per_unit": m.price_per_unit,
    }
    if m.mode in IMAGE_MODES:
        entry["max_input_images"] = (media.max_input_images or 1) if m.mode == "image_to_image" else 0
    else:
        entry.update({
            "min_duration": media.min_duration, "max_duration": media.max_duration,
            "default_duration": media.default_duration, "resolutions": media.resolutions,
            "audio": media.audio,
        })
    return entry


def _effective(request: Request, user: User) -> str:
    return resolve_effective_user_email(user, request.headers.get(X_CATFISH_USER_HEADER))


def _source(request: Request) -> str:
    return (request.headers.get("x-catfish-source", "").strip()
            or request.query_params.get("catfish_source", "").strip() or "unknown")


def _check_quota(user: User, email: str, model: ModelConfig) -> None:
    try:
        qc = _quota_module.check_quota(user_email=email, department=user.department, model=model.name,
                                       est_tokens=0, role=user.role)
    except Exception as e:  # noqa: BLE001 —— 配额子系统挂了不挡业务 (跟对话同一条)
        logger.warning("media check_quota 炸 (放行): %s", e)
        return
    if not qc.allowed:
        raise HTTPException(429, detail={
            "error": "quota_exceeded", "message": _quota_module.friendly_quota_message(qc, email, model.name),
            "dimension": qc.dimension, "current": qc.current, "limit": qc.limit, "reset_at": qc.reset_at,
            "model": model.name,
        })


def _prompt(body: dict[str, Any]) -> str:
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(400, "prompt 不能为空")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise HTTPException(400, f"prompt 太长 ({len(prompt)} 字), 最多 {MAX_PROMPT_CHARS} 字")
    return prompt


def _int_or_none(v: Any) -> int | None:
    try:
        return None if v in (None, "") else int(v)
    except (TypeError, ValueError):
        return None


def _upstream_http(e: MediaUpstreamError) -> HTTPException:
    return HTTPException(e.status, e.message)


def register_media_routes(app) -> None:

    @app.get("/v1/media/models")
    async def media_models(user: User = Depends(get_current_user)) -> dict[str, Any]:
        """每种用途现在会用哪个模型 (员工能用、key 配了的里面挑), 没有就是 null。"""
        cfg = get_config()
        out: dict[str, Any] = {}
        for mode in MEDIA_MODES:
            usable = [m for m in cfg.media_models(mode) if user.can_access(m) and m.upstream.is_available]
            out[mode] = {"active": model_entry(usable[0]) if usable else None,
                         "models": [model_entry(m) for m in usable]}
        return {"modes": out}

    @app.post("/v1/images/generations")
    async def images_generations(request: Request, user: User = Depends(get_current_user)) -> dict[str, Any]:
        body = await request.json()
        prompt = _prompt(body)
        images = body.get("images") or []
        if isinstance(images, str):
            images = [images]
        if not isinstance(images, list) or not all(isinstance(i, str) and i.strip() for i in images):
            raise HTTPException(400, "images 要是 data: URI 字符串列表")
        mode = "image_to_image" if images else "text_to_image"
        cfg = get_config()
        model = pick_model(cfg, user, mode, (body.get("model") or "").strip() or None)
        max_in = media_of(model).max_input_images or 1
        if len(images) > max_in:
            raise HTTPException(400, f"{model.display_name} 最多带 {max_in} 张原图 / 参考图, 这次给了 {len(images)} 张")
        aspect = str(body.get("aspect_ratio") or "landscape").strip().lower()
        if aspect not in VALID_IMAGE_ASPECTS:
            aspect = "landscape"
        n = _int_or_none(body.get("n"))
        n = max(1, min(n, 4)) if n else None
        email = _effective(request, user)
        _check_quota(user, email, model)

        start = time.time()
        try:
            outs = await generate_images(
                model, prompt=prompt, images=[i.strip() for i in images], aspect_ratio=aspect, n=n,
                negative_prompt=(str(body.get("negative_prompt") or "").strip() or None),
                seed=_int_or_none(body.get("seed")),
            )
        except MediaUpstreamError as e:
            log_request_metadata(user=email, model=model.name, latency_ms=(time.time() - start) * 1000,
                                 status="error", error=e.message[:200], source=_source(request))
            raise _upstream_http(e) from e
        latency = (time.time() - start) * 1000
        log_request_metadata(user=email, model=model.name, latency_ms=latency, status="ok", source=_source(request),
                             media_units=len(outs), media_unit=MODE_UNIT[mode])
        _quota_module.record_usage(user_email=email, department=user.department, model=model.name,
                                   tokens_in=0, tokens_out=0, media_units=len(outs))
        logger.info("media image: user=%s model=%s mode=%s n=%d inputs=%d %.0fms",
                    email, model.name, mode, len(outs), len(images), latency)
        return {
            "model": model.name, "mode": mode, "aspect_ratio": aspect,
            "data": [{"b64_json": base64.b64encode(o.data).decode(), "mime": o.mime} for o in outs],
            "usage": {"units": len(outs), "unit": MODE_UNIT[mode]},
        }

    @app.post("/v1/videos")
    async def videos_create(request: Request, user: User = Depends(get_current_user)) -> dict[str, Any]:
        body = await request.json()
        prompt = _prompt(body)
        image = body.get("image")
        if image is not None and (not isinstance(image, str) or not image.strip()):
            raise HTTPException(400, "image 要是 data: URI 字符串")
        mode = "image_to_video" if image else "text_to_video"
        cfg = get_config()
        model = pick_model(cfg, user, mode, (body.get("model") or "").strip() or None)
        email = _effective(request, user)
        _check_quota(user, email, model)
        audio = body.get("audio")
        params, seconds = video_request(
            model, image=image, duration=_int_or_none(body.get("duration")),
            aspect_ratio=str(body.get("aspect_ratio") or "16:9").strip(),
            resolution=(str(body.get("resolution") or "").strip() or None),
            negative_prompt=(str(body.get("negative_prompt") or "").strip() or None),
            seed=_int_or_none(body.get("seed")), audio=audio if isinstance(audio, bool) else None,
        )
        start = time.time()
        try:
            task = await submit_video(model, prompt=prompt, image=image.strip() if image else None, params=params)
        except MediaUpstreamError as e:
            log_request_metadata(user=email, model=model.name, latency_ms=(time.time() - start) * 1000,
                                 status="error", error=e.message[:200], source=_source(request))
            raise _upstream_http(e) from e
        job = media_jobs.create(user_email=email, department=user.department, model=model.name, mode=mode,
                                upstream_task=task)
        if seconds:
            media_jobs.update(job.id, status="running", units=seconds)
        logger.info("media video submit: user=%s model=%s mode=%s job=%s secs=%s", email, model.name, mode, job.id, seconds)
        return {"id": job.id, "status": "running", "model": model.name, "mode": mode,
                "duration": seconds, "params": {k: v for k, v in params.items() if k in ("size", "resolution", "duration")}}

    def _owned(job_id: str, request: Request, user: User) -> media_jobs.Job:
        job = media_jobs.get(job_id)
        if job is None or job.user_email != _effective(request, user):
            raise HTTPException(404, "没有这个视频任务")
        return job

    @app.get("/v1/videos/{job_id}")
    async def videos_status(job_id: str, request: Request, user: User = Depends(get_current_user)) -> dict[str, Any]:
        job = _owned(job_id, request, user)
        model = get_config().get_model(job.model)
        if job.status not in media_jobs.TERMINAL:
            if model is None:
                media_jobs.update(job.id, status="failed", error="这个模型已经从中央删掉了")
                return {"id": job.id, "status": "failed", "error": "这个模型已经从中央删掉了"}
            st = await poll_video(model, job.upstream_task, expected_seconds=job.units)
            if st.status == "running":
                return {"id": job.id, "status": "running", "model": job.model,
                        "elapsed_s": int(time.time() - job.ts_ms / 1000), "note": st.error or ""}
            media_jobs.update(job.id, status=st.status, units=st.seconds or job.units, result_url=st.url, error=st.error)
            job = media_jobs.get(job.id) or job
        if job.status == "succeeded" and media_jobs.claim_metering(job.id):
            _quota_module.record_usage(user_email=job.user_email, department=job.department, model=job.model,
                                       tokens_in=0, tokens_out=0, media_units=job.units)
            log_request_metadata(user=job.user_email, model=job.model,
                                 latency_ms=float(job.updated_ms - job.ts_ms), status="ok", source=_source(request),
                                 media_units=job.units, media_unit=MODE_UNIT.get(job.mode, "秒"))
            logger.info("media video done: user=%s model=%s job=%s secs=%s", job.user_email, job.model, job.id, job.units)
        elif job.status == "failed":
            log_request_metadata(user=job.user_email, model=job.model, status="error", error=job.error[:200],
                                 source=_source(request))
        return {"id": job.id, "status": job.status, "model": job.model, "mode": job.mode,
                "duration": job.units, "error": job.error or "",
                "content_url": f"/v1/videos/{job.id}/content" if job.status == "succeeded" else ""}

    @app.get("/v1/videos/{job_id}/content")
    async def videos_content(job_id: str, request: Request, user: User = Depends(get_current_user)) -> Response:
        job = _owned(job_id, request, user)
        if job.status != "succeeded" or not job.result_url:
            raise HTTPException(409, "视频还没做好")
        model = get_config().get_model(job.model)
        if model is None:
            raise HTTPException(410, "这个模型已经从中央删掉了, 取不到视频")
        if time.time() * 1000 - job.updated_ms > 23 * 3600 * 1000:
            raise HTTPException(410, "视频的临时下载地址已过期 (上游只留 24 小时)")
        try:
            raw, ctype = await fetch_video(model, job.result_url)
        except MediaUpstreamError as e:
            raise _upstream_http(e) from e
        return Response(content=raw, media_type=ctype)


__all__ = ["register_media_routes", "pick_model", "VIDEO_MODES"]
