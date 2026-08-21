"""catfish_email_create_draft —— 只建草稿, 绝不发送 (8/21)。

# 这个工具守的边界

「对话里生成初稿并确认发送」的正确形态: 模型最多把定稿放进**草稿箱**,
发送动作永远是员工在客户端里点。红线 CATFISH-ADVISOR-DESIGN.md:55
「任何级别都不代行: 不替发邮件」—— 落地方式是能力边界而不是 prompt 约定:
tool-bridge 根本没有发送工具。本文件第一条测试就钉这个。

# 正文为什么必须走 --body-file

正文是任意文本 (几 KB、含引号/换行), argv 有转义坑; 更要紧的是 **argv 对
本机所有进程可见** (ps 就能看) —— 正文可能含业务敏感内容。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from catfish_tool_bridge import email_draft_to_client as edc  # noqa: E402


# ─────────────────────────────────────────────────────────────────────
# 0. 红线: 模型工具面里不许出现"发送"
# ─────────────────────────────────────────────────────────────────────
def test_没有任何发送工具暴露给模型():
    """CLI 有 send 子命令, 但它**不许**进任何 schema。

    这条红了 = 有人给模型加了发邮件的能力 —— 那不是 bug 是事故,
    先回滚再讨论。
    """
    import glob

    schema_src = "".join(
        Path(f).read_text(encoding="utf-8", errors="replace")
        for f in glob.glob(str(SRC / "catfish_tool_bridge" / "catfish_tool_schemas*.py"))
    )
    names = set(re.findall(r'"name"\s*:\s*"(catfish_[a-z_]+)"', schema_src))
    senders = {n for n in names if "send" in n}
    assert not senders, (
        f"schema 里出现了发送类工具: {senders} —— "
        "红线「任何级别都不代行: 不替发邮件」是能力边界, 不是 prompt 约定。"
    )
    assert "catfish_email_create_draft" in names, "建草稿工具没注册进 schema"


# ─────────────────────────────────────────────────────────────────────
# 脚手架: 假 CLI
# ─────────────────────────────────────────────────────────────────────
@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    """把 _find_catfish_email 指到一个记录 argv + body 文件内容的假脚本。"""
    record = tmp_path / "record.json"
    script = tmp_path / "catfish-email"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "argv = sys.argv[1:]\n"
        "body = ''\n"
        "if '--body-file' in argv:\n"
        "    body = open(argv[argv.index('--body-file')+1], encoding='utf-8').read()\n"
        f"json.dump({{'argv': argv, 'body': body}}, open({str(record)!r}, 'w'))\n"
        "print(json.dumps({'id': 'draft-123'}))\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setattr(edc, "_find_catfish_email", lambda: str(script))
    return record


def _recorded(record: Path) -> dict:
    return json.loads(record.read_text(encoding="utf-8"))


# ─────────────────────────────────────────────────────────────────────
# 1. 正常路径
# ─────────────────────────────────────────────────────────────────────
def test_建草稿_参数透传_返回draft_id(fake_cli):
    r = edc.tool_email_create_draft({
        "to": "a@x.com,b@x.com",
        "subject": "Re: 摸底",
        "body": "收到, 9/10 前提交。",
        "cc": "c@x.com",
        "in_reply_to": "apple_mail|Chinatelecom|2057",
        "account": "work@x.com",
    })
    assert r["ok"] is True
    assert r["draft_id"] == "draft-123"

    rec = _recorded(fake_cli)
    argv = rec["argv"]
    assert argv[0] == "draft", "调的不是 draft 子命令"
    assert "--to" in argv and argv[argv.index("--to") + 1] == "a@x.com,b@x.com"
    assert "--in-reply-to" in argv, "回复场景 in_reply_to 没透传 — thread 会断"
    assert "--account" in argv and "--cc" in argv


def test_summary把发送在你说死(fake_cli):
    """summary 是模型转述给员工的底稿 —— 必须说清「没有发送」。

    不说清的话, 员工听到「草稿已建」很容易理解成「已经发了」——
    那这个工具就从守红线变成帮着糊弄红线了。
    """
    r = edc.tool_email_create_draft({"to": "a@x.com", "subject": "s", "body": "b"})
    assert "没有发送" in r["summary"]
    assert "自己点发送" in r["summary"]


# ─────────────────────────────────────────────────────────────────────
# 2. 正文走文件不走 argv
# ─────────────────────────────────────────────────────────────────────
def test_正文走body_file不进argv(fake_cli):
    tricky = '第一行\n"引号" $(危险) `反引号` 很长的正文' + "x" * 2000
    r = edc.tool_email_create_draft({"to": "a@x.com", "subject": "s", "body": tricky})
    assert r["ok"] is True

    rec = _recorded(fake_cli)
    assert rec["body"] == tricky, "CLI 从 body-file 读到的正文对不上"
    assert "--body-file" in rec["argv"]
    # 正文内容不许出现在 argv 里 (ps 可见面)
    assert not any(tricky[:20] in a for a in rec["argv"]), "正文泄进了 argv"


def test_临时文件用完即删(fake_cli, tmp_path):
    edc.tool_email_create_draft({"to": "a@x.com", "subject": "s", "body": "b"})
    rec = _recorded(fake_cli)
    body_file = rec["argv"][rec["argv"].index("--body-file") + 1]
    assert not Path(body_file).exists(), f"正文临时文件残留: {body_file}"


# ─────────────────────────────────────────────────────────────────────
# 3. 拒绝空壳
# ─────────────────────────────────────────────────────────────────────
def test_缺必填直接拒绝不落草稿(fake_cli):
    for bad in (
        {"subject": "s", "body": "b"},
        {"to": "a@x.com", "body": "b"},
        {"to": "a@x.com", "subject": "s"},
        {"to": "a@x.com", "subject": "s", "body": "   "},
    ):
        r = edc.tool_email_create_draft(bad)
        assert r["ok"] is False, f"空壳参数被放行了: {bad}"
    assert not fake_cli.exists(), "拒绝路径不该碰 CLI"


def test_CLI失败如实报错(fake_cli, monkeypatch, tmp_path):
    bad = tmp_path / "bad-cli"
    bad.write_text("#!/bin/sh\necho boom >&2\nexit 3\n", encoding="utf-8")
    bad.chmod(0o755)
    monkeypatch.setattr(edc, "_find_catfish_email", lambda: str(bad))
    r = edc.tool_email_create_draft({"to": "a@x.com", "subject": "s", "body": "b"})
    assert r["ok"] is False
    assert "3" in r["error"] and "boom" in r["error"]
