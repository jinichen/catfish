"""BL-FIX2 (5/8) — multimodal_tool_unwrap 单测.

覆盖:
  - 不动: text-only messages / 没 image marker / 非 JSON content / 短 content
  - 重组: tool 含 data_uri / tool 含 raw data + format 字段
  - tool message content 重组后只剩元数据 (path / summary, 无 data/data_uri)
  - 紧接着插一条 role=user multipart (text + image_url)
  - 幂等: 重组过的再调用不再动 (没 image marker 了)
  - tool_call_id 保留 (上游需要对应 assistant tool_calls)
"""
from __future__ import annotations

import json

import pytest

from catfish_gateway.multimodal_tool_unwrap import (
    _looks_like_tool_content_with_image,
    _extract_image_from_tool_content,
    unwrap_tool_images,
)


# ============================================================
# 预筛检测
# ============================================================


def test_looks_like_short_content_returns_false() -> None:
    assert not _looks_like_tool_content_with_image("ok")
    assert not _looks_like_tool_content_with_image('{"x": 1}')


def test_looks_like_no_image_marker_returns_false() -> None:
    body = '{"path": "/tmp/x.png", "summary": "ok"}' + "x" * 500
    assert not _looks_like_tool_content_with_image(body)


def test_looks_like_with_data_uri_returns_true() -> None:
    body = '{"data_uri": "data:image/png;base64,iVBOR' + "A" * 500 + '", "path": "/tmp/x"}'
    assert _looks_like_tool_content_with_image(body)


def test_looks_like_with_image_url_field_returns_true() -> None:
    body = '{"image_url": {"url": "..."}, "data": "' + "A" * 500 + '"}'
    assert _looks_like_tool_content_with_image(body)


def test_looks_like_non_string_returns_false() -> None:
    assert not _looks_like_tool_content_with_image(None)
    assert not _looks_like_tool_content_with_image({"a": 1})
    assert not _looks_like_tool_content_with_image(["a"])


# ============================================================
# 提取
# ============================================================


def test_extract_from_data_uri() -> None:
    fake_b64 = "iVBOR" + "A" * 500
    body = json.dumps({
        "type": "image",
        "data_uri": f"data:image/png;base64,{fake_b64}",
        "data": fake_b64,
        "path": "/tmp/shot.png",
        "summary": "截图完成",
        "size_bytes": 1234,
    })
    cleaned, image = _extract_image_from_tool_content(body)
    assert cleaned is not None
    assert image is not None
    # cleaned 不含 data / data_uri
    assert "data" not in cleaned
    assert "data_uri" not in cleaned
    # cleaned 保留 path / summary / size_bytes
    assert cleaned["path"] == "/tmp/shot.png"
    assert cleaned["summary"] == "截图完成"
    assert cleaned["size_bytes"] == 1234
    # cleaned 加了 _image_relocated 提示
    assert "_image_relocated" in cleaned
    # image 含 data:image URL
    assert image["url"].startswith("data:image/png;base64,")
    assert fake_b64 in image["url"]


def test_extract_from_raw_data_field_only() -> None:
    """没 data_uri 但有 data + format 字段, 也能拼出 data: URL"""
    fake_b64 = "iVBOR" + "B" * 500
    body = json.dumps({
        "data": fake_b64,
        "format": "jpeg",
        "path": "/tmp/x.jpg",
    })
    cleaned, image = _extract_image_from_tool_content(body)
    assert cleaned is not None
    assert image is not None
    assert image["url"].startswith("data:image/jpeg;base64,")


def test_extract_invalid_json_returns_none() -> None:
    cleaned, image = _extract_image_from_tool_content("not json {{{ random")
    assert cleaned is None
    assert image is None


def test_extract_no_image_field_returns_none() -> None:
    body = json.dumps({"path": "/tmp/x.png", "summary": "ok"})
    cleaned, image = _extract_image_from_tool_content(body)
    assert cleaned is None
    assert image is None


def test_extract_alt_text_falls_back_to_reason() -> None:
    fake_b64 = "x" * 500
    body = json.dumps({
        "data_uri": f"data:image/png;base64,{fake_b64}",
        "reason": "看下登录页",
    })
    _cleaned, image = _extract_image_from_tool_content(body)
    assert image is not None
    assert "看下登录页" in image["alt_text"]


# ============================================================
# 主转换 unwrap_tool_images
# ============================================================


def test_unwrap_empty_messages_returns_empty() -> None:
    assert unwrap_tool_images([]) == []


