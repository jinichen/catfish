"""同事群聊 11/07: 没送到的挂在消息下面、按原因补发 / 重发; 群主转让; 群里发文件; 小鲶后台进度。
假邮筒, 全部虚构, 邮箱都是 example.com。"""
from __future__ import annotations

import base64
import json
import sqlite3
import time
from pathlib import Path

import pytest

from catfish_tool_bridge import colleague_rooms as cr
from catfish_tool_bridge import colleague_rooms_files as rfiles
from catfish_tool_bridge import colleague_rooms_mend as mend
from catfish_tool_bridge import colleague_rooms_outbox as outbox
from catfish_tool_bridge import colleague_rooms_store as store
from catfish_tool_bridge import forms_net, mailbox_crypto

BOSS, ZHANG, LI = "zhou@example.com", "zhang@example.com", "li@example.com"


class World:
    def __init__(self, tmp_path, monkeypatch):
        self.tmp, self.mp = tmp_path, monkeypatch
        self.box: list[dict] = []
        self.who = ""
        self.fail: dict[str, Exception] = {}       # 收件人 → 投给他时抛的错
        self.drop: set[tuple[str, int]] = set()     # (收件人, 第几块) 投递时丢掉 (模拟取的时候断了)
        monkeypatch.setattr(forms_net, "me", lambda: self.who)
        monkeypatch.setattr(forms_net, "post", self._post)
        monkeypatch.setattr(forms_net, "take", self._take)
        # 后台补发线程: 测试里同步跑 (几个人共用一个进程, 换人会改 CATFISH_HOME)
        monkeypatch.setattr(outbox, "drain_in_background", lambda: [outbox.retry_due(500) for _ in range(3)])

    def be(self, email):
        self.who = email
        self.mp.setenv("CATFISH_HOME", str(self.tmp / email.split("@")[0]))
        return self

    def _post(self, to, kind, env, ttl, source=None):
        if to in self.fail:
            raise self.fail[to]
        c = env.get("chunk")
        if c and (to, c["i"]) in self.drop:
            self.drop.discard((to, c["i"]))
            return 0
        self.box.append({"from": self.who, "to": to, "kind": kind, "env": json.loads(json.dumps(env, ensure_ascii=False)),
                         "source": source})
        return len(self.box)

    def _take(self, kinds=forms_net.FORM_KINDS):
        mine = [m for m in self.box if m["to"] == self.who and m["kind"] in kinds]
        for m in mine:
            self.box.remove(m)
        return [{"from": m["from"], "kind": m["kind"], "env": m["env"], "created_at": None} for m in mine]

    def sync(self, who):
        self.be(who)
        return cr.sync()


