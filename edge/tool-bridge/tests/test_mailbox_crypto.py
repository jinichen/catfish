"""同事协作邮筒端到端加密 (11/6, docs/GROUP-CHAT-PLAN.md §4.1)。假中央: 公钥目录 + 邮筒都在内存里。邮箱都是 example.com。"""
from __future__ import annotations

import json

import pytest

from catfish_tool_bridge import forms_net, mailbox_crypto as mc

A, B, M = "alice@example.com", "bob@example.com", "mallory@example.com"


class Central:
    def __init__(self, tmp_path, monkeypatch):
        self.tmp, self.mp = tmp_path, monkeypatch
        self.keys: dict[str, dict[str, str]] = {}
        self.box: list[dict] = []
        self.who = ""
        self.has_keys_api = True
        monkeypatch.setattr(forms_net, "me", lambda: self.who)
        monkeypatch.setattr(forms_net, "_client_call", self.call)
        for d in (mc._cache, mc._registered):
            d.clear()
        mc._changes.clear()

    def be(self, email):
        self.who = email
        self.mp.setenv("CATFISH_HOME", str(self.tmp / email.split("@")[0]))
        return self

    def call(self, method, path, *, params=None, body=None, source=None):
        if path == mc.KEYS_PATH:
            if not self.has_keys_api:
                return 404, {"detail": "Not Found"}
            if method == "PUT":
                self.keys.setdefault(self.who, {})[body["device_id"]] = body["pubkey"]
                return 200, {"ok": True}
            subs = params["subs"].split(",")
            return 200, {"keys": {s: [{"device_id": d, "pubkey": p, "updated_at": "x"} for d, p in self.keys[s].items()]
                                  for s in subs if s in self.keys}}
        if method == "POST":
            self.box.append({"from": self.who, **body})
            return 201, {"id": len(self.box)}
        mine = [m for m in self.box if m["to"] == self.who and m["kind"] in params["kinds"].split(",")]
        for m in mine:
            self.box.remove(m)
        return 200, {"messages": [{"id": i, "from": m["from"], "kind": m["kind"], "payload": m["payload"], "created_at": "x"}
                                  for i, m in enumerate(mine, 1)]}


@pytest.fixture()
def c(tmp_path, monkeypatch):
    return Central(tmp_path, monkeypatch)


def _online(c, *who):
    for w in who:   # 升级后打开过一次: 取一次邮筒就把公钥登记上了
        c.be(w)
        forms_net.take(forms_net.ROOM_KINDS)


def test_加密收发_中央库里只有密文(c):
    _online(c, B)
    c.be(A)
    env = {"v": 1, "room_id": "r_x", "text": "周五前把拜访记录交上来, 客户是南岭置业"}
    forms_net.post(B, "room_msg", env, 3600)
    stored = c.box[0]["payload"]
    assert "南岭置业" not in stored and "拜访" not in stored
    assert json.loads(stored)["v"] == 2
    c.be(B)
    got = forms_net.take(forms_net.ROOM_KINDS)
    assert got[0]["env"] == env and got[0]["from"] == A


def test_对方还没公钥_拦住不发_说清原因(c):
    c.be(A)
    with pytest.raises(mc.NoKeyError, match="还没升级到能加密的鲶鱼版本"):
        forms_net.post(B, "task_invite", {"v": 1}, 3600)
    assert c.box == []
    _online(c, B)                     # 对方升级打开后, 不用等缓存过期就能发
    c.be(A)
    forms_net.post(B, "task_invite", {"v": 1}, 3600)
    assert len(c.box) == 1


def test_改密文_改发件人_冒充他人公钥_都不收(c):
    _online(c, B, M)
    c.be(A)
    forms_net.post(B, "room_msg", {"text": "1"}, 3600)
    forms_net.post(B, "room_msg", {"text": "2"}, 3600)
    forms_net.post(B, "room_msg", {"text": "3"}, 3600)
    p = json.loads(c.box[0]["payload"]); p["c"] = p["c"][:-6] + ("AAAAAA" if not p["c"].endswith("AAAAAA") else "BBBBBB")
    c.box[0]["payload"] = json.dumps(p)                 # 1. 内容被改
    c.box[1]["from"] = M                                # 2. 中央改了发件人
    c.be(M)                                             # 3. 马洛里用自己的钥匙加密, 中央把 from 写成爱丽丝
    forms_net.post(B, "room_msg", {"text": "我是爱丽丝"}, 3600)
    c.box[-1]["from"] = A
    c.be(B)
    got = forms_net.take(forms_net.ROOM_KINDS)
    assert [g["env"]["text"] for g in got] == ["3"]


def test_老版本明文照收_标未加密(c):
    _online(c, B)
    c.box.append({"from": A, "to": B, "kind": "room_msg", "payload": json.dumps({"v": 1, "text": "老版本"}), "ttl_seconds": 1})
    c.be(B)
    got = forms_net.take(forms_net.ROOM_KINDS)
    assert got[0]["env"]["_plain"] is True and got[0]["env"]["text"] == "老版本"


def test_换了电脑_公钥变了_记一条提示(c):
    _online(c, B)
    c.be(A)
    forms_net.post(B, "room_msg", {"text": "x"}, 3600)
    assert mc.pop_key_changes() == []
    import shutil
    shutil.rmtree(c.tmp / "bob" / "collab_keys")       # 张三换电脑: 新密钥
    c.keys[B].clear()
    mc._registered.clear()
    _online(c, B)
    c.be(A)
    mc._cache.clear()
    forms_net.post(B, "room_msg", {"text": "y"}, 3600)
    ch = mc.pop_key_changes()
    assert [x["email"] for x in ch] == [B] and len(ch[0]["fingerprint"].split()) == 8


def test_一个人两台电脑_两台都解得开(c, monkeypatch):
    _online(c, B)
    bob1 = (c.tmp / "bob" / "collab_keys" / "device.json").read_text()
    c.mp.setenv("CATFISH_HOME", str(c.tmp / "bob2"))     # 张三的第二台电脑
    c.who = B
    forms_net.take(forms_net.ROOM_KINDS)
    assert len(c.keys[B]) == 2
    c.be(A)
    forms_net.post(B, "room_msg", {"text": "两台都行"}, 3600)
    assert len(json.loads(c.box[0]["payload"])["k"]) == 2
    (c.tmp / "bob" / "collab_keys" / "device.json").write_text(bob1)
    c.be(B)
    assert forms_net.take(forms_net.ROOM_KINDS)[0]["env"]["text"] == "两台都行"


def test_最长的交回结果_加密后也在上限内(c):
    _online(c, B)
    c.be(A)
    forms_net.post(B, "task_reply", {"v": 1, "result": "拜访记录" * 3750}, 3600)   # 1.5 万字
    assert len(c.box[0]["payload"].encode()) < forms_net.MAX_PAYLOAD_BYTES


def test_老中央没有公钥接口_给人话(c):
    c.has_keys_api = False
    c.be(A)
    with pytest.raises(ValueError, match="中央端版本太旧, 还不支持加密传输"):
        forms_net.post(B, "room_msg", {"text": "x"}, 3600)


def test_私钥文件只有本人可读(c):
    import os
    import stat
    c.be(A)
    d = mc.device()
    mode = stat.S_IMODE(os.stat(mc.keys_dir() / "device.json").st_mode)
    assert mode == 0o600 and d["device_id"].startswith("dev_")
