"""11/17 出图 / 出视频 —— 跟对话模型一样是「模型」页上的配置, 不写死型号。

钉住:
1. 配置: 四种新用途能存、别的用途名拒; 挑模型 = 默认优先、没权限 / 没 key 的跳过; 不进对话模型选择器
2. 百炼适配: 原生地址从兼容地址换出来; 请求体形状 (文生图 / 改图 / 文生视频 / 图生视频); 结果字节取回来
3. 视频: 提交 → 轮询 → 成功只记一次账 → 只有本人能取
4. 计费: 张数 / 秒数进 quota_events.media_units, by_model 带出来
5. LiteLLM 通用那条路 (非百炼型号) 照样能走
"""
from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

_KEY_ENV = "CATFISH_TEST_MEDIA_KEY"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
PNG_URI = "data:image/png;base64," + base64.b64encode(PNG).decode()


def _model(name, mode, *, upstream="dashscope/qwen-image-2.0-pro", default=False, api_base=None, media=None,
           key_env=_KEY_ENV, price=None):
    from catfish_gateway.config import ModelConfig

    up = {"model": upstream, "api_key_env": key_env}
    if api_base:
        up["api_base"] = api_base
    return ModelConfig(name=name, display_name=name, mode=mode, default=default, upstream=up,
                       media=media, price_per_unit=price)


# ── 1. 配置 ─────────────────────────────────────────────────


def test_四种用途能存_不认识的拒():
    from catfish_gateway.config import ModelConfig

    for mode in ("text_to_image", "image_to_image", "text_to_video", "image_to_video", "chat", "embedding"):
        assert _model("m", mode).mode == mode
    with pytest.raises(ValueError):
        ModelConfig(name="x", display_name="x", mode="image", upstream={"model": "dashscope/x"})


def test_挑模型_默认优先_没key没权限的跳过(monkeypatch):
    from fastapi import HTTPException

    from catfish_gateway.auth import User
    from catfish_gateway.media_routes import pick_model

    monkeypatch.setenv(_KEY_ENV, "k")
    a = _model("img-a", "text_to_image")
    b = _model("img-b", "text_to_image", default=True)
    nokey = _model("img-c", "text_to_image", key_env="CATFISH_TEST_MEDIA_NOKEY")
    cfg = SimpleNamespace(models=[a, b, nokey], get_model={m.name: m for m in [a, b, nokey]}.get,
                          media_models=lambda mode: sorted([m for m in [a, b, nokey] if m.mode == mode],
                                                           key=lambda m: not m.default))
    emp = User(sub="e@x.com", role="employee", department="d")
    assert pick_model(cfg, emp, "text_to_image").name == "img-b"
    only_a = User(sub="e@x.com", role="employee", department="d", effective_allowed_models=["img-a"])
    assert pick_model(cfg, only_a, "text_to_image").name == "img-a"
    with pytest.raises(HTTPException) as ei:
        pick_model(cfg, emp, "text_to_video")
    assert ei.value.status_code == 503 and "文生视频" in ei.value.detail
    with pytest.raises(HTTPException) as ei:
        pick_model(cfg, emp, "image_to_image", "img-a")   # 点名的不是这个用途
    assert ei.value.status_code == 400


def test_真Config_media_models_默认排前():
    from catfish_gateway.config import Config

    cfg = Config(models=[_model("chat", "chat", upstream="openai/x"),
                         _model("v1", "text_to_video", upstream="dashscope/wan2.6-t2v"),
                         _model("v2", "text_to_video", upstream="dashscope/wan2.7-t2v", default=True)])
    assert [m.name for m in cfg.media_models("text_to_video")] == ["v2", "v1"]
    assert cfg.media_models("chat") == []
    assert cfg.default_model().name == "chat"


def test_出图模型不进对话模型选择器(monkeypatch):
    from catfish_gateway.catalog import build_catalog
    from catfish_gateway.config import Config

    monkeypatch.setenv(_KEY_ENV, "k")
    cfg = Config(models=[_model("chat", "chat", upstream="openai/x", default=True),
                         _model("img", "text_to_image", price=0.25)])
    cat = build_catalog(cfg, None, {})
    assert [m["id"] for m in cat["models"]] == ["chat"]
    # 单独列给审计页算成本 (按张 / 按秒)
    assert cat["media_models"] == [{"id": "img", "display_name": "img", "mode": "text_to_image", "tier": "private",
                                    "unit": "张", "price_per_unit": 0.25, "color": None, "dot_emoji": None}]


