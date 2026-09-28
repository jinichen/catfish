"""存好的草稿记一笔 —— Companion 对话结束后据此跳到草稿箱 (9/29)。

鸿波: "Windows 版落草稿箱后依旧没有跳转到草稿箱"。原来 Companion 靠认工具名 +
回合结束再列一次草稿箱按时间猜, 小鲶从终端里跑 `catfish-email draft` 时根本
认不出来。现在谁存的都记在这里 (catfish_email/last_draft.py)。
"""
from __future__ import annotations

import json
import time

import pytest

from catfish_email import last_draft
from catfish_email.__main__ import _cmd_draft
from catfish_email.adapters.base import EmailAdapterError

from .test_cli_main import _DraftingAdapter, _make_draft_args


@pytest.fixture
def catfish_home(tmp_path, monkeypatch):
    home = tmp_path / "catfish-home"
    monkeypatch.setenv("CATFISH_HOME", str(home))
    return home


def test_cmd_draft_records_the_saved_draft_for_companion(capsys, catfish_home):
    before = time.time()
    assert _cmd_draft([_DraftingAdapter(name="imap")], _make_draft_args()) == 0
    record = json.loads((catfish_home / "email-last-draft.json").read_text(encoding="utf-8"))
    assert record["id"] == "imap|drafts|new-1"
    assert record["adapter"] == "imap"
    assert before <= record["created_at"] <= time.time()


def test_cmd_draft_failure_leaves_no_record(capsys, catfish_home):
    class _Broken(_DraftingAdapter):
        def create_draft(self, **kw):  # type: ignore[override]
            raise EmailAdapterError("服务器拒绝")

    assert _cmd_draft([_Broken(name="imap")], _make_draft_args()) == 1
    assert not (catfish_home / "email-last-draft.json").exists(), "没存成的草稿不能让 Companion 去跳"


def test_record_failure_does_not_fail_the_draft(tmp_path):
    """草稿已经存好了 —— 记不下来只能少一次跳转, 不能反过来报存草稿失败。"""
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    last_draft.record("imap|d|1", "imap", path=blocker / "email-last-draft.json")  # 不抛
    assert not list(tmp_path.glob(".*.tmp")), "失败时临时文件要清掉"
