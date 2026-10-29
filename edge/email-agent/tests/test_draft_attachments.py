"""10/29: 草稿带附件 —— IMAP 拼 multipart、Apple Mail 传附件清单、CLI 校验和「改草稿保留旧附件」。

鸿波「新建缺少增加附件的能力」: 新建邮件只能把文件路径贴进正文。
"""
from __future__ import annotations

import email
import email.policy
import re
from types import SimpleNamespace
from unittest.mock import patch

from catfish_email import cli_action
from catfish_email.adapters import apple_mail as am
from catfish_email.adapters.apple_mail import FS, AppleMailAdapter

from tests.test_imap_writes import adapter, first  # noqa: F401  (pytest fixture)


# ── IMAP ────────────────────────────────────────────────────


def test_imap_draft_carries_attachments_with_chinese_filename(adapter, tmp_path):  # noqa: F811
    doc = tmp_path / "第二批订单结算单-已修正.docx"
    doc.write_bytes(b"PK\x03\x04 fake docx")
    draft_id = adapter.create_draft(to=["a@b.cn"], subject="结算单", body="请看附件", attachments=[str(doc)])
    raw = adapter._fetch_raw(draft_id)
    parsed = email.message_from_bytes(raw.as_bytes(), policy=email.policy.default)
    assert parsed.is_multipart()
    parts = list(parsed.iter_attachments())
    assert [p.get_filename() for p in parts] == ["第二批订单结算单-已修正.docx"]
    assert parts[0].get_content() == b"PK\x03\x04 fake docx"
    assert "请看附件" in parsed.get_body(("plain",)).get_content()


def test_imap_draft_without_attachments_stays_plain_text(adapter):  # noqa: F811
    draft_id = adapter.create_draft(to=["a@b.cn"], subject="s", body="正文")
    raw = adapter._fetch_raw(draft_id)
    assert not raw.is_multipart()


# ── CLI ─────────────────────────────────────────────────────


class _FakeDraftAdapter:
    name = "imap"
    supports_drafts = True

    def __init__(self, export_dir=None):
        self.calls: list[dict] = []
        self.export_dir = export_dir

    def create_draft(self, *, to, subject, body, cc=(), bcc=(), in_reply_to=None, account=None,
                     replaces=None, attachments=()):
        self.calls.append({"attachments": list(attachments), "replaces": replaces})
        return "imap|Drafts|1|9"

    def export_attachment(self, message_id, filename):
        p = self.export_dir / filename
        p.write_bytes(b"old attachment")
        return p


class _NoAttachmentAdapter(_FakeDraftAdapter):
    def create_draft(self, *, to, subject, body, cc=(), bcc=(), in_reply_to=None, account=None):  # noqa: D401
        raise AssertionError("不该被调到")


def _args(**kw):
    base = dict(to="a@b.cn", cc="", bcc="", subject="s", body="b", body_file=None, in_reply_to=None,
                replace=None, account=None, json=True, attach=[], keep_attachment=[])
    base.update(kw)
    return SimpleNamespace(**base)


def _run(adapter, args):
    with patch("catfish_email.last_draft.record"):
        return cli_action._cmd_draft([adapter], args)


def test_cli_passes_absolute_attachment_paths(tmp_path):
    f = tmp_path / "报价.pdf"
    f.write_bytes(b"%PDF")
    a = _FakeDraftAdapter()
    assert _run(a, _args(attach=[str(f)])) == 0
    assert a.calls[0]["attachments"] == [str(f)]


def test_cli_rejects_missing_file_and_saves_nothing(tmp_path, capsys):
    a = _FakeDraftAdapter()
    assert _run(a, _args(attach=[str(tmp_path / "不存在.docx")])) == 1
    assert a.calls == []
    assert "附件不存在" in capsys.readouterr().err


def test_cli_rejects_oversized_attachments(tmp_path, capsys):
    f = tmp_path / "big.zip"
    f.write_bytes(b"0")
    a = _FakeDraftAdapter()
    with patch.object(cli_action, "MAX_ATTACHMENTS_BYTES", 0):
        assert _run(a, _args(attach=[str(f)])) == 1
    assert a.calls == [] and "超过" in capsys.readouterr().err


def test_cli_says_so_when_the_source_cannot_attach(tmp_path, capsys):
    f = tmp_path / "x.txt"
    f.write_text("x")
    assert _run(_NoAttachmentAdapter(), _args(attach=[str(f)])) == 1
    assert "不支持带附件" in capsys.readouterr().err


def test_editing_a_draft_keeps_its_old_attachments(tmp_path):
    new = tmp_path / "新附件.xlsx"
    new.write_bytes(b"x")
    a = _FakeDraftAdapter(export_dir=tmp_path)
    args = _args(replace="imap|Drafts|1|5", attach=[str(new)], keep_attachment=["原来的.docx"])
    assert _run(a, args) == 0
    got = a.calls[0]["attachments"]
    assert got[0] == str(new) and got[1].endswith("原来的.docx")
    assert a.calls[0]["replaces"] == "imap|Drafts|1|5"


def test_keep_attachment_without_replace_is_refused(capsys):
    a = _FakeDraftAdapter()
    assert _run(a, _args(keep_attachment=["a.docx"])) == 1
    assert a.calls == [] and "--replace" in capsys.readouterr().err


# ── Apple Mail ──────────────────────────────────────────────


def test_apple_mail_gets_the_attachment_list_through_a_file(tmp_path):
    f = tmp_path / 'a "quoted" 名字.pdf'
    f.write_bytes(b"%PDF")
    seen: dict = {}

    def fake_run(script, **_):
        if "make new outgoing message" in script:
            m = re.search(r'set attPath to "((?:[^"\\]|\\.)*)"', script)
            assert m, "脚本里要有附件清单路径"
            with open(m.group(1), encoding="utf-8") as fh:
                seen["list"] = fh.read()
            seen["script"] = script
            return f"Chinatelecom{FS}2801{FS}4"
        return "OK"

    with (
        patch.object(am, "_is_mail_running", return_value=True),
        patch.object(am, "_run_osascript", side_effect=fake_run),
    ):
        AppleMailAdapter().create_draft(to=["a@x.cn"], subject="s", body="b", attachments=[str(f)])
    assert seen["list"] == str(f), "路径原样一行一个, 不经 AS 字符串转义"
    assert "make new attachment" in seen["script"]
    assert not re.search(r"\{[A-Z_]+\}", seen["script"]), "占位符没替换完"
