"""session_facts 单测.

覆盖:
  - read_session_facts: 文件不存在 / 损坏 / 正常各种情况
  - render_facts_block: 空 / 含特殊字符
  - inject_session_facts: 没 system / 有 system / facts 空 / multimodal system
"""
from __future__ import annotations

import json

import pytest

from catfish_gateway import session_facts


# ============================================================
# read_session_facts
# ============================================================


def test_read_no_file_returns_empty(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    assert session_facts.read_session_facts() == {}


def test_read_normal_file(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    fp = tmp_path / ".catfish" / "session_facts.json"
    fp.parent.mkdir(parents=True)
    fp.write_text(json.dumps({"eis_url": "http://eis.ffcs.cn"}, ensure_ascii=False))

    facts = session_facts.read_session_facts()
    assert facts == {"eis_url": "http://eis.ffcs.cn"}


def test_read_corrupt_json(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    fp = tmp_path / ".catfish" / "session_facts.json"
    fp.parent.mkdir(parents=True)
    fp.write_text("{not valid json")

    # 不抛, 返回空 dict
    assert session_facts.read_session_facts() == {}


def test_read_non_dict_root(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """文件被乱写成 list / string → 当空"""
    monkeypatch.setenv("HOME", str(tmp_path))
    fp = tmp_path / ".catfish" / "session_facts.json"
    fp.parent.mkdir(parents=True)
    fp.write_text(json.dumps(["a", "b"]))

    assert session_facts.read_session_facts() == {}


def test_read_filters_non_string(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """value 不是 str → 跳过 (例如有人写了 int / null)"""
    monkeypatch.setenv("HOME", str(tmp_path))
    fp = tmp_path / ".catfish" / "session_facts.json"
    fp.parent.mkdir(parents=True)
    fp.write_text(json.dumps({"good": "ok", "bad": 42, "null_v": None}))

    facts = session_facts.read_session_facts()
    assert facts == {"good": "ok"}


# ============================================================
# render_facts_block
# ============================================================


class TestRenderFactsBlock:
    def test_empty_facts(self) -> None:
        assert session_facts.render_facts_block({}) == ""

    def test_normal(self) -> None:
        text = session_facts.render_facts_block({
            "eis_url": "http://eis.ffcs.cn",
            "pwd_ref": "keychain://eis_password",
        })
        assert "eis_url" in text
        assert "http://eis.ffcs.cn" in text
        assert "pwd_ref" in text
        assert "硬事实" in text  # 有标题语境

    def test_value_with_newlines_replaced(self) -> None:
        text = session_facts.render_facts_block({"x": "line1\nline2"})
        # value 里换行被替换成空格防破坏 prompt 结构
        assert "\nline2" not in text
        assert "line1 line2" in text

    def test_value_with_backticks_replaced(self) -> None:
        text = session_facts.render_facts_block({"x": "use `code`"})
        # 反引号被替换成单引号
        assert "`code`" not in text
        assert "'code'" in text


# ============================================================
# inject_session_facts (主入口)
# ============================================================


class TestInjectSessionFacts:
    def _setup_facts(self, tmp_path, monkeypatch, facts: dict) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        fp = tmp_path / ".catfish" / "session_facts.json"
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(json.dumps(facts, ensure_ascii=False))

    def test_no_facts_unchanged(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        msgs = [
            {"role": "system", "content": "原 SOUL"},
            {"role": "user", "content": "hi"},
        ]
        result = session_facts.inject_session_facts(msgs)
        assert result == msgs

    def test_facts_appended_to_system(self, tmp_path, monkeypatch) -> None:
        self._setup_facts(tmp_path, monkeypatch, {"eis_url": "http://eis.ffcs.cn"})
        msgs = [
            {"role": "system", "content": "原 SOUL"},
            {"role": "user", "content": "登录 EIS"},
        ]
        result = session_facts.inject_session_facts(msgs)
        # system message content 被增强
        sys_content = result[0]["content"]
        assert "原 SOUL" in sys_content
        assert "eis_url" in sys_content
        assert "http://eis.ffcs.cn" in sys_content
        # user message 没被动
        assert result[1] == msgs[1]

    def test_no_system_no_inject(self, tmp_path, monkeypatch) -> None:
        """没 system message 时不强加 (跟 inject_identity 行为一致)"""
        self._setup_facts(tmp_path, monkeypatch, {"eis_url": "http://eis.ffcs.cn"})
        msgs = [{"role": "user", "content": "hi"}]
        result = session_facts.inject_session_facts(msgs)
        # 不动
        assert result == msgs

    def test_multiple_systems_appends_to_last(self, tmp_path, monkeypatch) -> None:
        """多个 system message — 加到最后一条 (离 user 最近)"""
        self._setup_facts(tmp_path, monkeypatch, {"key1": "val1"})
        msgs = [
            {"role": "system", "content": "first"},
            {"role": "system", "content": "second"},
            {"role": "user", "content": "hi"},
        ]
        result = session_facts.inject_session_facts(msgs)
        assert result[0]["content"] == "first"  # 不动
        assert "key1" in result[1]["content"]   # 第二个 system 被增强
        assert "val1" in result[1]["content"]

    def test_multimodal_system_appends_text_part(self, tmp_path, monkeypatch) -> None:
        """system content 是 list (multimodal) — 追加 text part"""
        self._setup_facts(tmp_path, monkeypatch, {"key1": "val1"})
        msgs = [
            {
                "role": "system",
                "content": [{"type": "text", "text": "原 SOUL"}],
            },
            {"role": "user", "content": "hi"},
        ]
        result = session_facts.inject_session_facts(msgs)
        # content 还是 list, 多了一个 text part
        sys_content = result[0]["content"]
        assert isinstance(sys_content, list)
        assert len(sys_content) == 2
        assert "key1" in sys_content[1]["text"]

    def test_does_not_mutate_input(self, tmp_path, monkeypatch) -> None:
        """inject 应该复制, 不 in-place 改原 messages 数组"""
        self._setup_facts(tmp_path, monkeypatch, {"key1": "val1"})
        original_msgs = [
            {"role": "system", "content": "original"},
            {"role": "user", "content": "hi"},
        ]
        msgs_snapshot_before = [dict(m) for m in original_msgs]

        session_facts.inject_session_facts(original_msgs)

        assert [dict(m) for m in original_msgs] == msgs_snapshot_before
        # 即使 msgs[0] 内部也没被动
        assert original_msgs[0]["content"] == "original"
