"""小鲶出图 / 出视频: 给 Hermes 自带的 image_generate / video_generate 接上「鲶鱼中央」供应商 (11/17)。

# 为什么这么接

Hermes 自带两个工具: image_generate (文生图 + 改图) 和 video_generate (文生视频 + 图生视频), 背后是
可插拔的「供应商」(agent/image_gen_provider.py、agent/video_gen_provider.py)。自带的供应商都要员工
自己的海外 key (FAL / OpenAI / xAI …), 鲶鱼员工机上一个都没有, 所以这两个工具一直用不了。

这里注册一个名为 `catfish` 的供应商, 两个工具都转给中央网关:
  POST /v1/images/generations   出图 (带原图就是改图)
  POST /v1/videos → GET /v1/videos/{id} → GET /v1/videos/{id}/content   出视频 (异步, 这里轮询到出来)
用哪个模型、多少钱、默认参数, 都是中央「模型」页上的配置 (文生图 / 图生图 / 文生视频 / 图生视频四种用途),
员工端不选型号 —— Companion 把 image_gen.provider / video_gen.provider 设成 catfish (hermes_policy_config.rs)。

不另开工具: 网关给模型的工具已经 44 个, 离 Qwen 50+ 撞空 400 的线不远; 用 Hermes 自带的这两个,
工具说明 (按中央模型的能力动态生成) 和微信等渠道的发图 / 发视频都是 Hermes 现成的。

# 身份 / 外发

跟插件其它调网关的地方一样: hermes→网关的服务凭证 (.env OPENAI_API_KEY) + X-Catfish-User (这一轮对话的员工,
拿不到用本机登录员工), 中央记在员工名下、按张 / 秒计费。原图 (改图 / 图生视频) 以 data: URI 发给中央 ——
这一跳经过 egress_log 的出口挂钩, 「数据外发记录」里记成「出图 / 改图」「出视频」(catfish_source 标签)。

# 结果

存到 ~/.catfish/outputs/ (小鲶产出的统一目录, catfish_list_my_outputs 能找到, Companion 右栏自动打开),
文件名 小鲶出图-YYYYMMDD-HHMMSS.png / 小鲶视频-YYYYMMDD-HHMMSS.mp4。
"""
from __future__ import annotations

import base64
import json
import logging
import mimetypes
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("catfish.xcatfish_user.media_gen")

PROVIDER_NAME = "catfish"
SOURCE_IMAGE = "plugin:media-image"
SOURCE_VIDEO = "plugin:media-video"
#: 单张原图上限 (中央也卡 10MB, 这里先拦, 免得白传)
INPUT_MAX_BYTES = 10 * 1024 * 1024
#: 视频轮询: 每 10 秒问一次, 最多等 15 分钟 (百炼通常 1–5 分钟)
VIDEO_POLL_S = 10.0
VIDEO_DEADLINE_S = 900.0
CAPS_TTL_S = 60.0

_IMG_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif", "image/bmp": "bmp"}


# ── 环境: 网关地址 / 凭证 / 员工 ───────────────────────────────────────

def _hermes_home() -> Path:
    env = os.environ.get("HERMES_HOME", "").strip()
    if env:
        return Path(os.path.expanduser(env))
    try:
        from hermes_constants import get_hermes_home  # noqa: PLC0415

        return Path(get_hermes_home())
    except Exception:  # noqa: BLE001
        pass
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "hermes"
    return Path.home() / ".hermes"  # windows-parity: Windows 在上一行已返回 %LOCALAPPDATA%\\hermes, 这里只有 macOS / Linux


def _env_key(key: str) -> str:
    """os.environ → hermes 目录下 .env (hermes load_env 不写 os.environ)。"""
    val = os.environ.get(key, "").strip()
    if val:
        return val
    try:
        p = _hermes_home() / ".env"
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith(f"{key}="):
                    raw = line[len(key) + 1:].strip()
                    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
                        raw = raw[1:-1]
                    return raw.strip()
    except OSError:
        pass
    return ""


