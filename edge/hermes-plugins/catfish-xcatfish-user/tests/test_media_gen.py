"""11/17 小鲶出图 / 出视频: hermes image_generate / video_generate 的「鲶鱼中央」供应商 (media_gen.py)。

用真 hermes 的供应商基类和工具入口 (没装 hermes 就跳过), 中央网关换成假的 (httpx.MockTransport)。
钉住:
1. 两个供应商是 hermes 认的类型、名字 catfish; 有凭证才算可用 (不联网)
2. 能力跟着中央配置走 (有没有改图 / 图生视频、时长、清晰度、配音)
3. 出图: 请求体、身份头、来源标签; 结果存到 ~/.catfish/outputs; 改图把本机原图转成 data: URI, 超 10MB / 不是图片当场拒
4. 出视频: 提交 → 轮询 → 取文件 → 存 mp4; 失败把中央的话带回来
5. 从 hermes 自己的工具入口 (_handle_image_generate / _handle_video_generate) 走一遍, 参数名对得上
"""
from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path

import httpx
import pytest

agent = pytest.importorskip("agent.image_gen_provider", reason="要真 hermes (HERMES_ROOT)")

PLUGIN = Path(__file__).resolve().parents[1]
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


def _load():
    spec = importlib.util.spec_from_file_location("_test_media_gen", PLUGIN / "media_gen.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MODES = {
    "text_to_image": {"active": {"id": "img", "display_name": "千问文生图", "unit": "张", "price_per_unit": 0.2}},
    "image_to_image": {"active": {"id": "edit", "display_name": "千问改图", "max_input_images": 3}},
    "text_to_video": {"active": {"id": "t2v", "display_name": "万相文生视频", "min_duration": 2, "max_duration": 15,
                                 "resolutions": ["720P", "1080P"], "audio": None}},
    "image_to_video": {"active": {"id": "i2v", "display_name": "万相图生视频", "min_duration": 2, "max_duration": 10,
                                  "resolutions": ["720P"], "audio": True}},
}


class FakeCentral:
    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.modes = dict(MODES)
        self.video_states = ["running", "succeeded"]
        self.video_error = ""

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        path = req.url.path
        if path == "/v1/media/models":
            return httpx.Response(200, json={"modes": self.modes})
        if path == "/v1/images/generations":
            body = json.loads(req.content)
            return httpx.Response(200, json={"model": "edit" if body.get("images") else "img",
                                             "mode": "image_to_image" if body.get("images") else "text_to_image",
                                             "data": [{"b64_json": base64.b64encode(PNG).decode(), "mime": "image/png"}]})
        if path == "/v1/videos":
            return httpx.Response(200, json={"id": "v_1", "status": "running", "model": "t2v", "mode": "text_to_video",
                                             "duration": 5})
        if path == "/v1/videos/v_1":
            st = self.video_states.pop(0) if self.video_states else "succeeded"
            return httpx.Response(200, json={"id": "v_1", "status": st, "duration": 5, "error": self.video_error})
        if path == "/v1/videos/v_1/content":
            return httpx.Response(200, content=b"MP4BYTES", headers={"content-type": "video/mp4"})
        return httpx.Response(404, json={"detail": "nope"})

    def posted(self, path):
        return [json.loads(r.content) for r in self.requests if r.url.path == path and r.method == "POST"]