def test_unwrap_text_only_passes_through() -> None:
    msgs = [
        {"role": "system", "content": "你是鲶鱼"},
        {"role": "user", "content": "今天天气怎么样"},
        {"role": "assistant", "content": "晴 26 度"},
    ]
    out = unwrap_tool_images(msgs)
    assert out == msgs


def test_unwrap_tool_without_image_passes_through() -> None:
    msgs = [
        {"role": "tool", "content": '{"result": "ok"}', "tool_call_id": "call_1"},
    ]
    out = unwrap_tool_images(msgs)
    assert out == msgs  # 不动


def test_unwrap_tool_with_image_inserts_user_multipart() -> None:
    fake_b64 = "iVBOR" + "C" * 500
    tool_content = json.dumps({
        "type": "image",
        "data_uri": f"data:image/png;base64,{fake_b64}",
        "data": fake_b64,
        "path": "/tmp/shot.png",
        "summary": "截图完成 (fullscreen, 200 KB)",
        "reason": "看 EIS 登录页",
    })
    msgs = [
        {"role": "user", "content": "登录 EIS"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "catfish_screenshot", "arguments": "{}"}}]},
        {"role": "tool", "content": tool_content, "tool_call_id": "call_1"},
    ]
    out = unwrap_tool_images(msgs)
    # 4 条 (原 3 条 + 1 条新插入的 user multipart)
    assert len(out) == 4
    # 前 2 条不动
    assert out[0]["content"] == "登录 EIS"
    assert out[1]["tool_calls"][0]["id"] == "call_1"
    # 第 3 条 (tool message) 改写: tool_call_id 保留, content 不再含 data
    assert out[2]["role"] == "tool"
    assert out[2]["tool_call_id"] == "call_1"
    cleaned = json.loads(out[2]["content"])
    assert "data" not in cleaned
    assert "data_uri" not in cleaned
    assert cleaned["path"] == "/tmp/shot.png"
    assert "_image_relocated" in cleaned
    # 第 4 条 (新插入) — user multipart 含 image_url
    assert out[3]["role"] == "user"
    assert isinstance(out[3]["content"], list)
    parts = out[3]["content"]
    types = {p["type"] for p in parts}
    assert "text" in types
    assert "image_url" in types
    img_part = [p for p in parts if p["type"] == "image_url"][0]
    assert img_part["image_url"]["url"].startswith("data:image/png;base64,")


def test_unwrap_idempotent() -> None:
    """重组过的 messages 再跑一次 unwrap, 不会再动 (因为 cleaned 不再含 image marker)"""
    fake_b64 = "iVBOR" + "D" * 500
    tool_content = json.dumps({
        "data_uri": f"data:image/png;base64,{fake_b64}",
        "path": "/tmp/x.png",
    })
    msgs = [
        {"role": "tool", "content": tool_content, "tool_call_id": "call_1"},
    ]
    once = unwrap_tool_images(msgs)
    twice = unwrap_tool_images(once)
    assert once == twice


def test_unwrap_multiple_tool_messages() -> None:
    """多条 tool 含图 — 各自独立重组, 各自插一条 user multipart"""
    b1 = "iVBOR" + "E" * 500
    b2 = "iVBOR" + "F" * 500
    msgs = [
        {"role": "user", "content": "干两次"},
        {"role": "tool", "content": json.dumps({"data_uri": f"data:image/png;base64,{b1}", "path": "/tmp/1.png"}), "tool_call_id": "c1"},
        {"role": "tool", "content": json.dumps({"data_uri": f"data:image/png;base64,{b2}", "path": "/tmp/2.png"}), "tool_call_id": "c2"},
    ]
    out = unwrap_tool_images(msgs)
    # 1 user + 2 tool + 2 user multipart = 5
    assert len(out) == 5
    # 验 2 条 tool 改写的位置 + 紧跟 user multipart
    assert out[1]["role"] == "tool" and out[1]["tool_call_id"] == "c1"
    assert out[2]["role"] == "user"
    assert out[3]["role"] == "tool" and out[3]["tool_call_id"] == "c2"
    assert out[4]["role"] == "user"


def test_unwrap_invalid_msg_passes_through() -> None:
    """非 dict / 缺 role 字段 等异常输入不应让函数崩"""
    msgs = [
        "not a dict",  # 异常类型
        None,
        {"role": "tool"},  # 缺 content
        {"role": "tool", "content": None, "tool_call_id": "x"},
    ]
    out = unwrap_tool_images(msgs)  # type: ignore[arg-type]
    # 全原样 (没崩)
    assert out == msgs