def gateway_base() -> str:
    """CATFISH_GATEWAY_URL → hermes 对话用的 model.base_url (去掉 /v1) → 本机默认。"""
    env = _env_key("CATFISH_GATEWAY_URL")
    if env:
        return env.rstrip("/")
    try:
        from hermes_cli.config import load_config  # noqa: PLC0415

        base = str(((load_config() or {}).get("model") or {}).get("base_url") or "").strip().rstrip("/")
        if base:
            return base[:-3] if base.endswith("/v1") else base
    except Exception:  # noqa: BLE001
        pass
    return "http://127.0.0.1:8999"


def _token() -> str:
    return _env_key("OPENAI_API_KEY") or _env_key("CATFISH_INTERNAL_DEV_TOKEN")


def _current_user() -> str:
    """这一轮对话的员工 (Companion 经 api_server 传来的 X-Catfish-User) → 本机登录员工。"""
    # plugin_ctx 把 ContextVar 挂在进程级的固定模块上 (插件会被加载好几份, 见 plugin_ctx 文件头);
    # 这个文件是 spec_from_file_location 单独加载的, 没法 relative import, 直接取那个模块。
    import sys  # noqa: PLC0415

    shared = sys.modules.get("_catfish_xcatfish_user_shared_ctx")
    cv = getattr(shared, "catfish_outgoing_user", None) if shared is not None else None
    if cv is not None:
        try:
            u = (cv.get() or "").strip()
            if u:
                return u
        except Exception:  # noqa: BLE001
            pass
    u = _env_key("CATFISH_DEFAULT_USER")
    return u if "@" in u else ""


def _headers(source: str) -> dict[str, str]:
    h = {"Authorization": f"Bearer {_token()}", "X-Catfish-Source": source}
    user = _current_user()
    if user:
        h["X-Catfish-User"] = user
    return h


def _client(timeout: float):
    import httpx  # noqa: PLC0415

    # trust_env=False: 员工 shell 里的代理 (Clash / PAC) 会把发往本机 / 中央的请求送错出口 (gateway_llm 同一条)
    return httpx.Client(timeout=timeout, trust_env=False)


class GatewayError(Exception):
    pass


def _detail(resp) -> str:
    try:
        d = resp.json()
    except ValueError:
        return resp.text[:300]
    det = d.get("detail") if isinstance(d, dict) else d
    if isinstance(det, dict):
        det = det.get("message") or json.dumps(det, ensure_ascii=False)
    return str(det)[:500]


def _call(method: str, path: str, *, source: str, timeout: float, body: Optional[dict] = None):
    url = f"{gateway_base()}{path}"
    sep = "&" if "?" in url else "?"
    url = f"{url}{sep}catfish_source={source}"
    try:
        with _client(timeout) as c:
            resp = c.request(method, url, json=body, headers=_headers(source))
    except Exception as e:  # noqa: BLE001
        raise GatewayError(f"连不上鲶鱼中央 ({gateway_base()}): {e}") from e
    if resp.status_code != 200:
        raise GatewayError(f"鲶鱼中央返回 {resp.status_code}: {_detail(resp)}")
    return resp


# ── 中央现在配了什么 (缓存 60 秒) ─────────────────────────────────────

_caps_lock = threading.Lock()
_caps: dict[str, Any] = {"at": 0.0, "modes": None}


def media_modes(force: bool = False) -> dict[str, Any]:
    """GET /v1/media/models 的 modes; 失败返回上一次的 (第一次失败返回 {})。"""
    with _caps_lock:
        if not force and _caps["modes"] is not None and time.time() - _caps["at"] < CAPS_TTL_S:
            return _caps["modes"]
    try:
        modes = _call("GET", "/v1/media/models", source=SOURCE_IMAGE, timeout=4.0).json().get("modes") or {}
    except Exception as e:  # noqa: BLE001
        logger.debug("media_gen: 读中央出图 / 出视频配置失败: %s", e)
        with _caps_lock:
            if _caps["modes"] is None:
                _caps.update(at=time.time() - CAPS_TTL_S + 10, modes={})  # 10 秒后再试
            return _caps["modes"]
    with _caps_lock:
        _caps.update(at=time.time(), modes=modes)
    return modes


