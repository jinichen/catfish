"""tool_archive — 6/7 BL-MANIFESTO-CLEAN-DEAD 后只剩 prepare_tool_messages.

老 archive_tool_messages / db / prompts / reader / features / summary_worker /
router 整套 5/22 已经 edge 端 (tool-bridge tool_archive_local.py) 接管, 6/7 整套
rm. 这 file 只测剩下的 prepare_tool_messages wrapper (永远走 truncate).

老 test 见 git history commit BL-MANIFESTO-CLEAN-DEAD 之前.
"""
from __future__ import annotations


def test_prepare_always_truncates_never_archives(monkeypatch):
    """6/7 BL-CATFISH-MANIFESTO: prepare_tool_messages **永远 truncate**, 永不写 PG.

    跟 manifesto 公理 4 "API surface 物理无能" 一致 — 中央服务物理上不存对话内容
    archive. 这条 test 保 hard guarantee 不被回滚."""
    monkeypatch.setenv("CATFISH_GATEWAY_TOOL_ARCHIVE_ENABLE", "1")  # 旧 env 设了也没用
    monkeypatch.setenv("CATFISH_TOOL_ARCHIVE_ENABLED", "1")
    from catfish_gateway.tool_archive import archiver
    msgs = [{"role": "tool", "tool_call_id": "a", "content": "x" * 10000}]
    out = archiver.prepare_tool_messages(msgs, user_email="t@x.com")
    assert "已截断" in out[0]["content"]
    assert "已归档" not in out[0]["content"]
    assert len(out) == 1


def test_prepare_default_truncates(monkeypatch):
    """默认 (env 不设) 也走 truncate (跟 enabled 同 — 6/7 后没区别了)."""
    monkeypatch.delenv("CATFISH_GATEWAY_TOOL_ARCHIVE_ENABLE", raising=False)
    monkeypatch.setenv("CATFISH_TOOL_ARCHIVE_ENABLED", "1")
    from catfish_gateway.tool_archive import archiver
    msgs = [{"role": "tool", "tool_call_id": "a", "content": "x" * 10000}]
    out = archiver.prepare_tool_messages(msgs, user_email="t@x.com")
    assert "已截断" in out[0]["content"]
    assert "已归档" not in out[0]["content"]


def test_prepare_env_rollback_blocked(monkeypatch):
    """6/7 BL-CATFISH-MANIFESTO: 即使设了 CATFISH_GATEWAY_TOOL_ARCHIVE_ENABLE=1
    回滚 env, 也不能恢复 PG archive 路径 (彻底删了, 不是开关). 防 ops 手抖回滚."""
    monkeypatch.setenv("CATFISH_GATEWAY_TOOL_ARCHIVE_ENABLE", "true")  # 老 ops 习惯写 true
    from catfish_gateway.tool_archive import archiver
    msgs = [{"role": "tool", "tool_call_id": "a", "content": "x" * 10000}]
    out = archiver.prepare_tool_messages(msgs, user_email="t@x.com")
    assert "已截断" in out[0]["content"]
    assert "已归档" not in out[0]["content"]
