"""群里发大文件 (11/07): 合计超 5MB 走中央暂存一份密文, 收件人各自取。假中央 (内存), 邮箱都是 example.com。"""
from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import pytest

from catfish_tool_bridge import colleague_rooms as cr
from catfish_tool_bridge import colleague_rooms_bigfiles as big
from catfish_tool_bridge import colleague_rooms_files as rfiles
from catfish_tool_bridge import colleague_rooms_outbox as outbox
from catfish_tool_bridge import forms_net

BOSS, ZHANG, LI = "zhou@example.com", "zhang@example.com", "li@example.com"


class Central:
    """中央暂存的替身: 跟 room_link_blob_router 一样的规矩 (只有发件人传、只有收件人取、都取完删)。"""

    def __init__(self):
        self.blobs: dict[str, dict] = {}
        self.old = False
        self.down = False
        self.seen: list[bytes] = []

    def http(self, who, method, path, json=None, content=None):
        if self.down:
            raise ValueError("连不上中央服务: refused")
        if self.old:
            return 404, {"detail": "Not Found"}
        parts = path.split("/")[4:]          # /api/room-link/blobs/<id>/...
        if method == "POST" and not parts:
            bid = f"b_{len(self.blobs):032x}"
            self.blobs[bid] = {"owner": who, "to": set(json["recipients"]), "n": json["n"], "size": json["size"],
                               "chunks": {}, "done": False, "taken": set(), "purged": False}
            return 201, {"id": bid}
        b = self.blobs.get(parts[0])
        if not b:
            return 404, {"detail": "没有"}
        if method == "PUT":
            assert who == b["owner"]
            self.seen.append(content)
            b["chunks"][int(parts[2])] = content
            return 200, {"ok": True}
        if method == "POST" and parts[1] == "finish":
            assert len(b["chunks"]) == b["n"] and sum(map(len, b["chunks"].values())) == b["size"]
            b["done"] = True
            return 200, {"ok": True}
        if method == "GET" and len(parts) == 1:
            return 200, {"complete": b["done"], "purged": b["purged"], "n": b["n"]}
        if method == "GET":
            assert who in b["to"]
            return (410, {"detail": "删了"}) if b["purged"] else (200, b["chunks"][int(parts[2])])
        if method == "POST" and parts[1] == "taken":
            b["taken"].add(who)
            if b["taken"] >= b["to"]:
                b["purged"], b["chunks"] = True, {}
            return 200, {"everyone": b["purged"]}
        raise AssertionError(path)


class World:
    def __init__(self, tmp_path, monkeypatch):
        self.tmp, self.mp, self.box, self.who = tmp_path, monkeypatch, [], ""
        self.central = Central()
        monkeypatch.setattr(forms_net, "me", lambda: self.who)
        monkeypatch.setattr(forms_net, "post", self._post)
        monkeypatch.setattr(forms_net, "take", self._take)
        monkeypatch.setattr(outbox, "drain_in_background", lambda: outbox.retry_due(500))
        monkeypatch.setattr(big, "_http", lambda method, path, json=None, content=None:
                            self.central.http(self.who, method, path, json, content))
        monkeypatch.setattr(big, "kick", self._work)

    def _work(self):
        for _ in range(20):
            if not big._due(1):
                return
            big.work()

    def be(self, email):
        self.who = email
        self.mp.setenv("CATFISH_HOME", str(self.tmp / email.split("@")[0]))
        return self

    def _post(self, to, kind, env, ttl, source=None):
        self.box.append({"from": self.who, "to": to, "kind": kind, "env": json.loads(json.dumps(env))})
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


def _room(w, members=(ZHANG, LI)):
    w.be(BOSS)
    rid = cr.create({"name": "华东项目群", "members": list(members)})["room"]["room_id"]
    for m in members:
        w.sync(m)
        cr.join(rid)
    w.sync(BOSS)
    return rid


def _stage(data: bytes, step=big.CHUNK) -> str:
    sid = None
    for off in range(0, len(data), step):
        sid = big.stage({"stage_id": sid, "offset": off, "data": base64.b64encode(data[off:off + step]).decode()})["stage_id"]
    return sid


def _blob(size, seed=3):
    return bytes((i * 31 + seed) % 256 for i in range(size))


def test_大文件_中央只存一份密文_大家取完就删_能打开(w, monkeypatch):
    rid = _room(w)
    w.be(BOSS)
    data = _blob(big.CHUNK * 7 + 1234)               # 7MB 多: 超 5MB, 走中央暂存
    sid = _stage(data)
    msg = cr.send(rid, "方案全套", files=[{"name": "方案.zip", "stage_id": sid}])["message"]
    f = msg["files"][0]
    assert f["n"] == 8 and f["blob"]["id"] in w.central.blobs
    assert not any(m["env"].get("chunk") for m in w.box)   # 没走邮筒分块
    b = w.central.blobs[f["blob"]["id"]]
    assert b["done"] and b["to"] == {ZHANG, LI}
    assert all(data[:1000] not in c for c in w.central.seen)   # 中央只见密文
    assert not (big._dir("staging") / sid).exists()           # 暂存的明文删了
    mine = cr.get(rid)["messages"][-1]["files"][0]
    assert (mine["have"], mine["big"], mine["via"]) == (True, "sent", "central") and "blob" not in mine

    w.sync(ZHANG)
    got = cr.get(rid)["messages"][-1]["files"][0]
    assert (got["have"], got["big"], got["got"]) == (True, "ready", 8)
    monkeypatch.setattr(Path, "home", lambda: w.tmp / "zhang-home")
    out = rfiles.save(cr.load(rid), got["id"])
    assert Path(out["path"]).read_bytes() == data
    assert not b["purged"]                            # 李四还没取
    w.sync(LI)
    assert b["purged"] and b["chunks"] == {}          # 都取完了: 中央删内容