def _active(mode: str) -> Optional[dict[str, Any]]:
    entry = (media_modes().get(mode) or {}).get("active")
    return entry if isinstance(entry, dict) else None


# ── 原图 → data: URI ─────────────────────────────────────────────────

def _sniff(raw: bytes) -> str:
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if raw[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    if raw[:4] in (b"GIF8",):
        return "image/gif"
    if raw[:2] == b"BM":
        return "image/bmp"
    return ""


def to_data_uri(src: str) -> str:
    """本机路径 / data: URI / http(s) 地址 → data: URI。超 10MB、不是图片抛 ValueError。"""
    s = (src or "").strip()
    if s.startswith("data:"):
        if not s.startswith("data:image/"):
            raise ValueError("原图不是图片")
        return s
    if s.startswith(("http://", "https://")):
        with _client(60.0) as c:
            r = c.get(s, follow_redirects=True)
        if r.status_code != 200:
            raise ValueError(f"原图地址取不到 (HTTP {r.status_code})")
        raw = r.content
    else:
        p = Path(os.path.expanduser(s[7:] if s.startswith("file://") else s))
        if not p.is_file():
            raise ValueError(f"找不到原图: {s}")
        if p.stat().st_size > INPUT_MAX_BYTES:
            raise ValueError(f"原图太大 ({p.stat().st_size // 1024 // 1024}MB), 一张最多 10MB")
        raw = p.read_bytes()
    if len(raw) > INPUT_MAX_BYTES:
        raise ValueError(f"原图太大 ({len(raw) // 1024 // 1024}MB), 一张最多 10MB")
    mime = _sniff(raw) or (mimetypes.guess_type(s)[0] or "")
    if not mime.startswith("image/"):
        raise ValueError("原图不是认得的图片格式 (png / jpg / webp / gif / bmp)")
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


# ── 存结果 ───────────────────────────────────────────────────────────

def outputs_dir() -> Path:
    home = Path(os.environ.get("CATFISH_HOME") or (Path.home() / ".catfish"))
    d = home / "outputs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save(raw: bytes, *, prefix: str, ext: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    d = outputs_dir()
    p = d / f"{prefix}-{stamp}.{ext}"
    i = 2
    while p.exists():
        p = d / f"{prefix}-{stamp}-{i}.{ext}"
        i += 1
    p.write_bytes(raw)
    return p


# ── 供应商 ───────────────────────────────────────────────────────────

def make_providers():
    """建两个供应商实例。在 register() 里调 (这时 hermes 的 agent 包已可导入)。"""
    from agent import image_gen_provider as IG  # noqa: PLC0415
    from agent import video_gen_provider as VG  # noqa: PLC0415

    class CatfishImageGen(IG.ImageGenProvider):
        @property
        def name(self) -> str:
            return PROVIDER_NAME

        @property
        def display_name(self) -> str:
            return "鲶鱼中央"

        def is_available(self) -> bool:
            return bool(_token())  # 不许联网 (picker 每次绘制都调)

        def list_models(self) -> list[dict[str, Any]]:
            out = []
            for mode in ("text_to_image", "image_to_image"):
                a = _active(mode)
                if a:
                    out.append({"id": a["id"], "display": a.get("display_name") or a["id"],
                                "price": f"{a['price_per_unit']} 元/张" if a.get("price_per_unit") is not None else ""})
            return out

        def default_model(self) -> Optional[str]:
            return None  # 中央按用途挑, 不由员工端定

        def capabilities(self) -> dict[str, Any]:
            edit = _active("image_to_image")
            if not edit:
                return {"modalities": ["text"], "max_reference_images": 0}
            return {"modalities": ["text", "image"],
                    "max_reference_images": max(0, int(edit.get("max_input_images") or 1) - 1)}

        def generate(self, prompt: str, aspect_ratio: str = IG.DEFAULT_ASPECT_RATIO, *,
                     image_url: Optional[str] = None, reference_image_urls: Optional[list[str]] = None,
                     **kwargs: Any) -> dict[str, Any]:
            aspect = IG.resolve_aspect_ratio(aspect_ratio)
            err = lambda msg, t="provider_error": IG.error_response(  # noqa: E731
                error=msg, error_type=t, provider=PROVIDER_NAME, prompt=prompt, aspect_ratio=aspect)
            if not (prompt or "").strip():
                return err("prompt is required", "invalid_request")
            if not _token():
                return err("这台电脑还没拿到连鲶鱼中央的凭证 (重新登录 Companion 试试)", "missing_credentials")
            sources = ([image_url] if image_url else []) + list(reference_image_urls or [])
            try:
                images = [to_data_uri(s) for s in sources]
            except ValueError as e:
                return err(str(e), "invalid_request")
            body: dict[str, Any] = {"prompt": prompt, "aspect_ratio": aspect}
            if images:
                body["images"] = images
            try:
                # 改图实测 2–3 分钟 (qwen-image-3.0-pro 146 秒); 中央模型超时 280 秒、nginx 300 秒, 这里等满 300
                d = _call("POST", "/v1/images/generations", source=SOURCE_IMAGE, timeout=300.0, body=body).json()
            except GatewayError as e:
                return err(str(e))
            paths = []
            for item in d.get("data") or []:
                raw = base64.b64decode(item.get("b64_json") or "")
                if raw:
                    paths.append(str(_save(raw, prefix="小鲶出图" if not images else "小鲶改图",
                                           ext=_IMG_EXT.get(item.get("mime") or "", "png"))))
            if not paths:
                return err("中央没返回图片")
            logger.info("media_gen: %s %s → %s", d.get("mode"), d.get("model"), paths)
            return IG.success_response(
                image=paths[0], model=str(d.get("model") or ""), prompt=prompt, aspect_ratio=aspect,
                provider=PROVIDER_NAME, modality="image" if images else "text",
                extra={"images": paths, "saved_to": str(outputs_dir())})

    class CatfishVideoGen(VG.VideoGenProvider):
        @property
        def name(self) -> str:
            return PROVIDER_NAME

        @property
        def display_name(self) -> str:
            return "鲶鱼中央"

        def is_available(self) -> bool:
            return bool(_token())

        def list_models(self) -> list[dict[str, Any]]:
            out = []
            for mode, modality in (("text_to_video", "text"), ("image_to_video", "image")):
                a = _active(mode)
                if a:
                    out.append({"id": a["id"], "display": a.get("display_name") or a["id"], "modalities": [modality],
                                "price": f"{a['price_per_unit']} 元/秒" if a.get("price_per_unit") is not None else ""})
            return out

        def default_model(self) -> Optional[str]:
            return None

        def capabilities(self) -> dict[str, Any]:
            t2v, i2v = _active("text_to_video"), _active("image_to_video")
            actives = [a for a in (t2v, i2v) if a]
            mins = [a["min_duration"] for a in actives if a.get("min_duration")]
            maxs = [a["max_duration"] for a in actives if a.get("max_duration")]
            res: list[str] = []
            for a in actives:
                for r in a.get("resolutions") or []:
                    if r.lower() not in res:
                        res.append(r.lower())
            return {
                "modalities": (["text"] if t2v else []) + (["image"] if i2v else []) or ["text"],
                "aspect_ratios": ["16:9", "9:16", "1:1", "4:3", "3:4"],
                "resolutions": res or list(VG.COMMON_RESOLUTIONS),
                "min_duration": min(mins) if mins else 1, "max_duration": max(maxs) if maxs else 10,
                "supports_audio": any(a.get("audio") for a in actives), "supports_negative_prompt": True,
                "supports_seed": True, "supports_upscale": False, "max_reference_images": 0,
            }

        def generate(self, prompt: str, *, model: Optional[str] = None, image_url: Optional[str] = None,
                     reference_image_urls: Optional[list[str]] = None, duration: Optional[int] = None,
                     aspect_ratio: str = VG.DEFAULT_ASPECT_RATIO, resolution: str = VG.DEFAULT_RESOLUTION,
                     negative_prompt: Optional[str] = None, audio: Optional[bool] = None,
                     seed: Optional[int] = None, **kwargs: Any) -> dict[str, Any]:
            err = lambda msg, t="provider_error": VG.error_response(  # noqa: E731
                error=msg, error_type=t, provider=PROVIDER_NAME, prompt=prompt, aspect_ratio=aspect_ratio)
            if not (prompt or "").strip():
                return err("prompt is required", "invalid_request")
            if not _token():
                return err("这台电脑还没拿到连鲶鱼中央的凭证 (重新登录 Companion 试试)", "missing_credentials")
            first = image_url or (reference_image_urls or [None])[0]
            body: dict[str, Any] = {"prompt": prompt, "aspect_ratio": aspect_ratio, "resolution": resolution}
            for k, v in (("duration", duration), ("negative_prompt", negative_prompt), ("audio", audio), ("seed", seed)):
                if v is not None:
                    body[k] = v
            if first:
                try:
                    body["image"] = to_data_uri(first)
                except ValueError as e:
                    return err(str(e), "invalid_request")
            try:
                job = _call("POST", "/v1/videos", source=SOURCE_VIDEO, timeout=90.0, body=body).json()
            except GatewayError as e:
                return err(str(e))
            jid = job.get("id")
            deadline = time.monotonic() + VIDEO_DEADLINE_S
            st: dict[str, Any] = job
            while True:
                time.sleep(VIDEO_POLL_S)
                try:
                    st = _call("GET", f"/v1/videos/{jid}", source=SOURCE_VIDEO, timeout=60.0).json()
                except GatewayError as e:
                    logger.info("media_gen: 视频 %s 这次没查到进度: %s", jid, e)
                    st = {"status": "running"}
                if st.get("status") in ("succeeded", "failed"):
                    break
                if time.monotonic() > deadline:
                    return err(f"视频 {int(VIDEO_DEADLINE_S // 60)} 分钟还没做好 (任务号 {jid}), 稍后再问一次", "timeout")
            if st.get("status") == "failed":
                return err(f"视频没做成: {st.get('error') or '上游失败'}")
            try:
                with _client(600.0) as c:
                    resp = c.get(f"{gateway_base()}/v1/videos/{jid}/content?catfish_source={SOURCE_VIDEO}",
                                 headers=_headers(SOURCE_VIDEO))
            except Exception as e:  # noqa: BLE001
                return err(f"视频做好了但取不回来: {e}")
            if resp.status_code != 200 or not resp.content:
                return err(f"视频做好了但取不回来: {resp.status_code} {_detail(resp)}")
            path = _save(resp.content, prefix="小鲶视频", ext="mp4")
            logger.info("media_gen: %s %s → %s", job.get("mode"), job.get("model"), path)
            return VG.success_response(
                video=str(path), model=str(job.get("model") or ""), prompt=prompt,
                modality="image" if first else "text", aspect_ratio=aspect_ratio,
                duration=int(float(st.get("duration") or job.get("duration") or 0)), provider=PROVIDER_NAME,
                extra={"saved_to": str(outputs_dir()), "job_id": jid})

    return CatfishImageGen(), CatfishVideoGen()


def register(ctx) -> bool:
    """注册两个供应商。hermes 没有这两个接口 (老版本) 时跳过, 返回 False。"""
    if not (hasattr(ctx, "register_image_gen_provider") and hasattr(ctx, "register_video_gen_provider")):
        return False
    img, vid = make_providers()
    ctx.register_image_gen_provider(img)
    ctx.register_video_gen_provider(vid)
    return True