@pytest.fixture()
def mg(monkeypatch, tmp_path):
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    (hermes_home / ".env").write_text("OPENAI_API_KEY=svc-token\nCATFISH_DEFAULT_USER=alice@example.com\n")
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path / "catfish"))
    monkeypatch.setenv("CATFISH_GATEWAY_URL", "http://central.test:8999")
    for k in ("OPENAI_API_KEY", "CATFISH_DEFAULT_USER", "CATFISH_INTERNAL_DEV_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    mod = _load()
    central = FakeCentral()
    monkeypatch.setattr(mod, "_client", lambda timeout: httpx.Client(transport=httpx.MockTransport(central.handler)))
    monkeypatch.setattr(mod, "VIDEO_POLL_S", 0.0)
    img, vid = mod.make_providers()
    return mod, central, img, vid, tmp_path


def test_供应商类型和可用性(mg, monkeypatch):
    from agent.image_gen_provider import ImageGenProvider
    from agent.video_gen_provider import VideoGenProvider

    mod, _, img, vid, tmp = mg
    assert isinstance(img, ImageGenProvider) and isinstance(vid, VideoGenProvider)
    assert img.name == vid.name == "catfish"
    assert img.is_available() and vid.is_available()
    (tmp / "hermes" / ".env").write_text("")
    assert not img.is_available(), "没凭证 = 不可用 (不联网判断)"


def test_能力跟着中央配置走(mg):
    mod, central, img, vid, _ = mg
    assert img.capabilities() == {"modalities": ["text", "image"], "max_reference_images": 2}
    caps = vid.capabilities()
    assert caps["modalities"] == ["text", "image"] and caps["min_duration"] == 2 and caps["max_duration"] == 15
    assert caps["resolutions"] == ["720p", "1080p"] and caps["supports_audio"] is True
    assert [m["id"] for m in img.list_models()] == ["img", "edit"]
    # 中央没配改图 / 图生视频 → 只报文生
    central.modes = {"text_to_image": MODES["text_to_image"], "text_to_video": MODES["text_to_video"],
                     "image_to_image": {"active": None}, "image_to_video": {"active": None}}
    mod.media_modes(force=True)
    assert img.capabilities() == {"modalities": ["text"], "max_reference_images": 0}
    assert vid.capabilities()["modalities"] == ["text"]


def test_文生图_请求和存盘(mg):
    mod, central, img, _, tmp = mg
    r = img.generate("一只橘猫在窗台上", aspect_ratio="square")
    assert r["success"], r
    p = Path(r["image"])
    assert p.parent == tmp / "catfish" / "outputs" and p.name.startswith("小鲶出图-") and p.suffix == ".png"
    assert p.read_bytes() == PNG and r["model"] == "img" and r["provider"] == "catfish" and r["modality"] == "text"
    (body,) = central.posted("/v1/images/generations")
    assert body == {"prompt": "一只橘猫在窗台上", "aspect_ratio": "square"}
    req = [x for x in central.requests if x.url.path == "/v1/images/generations"][0]
    assert req.headers["authorization"] == "Bearer svc-token"
    assert req.headers["x-catfish-user"] == "alice@example.com"
    assert req.url.params["catfish_source"] == "plugin:media-image", "数据外发记录按这个标「出图 / 改图」"
    assert str(req.url).startswith("http://central.test:8999/")


def test_改图_本机原图转data_uri_超限拒(mg):
    mod, central, img, _, tmp = mg
    src = tmp / "原图.png"
    src.write_bytes(PNG)
    r = img.generate("把背景换成蓝天", image_url=str(src), reference_image_urls=[str(src)])
    assert r["success"] and Path(r["image"]).name.startswith("小鲶改图-") and r["modality"] == "image"
    (body,) = central.posted("/v1/images/generations")
    uri = "data:image/png;base64," + base64.b64encode(PNG).decode()
    assert body["images"] == [uri, uri]
    big = tmp / "big.png"
    big.write_bytes(PNG + b"0" * (10 * 1024 * 1024))
    r = img.generate("x", image_url=str(big))
    assert not r["success"] and "10MB" in r["error"]
    txt = tmp / "a.txt"
    txt.write_text("hello")
    r = img.generate("x", image_url=str(txt))
    assert not r["success"] and "不是认得的图片" in r["error"]
    assert len(central.posted("/v1/images/generations")) == 1, "拒掉的不发中央"


def test_中央拒了_原话带回(mg, monkeypatch):
    mod, _, img, _, _ = mg
    monkeypatch.setattr(mod, "_client", lambda timeout: httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(503, json={"detail": "中央还没配置可用的文生图模型"}))))
    r = img.generate("x")
    assert not r["success"] and "中央还没配置可用的文生图模型" in r["error"]


