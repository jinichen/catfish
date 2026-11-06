"""群聊记录保留期限 (11/6, docs/GROUP-CHAT-PLAN.md §4.2): 默认 / 公司 / 群主 / 个人, 取最短; 到期删。邮箱都是 example.com。"""
from __future__ import annotations

import json
import time

import pytest

from catfish_tool_bridge import colleague_rooms as cr
from catfish_tool_bridge import colleague_rooms_retention as keep
from catfish_tool_bridge import forms_net
from tests.test_colleague_rooms import BOSS, LI, ZHANG, World, _room_all_joined


@pytest.fixture()
def w(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


def _company(days):
    cr.store.set_meta("org_policy", json.dumps({"room_retention_days": days, "fetched_at": time.time()}))


def test_四层取最短_只能往短里调(w):
    rid = _room_all_joined(w, (ZHANG,))
    room = cr.load(rid)
    assert keep.effective(room) == {"days": 180, "source": "default", "label": "默认"}
    _company(90)
    assert keep.effective(room)["days"] == 90 and keep.effective(room)["source"] == "company"
    with pytest.raises(ValueError, match="不能比公司规定的 90 天还长"):
        cr.update_info(rid, {"retention_days": 120})
    cr.update_info(rid, {"retention_days": 30})
    assert keep.effective(cr.load(rid))["days"] == 30 and keep.effective(cr.load(rid))["label"] == "群主设置"
    keep.set_personal(14)
    assert keep.effective(cr.load(rid)) == {"days": 14, "source": "personal", "label": "你自己设的"}
    with pytest.raises(ValueError, match="之间"):
        keep.set_personal(3)


def test_群主设的保留天数_通知到成员(w):
    rid = _room_all_joined(w, (ZHANG,))
    w.be(BOSS)
    cr.update_info(rid, {"retention_days": 60})
    w.sync(ZHANG)
    assert cr.load(rid)["retention_days"] == 60
    assert any("群主把记录保留改成 60 天" in m["text"] for m in cr.messages(rid))
    assert cr.list_rooms()["rooms"][0]["retention"]["days"] == 60


def test_到期删旧消息_结束满期的群整个删掉(w):
    rid = _room_all_joined(w, (ZHANG, LI))
    w.be(ZHANG)
    cr.store.add_message(rid, {"msg_id": "m_" + "9" * 20, "kind": "text", "from": BOSS, "at": "2025-01-01T09:00:00+08:00", "text": "两年前", "mentions": []})
    assert keep.maintain(force=True) == 1
    assert "两年前" not in [m["text"] for m in cr.messages(rid)]
    room = cr.load(rid)
    room.update(my_state="left", ended_at="2025-01-01T09:00:00+08:00")
    cr._save(room)
    keep.maintain(force=True)
    assert cr.list_rooms()["rooms"] == []


def test_公司设置每小时取一次_老中央按默认(w, monkeypatch):
    calls = []
    def fake(method, path, **kw):
        calls.append(path)
        return 200, {"room_retention_days": 45}
    monkeypatch.setattr(forms_net, "_client_call", fake)
    w.be(BOSS)
    assert keep.company_days() == 45 and keep.company_days() == 45 and calls == ["/api/org/policy"]
    monkeypatch.setattr(forms_net, "_client_call", lambda *a, **k: (404, {"detail": "Not Found"}))
    assert keep.company_days(refresh=True) is None
    monkeypatch.setattr(forms_net, "_client_call", lambda *a, **k: (_ for _ in ()).throw(ValueError("连不上中央服务")))
    _company(30)
    assert keep.company_days(refresh=True) == 30   # 连不上: 用上次取到的
