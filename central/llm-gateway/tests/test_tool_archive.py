"""BL-Q3-ARCHIVE 单测 (5/11).

覆盖:
  - archiver.archive_tool_messages: 阈值边界 / 消息数不变 / ref 幂等
  - prompts.build_replacement_text: 3 种摘要态 (ready / pending / failed)
  - reader.read_archive_content: grep / line_range / full
  - db.upsert + get (jsonl fallback 路径)
  - features 灰度白名单
  - prepare_tool_messages 整合 (enabled / disabled / archive 挂时降级 FIX41)
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_archive_dir(monkeypatch):
    """每个测试用独立 archive dir + 关 PG, 强制走 jsonl."""
    tmp = tempfile.mkdtemp(prefix="test_archive_")
    monkeypatch.setenv("CATFISH_TOOL_ARCHIVE_DIR", tmp)
    monkeypatch.delenv("CATFISH_DB_URL", raising=False)
    monkeypatch.setenv("CATFISH_TOOL_ARCHIVE_ENABLED", "1")
    monkeypatch.delenv("CATFISH_TOOL_ARCHIVE_USERS", raising=False)

    # reload db module to pick up new ARCHIVE_DIR
    import importlib

    from catfish_gateway.tool_archive import db as db_module
    importlib.reload(db_module)
    yield tmp
    shutil.rmtree(tmp, ignore_errors=True)


# ─── archiver ────────────────────────────────────────────────────


def test_archive_threshold_boundary():
    from catfish_gateway.tool_archive import archiver
    # 3999B 不归档, 4001B 归档
    just_under = "x" * 3999
    just_over = "x" * 4001
    msgs = [
        {"role": "tool", "tool_call_id": "a", "content": just_under},
        {"role": "tool", "tool_call_id": "b", "content": just_over},
    ]
    out = archiver.archive_tool_messages(
        msgs, user_email="t@x.com", session_id="t:s1",
    )
    assert out[0]["content"] == just_under
    assert "已归档" in out[1]["content"]


def test_archive_preserves_message_count():
    from catfish_gateway.tool_archive import archiver
    big = "x" * 10000
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": ""},
        {"role": "tool", "tool_call_id": "a", "content": big},
        {"role": "assistant", "content": ""},
        {"role": "tool", "tool_call_id": "b", "content": big},
    ]
    out = archiver.archive_tool_messages(
        msgs, user_email="t@x.com", session_id="t:s1",
    )
    assert len(out) == len(msgs)


def test_archive_ref_idempotent():
    from catfish_gateway.tool_archive import archiver
    r1 = archiver._compute_ref("hello world", "tc_a")
    r2 = archiver._compute_ref("hello world", "tc_a")
    assert r1 == r2
    # 不同 tool_call_id → 不同 ref
    r3 = archiver._compute_ref("hello world", "tc_b")
    assert r3 != r1


def test_archive_no_mutation():
    from catfish_gateway.tool_archive import archiver
    msgs = [{"role": "tool", "tool_call_id": "a", "content": "x" * 10000}]
    original = msgs[0]["content"]
    archiver.archive_tool_messages(msgs, user_email="t@x.com", session_id="t:s1")
    assert msgs[0]["content"] == original  # 原 list 不动


def test_archive_skips_already_archived():
    """跑两次 archive 不会重复打."""
    from catfish_gateway.tool_archive import archiver
    msgs = [{"role": "tool", "tool_call_id": "a", "content": "x" * 10000}]
    out1 = archiver.archive_tool_messages(msgs, user_email="t@x.com", session_id="t:s1")
    out2 = archiver.archive_tool_messages(out1, user_email="t@x.com", session_id="t:s1")
    assert out1[0]["content"] == out2[0]["content"]


def test_archive_skips_non_tool():
    """user / assistant / system 不动."""
    from catfish_gateway.tool_archive import archiver
    big = "x" * 10000
    msgs = [
        {"role": "user", "content": big},
        {"role": "assistant", "content": big},
        {"role": "system", "content": big},
    ]
    out = archiver.archive_tool_messages(msgs, user_email="t@x.com", session_id="t:s1")
    for i, m in enumerate(msgs):
        assert out[i]["content"] == m["content"]


def test_archive_skips_list_content():
    """role=tool 但 content 是 list (BL-FIX2 unwrap 之前) — 跳过."""
    from catfish_gateway.tool_archive import archiver
    msgs = [{
        "role": "tool",
        "tool_call_id": "a",
        "content": [{"type": "image_url", "image_url": {"url": "data:..."}}],
    }]
    out = archiver.archive_tool_messages(msgs, user_email="t@x.com", session_id="t:s1")
    assert out[0]["content"] == msgs[0]["content"]


# ─── prompts ─────────────────────────────────────────────────────


def test_prompts_build_with_summary():
    from catfish_gateway.tool_archive import prompts
    text = prompts.build_replacement_text(
        ref="abc12345", content="x" * 12000, tool_name="execute_code",
        content_bytes=12000, lines=287, summary="pytest 12/13 pass", summary_error=None,
    )
    assert "abc12345" in text
    assert "pytest 12/13 pass" in text
    assert "📂 头部" in text
    assert "📂 尾部" in text
    assert "catfish_read_tool_archive" in text


def test_prompts_build_pending_summary():
    from catfish_gateway.tool_archive import prompts
    text = prompts.build_replacement_text(
        ref="abc12345", content="x" * 12000, tool_name="execute_code",
        content_bytes=12000, lines=287, summary=None, summary_error=None,
    )
    assert "生成中" in text


def test_prompts_build_failed_summary():
    from catfish_gateway.tool_archive import prompts
    text = prompts.build_replacement_text(
        ref="abc12345", content="x" * 12000, tool_name="execute_code",
        content_bytes=12000, lines=287, summary=None, summary_error="429 quota",
    )
    assert "生成失败" in text


def test_prompts_byte_safe_chinese_preview():
    """中文一字符 3 字节, 头尾切可能切坏多字节 — errors='ignore' 兜底."""
    from catfish_gateway.tool_archive import prompts
    text = prompts.build_replacement_text(
        ref="abc12345", content="这是中文" * 1000, tool_name="execute_code",
        content_bytes=12000, lines=1, summary="中文摘要",
    )
    # decode 不应该崩
    assert isinstance(text, str)


def test_prompts_truncate_for_summary():
    """摘要器输入 > 50KB 头 25K + 尾 25K."""
    from catfish_gateway.tool_archive import prompts
    big = "x" * 60_000
    truncated = prompts.truncate_for_summary(big)
    assert "中段省略" in truncated
    assert len(truncated.encode("utf-8")) < 55_000


# ─── reader ──────────────────────────────────────────────────────


def test_reader_grep_with_context():
    from catfish_gateway.tool_archive import reader
    content = "\n".join(f"line {i}: {'error here' if i in (10, 50) else 'ok'}" for i in range(100))
    out = reader.read_archive_content(content=content, grep="error here")
    assert "命中 2 行" in out
    assert "召回 2 段" in out
    assert "line 10" in out
    assert "line 50" in out


def test_reader_grep_not_found():
    from catfish_gateway.tool_archive import reader
    out = reader.read_archive_content(content="hello\nworld", grep="nothing")
    assert "未找到" in out


def test_reader_line_range():
    from catfish_gateway.tool_archive import reader
    content = "\n".join(f"line {i}" for i in range(100))
    out = reader.read_archive_content(content=content, line_range="10-15")
    assert "line 9" in out  # 1-indexed 第 10 行 = "line 9"
    assert "line 14" in out
    assert "line 16" not in out  # range 不该越界


def test_reader_line_range_single():
    from catfish_gateway.tool_archive import reader
    content = "\n".join(f"line {i}" for i in range(100))
    out = reader.read_archive_content(content=content, line_range="50")
    assert "line 49" in out


def test_reader_line_range_out_of_bounds():
    from catfish_gateway.tool_archive import reader
    content = "abc\ndef"
    out = reader.read_archive_content(content=content, line_range="100-200")
    assert "超出范围" in out


def test_reader_full_with_max_bytes():
    from catfish_gateway.tool_archive import reader
    content = "x" * 20_000
    out = reader.read_archive_content(content=content, max_bytes=5000)
    assert "max_bytes=5000" in out
    assert len(out.encode("utf-8")) <= 5500


def test_reader_grep_max_bytes_cap():
    from catfish_gateway.tool_archive import reader
    content = "\n".join(f"error line {i}" for i in range(10_000))
    out = reader.read_archive_content(content=content, grep="error", max_bytes=2000)
    # 截到 max_bytes 上限附近
    assert len(out.encode("utf-8")) <= 2500


# ─── db (jsonl 兜底路径) ──────────────────────────────────────────


def test_db_upsert_and_get_jsonl():
    from catfish_gateway.tool_archive import db
    row = {
        "ref": "testref01234567",
        "session_id": "t_at_x.com_s1",
        "user_email": "t@x.com",
        "tool_call_id": "tc1",
        "tool_name": "execute_code",
        "content": "Hello World\n" * 100,
        "content_bytes": 1200,
        "lines": 100,
    }
    ok, backend = db.upsert_archive(row)
    assert ok
    assert backend == "jsonl"

    got = db.get_archive("testref01234567")
    assert got is not None
    assert got["user_email"] == "t@x.com"


def test_db_get_nonexistent():
    from catfish_gateway.tool_archive import db
    assert db.get_archive("doesnotexist0000") is None


def test_db_pick_unsummarized_and_update():
    from catfish_gateway.tool_archive import db

    row = {
        "ref": "summref000000001",
        "session_id": "t_at_x.com_s1",
        "user_email": "t@x.com",
        "tool_name": "execute_code",
        "content": "log\n" * 100,
        "content_bytes": 400,
        "lines": 100,
    }
    db.upsert_archive(row)
    pending = db.pick_unsummarized(limit=10)
    assert any(r["ref"] == "summref000000001" for r in pending)

    db.update_summary("summref000000001", summary="ok 100 行 log", model="haiku-test")
    after = db.pick_unsummarized(limit=10)
    assert not any(r["ref"] == "summref000000001" for r in after)


# ─── features ────────────────────────────────────────────────────


def test_features_enabled_by_default(monkeypatch):
    monkeypatch.delenv("CATFISH_TOOL_ARCHIVE_ENABLED", raising=False)
    monkeypatch.delenv("CATFISH_TOOL_ARCHIVE_USERS", raising=False)
    from catfish_gateway.tool_archive import features
    assert features.is_archive_enabled("anyone@x.com")


def test_features_user_whitelist(monkeypatch):
    monkeypatch.setenv("CATFISH_TOOL_ARCHIVE_ENABLED", "1")
    monkeypatch.setenv("CATFISH_TOOL_ARCHIVE_USERS", "admin@x.com,vip@x.com")
    from catfish_gateway.tool_archive import features
    assert features.is_archive_enabled("admin@x.com")
    assert features.is_archive_enabled("vip@x.com")
    assert not features.is_archive_enabled("nobody@x.com")
    assert not features.is_archive_enabled(None)


def test_features_global_off(monkeypatch):
    monkeypatch.setenv("CATFISH_TOOL_ARCHIVE_ENABLED", "0")
    from catfish_gateway.tool_archive import features
    assert not features.is_archive_enabled("anyone@x.com")


def test_features_threshold_env(monkeypatch):
    from catfish_gateway.tool_archive import features
    monkeypatch.setenv("CATFISH_TOOL_ARCHIVE_THRESHOLD", "8000")
    assert features.threshold_bytes() == 8000
    monkeypatch.setenv("CATFISH_TOOL_ARCHIVE_THRESHOLD", "garbage")
    assert features.threshold_bytes() == 4000  # 默认


# ─── prepare_tool_messages 整合 ────────────────────────────────


def test_prepare_enabled_archives(monkeypatch):
    monkeypatch.setenv("CATFISH_TOOL_ARCHIVE_ENABLED", "1")
    from catfish_gateway.tool_archive import archiver
    msgs = [{"role": "tool", "tool_call_id": "a", "content": "x" * 10000}]
    out = archiver.prepare_tool_messages(msgs, user_email="t@x.com")
    assert "已归档" in out[0]["content"]
    assert len(out) == 1


def test_prepare_disabled_falls_back_to_fix41(monkeypatch):
    monkeypatch.setenv("CATFISH_TOOL_ARCHIVE_ENABLED", "0")
    from catfish_gateway.tool_archive import archiver
    msgs = [{"role": "tool", "tool_call_id": "a", "content": "x" * 10000}]
    out = archiver.prepare_tool_messages(msgs, user_email="t@x.com")
    # FIX41 标记
    assert "已截断" in out[0]["content"]
    assert "已归档" not in out[0]["content"]


def test_derive_session_id_with_messages():
    from catfish_gateway.tool_archive import archiver
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello world"},
    ]
    sid = archiver.derive_session_id("t@x.com", messages=msgs)
    assert sid.startswith("t@x.com:")
    # 同 user message 派生稳定
    sid2 = archiver.derive_session_id("t@x.com", messages=msgs)
    assert sid == sid2


def test_derive_session_id_with_conversation_id():
    from catfish_gateway.tool_archive import archiver
    sid = archiver.derive_session_id("t@x.com", conversation_id="conv_xyz")
    assert sid == "t@x.com:conv_xyz"


def test_derive_session_id_fallback_to_date():
    from catfish_gateway.tool_archive import archiver
    sid = archiver.derive_session_id("t@x.com")
    assert sid.startswith("t@x.com:20")  # 2026-xx-xx