# ── 2. 百炼适配 ─────────────────────────────────────────────


def test_百炼原生地址():
    from catfish_gateway.media_upstream import dashscope_base

    assert dashscope_base(_model("m", "text_to_image", api_base="https://dashscope.aliyuncs.com/compatible-mode/v1")) \
        == "https://dashscope.aliyuncs.com/api/v1"
    assert dashscope_base(_model("m", "text_to_image")) == "https://dashscope.aliyuncs.com/api/v1"
    assert dashscope_base(_model("m", "text_to_image", api_base="https://ws1.cn-beijing.maas.aliyuncs.com")) \
        == "https://ws1.cn-beijing.maas.aliyuncs.com/api/v1"
    assert dashscope_base(_model("m", "text_to_image", api_base="https://ws1.cn-beijing.maas.aliyuncs.com/api/v1/")) \
        == "https://ws1.cn-beijing.maas.aliyuncs.com/api/v1"


def test_视频参数_时长夹住_文生用size_图生用resolution():
    from catfish_gateway.media_config import MediaConfig
    from catfish_gateway.media_upstream import video_request

    media = MediaConfig(min_duration=2, max_duration=15, default_duration=5, resolutions=["720P", "1080P"],
                        params={"watermark": False})
    t2v = _model("t2v", "text_to_video", upstream="dashscope/wan2.6-t2v", media=media)
    p, secs = video_request(t2v, image=None, duration=30, aspect_ratio="9:16", resolution=None,
                            negative_prompt=None, seed=None, audio=None)
    assert secs == 15 and p["duration"] == 15 and p["size"] == "720*1280" and p["watermark"] is False
    assert "resolution" not in p
    i2v = _model("i2v", "image_to_video", upstream="dashscope/wan2.6-i2v-flash", media=media)
    p, secs = video_request(i2v, image=PNG_URI, duration=None, aspect_ratio="16:9", resolution="1080p",
                            negative_prompt=None, seed=None, audio=None)
    assert secs == 5 and p["resolution"] == "1080P" and "size" not in p
    # 不在允许档位里的 → 退到第一个
    p, _ = video_request(i2v, image=PNG_URI, duration=None, aspect_ratio="16:9", resolution="480P",
                         negative_prompt=None, seed=None, audio=None)
    assert p["resolution"] == "720P"


def test_强制参数盖过员工给的():
    from catfish_gateway.media_upstream import merged_params

    m = _model("m", "text_to_image")
    m.media = __import__("catfish_gateway.media_config", fromlist=["MediaConfig"]).MediaConfig(params={"watermark": True})
    m.upstream.param_overrides = {"watermark": False}
    assert merged_params(m, {"watermark": True, "n": 2}) == {"watermark": False, "n": 2}


class _Upstream:
    """拦 media_upstream 里的 httpx.AsyncClient, 按 URL 回假数据, 记下请求。"""

    def __init__(self, monkeypatch):
        from catfish_gateway import media_upstream as MU

        self.requests: list[httpx.Request] = []
        self.task_status = "RUNNING"
        real = httpx.AsyncClient

        def handler(req: httpx.Request) -> httpx.Response:
            self.requests.append(req)
            u = str(req.url)
            if u.endswith("/multimodal-generation/generation"):
                return httpx.Response(200, json={"output": {"choices": [{"message": {"content": [
                    {"image": "https://oss.example/out1.png"}]}}]}, "usage": {"image_count": 1}})
            if u.endswith("/video-synthesis"):
                return httpx.Response(200, json={"output": {"task_id": "up-123", "task_status": "PENDING"}})
            if "/tasks/up-123" in u:
                if self.task_status == "SUCCEEDED":
                    return httpx.Response(200, json={"output": {"task_status": "SUCCEEDED",
                                                                "video_url": "https://oss.example/v.mp4"},
                                                     "usage": {"output_video_duration": 5}})
                return httpx.Response(200, json={"output": {"task_status": self.task_status}})
            if u == "https://oss.example/out1.png":
                return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
            if u == "https://oss.example/v.mp4":
                return httpx.Response(200, content=b"MP4DATA", headers={"content-type": "video/mp4"})
            return httpx.Response(404, json={"code": "NotFound", "message": u})

        def factory(**kw):
            kw.pop("follow_redirects", None)
            return real(transport=httpx.MockTransport(handler), **kw)

        monkeypatch.setattr(MU, "_client", factory)

    def bodies(self, suffix):
        return [json.loads(r.content) for r in self.requests if str(r.url).endswith(suffix)]


