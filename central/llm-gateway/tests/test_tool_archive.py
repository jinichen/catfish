"""tool_archive — 6/7 BL-MANIFESTO-CLEAN-DEAD 后只剩 prepare_tool_messages.

老 archive_tool_messages / db / prompts / reader / features / summary_worker /
router 整套 5/22 已经 edge 端 (tool-bridge tool_archive_local.py) 接管, 6/7 整套
rm. 这 file 只测剩下的 prepare_tool_messages wrapper (永远走 truncate).

老 test 见 git history commit BL-MANIFESTO-CLEAN-DEAD 之前.

## 7/30 修: 测试数据不再写死字节数

原本三个用例都喂 `"x" * 10000` 并断言会被截断。写的时候这是对的 ——
当时 MAX_BYTES_PER_TOOL = 2_000, 一万字节远超阈值。

6/12 (P3.3.30) 把阈值从 2_000 提到 20_000, 理由充分: 老的 2K 对 xlsx 多轮
修改太激进, 上一轮的完整状态被截到 2K 后 LLM 看不到中间项就开始编造
(鸿波 6/11 实测周报反复改 6-7 轮才修对)。阈值提高 10 倍之后, 一万字节
**低于阈值、本来就不该截** —— 这三个用例从那天起一直红着, 红了七周。

实现没错, 是测试数据跟阈值绑死了。所以这次不是把 10000 改成 30000
(那只是把同一个坑往后挪一格), 而是让数据从常量推导, 阈值将来再调也不会漂。

## 这几个用例真正守的是什么

`"已归档" not in` 才是 manifesto 公理 4 的硬保证 (中央服务物理上不存对话
内容 archive)。那条断言七周来一直通过 —— 也就是说**真正要守的不变式没有
失守**, 破的是配套的截断断言。

这个区别值得写下来: 一个红了很久的测试, 不等于它守护的东西已经坏了。
但也不能因此就放着 —— 红着的测试没人看, 下次真的破了也不会有人发现。
"""
from __future__ import annotations

import pytest

from catfish_gateway.tool_msg_truncator import (
    MAX_BYTES_PER_TOOL,
    MIN_BYTES_TO_TRUNCATE,
)

# 必然触发截断的尺寸 —— 从常量推导, 不写死。
# 多给 1000 字节是为了离阈值有明确距离, 免得边界情况干扰判断。
_BIG = "x" * (MIN_BYTES_TO_TRUNCATE + 1000)

# 必然不触发截断的尺寸。取阈值一半, 同样跟着常量走。
_SMALL = "x" * (MIN_BYTES_TO_TRUNCATE // 2)


def _one(content: str) -> list[dict]:
    return [{"role": "tool", "tool_call_id": "a", "content": content}]


def test_prepare_always_truncates_never_archives(monkeypatch):
    """6/7 BL-CATFISH-MANIFESTO: prepare_tool_messages **永远 truncate**, 永不写 PG.

    跟 manifesto 公理 4 "API surface 物理无能" 一致 — 中央服务物理上不存对话内容
    archive. 这条 test 保 hard guarantee 不被回滚."""
    monkeypatch.setenv("CATFISH_GATEWAY_TOOL_ARCHIVE_ENABLE", "1")  # 旧 env 设了也没用
    monkeypatch.setenv("CATFISH_TOOL_ARCHIVE_ENABLED", "1")
    from catfish_gateway.tool_archive import archiver
    out = archiver.prepare_tool_messages(_one(_BIG), user_email="t@x.com")
    assert "已截断" in out[0]["content"]
    assert "已归档" not in out[0]["content"]
    assert len(out) == 1


def test_prepare_default_truncates(monkeypatch):
    """默认 (env 不设) 也走 truncate (跟 enabled 同 — 6/7 后没区别了)."""
    monkeypatch.delenv("CATFISH_GATEWAY_TOOL_ARCHIVE_ENABLE", raising=False)
    monkeypatch.setenv("CATFISH_TOOL_ARCHIVE_ENABLED", "1")
    from catfish_gateway.tool_archive import archiver
    out = archiver.prepare_tool_messages(_one(_BIG), user_email="t@x.com")
    assert "已截断" in out[0]["content"]
    assert "已归档" not in out[0]["content"]


def test_prepare_env_rollback_blocked(monkeypatch):
    """6/7 BL-CATFISH-MANIFESTO: 即使设了 CATFISH_GATEWAY_TOOL_ARCHIVE_ENABLE=1
    回滚 env, 也不能恢复 PG archive 路径 (彻底删了, 不是开关). 防 ops 手抖回滚."""
    monkeypatch.setenv("CATFISH_GATEWAY_TOOL_ARCHIVE_ENABLE", "true")  # 老 ops 习惯写 true
    from catfish_gateway.tool_archive import archiver
    out = archiver.prepare_tool_messages(_one(_BIG), user_email="t@x.com")
    assert "已截断" in out[0]["content"]
    assert "已归档" not in out[0]["content"]


def test_阈值以下不截断也不归档():
    """阈值以下原样通过 —— 这正是 6/12 提高阈值的目的.

    单独钉一条, 是因为上面三个只证明"大的会被截", 证明不了"小的不会被误伤"。
    而 xlsx 多轮修改那个场景 (P3.3.30 的动因) 依赖的恰恰是这一条: 中等大小的
    工具输出必须完整传给 LLM, 截了它就开始编造中间状态。
    """
    from catfish_gateway.tool_archive import archiver
    out = archiver.prepare_tool_messages(_one(_SMALL), user_email="t@x.com")
    assert out[0]["content"] == _SMALL, "阈值以下的内容不该被动过"
    assert "已归档" not in out[0]["content"]


def test_截断后的尺寸跟常量一致():
    """防"改了常量但截断逻辑里用的是别处写死的数"这类不一致."""
    from catfish_gateway.tool_archive import archiver
    out = archiver.prepare_tool_messages(_one(_BIG), user_email="t@x.com")
    n = len(out[0]["content"].encode("utf-8"))
    # 截断标记本身约 60 字节, 给 500 的宽容度
    assert n <= MAX_BYTES_PER_TOOL + 500, (
        f"截断后 {n} 字节, 比 MAX_BYTES_PER_TOOL={MAX_BYTES_PER_TOOL} 超出太多 —— "
        "截断逻辑用的可能不是这个常量"
    )


def test_幂等_截两次结果一致():
    """MIN_BYTES_TO_TRUNCATE 特意比 MAX 多留 200 字节 slack 就是为了这个
    (见常量注释): 截断标记本身不该把内容推过阈值再切一刀."""
    from catfish_gateway.tool_archive import archiver
    once = archiver.prepare_tool_messages(_one(_BIG), user_email="t@x.com")
    twice = archiver.prepare_tool_messages(once, user_email="t@x.com")
    assert once[0]["content"] == twice[0]["content"], "截两次结果不一致, 不幂等"


@pytest.mark.parametrize("role", ["user", "assistant", "system"])
def test_只动_tool_角色的消息(role):
    """非 tool 角色的长内容不该被截 —— 那是员工真正的输入和模型的回答."""
    from catfish_gateway.tool_archive import archiver
    msgs = [{"role": role, "content": _BIG}]
    out = archiver.prepare_tool_messages(msgs, user_email="t@x.com")
    assert out[0]["content"] == _BIG, f"role={role} 的内容被动了"
