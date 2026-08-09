"""P46: picker 是唯一真源 —— 会话持久化的模型不参与 (8/9)。

# 这条闸防的是什么

8/9 实撞: 工作台选 deepseek, 同一次早安页刷新里一部分请求走 deepseek (成功)、
一部分走 qwen (周配额耗尽 → 429 → 早安页出不来)。根因是 hermes 的会话级模型
override 持久化在 `state.db sessions.model`, 一次写入永久生效, 员工换 picker
也不解除, 而且 session_key 是 prompt 哈希 —— 员工看不到也清不掉。

鸿波的判断: **在要求确定性的环境里, "同一次操作用了不同模型"是故障, 不是配置
问题。** 所以规则收成三条, 会话持久化那条直接砍掉。

# 判据长什么样

    请求体显式带 model  →  用它            (Companion picker 就这么传)
    没带              →  picker_state.json
    都没有            →  空串 (不表态, 让 hermes 自己决定)

`catfish-auto` **不算显式** —— 它是 hermes config.yaml 的静态占位符, 微信那条路
恒发它。当成显式会把 picker 整个挡住。
"""
from __future__ import annotations

import json

import pytest

import model_authority as ma


# ── 判据本身 ──────────────────────────────────────────────────

def test_请求体带了就用请求体的():
    assert ma.decide_model(request_model="m-explicit", picker_model="m-picker") == "m-explicit"


def test_请求体没带就用_picker():
    assert ma.decide_model(request_model=None, picker_model="m-picker") == "m-picker"
    assert ma.decide_model(request_model="", picker_model="m-picker") == "m-picker"
    assert ma.decide_model(request_model="   ", picker_model="m-picker") == "m-picker"


def test_两个都没有就不表态():
    """返空串 = 我们不覆盖, 让 hermes 自己那套决定。不猜、不硬编码兜底模型。"""
    assert ma.decide_model(request_model=None, picker_model="") == ""
    assert ma.decide_model(request_model=None, picker_model=None if False else "") == ""


def test_catfish_auto_不算显式选择():
    """微信那条路员工没 picker, hermes 恒发 catfish-auto。

    当成显式会让 picker 永远生效不了 —— 而且表现是"设置了没用", 极难查。
    """
    assert ma.decide_model(request_model="catfish-auto", picker_model="m-picker") == "m-picker"
    assert ma.decide_model(request_model="CATFISH-AUTO", picker_model="m-picker") == "m-picker"


def test_decide_model_压根不接受_session_model():
    """结构性: 想传都传不进来 —— 这是这个模块存在的全部意义。

    少了这条, 有人"顺手加个参数支持一下会话 override"就把 8/9 的故障放回来了。
    """
    with pytest.raises(TypeError):
        ma.decide_model(request_model=None, session_model="qwen")  # type: ignore[call-arg]


def test_只接受关键字参数():
    """防位置传参把 request_model / picker_model 弄反 —— 弄反的表现是
    "picker 永远赢不过请求体", 静默且方向相反, 最难查。"""
    with pytest.raises(TypeError):
        ma.decide_model("m-explicit")  # type: ignore[misc]


# ── 读 picker_state.json ─────────────────────────────────────

def _write(tmp_path, payload):
    p = tmp_path / "picker_state.json"
    p.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
    return p


def test_读到正常的_picker(tmp_path):
    p = _write(tmp_path, {"chat_model": "catfish-public-deepseek-flash"})
    assert ma.read_picker_model(p) == "catfish-public-deepseek-flash"


def test_文件不存在返空串(tmp_path):
    assert ma.read_picker_model(tmp_path / "没有这个文件.json") == ""


@pytest.mark.parametrize("payload", [
    "{坏 JSON",
    json.dumps(["不是 dict"]),
    json.dumps({"chat_model": 42}),
    json.dumps({"chat_model": "   "}),
    json.dumps({}),
])
def test_各种坏数据都返空串不抛(tmp_path, payload):
    """读 picker 失败不该让 agent 创建挂掉 —— 空串 = 不表态, 退回 hermes。"""
    assert ma.read_picker_model(_write(tmp_path, payload)) == ""


def test_前后空白会被去掉(tmp_path):
    p = _write(tmp_path, {"chat_model": "  m-picker \n"})
    assert ma.read_picker_model(p) == "m-picker"


# ── 8/9 那次故障的原样重放 ───────────────────────────────────

def test_回放_8月9日_早安页那次(tmp_path):
    """会话钉着 qwen (配额耗尽), picker 是 deepseek, 请求体没带 model。

    老行为: 会话 override 赢 → qwen → 429 → 早安页出不来。
    现在: picker 赢。
    """
    picker = _write(tmp_path, {"chat_model": "catfish-public-deepseek-flash"})
    decided = ma.decide_model(
        request_model=None,
        picker_model=ma.read_picker_model(picker),
    )
    assert decided == "catfish-public-deepseek-flash"
    assert "qwen" not in decided