@pytest.fixture()
def gw(monkeypatch, tmp_path):
    """TestClient + 打桩 config / 用户; 用量写 tmp sqlite (CATFISH_QUOTA_DB)。"""
    import logging

    logging.disable(logging.CRITICAL)
    monkeypatch.setenv(_KEY_ENV, "test-key")
    monkeypatch.delenv("CATFISH_DB_URL", raising=False)
    monkeypatch.setenv("CATFISH_QUOTA_DB", str(tmp_path / "quota.db"))
    from catfish_gateway import media_routes as MR
    from catfish_gateway.app import app
    from catfish_gateway.auth import User, get_current_user
    from catfish_gateway.config import Config
    from catfish_gateway.media_config import MediaConfig

    models = [
        _model("chat", "chat", upstream="openai/x", default=True),
        _model("img", "text_to_image", api_base="https://dashscope.aliyuncs.com/compatible-mode/v1", price=0.2,
               media=MediaConfig(params={"watermark": False, "prompt_extend": True})),
        _model("edit", "image_to_image", media=MediaConfig(max_input_images=2)),
        _model("t2v", "text_to_video", upstream="dashscope/wan2.6-t2v",
               media=MediaConfig(default_duration=5, resolutions=["720P"], params={"watermark": False})),
        _model("i2v", "image_to_video", upstream="dashscope/wan2.6-i2v-flash",
               media=MediaConfig(default_duration=5, resolutions=["720P"], audio=True)),
    ]
    cfg = Config(models=models)
    monkeypatch.setattr(MR, "get_config", lambda: cfg)
    monkeypatch.setattr(MR, "log_request_metadata", lambda **kw: None)
    up = _Upstream(monkeypatch)
    who = {"sub": "e@x.com"}

    async def _user() -> User:
        return User(sub=who["sub"], role="employee", department="研发部")

    app.dependency_overrides[get_current_user] = _user
    yield TestClient(app), up, who
    app.dependency_overrides.clear()
    logging.disable(logging.NOTSET)


def _usage():
    from catfish_gateway.quota_base import _get_conn

    conn = _get_conn()
    rows = conn.execute("SELECT user_email, model, tokens_in, tokens_out, media_units FROM quota_events").fetchall()
    conn.close()
    return rows