@pytest.fixture()
def w(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


def _room_with(w, members=(ZHANG, LI)):
    w.be(BOSS)
    rid = cr.create({"name": "华东项目群", "members": [{"email": m, "name": m.split("@")[0]} for m in members]})["room"]["room_id"]
    for m in members:
        w.sync(m)
        cr.join(rid)
    w.sync(BOSS)
    return rid


def _delivery(rid, text):
    m = next(m for m in cr.get(rid)["messages"] if m["text"] == text)
    return {d["email"]: d["state"] for d in m.get("delivery") or []}


def _age(rid, seconds):
    """把 outbox 里的行往前推 (到点 / 过期)。"""
    with store.tx() as con:
        con.execute("UPDATE outbox SET next_at = next_at - ?, created_at = created_at - ? WHERE room_id = ?", (seconds, seconds, rid))


# ── 没送到的: 挂在消息下面, 按原因补 ─────────────────────────────

def test_对方没公钥_挂在消息下_他登记了自动补发(w):
    rid = _room_with(w)
    w.be(BOSS)
    w.fail[LI] = mailbox_crypto.NoKeyError("li 还没升级")
    r = cr.send(rid, "周五交周报")
    assert [f["reason"] for f in r["failed"]] == ["nokey"]
    assert _delivery(rid, "周五交周报") == {LI: "nokey"}
    del w.fail[LI]
    cr.sync()                                    # 还没到 10 分钟: 不补
    assert _delivery(rid, "周五交周报") == {LI: "nokey"}
    _age(rid, outbox.NOKEY_EVERY + 1)
    cr.sync()                                    # 到点了, 他也登记了: 补发, 那行没了
    assert _delivery(rid, "周五交周报") == {}
    assert [m["to"] for m in w.box if m["kind"] == "room_msg"][-1] == LI
    w.sync(LI)
    assert "周五交周报" in [m["text"] for m in cr.messages(rid)]


def test_连不上中央_重试三次后放弃_点重发能补(w):
    rid = _room_with(w, (ZHANG,))
    w.be(BOSS)
    w.fail[ZHANG] = ValueError("连不上中央服务: timed out")
    cr.send(rid, "开会改到三点")
    assert _delivery(rid, "开会改到三点") == {ZHANG: "sending"}
    for wait in outbox.BACKOFF:
        _age(rid, wait + 1)
        cr.sync()
    assert _delivery(rid, "开会改到三点") == {ZHANG: "failed"}
    _age(rid, 10_000)
    cr.sync()                                    # 放弃了的不再自动试
    assert _delivery(rid, "开会改到三点") == {ZHANG: "failed"}
    del w.fail[ZHANG]
    msg = next(m for m in cr.get(rid)["messages"] if m["text"] == "开会改到三点")
    r = mend.resend(rid, msg["msg_id"])
    assert (r["sent"], r["left"]) == (1, 0)
    assert _delivery(rid, "开会改到三点") == {}


def test_中央太旧_整个群顶上提示(w):
    rid = _room_with(w, (ZHANG,))
    w.be(BOSS)
    w.fail[ZHANG] = ValueError("投递给 zhang 失败: 中央端版本太旧, 还不支持同事群聊")
    cr.send(rid, "x")
    assert cr.get(rid)["room"]["issue"] == "too_old"
    assert _delivery(rid, "x") == {ZHANG: "too_old"}


def test_邀请没送到_挂在那条系统提示下(w):
    w.be(BOSS)
    w.fail[LI] = mailbox_crypto.NoKeyError("li 还没升级")
    rid = cr.create({"name": "群", "members": [ZHANG, LI]})["room"]["room_id"]
    sys_msg = next(m for m in cr.get(rid)["messages"] if m["kind"] == "system")
    assert {d["email"]: d["state"] for d in sys_msg["delivery"]} == {LI: "nokey"}
    del w.fail[LI]
    _age(rid, outbox.NOKEY_EVERY + 1)
    cr.sync()
    w.sync(LI)
    assert cr.list_rooms()["rooms"][0]["my_state"] == "invited"


def test_收件人被移出了_没送到的那行直接删(w):
    rid = _room_with(w)
    w.be(BOSS)
    w.fail[LI] = ValueError("连不上中央服务")
    cr.send(rid, "给大家")
    del w.fail[LI]
    cr.remove_member(rid, LI)
    _age(rid, 61)
    w.box.clear()
    cr.sync()
    assert not [m for m in w.box if m["kind"] == "room_msg" and m["to"] == LI]
    assert _delivery(rid, "给大家") == {}


def test_升级前只记了_undelivered_的老消息_显示没送到_能重发(w):
    rid = _room_with(w, (ZHANG,))
    w.be(BOSS)
    cr.send(rid, "老消息")
    msg = next(m for m in cr.messages(rid) if m["text"] == "老消息")
    msg["undelivered"] = [ZHANG]
    store.update_message(rid, msg)
    assert _delivery(rid, "老消息") == {ZHANG: "failed"}
    w.box.clear()
    mend.resend(rid, msg["msg_id"])
    assert [(m["to"], m["env"]["text"]) for m in w.box] == [(ZHANG, "老消息")]
    assert _delivery(rid, "老消息") == {}


# ── 群主转让 ─────────────────────────────────────────────────────

def test_群主转让_之后只认新群主_原群主能退群(w):
    rid = _room_with(w)
    w.be(BOSS)
    with pytest.raises(ValueError, match="只能转给已经加入的成员"):
        cr.transfer(rid, "nobody@example.com")
    cr.transfer(rid, ZHANG)
    assert cr.load(rid)["owner"] == ZHANG
    with pytest.raises(ValueError, match="只有群主"):
        cr.update_info(rid, {"name": "原群主还想改"})
    w.sync(ZHANG)
    assert cr.load(rid)["owner"] == ZHANG
    assert any(m["text"] == "群主把群主转给了你" for m in cr.messages(rid))
    w.sync(LI)
    assert cr.load(rid)["owner"] == ZHANG
    # 原群主再发名单 (伪造): 李四不认
    w.box.append({"from": BOSS, "to": LI, "kind": "room_member",
                  "env": {"v": 1, "room_id": rid, "ver": 99, "op": "disband", "members": {}}})
    w.sync(LI)
    assert cr.load(rid)["my_state"] == "joined"
    # 新群主改群名, 大家都认
    w.be(ZHANG)
    cr.update_info(rid, {"name": "新名字"})
    w.sync(LI)
    assert cr.load(rid)["name"] == "新名字"
    w.be(BOSS)
    cr.leave(rid)                                # 原群主现在能退了
    assert cr.load(rid)["my_state"] == "left"


# ── 群文件 ───────────────────────────────────────────────────────

def _file(name, size, seed=1):
    data = bytes((i * 7 + seed) % 251 for i in range(size))
    return {"name": name, "data": base64.b64encode(data).decode()}, data


def test_发文件_分块后台发_收齐核对_打开写到下载(w, monkeypatch):
    rid = _room_with(w, (ZHANG,))
    w.be(BOSS)
    f, data = _file("报价单.xlsx", rfiles.CHUNK * 2 + 100)
    r = cr.send(rid, "", files=[f])
    assert r["message"]["files"][0]["n"] == 3 and "data" not in r["message"]["files"][0]
    chunks = [m for m in w.box if m["env"].get("chunk")]
    assert len(chunks) == 3 and all(m["source"] == "room-file" for m in chunks)
    assert cr.list_rooms()["rooms"][0]["last"]["text"] == "📎 报价单.xlsx"
    w.sync(ZHANG)
    msg = cr.get(rid)["messages"][-1]
    assert msg["files"][0]["have"] is True
    monkeypatch.setattr(Path, "home", lambda: w.tmp / "zhang-home")
    out = rfiles.save(cr.load(rid), msg["files"][0]["id"])
    assert Path(out["path"]).read_bytes() == data
    assert Path(out["path"]).parent.name == "华东项目群"
    assert rfiles.save(cr.load(rid), msg["files"][0]["id"])["path"] == out["path"]   # 再开一次不重复写


def test_文件块丢了_找发件人要_补齐(w, monkeypatch):
    rid = _room_with(w, (ZHANG,))
    w.be(BOSS)
    f, data = _file("方案.pdf", rfiles.CHUNK * 3, seed=5)
    w.drop.add((ZHANG, 1))
    cr.send(rid, "方案见附件", files=[f])
    w.sync(ZHANG)
    fid = cr.get(rid)["messages"][-1]["files"][0]["id"]
    assert cr.get(rid)["messages"][-1]["files"][0] == {**cr.get(rid)["messages"][-1]["files"][0], "have": False, "got": 2}
    now = time.time() + rfiles.NEED_AFTER + 5
    monkeypatch.setattr(rfiles.time, "time", lambda: now)
    cr.sync()                                    # 10 分钟没新块: 找群主要第 1 块
    need = [m for m in w.box if m["env"].get("need")]
    assert [(m["to"], m["env"]["need"]["missing"]) for m in need] == [(BOSS, [1])]
    w.sync(BOSS)                                 # 群主电脑自动补
    w.sync(ZHANG)
    st = rfiles.status(rid, [{"id": fid, "n": 3}])
    assert st[fid] == {"have": True, "got": 3}


def test_文件_上限和伪造块(w, monkeypatch):
    from catfish_tool_bridge import colleague_rooms_bigfiles as bigmod
    rid = _room_with(w, (ZHANG,))
    w.be(BOSS)
    monkeypatch.setattr(bigmod, "MAX_FILE", rfiles.MAX_TOTAL)    # 真上限 200MB, 测试里缩小
    big, _ = _file("大.zip", rfiles.MAX_TOTAL + 1)
    with pytest.raises(ValueError, match="文件太大"):
        cr.send(rid, "", files=[big])
    with pytest.raises(ValueError, match="最多带"):
        cr.send(rid, "", files=[_file(f"{i}.txt", 10)[0] for i in range(rfiles.MAX_FILES + 1)])
    assert not w.box
    f, data = _file("a.txt", 50)
    cr.send(rid, "看", files=[f])
    w.box = [m for m in w.box if not m["env"].get("chunk")]
    msg = next(m for m in cr.messages(rid) if m["text"] == "看")
    fid = msg["files"][0]["id"]
    # 内容被换掉的块: 核对 sha256 不过, 不算收到
    w.box.append({"from": BOSS, "to": ZHANG, "kind": "room_msg",
                  "env": {"v": 1, "room_id": rid, "msg_id": msg["msg_id"],
                          "chunk": {"file_id": fid, "i": 0, "n": 1, "data": base64.b64encode(b"x" * 50).decode()}}})
    w.sync(ZHANG)
    got = cr.get(rid)["messages"][-1]["files"][0]
    assert (got["have"], got["got"]) == (False, 0)


def test_路径名不能跳出下载目录(w, monkeypatch):
    rid = _room_with(w, (ZHANG,))
    w.be(BOSS)
    f, _ = _file("../../etc/passwd", 10)
    r = cr.send(rid, "x", files=[f])
    assert r["message"]["files"][0]["name"] == "passwd"


# ── 小鲶后台做到哪了 ─────────────────────────────────────────────

def test_后台进度_读_hermes_定时任务会话(tmp_path, monkeypatch):
    from catfish_tool_bridge import colleague_tasks_schedule as sched
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    con = sqlite3.connect(tmp_path / "state.db")
    con.executescript("""
        CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, started_at REAL NOT NULL, ended_at REAL);
        CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, tool_calls TEXT,
                               tool_name TEXT, timestamp REAL NOT NULL);
    """)
    con.execute("INSERT INTO sessions VALUES ('cron_abc123_20261107_100000', 'cron', 1, NULL)")
    calls = lambda name, args: json.dumps([{"function": {"name": name, "arguments": json.dumps(args)}}])  # noqa: E731
    con.execute("INSERT INTO messages (session_id, role, tool_calls, timestamp) VALUES (?, 'assistant', ?, 2)",
                ("cron_abc123_20261107_100000", calls("catfish_email_search", {"query": "拜访记录"})))
    con.execute("INSERT INTO messages (session_id, role, tool_calls, timestamp) VALUES (?, 'assistant', ?, 3)",
                ("cron_abc123_20261107_100000", calls("read_file", {"path": "/Users/x/周报.docx"})))
    con.commit()
    con.close()
    p = sched.run_progress("abc123")
    assert (p["running"], p["steps"], p["tool"], p["detail"]) == (True, 2, "read_file", "/Users/x/周报.docx")
    assert sched.run_progress("nope") is None
    assert sched.run_progress("x'; drop") is None


def test_公钥登记_文件_在外发记录里有名字():
    from catfish_tool_bridge import egress_log
    assert egress_log.purpose("http://h:8999/api/room-link/keys") == "同事协作 · 加密公钥"
    assert egress_log.purpose("http://h:8999/api/room-link/mailbox", "room-file") == "群聊 · 发文件"


def test_重发_文件几十块一直发不出去_不会卡死_交给后台(w):
    rid = _room_with(w, (ZHANG,))
    w.be(BOSS)
    w.fail[ZHANG] = ValueError("投递给 zhang 失败 (403): 不收")
    f, _ = _file("大方案.pdf", rfiles.CHUNK * 62)
    r = cr.send(rid, "大文件", files=[f])
    msg_id = r["message"]["msg_id"]
    assert _delivery(rid, "大文件") == {ZHANG: "failed"}
    out = mend.resend(rid, msg_id)                # 63 封 > 一批 (60): 不当场发, 后台发, 照样发不出去就停
    assert out["left"] == 63
    assert _delivery(rid, "大文件") == {ZHANG: "failed"}


def test_同事来要缺块_同一个文件最多补三轮_上一轮没补完不再排(w):
    rid = _room_with(w, (ZHANG,))
    w.be(BOSS)
    f, _ = _file("a.pdf", rfiles.CHUNK * 2)
    msg = cr.send(rid, "a", files=[f])["message"]
    fid = msg["files"][0]["id"]
    need = {"v": 1, "room_id": rid, "msg_id": msg["msg_id"], "need": {"file_id": fid, "missing": [0, 1]}}
    w.box.clear()
    w.fail[ZHANG] = ValueError("连不上中央服务")
    assert rfiles.serve_need(rid, ZHANG, BOSS, need) == 2
    assert rfiles.serve_need(rid, ZHANG, BOSS, need) == 0      # 上一轮还在 outbox 里
    del w.fail[ZHANG]
    for _ in range(3):
        with store.tx() as con:
            con.execute("DELETE FROM outbox")
        rfiles.serve_need(rid, ZHANG, BOSS, need)
    with store.tx() as con:
        con.execute("DELETE FROM outbox")
    assert rfiles.serve_need(rid, ZHANG, BOSS, need) == 0      # 第 4 轮不补了
