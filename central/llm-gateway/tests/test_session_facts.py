"""session_facts 单测.

覆盖:
  - read_session_facts: 文件不存在 / 损坏 / 正常各种情况 / v2 schema 兼容
  - render_facts_block: 空 / 含特殊字符 / 多 revision
  - inject_session_facts: 没 system / 有 system / facts 空 / multimodal system
"""
from __future__ import annotations

import json

import pytest

from catfish_gateway import session_facts


# v2 schema helper: 包成单 revision list (跟旧 dict[str,str] 等价的形态)
def _wrap(value: str, prev_value: str | None = None, ts: float = 0.0) -> list[dict]:
    return [{"value": value, "ts": ts, "prev_value": prev_value}]


# ============================================================
# read_session_facts
# ============================================================


def test_read_no_file_returns_empty(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    assert session_facts.read_session_facts() == {}


def test_read_legacy_schema_string_value(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """BL-MM2 兼容: 旧文件 {key: "string"} → 包成单 revision."""
    monkeypatch.setenv("HOME", str(tmp_path))
    fp = tmp_path / ".catfish" / "session_facts.json"
    fp.parent.mkdir(parents=True)
    fp.write_text(json.dumps({"eis_url": "http://eis.ffcs.cn"}, ensure_ascii=False))

    facts = session_facts.read_session_facts()
    assert facts == {
        "eis_url": [{"value": "http://eis.ffcs.cn", "ts": 0.0, "prev_value": None}]
    }


def test_read_v2_schema_revision_list(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """BL-MM2: v2 schema 直接读 revision list."""
    monkeypatch.setenv("HOME", str(tmp_path))
    fp = tmp_path / ".catfish" / "session_facts.json"
    fp.parent.mkdir(parents=True)
    fp.write_text(json.dumps({
        "eis_url": [
            {"value": "https://old.eis", "ts": 100.0, "prev_value": None},
            {"value": "http://eis.ffcs.cn", "ts": 200.0, "prev_value": "https://old.eis"},
        ],
    }, ensure_ascii=False))

    facts = session_facts.read_session_facts()
    assert "eis_url" in facts
    revs = facts["eis_url"]
    assert len(revs) == 2
    assert revs[-1]["value"] == "http://eis.ffcs.cn"
    assert revs[-1]["prev_value"] == "https://old.eis"


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
    """value 不是 str / list → 跳过 (例如有人写了 int / null)"""
    monkeypatch.setenv("HOME", str(tmp_path))
    fp = tmp_path / ".catfish" / "session_facts.json"
    fp.parent.mkdir(parents=True)
    fp.write_text(json.dumps({"good": "ok", "bad": 42, "null_v": None}))

    facts = session_facts.read_session_facts()
    assert facts == {"good": _wrap("ok")}


def test_read_filters_bad_revisions_in_list(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """v2 list 里有非 dict 项 → 跳过, 保留合法的."""
    monkeypatch.setenv("HOME", str(tmp_path))
    fp = tmp_path / ".catfish" / "session_facts.json"
    fp.parent.mkdir(parents=True)
    fp.write_text(json.dumps({
        "k": [
            "not a dict",
            {"value": "good", "ts": 1.0, "prev_value": None},
            {"no_value": "missing field"},
            42,
        ],
    }))

    facts = session_facts.read_session_facts()
    assert "k" in facts
    assert len(facts["k"]) == 1
    assert facts["k"][0]["value"] == "good"


# ============================================================
# render_facts_block
# ============================================================


class TestRenderFactsBlock:
    def test_empty_facts(self) -> None:
        assert session_facts.render_facts_block({}) == ""

    def test_normal_single_revision(self) -> None:
        text = session_facts.render_facts_block({
            "eis_url": _wrap("http://eis.ffcs.cn"),
            "pwd_ref": _wrap("keychain://eis_password"),
        })
        assert "eis_url" in text
        assert "http://eis.ffcs.cn" in text
        assert "pwd_ref" in text
        assert "硬事实" in text  # 有标题语境
        # 单 revision 不应该出现"已更新 N 次"
        assert "已更新" not in text

    def test_multi_revision_shows_prev_value(self) -> None:
        """BL-MM2: 多 revision 时显式给模型看上次值, 配合 SOUL BL-MM1 quote 旧值纪律."""
        text = session_facts.render_facts_block({
            "eis_url": [
                {"value": "https://old.eis", "ts": 100.0, "prev_value": None},
                {"value": "http://eis.ffcs.cn", "ts": 200.0, "prev_value": "https://old.eis"},
            ],
        })
        # 当前值在
        assert "http://eis.ffcs.cn" in text
        # 提示已更新 2 次
        assert "已更新 2 次" in text
        # 上次值显式列出 (BL-MM1)
        assert "https://old.eis" in text
        assert "上次值" in text

    def test_value_with_newlines_replaced(self) -> None:
        text = session_facts.render_facts_block({"x": _wrap("line1\nline2")})
        # value 里换行被替换成空格防破坏 prompt 结构
        assert "\nline2" not in text
        assert "line1 line2" in text

    def test_value_with_backticks_replaced(self) -> None:
        text = session_facts.render_facts_block({"x": _wrap("use `code`")})
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