def test_文生图_百炼请求体_回图片字节_按张记账(gw):
    c, up, _ = gw
    r = c.post("/v1/images/generations", json={"prompt": "一只猫", "aspect_ratio": "portrait"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["model"] == "img" and d["mode"] == "text_to_image"
    assert base64.b64decode(d["data"][0]["b64_json"]) == PNG and d["data"][0]["mime"] == "image/png"
    (body,) = up.bodies("/multimodal-generation/generation")
    assert str(up.requests[0].url) == ("https://dashscope.aliyuncs.com/api/v1/services/aigc/"
                                       "multimodal-generation/generation")
    assert up.requests[0].headers["authorization"] == "Bearer test-key"
    assert body["model"] == "qwen-image-2.0-pro"
    assert body["input"]["messages"][0]["content"] == [{"text": "一只猫"}]
    assert body["parameters"] == {"watermark": False, "prompt_extend": True, "size": "928*1664"}
    assert _usage() == [("e@x.com", "img", 0, 0, 1.0)]


def test_改图_原图在前文字在后_张数上限(gw):
    c, up, _ = gw
    r = c.post("/v1/images/generations", json={"prompt": "换成蓝天", "images": [PNG_URI]})
    assert r.status_code == 200, r.text
    assert r.json()["model"] == "edit" and r.json()["mode"] == "image_to_image"
    (body,) = up.bodies("/multimodal-generation/generation")
    assert body["input"]["messages"][0]["content"] == [{"image": PNG_URI}, {"text": "换成蓝天"}]
    r = c.post("/v1/images/generations", json={"prompt": "x", "images": [PNG_URI] * 3})
    assert r.status_code == 400 and "最多带 2 张" in r.json()["detail"]
    r = c.post("/v1/images/generations", json={"prompt": "x", "images": ["https://evil/a.png"]})
    assert r.status_code == 400


def test_上游内容审核拒了_原话告诉员工(gw, monkeypatch):
    from catfish_gateway import media_upstream as MU

    real = httpx.AsyncClient

    def factory(**kw):
        kw.pop("follow_redirects", None)
        return real(transport=httpx.MockTransport(lambda req: httpx.Response(
            400, json={"code": "DataInspectionFailed", "message": "Input data may contain inappropriate content."})), **kw)

    monkeypatch.setattr(MU, "_client", factory)
    c, _, _ = gw
    r = c.post("/v1/images/generations", json={"prompt": "x"})
    assert r.status_code == 400 and "DataInspectionFailed" in r.json()["detail"]
    assert _usage() == []


def test_视频_提交_轮询_成功只记一次账_只有本人能取(gw):
    c, up, who = gw
    r = c.post("/v1/videos", json={"prompt": "海边日落", "aspect_ratio": "9:16"})
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["status"] == "running" and job["model"] == "t2v" and job["duration"] == 5
    (body,) = up.bodies("/video-synthesis")
    assert body == {"model": "wan2.6-t2v", "input": {"prompt": "海边日落"},
                    "parameters": {"watermark": False, "duration": 5, "size": "720*1280"}}
    sub = [r for r in up.requests if str(r.url).endswith("/video-synthesis")][0]
    assert sub.headers["x-dashscope-async"] == "enable"

    r = c.get(f"/v1/videos/{job['id']}")
    assert r.json()["status"] == "running"
    assert c.get(f"/v1/videos/{job['id']}/content").status_code == 409

    up.task_status = "SUCCEEDED"
    for _ in range(3):  # 员工端会反复轮询
        r = c.get(f"/v1/videos/{job['id']}")
        assert r.json()["status"] == "succeeded" and r.json()["duration"] == 5
    assert _usage() == [("e@x.com", "t2v", 0, 0, 5.0)]
    r = c.get(f"/v1/videos/{job['id']}/content")
    assert r.status_code == 200 and r.content == b"MP4DATA" and r.headers["content-type"] == "video/mp4"

    who["sub"] = "other@x.com"
    assert c.get(f"/v1/videos/{job['id']}").status_code == 404
    assert c.get(f"/v1/videos/{job['id']}/content").status_code == 404


def test_图生视频_首帧和清晰度(gw):
    c, up, _ = gw
    r = c.post("/v1/videos", json={"prompt": "让画面动起来", "image": PNG_URI, "audio": False})
    assert r.status_code == 200, r.text
    assert r.json()["model"] == "i2v"
    (body,) = up.bodies("/video-synthesis")
    assert body["input"] == {"prompt": "让画面动起来", "img_url": PNG_URI}
    assert body["parameters"] == {"duration": 5, "audio": False, "resolution": "720P"}


def test_视频失败_不记账(gw):
    c, up, _ = gw
    job = c.post("/v1/videos", json={"prompt": "x"}).json()
    up.task_status = "FAILED"
    r = c.get(f"/v1/videos/{job['id']}")
    assert r.json()["status"] == "failed"
    assert _usage() == []


def test_media_models_列出每种用途在用的(gw):
    c, _, _ = gw
    d = c.get("/v1/media/models").json()["modes"]
    assert d["text_to_image"]["active"]["id"] == "img" and d["text_to_image"]["active"]["unit"] == "张"
    assert d["image_to_image"]["active"]["max_input_images"] == 2
    assert d["image_to_video"]["active"]["audio"] is True and d["text_to_video"]["active"]["unit"] == "秒"


def test_v1_models_不列出图模型(gw):
    c, _, _ = gw
    from catfish_gateway import misc_routes as M
    from catfish_gateway import media_routes as MR

    import pytest as _p  # noqa: F401
    M_get = MR.get_config
    mp = _p.MonkeyPatch()
    mp.setattr(M, "get_config", M_get)
    try:
        ids = [m["id"] for m in c.get("/v1/models").json()["data"]]
    finally:
        mp.undo()
    assert ids == ["chat"]


# ── 4. 计费聚合 ─────────────────────────────────────────────


def test_by_model_带出张数秒数(monkeypatch, tmp_path):
    monkeypatch.delenv("CATFISH_DB_URL", raising=False)
    monkeypatch.setenv("CATFISH_QUOTA_DB", str(tmp_path / "q.db"))
    from catfish_gateway import quota
    from catfish_gateway.quota_audit import audit_summary_user_since

    quota.record_usage("a@x.com", "d", "img", 0, 0, media_units=2)
    quota.record_usage("a@x.com", "d", "img", 0, 0, media_units=1)
    quota.record_usage("a@x.com", "d", "chat", 10, 5)
    rows = {r["model"]: r for r in audit_summary_user_since("a@x.com", 0)["by_model"]}
    assert rows["img"]["media_units"] == 3.0 and rows["img"]["count"] == 2
    assert rows["chat"]["media_units"] == 0.0 and rows["chat"]["tokens_in"] == 10


# ── 5. LiteLLM 通用那条路 ───────────────────────────────────


def test_非百炼型号走litellm(monkeypatch):
    import asyncio

    import litellm

    from catfish_gateway import media_upstream as MU

    monkeypatch.setenv(_KEY_ENV, "k")
    seen = {}

    async def fake_gen(**kw):
        seen["gen"] = kw
        return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(PNG).decode(), url=None)])

    async def fake_edit(**kw):
        seen["edit"] = kw
        return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(PNG).decode(), url=None)])

    monkeypatch.setattr(litellm, "aimage_generation", fake_gen)
    monkeypatch.setattr(litellm, "aimage_edit", fake_edit)
    m = _model("gpt-img", "text_to_image", upstream="openai/gpt-image-1")
    out = asyncio.run(MU.generate_images(m, prompt="cat", images=[], aspect_ratio="square"))
    assert out[0].data == PNG and seen["gen"]["model"] == "openai/gpt-image-1" and seen["gen"]["size"] == "1024x1024"
    out = asyncio.run(MU.generate_images(m, prompt="cat", images=[PNG_URI], aspect_ratio="square"))
    assert seen["edit"]["image"][1] == PNG and seen["edit"]["image"][2] == "image/png"


