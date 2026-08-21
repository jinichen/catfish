"""email_index (8/21 治本) —— 增量对账的正确性。

# 这个索引在治什么

EmailTab 每次挂载 (切 tab 就卸载重挂) 都 spawn `catfish-email list`, 而
Mail.app 在跑时走 AppleScript: 每封 8 个字段 = 8 次 Apple Event, 500 封 ×
4 账号 ≈ 1.6 万次 IPC, Sent 再来一遍。切一次 tab 全量重抽 —— 这就是
「切回邮件页要等很久」的真因。同一个根源还烧过配额 (8/15, 83 分钟 2470 万
token): 一切现抓现算, 没有东西落地。

索引把它变成: readdir+stat 对账 + SQLite 查询, 解析只在文件首次/变更时发生。

# 测法

**解析次数是增量正确性的直接判据** —— reconcile 的 parse 参数是注入的,
测试传一个带计数器的假解析器。断言"跳过了"不看日志看计数: 第二次对账
parse 计数必须是 0, 改一个文件必须恰好是 1。

跟今天修过的几个坑同款教训: 判据要贴着真事, 别数错东西。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from catfish_email import index_store
from catfish_email.adapters.base import Message


# ─────────────────────────────────────────────────────────────────────
# 脚手架
# ─────────────────────────────────────────────────────────────────────
def _emlx(dir_: Path, name: str, subject: str, *, read: bool = False,
          date: str = "Sat, 17 May 2026 13:30:00 +0000") -> Path:
    """造一个格式正确的 .emlx (byte_count + RFC822 + plist trailer)。"""
    rfc = (
        b"From: alice@x.com\r\nTo: me@x.com\r\n"
        b"Subject: " + subject.encode() + b"\r\n"
        b"Message-Id: <" + name.encode() + b"@x>\r\n"
        b"Date: " + date.encode() + b"\r\n\r\nbody of " + name.encode()
    )
    plist = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<plist version="1.0"><dict><key>flags</key><integer>'
        + (b"1" if read else b"0") + b"</integer></dict></plist>"
    )
    p = dir_ / f"{name}.emlx"
    p.write_bytes(f"{len(rfc)}\n".encode() + rfc + plist)
    return p


class _CountingParser:
    """真解析器外面包一层计数 —— parse 次数是增量的直接判据。"""

    def __init__(self, account: str = "工作", folder: str = "Inbox") -> None:
        from catfish_email.adapters.apple_mail_emlx import _parse_emlx_summary
        self._real = _parse_emlx_summary
        self.account, self.folder = account, folder
        self.calls: list[str] = []

    def __call__(self, p: Path) -> Message:
        self.calls.append(p.name)
        return self._real(p, self.account, self.folder)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path / "home"))
    mail = tmp_path / "mail"
    mail.mkdir()
    return mail


def _reconcile(mail_dir: Path, parser: _CountingParser) -> index_store.ReconcileStats:
    conn = index_store.open_index()
    try:
        return index_store.reconcile(
            conn, account=parser.account, folder=parser.folder,
            emlx_files=sorted(mail_dir.glob("*.emlx")), parse=parser,
        )
    finally:
        conn.close()


def _query(**kw) -> list[Message]:
    conn = index_store.open_index()
    try:
        return index_store.query_messages(
            conn, account=kw.pop("account", "工作"),
            folder=kw.pop("folder", "Inbox"), **kw,
        )
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────
# 1. 增量 —— 解析次数说话
# ─────────────────────────────────────────────────────────────────────
def test_首次全解析_第二次零解析(env):
    for i in range(5):
        _emlx(env, f"m{i}", f"主题{i}")
    parser = _CountingParser()

    s1 = _reconcile(env, parser)
    assert s1.parsed == 5 and s1.unchanged == 0

    parser.calls.clear()
    s2 = _reconcile(env, parser)
    assert parser.calls == [], (
        f"文件没变却又解析了 {parser.calls} —— 增量失效, "
        "切 tab 又回到全量重抽 (治本白治)"
    )
    assert s2.unchanged == 5 and s2.parsed == 0


def test_只重解析变过的那一个(env):
    import os, time
    for i in range(4):
        _emlx(env, f"m{i}", f"主题{i}")
    parser = _CountingParser()
    _reconcile(env, parser)

    # 改一个: 内容变 (size 变) + mtime 推后
    p = _emlx(env, "m2", "主题2改过了变长了")
    os.utime(p, (time.time() + 5, time.time() + 5))

    parser.calls.clear()
    s = _reconcile(env, parser)
    assert parser.calls == ["m2.emlx"], f"该只解析 m2, 实际 {parser.calls}"
    assert s.parsed == 1 and s.unchanged == 3

    got = {m.subject for m in _query(limit=10)}
    assert "主题2改过了变长了" in got, "变更没写回索引"


def test_新文件只解析新的(env):
    _emlx(env, "old", "旧的")
    parser = _CountingParser()
    _reconcile(env, parser)

    _emlx(env, "new", "新的")
    parser.calls.clear()
    _reconcile(env, parser)
    assert parser.calls == ["new.emlx"]


def test_删掉的文件从索引消失(env):
    a = _emlx(env, "keep", "留着")
    b = _emlx(env, "gone", "要删")
    parser = _CountingParser()
    _reconcile(env, parser)

    b.unlink()
    s = _reconcile(env, parser)
    assert s.removed == 1
    subjects = {m.subject for m in _query(limit=10)}
    assert subjects == {"留着"}, (
        f"索引残留: {subjects} —— Mail.app 删了信, 鲶鱼里还显示"
    )
    assert a.exists()


def test_坏文件跳过不拖垮整次(env):
    _emlx(env, "good", "好的")
    (env / "corrupt.emlx").write_bytes(b"not a number\ngarbage")
    parser = _CountingParser()

    s = _reconcile(env, parser)
    # 坏文件解析不炸 (解析器宽容) 或计入 errors —— 两种都行, 但好文件必须进索引
    subjects = {m.subject for m in _query(limit=10)}
    assert "好的" in subjects
    assert s.scanned == 2


# ─────────────────────────────────────────────────────────────────────
# 2. 查询语义
# ─────────────────────────────────────────────────────────────────────
def test_unread_only和排序(env):
    _emlx(env, "a", "旧未读", read=False, date="Fri, 01 May 2026 08:00:00 +0000")
    _emlx(env, "b", "新已读", read=True,  date="Sun, 17 May 2026 08:00:00 +0000")
    _emlx(env, "c", "最新未读", read=False, date="Mon, 18 May 2026 08:00:00 +0000")
    _reconcile(env, _CountingParser())

    all_ = _query(limit=10)
    assert [m.subject for m in all_] == ["最新未读", "新已读", "旧未读"], "date DESC 排序错"

    unread = _query(unread_only=True, limit=10)
    assert {m.subject for m in unread} == {"最新未读", "旧未读"}

    top1 = _query(limit=1)
    assert [m.subject for m in top1] == ["最新未读"], "limit 没生效"


def test_已读状态变化靠mtime捕捉(env):
    """员工在 Mail.app 里读了一封 → Mail 重写 emlx plist → mtime/size 变 →
    对账重解析 → 索引里翻成已读。这是「已读可能滞后」那条已知限制的另一半:
    **一旦 Mail 落盘, 我们必须跟上**。"""
    import os, time
    _emlx(env, "m", "一封信", read=False)
    parser = _CountingParser()
    _reconcile(env, parser)
    assert _query(limit=5)[0].is_read is False

    p = _emlx(env, "m", "一封信", read=True)  # 重写: flags 0→1 (size 同, 内容变)
    os.utime(p, (time.time() + 5, time.time() + 5))
    _reconcile(env, parser)
    assert _query(limit=5)[0].is_read is True, "Mail 已落盘的已读状态没跟上"


def test_thread三件套进了索引(env):
    """message_id / in_reply_to / references 是前端 isReplied 的判据
    (P3.5.58), 索引丢了它们 replied badge 就全灭。"""
    _emlx(env, "t", "带线程头")
    _reconcile(env, _CountingParser())
    m = _query(limit=5)[0]
    assert m.message_id == "<t@x>", f"message_id 丢了: {m.message_id!r}"


# ─────────────────────────────────────────────────────────────────────
# 3. schema 版本
# ─────────────────────────────────────────────────────────────────────
def test_schema版本变了就重建不迁移(env, monkeypatch):
    _emlx(env, "m", "旧数据")
    _reconcile(env, _CountingParser())

    monkeypatch.setattr(index_store, "_SCHEMA_VERSION", 999)
    conn = index_store.open_index()
    try:
        n = conn.execute("SELECT count(*) FROM messages").fetchone()[0]
        ver = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
    finally:
        conn.close()
    assert n == 0, "schema 变了没重建 —— 旧行会以错误的列语义被读"
    assert ver == "999"
