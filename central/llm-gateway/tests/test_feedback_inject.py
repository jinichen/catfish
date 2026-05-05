"""BL-MM6 feedback_inject 单测.

覆盖:
  - read_recent_negative: 文件不存在 / 损坏 / 旧 ts 过滤 / 只 negative
  - render_feedback_block: 空 / 含特殊字符 / 含/不含 comment
  - inject_feedback: 没 system / 有 system / multimodal system / 空 feedback / 不动原 messages
"""
from __future__ import annotations

import json
import time

import pytest

from catfish_gateway import feedback_inject


def _setup_jsonl(tmp_path, monkeypatch, events):
    """写一组 events 到 ~/.catfish/feedback.jsonl"""
    monkeypatch.setenv("HOME", str(tmp_path))
    fp = tmp_path / ".catfish" / "feedback.jsonl"
    fp.parent.mkdir(parents=True, exist_ok=True)
    with fp.open("w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


# ============================================================
# read_recent_negative
# ============================================================


def test_read_no_file(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert feedback_inject.read_recent_negative() == []


def test_read_only_negative(tmp_path, monkeypatch):
    """thumb_up 不算 negative, 应过滤掉."""
    now = time.time()
    _setup_jsonl(tmp_path, monkeypatch, [
        {"ts": now - 100, "kind": "thumb_up", "session_id": "s1",
         "message_id": "m1", "preview": "好答案", "comment": None},
        {"ts": now - 50, "kind": "thumb_down", "session_id": "s1",
         "message_id": "m2", "preview": "啰嗦", "comment": "太啰嗦了"},
        {"ts": now - 30, "kind": "edit", "session_id": "s1",
         "message_id": "m3", "preview": "格式不对", "comment": "改成 markdown"},
    ])
    items = feedback_inject.read_recent_negative()
    kinds = [i["kind"] for i in items]
    assert "thumb_up" not in kinds
    assert "thumb_down" in kinds
    assert "edit" in kinds
    assert len(items) == 2


def test_read_filters_old(tmp_path, monkeypatch):
    """超过 7 天的 negative 不算 (BL-MM6 默认 7 天 cutoff)."""
    now = time.time()
    too_old = now - 86400 * 30  # 30 天前
    _setup_jsonl(tmp_path, monkeypatch, [
        {"ts": too_old, "kind": "thumb_down", "session_id": "s1",
         "message_id": "m1", "preview": "old", "comment": "old comment"},
        {"ts": now - 100, "kind": "thumb_down", "session_id": "s1",
         "message_id": "m2", "preview": "new", "comment": "new comment"},
    ])
    items = feedback_inject.read_recent_negative()
    assert len(items) == 1
    assert items[0]["preview"] == "new"


def test_read_orders_desc(tmp_path, monkeypatch):
    """最近的在前."""
    now = time.time()
    _setup_jsonl(tmp_path, monkeypatch, [
        {"ts": now - 200, "kind": "thumb_down", "session_id": "s1",
         "message_id": "m1", "preview": "older", "comment": "a"},
        {"ts": now - 100, "kind": "thumb_down", "session_id": "s1",
         "message_id": "m2", "preview": "middle", "comment": "b"},
        {"ts": now - 10, "kind": "thumb_down", "session_id": "s1",
         "message_id": "m3", "preview": "newest", "comment": "c"},
    ])
    items = feedback_inject.read_recent_negative()
    assert [i["preview"] for i in items] == ["newest", "middle", "older"]


def test_read_corrupt_lines(tmp_path, monkeypatch):
    """JSON 损坏的行跳过, 合法的留."""
    now = time.time()
    fp = tmp_path / ".catfish" / "feedback.jsonl"
    fp.parent.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    fp.write_text("\n".join([
        "{not valid json",
        json.dumps({"ts": now - 100, "kind": "thumb_down", "session_id": "s1",
                    "message_id": "m1", "preview": "good", "comment": "x"}),
        "",
        "[]",  # array 不是 dict
    ]), encoding="utf-8")
    items = feedback_inject.read_recent_negative()
    assert len(items) == 1
    assert items[0]["preview"] == "good"


def test_read_caps_at_max_items(tmp_path, monkeypatch):
    """超过 MAX_ITEMS=10 截断."""
    now = time.time()
    events = [
        {"ts": now - i, "kind": "thumb_down", "session_id": "s1",
         "message_id": f"m{i}", "preview": f"p{i}", "comment": f"c{i}"}
        for i in range(20)
    ]
    _setup_jsonl(tmp_path, monkeypatch, events)
    items = feedback_inject.read_recent_negative()
    assert len(items) == 10


# ============================================================
# render_feedback_block
# ============================================================


class TestRenderFeedbackBlock:
    def test_empty(self):
        assert feedback_inject.render_feedback_block([]) == ""

    def test_basic(self):
        text = feedback_inject.render_feedback_block([
            {"ts": time.time() - 100, "kind": "thumb_down",
             "session_id": "s1", "message_id": "m1",
             "preview": "之前的话", "comment": "太啰嗦"},
        ])
        assert "员工最近给你的反馈" in text
        assert "👎" in text
        assert "太啰嗦" in text
        assert "之前的话" in text

    def test_edit_emoji(self):
        text = feedback_inject.render_feedback_block([
            {"ts": time.time() - 100, "kind": "edit",
             "session_id": "s1", "message_id": "m1",
             "preview": "之前", "comment": "改成 X"},
        ])
        assert "✏️" in text
        assert "改成 X" in text

    def test_no_comment(self):
        text = feedback_inject.render_feedback_block([
            {"ts": time.time() - 100, "kind": "thumb_down",
             "session_id": "s1", "message_id": "m1",
             "preview": "之前", "comment": None},
        ])
        assert "(没写理由)" in text

    def test_strips_backticks_newlines(self):
        text = feedback_inject.render_feedback_block([
            {"ts": time.time() - 100, "kind": "thumb_down",
             "session_id": "s1", "message_id": "m1",
             "preview": "p1\nline2", "comment": "use `code` here"},
        ])
        # 反引号被替成单引号, 换行被替成空格
        assert "`code`" not in text
        assert "'code'" in text
        assert "p1\nline2" not in text


# ============================================================
# inject_feedback (主入口)
# ============================================================


class TestInjectFeedback:
    def test_no_feedback_unchanged(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        msgs = [
            {"role": "system", "content": "原 SOUL"},
            {"role": "user", "content": "hi"},
        ]
        result = feedback_inject.inject_feedback(msgs)
        assert result == msgs

    def test_feedback_appended(self, tmp_path, monkeypatch):
        _setup_jsonl(tmp_path, monkeypatch, [
            {"ts": time.time() - 100, "kind": "thumb_down",
             "session_id": "s1", "message_id": "m1",
             "preview": "啰嗦答", "comment": "下次直接给结论"},
        ])
        msgs = [
            {"role": "system", "content": "原 SOUL"},
            {"role": "user", "content": "hi"},
        ]
        result = feedback_inject.inject_feedback(msgs)
        sys_content = result[0]["content"]
        assert "原 SOUL" in sys_content
        assert "下次直接给结论" in sys_content
        # user message 没动
        assert result[1] == msgs[1]

    def test_no_system_no_inject(self, tmp_path, monkeypatch):
        """跟 inject_session_facts 行为一致: 没 system 不强加."""
        _setup_jsonl(tmp_path, monkeypatch, [
            {"ts": time.time() - 100, "kind": "thumb_down",
             "session_id": "s1", "message_id": "m1",
             "preview": "p", "comment": "c"},
        ])
        msgs = [{"role": "user", "content": "hi"}]
        result = feedback_inject.inject_feedback(msgs)
        assert result == msgs

    def test_multimodal_system(self, tmp_path, monkeypatch):
        """system content 是 list 时, 追加 text part."""
        _setup_jsonl(tmp_path, monkeypatch, [
            {"ts": time.time() - 100, "kind": "thumb_down",
             "session_id": "s1", "message_id": "m1",
             "preview": "p", "comment": "c"},
        ])
        msgs = [
            {"role": "system", "content": [{"type": "text", "text": "原"}]},
            {"role": "user", "content": "hi"},
        ]
        result = feedback_inject.inject_feedback(msgs)
        sys_content = result[0]["content"]
        assert isinstance(sys_content, list)
        assert len(sys_content) == 2
        assert "c" in sys_content[1]["text"]

    def test_does_not_mutate_input(self, tmp_path, monkeypatch):
        _setup_jsonl(tmp_path, monkeypatch, [
            {"ts": time.time() - 100, "kind": "thumb_down",
             "session_id": "s1", "message_id": "m1",
             "preview": "p", "comment": "c"},
        ])
        msgs = [
            {"role": "system", "content": "original"},
            {"role": "user", "content": "hi"},
        ]
        snapshot = [dict(m) for m in msgs]
        feedback_inject.inject_feedback(msgs)
        assert [dict(m) for m in msgs] == snapshot