def test_litellm视频_状态和取内容(monkeypatch):
    import asyncio

    import litellm

    from catfish_gateway import media_upstream as MU

    monkeypatch.setenv(_KEY_ENV, "k")

    async def fake_create(**kw):
        assert kw["seconds"] == "8" and kw["size"] == "1280x720"
        return SimpleNamespace(id="vid_1")

    async def fake_status(**kw):
        return SimpleNamespace(status="completed", seconds="8")

    async def fake_content(**kw):
        return b"MP4"

    monkeypatch.setattr(litellm, "avideo_generation", fake_create)
    monkeypatch.setattr(litellm, "avideo_status", fake_status)
    monkeypatch.setattr(litellm, "avideo_content", fake_content)
    m = _model("sora", "text_to_video", upstream="openai/sora-2")
    task = asyncio.run(MU.submit_video(m, prompt="x", image=None, params={"duration": 8, "size": "1280*720"}))
    st = asyncio.run(MU.poll_video(m, task))
    assert st.status == "succeeded" and st.seconds == 8.0 and st.url == "litellm:vid_1"
    assert asyncio.run(MU.fetch_video(m, st.url)) == (b"MP4", "video/mp4")


# ── 6. 百炼 wan3.0-video 的请求形状 (11/17 实测: 传 size / img_url 会被静默忽略) ──


def test_wan3_文生_档位加画幅比_不传size(gw, monkeypatch):
    from catfish_gateway import media_routes as MR
    from catfish_gateway.config import Config
    from catfish_gateway.media_config import MediaConfig

    media = MediaConfig(default_duration=5, resolutions=["720P", "1080P"], size_param="resolution_ratio",
                        image_input="first_frame", audio=True)
    cfg = Config(models=[_model("w3t", "text_to_video", upstream="dashscope/wan3.0-video", media=media),
                         _model("w3i", "image_to_video", upstream="dashscope/wan3.0-video", media=media)])
    monkeypatch.setattr(MR, "get_config", lambda: cfg)
    c, up, _ = gw
    assert c.post("/v1/videos", json={"prompt": "海边", "aspect_ratio": "9:16"}).status_code == 200
    assert c.post("/v1/videos", json={"prompt": "动起来", "image": PNG_URI, "aspect_ratio": "3:2"}).status_code == 200
    t2v, i2v = up.bodies("/video-synthesis")
    assert t2v["parameters"] == {"duration": 5, "resolution": "720P", "ratio": "9:16"}
    assert t2v["input"] == {"prompt": "海边"}
    assert i2v["input"] == {"prompt": "动起来", "media": [{"type": "first_frame", "url": PNG_URI}]}
    assert i2v["parameters"]["ratio"] == "adaptive", "带首帧: 画幅跟原图"
    assert "size" not in t2v["parameters"] and "img_url" not in i2v["input"]
