"""出图 / 出视频模型的配置 (11/17)。

文生图、图生图 (改图)、文生视频、图生视频跟对话模型、向量模型一样, 是「模型」页上的一行配置:
上游型号、供应商、单价、默认参数都在配置里, 代码里不写任何型号名。百炼一个月就出好几个新型号
(qwen-image-2.0 → 3.0、wan2.6 → 2.7 → 3.0、happyhorse), 换型号是管理员在界面上改一行的事。

每种用途 (mode) 各有一个默认模型 (挂「默认」的那个; 没挂就按列表顺序取第一个能用的)。员工端
(Hermes 的 image_generate / video_generate) 不选型号, 只说要做什么, 由网关按用途挑。

计费单位跟着用途走: 图片按张、视频按秒。单价是 price_per_unit (元 / 张 或 元 / 秒),
用量记在 quota_events.media_units。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

IMAGE_MODES: tuple[str, ...] = ("text_to_image", "image_to_image")
VIDEO_MODES: tuple[str, ...] = ("text_to_video", "image_to_video")
MEDIA_MODES: tuple[str, ...] = IMAGE_MODES + VIDEO_MODES
#: 模型页上能选的全部用途。chat / embedding 是原来的两种。
ALL_MODES: tuple[str, ...] = ("chat", "embedding") + MEDIA_MODES

MODE_LABEL: dict[str, str] = {
    "chat": "对话",
    "embedding": "向量",
    "text_to_image": "文生图",
    "image_to_image": "图生图 (改图)",
    "text_to_video": "文生视频",
    "image_to_video": "图生视频",
}

#: 计费单位
MODE_UNIT: dict[str, str] = {
    "text_to_image": "张",
    "image_to_image": "张",
    "text_to_video": "秒",
    "image_to_video": "秒",
}


def is_media_mode(mode: str | None) -> bool:
    return (mode or "chat") in MEDIA_MODES


def is_video_mode(mode: str | None) -> bool:
    return (mode or "chat") in VIDEO_MODES


class MediaConfig(BaseModel):
    """出图 / 出视频模型的附加配置。全部可选, 不填走上游自己的默认。"""

    #: 每次调用默认带给上游的参数, 原样放进上游请求 (百炼是 parameters 里), 例:
    #:   {watermark: false, prompt_extend: true}            —— 图片
    #:   {duration: 5, watermark: false, audio: false}       —— 视频
    #: 员工请求里给了同名参数时以员工的为准; 不想让员工改的写进 upstream.param_overrides (强制)。
    params: dict[str, Any] = Field(default_factory=dict)

    #: 图片: 横 / 方 / 竖三种画幅各用什么尺寸 (百炼写 "宽*高")。不填 → 用上游适配层的默认表。
    sizes: dict[str, str] = Field(default_factory=dict)

    #: 图生图: 最多带几张原图 / 参考图 (百炼 Qwen-Image 是 1–3)。不填 = 1。
    max_input_images: int | None = Field(default=None, ge=1, le=16)

    #: 视频: 时长范围 (秒) 和默认时长。不填 → 不在网关这层卡, 交给上游。
    min_duration: int | None = Field(default=None, ge=1)
    max_duration: int | None = Field(default=None, ge=1)
    default_duration: int | None = Field(default=None, ge=1)

    #: 视频: 允许的清晰度档位 (如 ["720P", "1080P"]), 第一个是默认。不填 → 不卡。
    resolutions: list[str] = Field(default_factory=list)

    #: 视频: 上游怎么表达画面大小 ——
    #:   size              "宽*高" (百炼文生视频 wan2.x-t2v: 按档位 + 画幅比查表)
    #:   resolution        只给档位 "720P" (百炼图生视频 wan2.x-i2v: 宽高比跟着原图)
    #:   resolution_ratio  档位 + 画幅比 ratio (百炼 wan3.0-video: 没有 size; 带首帧时 ratio=adaptive 跟原图)
    #: 不填 → 文生视频用 size, 图生视频用 resolution。
    #: 11/17 实测: 给 wan3.0-video 传 size 被忽略, 按它的默认 1080P 出 (更贵) —— 型号换了要跟着改这一项。
    size_param: Literal["size", "resolution", "resolution_ratio"] | None = None

    #: 视频: 首帧图怎么传 ——
    #:   img_url      input.img_url (百炼 wan2.x-i2v)
    #:   first_frame  input.media = [{type: first_frame, url}] (百炼 wan3.0-video; 传 img_url 会被忽略, 视频跟原图无关)
    #: 不填 → img_url。
    image_input: Literal["img_url", "first_frame"] | None = None

    #: 视频: 模型是否自带配音 (告诉员工端, 让小鲶知道能不能在提示词里描述声音)。
    audio: bool | None = None