def test_出视频_轮询取回存mp4(mg):
    mod, central, _, vid, tmp = mg
    r = vid.generate("海边日落延时", duration=8, aspect_ratio="9:16", resolution="1080p")
    assert r["success"], r
    p = Path(r["video"])
    assert p.parent == tmp / "catfish" / "outputs" and p.suffix == ".mp4" and p.read_bytes() == b"MP4BYTES"
    assert r["duration"] == 5 and r["modality"] == "text"
    (body,) = central.posted("/v1/videos")
    assert body == {"prompt": "海边日落延时", "aspect_ratio": "9:16", "resolution": "1080p", "duration": 8}
    polls = [x for x in central.requests if x.url.path == "/v1/videos/v_1"]
    assert len(polls) == 2 and polls[0].url.params["catfish_source"] == "plugin:media-video"


def test_图生视频_带首帧(mg):
    mod, central, _, vid, tmp = mg
    src = tmp / "first.png"
    src.write_bytes(PNG)
    r = vid.generate("让画面动起来", image_url=str(src), audio=False)
    assert r["success"] and r["modality"] == "image"
    (body,) = central.posted("/v1/videos")
    assert body["image"].startswith("data:image/png;base64,") and body["audio"] is False


def test_出视频失败(mg):
    mod, central, _, vid, _ = mg
    central.video_states = ["failed"]
    central.video_error = "DataInspectionFailed: 内容不合规"
    r = vid.generate("x")
    assert not r["success"] and "DataInspectionFailed" in r["error"]


def test_register_两个都注册():
    mod = _load()

    class Ctx:
        def __init__(self):
            self.got = []

        def register_image_gen_provider(self, p):
            self.got.append(("image", p.name))

        def register_video_gen_provider(self, p):
            self.got.append(("video", p.name))

    ctx = Ctx()
    assert mod.register(ctx) is True
    assert ctx.got == [("image", "catfish"), ("video", "catfish")]
    assert mod.register(object()) is False, "老 hermes 没有这两个接口: 跳过不报错"


def test_走hermes自己的工具入口(mg, monkeypatch):
    """image_generate / video_generate 的 handler 按 config 选 catfish, 参数名跟我们的 generate 对得上。"""
    pytest.importorskip("tools.image_generation_tool", reason="hermes 工具模块要 hermes 自己的 venv (依赖全)")
    mod, central, img, vid, tmp = mg
    (tmp / "hermes" / "config.yaml").write_text("image_gen:\n  provider: catfish\nvideo_gen:\n  provider: catfish\n")
    from agent import image_gen_registry, video_gen_registry

    snap_i, snap_v = dict(image_gen_registry._providers), dict(video_gen_registry._providers)
    try:
        image_gen_registry.register_provider(img)
        video_gen_registry.register_provider(vid)
        from tools import image_generation_tool as IT
        from tools import video_generation_tool as VT

        monkeypatch.setattr(IT, "_plugin_provider_name", lambda: "catfish")
        monkeypatch.setattr(IT, "_get_plugin_provider", lambda name, force=False: img)
        out = json.loads(IT._handle_image_generate({"prompt": "测试图", "aspect_ratio": "portrait"}))
        assert out["success"] and out["provider"] == "catfish", out
        assert Path(out["image"]).exists()

        monkeypatch.setattr(VT, "_resolve_active_provider", lambda: vid)
        out = json.loads(VT._handle_video_generate({"prompt": "测试视频", "duration": 6}))
        assert out["success"] and Path(out["video"]).exists(), out
        (body,) = central.posted("/v1/videos")
        assert body["duration"] == 6 and body["resolution"] == "720p"
    finally:
        for reg, snap in ((image_gen_registry, snap_i), (video_gen_registry, snap_v)):
            reg._providers.clear()
            reg._providers.update(snap)