def test_中央太旧_整条不发_小文件照常(w):
    rid = _room(w, (ZHANG,))
    w.be(BOSS)
    w.central.old = True
    with pytest.raises(ValueError, match="中央端版本太旧"):
        cr.send(rid, "大的", files=[{"name": "a.bin", "stage_id": _stage(_blob(rfiles.MAX_TOTAL + 1))}])
    assert not [m for m in w.box if m["kind"] == "room_msg"]
    cr.send(rid, "小的", files=[{"name": "b.txt", "stage_id": _stage(b"hello")}])   # 5MB 以内: 照旧走邮筒
    w.sync(ZHANG)
    assert cr.get(rid)["messages"][-1]["files"][0]["have"] is True


def test_传一半断网_之后接着传_对方等着(w):
    rid = _room(w, (ZHANG,))
    w.be(BOSS)
    data = _blob(big.CHUNK * 6)
    real = w.central.http
    calls = {"n": 0}

    def flaky(who, method, path, json=None, content=None):
        if method == "PUT":
            calls["n"] += 1
            if calls["n"] == 3:
                raise ValueError("连不上中央服务: reset")
        return real(who, method, path, json, content)
    w.central.http = flaky
    msg = cr.send(rid, "", files=[{"name": "录像.mp4", "stage_id": _stage(data)}])["message"]
    st = cr.get(rid)["messages"][-1]["files"][0]
    assert (st["big"], st["got"]) == ("uploading", 2)
    w.sync(ZHANG)                                    # 发件人没传完: 对方等着
    assert cr.get(rid)["messages"][-1]["files"][0]["big"] == "waiting"
    w.be(BOSS)
    with big._db() as con:
        con.execute("UPDATE bigfiles SET next_at = 0")
    cr.sync()                                        # 接着传完
    assert cr.get(rid)["messages"][-1]["files"][0]["big"] == "sent"
    w.sync(ZHANG)
    with big._db() as con:
        con.execute("UPDATE bigfiles SET next_at = 0")
    cr.sync()
    f = cr.get(rid)["messages"][-1]["files"][0]
    assert (f["big"], f["have"]) == ("ready", True)
    assert big._sha_of(big.blob_path(f["id"]), big.row(rid, f["id"])) == msg["files"][0]["sha256"]


def test_中央内容被改_核对不上不算收到(w):
    rid = _room(w, (ZHANG,))
    w.be(BOSS)
    msg = cr.send(rid, "", files=[{"name": "x.pdf", "stage_id": _stage(_blob(big.CHUNK * 6))}])["message"]
    b = w.central.blobs[msg["files"][0]["blob"]["id"]]
    b["chunks"][2] = bytes(len(b["chunks"][2]))       # 中央 (或路上) 改了一块
    w.sync(ZHANG)
    f = cr.get(rid)["messages"][-1]["files"][0]
    assert f["have"] is False and f["big"] in ("downloading", "failed")


def test_暂存_续传和上限(w, monkeypatch):
    w.be(BOSS)
    r = big.stage({"offset": 0, "data": base64.b64encode(b"abc").decode()})
    again = big.stage({"stage_id": r["stage_id"], "offset": 0, "data": base64.b64encode(b"abc").decode()})
    assert (again["ok"], again["size"]) == (False, 3)   # offset 对不上: 告诉界面从 3 续
    monkeypatch.setattr(big, "STAGE_MAX", 4)
    with pytest.raises(ValueError, match="文件太大"):
        big.stage({"stage_id": r["stage_id"], "offset": 3, "data": base64.b64encode(b"de").decode()})
    with pytest.raises(ValueError, match="暂存编号不对"):
        big.staged("../../etc/passwd")


def test_消息删了_本机副本一起删_暂存放久了也删(w):
    rid = _room(w, (ZHANG,))
    w.be(BOSS)
    msg = cr.send(rid, "", files=[{"name": "a.bin", "stage_id": _stage(_blob(big.CHUNK * 6))}])["message"]
    fid = msg["files"][0]["id"]
    assert big.blob_path(fid).exists()
    old = _stage(b"never sent")
    p = big._dir("staging") / old
    t = time.time() - big.STAGE_KEEP - 10
    import os
    os.utime(p, (t, t))
    from catfish_tool_bridge import colleague_rooms_store as store
    store.prune_before("2999-01-01")
    big.drop_orphans()
    assert not big.blob_path(fid).exists() and not p.exists()
