"""同事群聊 (docs/GROUP-CHAT-PLAN.md 第一步)。假邮筒, 全部虚构, 邮箱都是 example.com。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from catfish_tool_bridge import colleague_rooms as cr
from catfish_tool_bridge import forms, forms_net

BOSS, ZHANG, LI, WANG = "zhou@example.com", "zhang@example.com", "li@example.com", "wang@example.com"


class World:
    def __init__(self, tmp_path, monkeypatch):
        self.tmp, self.mp = tmp_path, monkeypatch
        self.box: list[dict] = []
        self.who = ""
        monkeypatch.setattr(forms_net, "me", lambda: self.who)
        monkeypatch.setattr(forms_net, "post", self._post)
        monkeypatch.setattr(forms_net, "take", self._take)

    def be(self, email):
        self.who = email
        self.mp.setenv("CATFISH_HOME", str(self.tmp / email.split("@")[0]))
        return self

    def _post(self, to, kind, env, ttl):
        assert ttl == cr.TTL_SECONDS
        self.box.append({"from": self.who, "to": to, "kind": kind, "env": json.loads(json.dumps(env, ensure_ascii=False))})
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


def _make_room(w, members=(ZHANG, LI)):
    w.be(BOSS)
    out = cr.create({"name": "华东项目群", "purpose": "周报和客户跟进",
                     "members": [{"email": m, "name": m.split("@")[0]} for m in members]})
    return out["room"]["room_id"]


def _texts(rid):
    return [m["text"] for m in cr.messages(rid) if m.get("kind") == "text"]


def test_建群_拉人_加入_发消息只给已加入的人(w):
    rid = _make_room(w)
    assert sorted(m["to"] for m in w.box) == [LI, ZHANG]
    assert {m["kind"] for m in w.box} == {"room_invite"}

    ev = w.sync(ZHANG)["events"]
    assert [e["kind"] for e in ev] == ["invite"]
    assert cr.list_rooms()["rooms"][0]["my_state"] == "invited"
    cr.join(rid)                                     # 张三加入: 通知群主和还在群里的李四
    assert sorted(m["to"] for m in w.box if m["kind"] == "room_join") == sorted([BOSS, LI])

    assert [e["kind"] for e in w.sync(BOSS)["events"]] == ["join"]
    w.box.clear()
    r = cr.send(rid, "大家周五前把拜访记录发我", mentions=[ZHANG])
    assert r["sent_to"] == 1                         # 李四还没加入, 收不到
    assert [m["to"] for m in w.box] == [ZHANG]

    ev = w.sync(ZHANG)["events"]
    assert [(e["kind"], e["text"]) for e in ev] == [("mention", "大家周五前把拜访记录发我")]
    room = cr.list_rooms()["rooms"][0]
    assert (room["unread"], room["mentioned"]) == (1, 1)
    cr.mark_read(rid)
    assert cr.list_rooms()["rooms"][0]["unread"] == 0
    cr.send(rid, "收到")
    w.sync(BOSS)
    assert _texts(rid) == ["大家周五前把拜访记录发我", "收到"]


def test_后加入的人看不到加入前的记录(w):
    rid = _make_room(w)
    w.sync(ZHANG)
    cr.join(rid)
    w.sync(BOSS)
    cr.send(rid, "第一条")
    w.sync(LI)                                       # 李四之前没加入: 第一条没发给他
    cr.join(rid)
    w.sync(BOSS)
    cr.send(rid, "第二条")
    w.sync(LI)
    assert _texts(rid) == ["第二条"]
    assert any("加入前的消息看不到" in m["text"] for m in cr.messages(rid) if m["kind"] == "system")


def test_防冒名_不是成员的消息_非群主改名单_都不收(w):
    rid = _make_room(w)
    w.sync(ZHANG)
    cr.join(rid)
    w.box.append({"from": WANG, "to": ZHANG, "kind": "room_msg",
                  "env": {"v": 1, "room_id": rid, "msg_id": "m_" + "a" * 20, "at": "x", "text": "我是外人"}})
    w.box.append({"from": LI, "to": ZHANG, "kind": "room_member",
                  "env": {"v": 1, "room_id": rid, "ver": 99, "op": "disband", "members": {}}})
    w.box.append({"from": WANG, "to": ZHANG, "kind": "room_invite",
                  "env": {"v": 1, "room_id": rid, "ver": 9, "owner": WANG, "name": "抢群", "members": {ZHANG: {"state": "invited"}}}})
    assert w.sync(ZHANG)["events"] == []
    room = cr.load(rid)
    assert (room["my_state"], room["name"], room["owner"]) == ("joined", "华东项目群", BOSS)
    assert _texts(rid) == []


def test_同一条消息只收一次(w):
    rid = _make_room(w, (ZHANG,))
    w.sync(ZHANG)
    cr.join(rid)
    w.sync(BOSS)
    cr.send(rid, "hi")
    dup = dict(w.box[-1])
    w.box.append(dup)
    w.sync(ZHANG)
    assert _texts(rid) == ["hi"]


def test_群主拉人_移出_改名_解散(w):
    rid = _make_room(w, (ZHANG,))
    w.sync(ZHANG)
    cr.join(rid)
    w.sync(BOSS)
    w.box.clear()
    cr.add_members(rid, [{"email": LI, "name": "李四"}])
    assert sorted((m["to"], m["kind"]) for m in w.box) == [(LI, "room_invite"), (ZHANG, "room_member")]
    ev = w.sync(ZHANG)["events"]
    assert [e["kind"] for e in ev] == ["member"]
    assert any("群主邀请了 李四" in m["text"] for m in cr.messages(rid))

    w.be(BOSS)
    cr.update_info(rid, {"name": "华东项目群 (11 月)", "purpose": "每周五交周报"})
    w.sync(ZHANG)
    assert (cr.load(rid)["name"], cr.load(rid)["purpose"]) == ("华东项目群 (11 月)", "每周五交周报")

    w.be(BOSS)
    cr.remove_member(rid, ZHANG)
    assert [e["kind"] for e in w.sync(ZHANG)["events"]] == ["removed"]
    assert cr.load(rid)["my_state"] == "removed"
    with pytest.raises(ValueError, match="不在这个群"):
        cr.send(rid, "还能发吗")
    cr.delete_local(rid)
    assert cr.list_rooms()["rooms"] == []

    w.sync(LI)
    w.be(BOSS)
    cr.disband(rid)
    assert [e["kind"] for e in w.sync(LI)["events"]] == ["disband"]
    assert cr.load(rid)["my_state"] == "disbanded"


def test_只有群主能管_群主不能退(w):
    rid = _make_room(w, (ZHANG,))
    with pytest.raises(ValueError, match="群主不能退群"):
        cr.leave(rid)
    w.sync(ZHANG)
    cr.join(rid)
    for fn in (lambda: cr.add_members(rid, [LI]), lambda: cr.remove_member(rid, BOSS),
               lambda: cr.update_info(rid, {"name": "x"}), lambda: cr.disband(rid)):
        with pytest.raises(ValueError, match="只有群主"):
            fn()
    w.box.clear()
    cr.leave(rid)
    assert [(m["to"], m["kind"]) for m in w.box] == [(BOSS, "room_leave")]
    assert [e["kind"] for e in w.sync(BOSS)["events"]] == ["leave"]
    assert cr.get(rid)["room"]["joined"] == 1


def test_不加入_只回群主(w):
    rid = _make_room(w)
    w.sync(LI)
    w.box.clear()
    cr.decline(rid)
    assert [(m["to"], m["kind"], m["env"]["status"]) for m in w.box] == [(BOSS, "room_join", "declined")]
    assert [e["kind"] for e in w.sync(BOSS)["events"]] == ["declined"]
    assert cr.get(rid)["room"]["members"] == 2


def test_上限_50人_4000字(w):
    w.be(BOSS)
    with pytest.raises(ValueError, match="最多 50 人"):
        cr.create({"name": "大群", "members": [f"u{i}@example.com" for i in range(50)]})
    with pytest.raises(ValueError, match="至少拉一位"):
        cr.create({"name": "空群", "members": [BOSS]})
    rid = _make_room(w, (ZHANG,))
    with pytest.raises(ValueError, match="4000"):
        cr.send(rid, "字" * 4001)
    with pytest.raises(ValueError, match="空的"):
        cr.send(rid, "   ")


def test_超过7天没取邮筒_提示可能缺消息(w):
    rid = _make_room(w, (ZHANG,))
    w.sync(BOSS)
    old = (datetime.now().astimezone() - timedelta(days=8)).isoformat(timespec="seconds")
    forms._write(cr.rooms_dir() / "_sync.json", {"at": old})
    assert w.sync(BOSS)["gap"] is True
    assert any("可能没收到" in m["text"] for m in cr.messages(rid))
    assert w.sync(BOSS)["gap"] is False


def test_没收到加入就先收到消息_按已加入算(w):
    """张三加入的「加入」信封还没到李四那边, 张三的消息先到: 收下, 张三算已加入。"""
    rid = _make_room(w)
    w.sync(LI)
    cr.join(rid)
    w.sync(ZHANG)
    cr.join(rid)
    w.sync(BOSS)
    # 张三发给李四的 room_join 拿掉, 模拟延迟
    w.box[:] = [m for m in w.box if not (m["to"] == LI and m["kind"] == "room_join")]
    w.be(ZHANG)
    cr.send(rid, "我是张三")
    w.sync(LI)
    assert _texts(rid) == ["我是张三"]
    assert {p["email"]: p["state"] for p in cr.get(rid)["members"]}[ZHANG] == "joined"


def test_老中央不认_room_kind_给人话(monkeypatch):
    monkeypatch.setattr(forms_net, "_client_call", lambda *a, **k: (400, {"detail": "kind=room_msg 不在 [...]"}))
    with pytest.raises(ValueError, match="还不支持同事群聊"):
        forms_net.post(ZHANG, "room_msg", {"v": 1}, cr.TTL_SECONDS)
    monkeypatch.setattr(forms_net, "_client_call", lambda *a, **k: (400, {"detail": "kinds 里有不认识的: ['room_msg']"}))
    with pytest.raises(ValueError, match="还不支持同事群聊"):
        forms_net.take(forms_net.ROOM_KINDS)
