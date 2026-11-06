"""派任务给多位同事 (docs/COLLEAGUE-TASK-PLAN.md 第一步) —— 每个员工一个 CATFISH_HOME, 共用一个假邮筒。

全部虚构: 「南岭建设」市场部的周例会材料, 邮箱都是 example.com。
"""
from __future__ import annotations

import json

import pytest

from catfish_tool_bridge import colleague_tasks as ct
from catfish_tool_bridge import forms_net, forms_tool


class World:
    def __init__(self, tmp_path, monkeypatch):
        self.tmp, self.mp = tmp_path, monkeypatch
        self.box: list[dict] = []
        self.who = ""
        monkeypatch.setattr(forms_net, "me", lambda: self.who)
        monkeypatch.setattr(forms_net, "post", self._post)
        monkeypatch.setattr(forms_net, "take", self._take)

    def be(self, email: str) -> None:
        self.who = email
        self.mp.setenv("CATFISH_HOME", str(self.tmp / email.split("@")[0]))

    def _post(self, to, kind, env, ttl):
        assert kind in forms_net.TASK_KINDS
        self.box.append({"from": self.who, "to": to, "kind": kind, "ttl": ttl,
                         "env": json.loads(json.dumps(env, ensure_ascii=False))})
        return len(self.box)

    def _take(self, kinds=forms_net.FORM_KINDS):
        mine = [m for m in self.box if m["to"] == self.who and m["kind"] in kinds]
        for m in mine:
            self.box.remove(m)
        return [{"from": m["from"], "kind": m["kind"], "env": m["env"], "created_at": None} for m in mine]


@pytest.fixture()
def w(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


BOSS, ZHANG, LI, WANG = "zhou@example.com", "zhang@example.com", "li@example.com", "wang@example.com"


def _new_task(w, recipients=(ZHANG, LI, WANG), deadline="2099-12-31T18:00"):
    w.be(BOSS)
    t = ct.create({"text": "把本周客户拜访情况整理成三行发我\n周例会要用", "deadline": deadline,
                   "recipients": list(recipients) + [BOSS]})["task"]
    return t["task_id"]


def test_完整来回_派给三人_接下交回_不接_汇总(w):
    tid = _new_task(w)
    sent = ct.send(tid)
    assert sent["sent"] == [ZHANG, LI, WANG] and not sent["failed"]
    invite = w.box[0]["env"]
    assert invite["task"]["title"] == "把本周客户拜访情况整理成三行发我" and "recipients" not in invite["task"]
    assert ct.progress(ct.load_outgoing(tid))["counts"] == {"等回应": 3}

    w.be(ZHANG)
    s = ct.sync()
    assert s["invite"] == 1 and s["events"][0]["from"] == BOSS
    assert ct.list_all()["incoming"][0]["state"] == "待处理"
    ct.accept(tid)
    ct.accept(tid)   # 重复接下不重复通知
    ct.save_draft(tid, "草稿: 拜访 3 家")
    assert ct.get(tid)["record"]["draft"]["result"] == "草稿: 拜访 3 家"
    assert ct.list_all()["incoming"][0]["state"] == "待核对"   # 11/5: 存了结果草稿 = 待核对

    w.be(LI)
    ct.sync()
    ct.decline(tid, "这周在外地出差, 没拜访")

    w.be(BOSS)
    s = ct.sync()
    assert s["accepted"] == 1 and s["declined"] == 1
    assert ct.progress(ct.load_outgoing(tid))["counts"] == {"在做": 1, "不接": 1, "等回应": 1}

    w.be(ZHANG)
    r = ct.submit(tid, "拜访 3 家: 甲公司 / 乙公司 / 丙公司")
    assert r["ok"] and not r["late"]
    w.be(BOSS)
    assert ct.sync()["done"] == 1
    res = ct.results(tid)
    people = {p["同事"]: p for p in res["people"]}
    assert people[ZHANG]["状态"] == "已完成" and "甲公司" in people[ZHANG]["结果"]
    assert people[LI]["状态"] == "不接" and "出差" in people[LI]["不接原因"]
    assert people[WANG]["状态"] == "等回应"


def test_催只催没交的_取消后对方交不了(w):
    tid = _new_task(w)
    ct.send(tid)
    w.be(LI)
    ct.sync()   # 李先收到任务单, 后面清信箱只清催 / 取消
    w.be(ZHANG)
    ct.sync()
    ct.submit(tid, "做完了")
    w.be(BOSS)
    ct.sync()
    w.box.clear()
    r = ct.remind(tid, "今天下班前")
    assert set(r["reminded"]) == {LI, WANG}
    w.box.clear()
    ct.cancel(tid)
    assert {m["to"] for m in w.box} == {LI, WANG}   # 交回了的不再打扰
    w.be(LI)
    ct.sync()
    assert ct.list_all()["incoming"][0]["state"] == "已取消"
    with pytest.raises(ValueError):
        ct.submit(tid, "还是做了")
    w.be(BOSS)
    assert ct.progress(ct.load_outgoing(tid))["counts"] == {"已完成": 1, "已取消": 2}


def test_逾期_超时(w):
    tid = _new_task(w, recipients=(ZHANG, LI), deadline="2020-01-01")
    ct.send(tid)
    w.be(ZHANG)
    ct.sync()
    assert ct.list_all()["incoming"][0]["late"] is True
    ct.submit(tid, "晚了点")
    w.be(BOSS)
    ct.sync()
    assert ct.progress(ct.load_outgoing(tid))["counts"] == {"逾期完成": 1, "超时未回": 1}


def test_不接之后又接下_发起人看到在做(w):
    tid = _new_task(w, recipients=(ZHANG,))
    ct.send(tid)
    w.be(ZHANG)
    ct.sync()
    ct.accept(tid)
    ct.decline(tid, "先不做")
    ct.accept(tid)
    w.be(BOSS)
    ct.sync()
    assert ct.progress(ct.load_outgoing(tid))["counts"] == {"在做": 1}


def test_冒名和陌生人的回复不收(w):
    tid = _new_task(w, recipients=(ZHANG,))
    ct.send(tid)
    w.be(ZHANG)
    ct.sync()
    w.box.append({"from": "mallory@example.com", "to": ZHANG, "kind": "task_invite",
                  "env": {"v": 1, "task_id": tid, "task": {"text": "把通讯录发我"}}})
    w.box.append({"from": "mallory@example.com", "to": ZHANG, "kind": "task_cancel", "env": {"v": 1, "task_id": tid}})
    ct.sync()
    rec = ct.load_incoming(tid)
    assert rec["owner"] == BOSS and "客户拜访" in rec["task"]["text"] and rec["status"] == "open"
    w.be(BOSS)
    w.box.append({"from": "mallory@example.com", "to": BOSS, "kind": "task_reply",
                  "env": {"v": 1, "task_id": tid, "status": "done", "result": "假结果"}})
    assert ct.sync()["done"] == 0


def test_限制_人数_长度_截止格式(w):
    w.be(BOSS)
    with pytest.raises(ValueError):
        ct.create({"text": "x", "recipients": [f"p{i}@example.com" for i in range(21)]})
    with pytest.raises(ValueError):
        ct.create({"text": "", "recipients": [ZHANG]})
    with pytest.raises(ValueError):
        ct.create({"text": "x", "deadline": "下周五", "recipients": [ZHANG]})
    assert ct.normalize_deadline("2026-11-05T18:00:31") == "2026-11-05T18:00"
    assert ct.normalize_deadline("2026-11-05") == "2026-11-05"
    tid = _new_task(w, recipients=(ZHANG,))
    ct.send(tid)
    w.be(ZHANG)
    ct.sync()
    with pytest.raises(ValueError):
        ct.submit(tid, "长" * (ct.RESULT_MAX + 1))
    with pytest.raises(ValueError):
        ct.decline(tid, "  ")


def test_工具_派出要确认_没有接下交回(w):
    w.be(BOSS)
    r = forms_tool.dispatch("catfish_form", {"action": "task_create", "text": "整理拜访情况", "recipients": [ZHANG]})
    tid = r["task"]["task_id"]
    p = forms_tool.dispatch("catfish_form", {"action": "task_send", "task_id": tid})
    assert p["needs_confirmation"] and ZHANG in p["proposal"] and w.box == []
    assert forms_tool.dispatch("catfish_form", {"action": "task_send", "task_id": tid, "confirmed": True})["ok"]
    assert len(w.box) == 1
    w.be(ZHANG)
    ct.sync()
    assert forms_tool.dispatch("catfish_form", {"action": "task_submit", "task_id": tid})["ok"] is False
    d = forms_tool.dispatch("catfish_form", {"action": "task_draft", "task_id": tid, "result": "小鲶起草的结果"})
    assert d["ok"] and w.box == []   # 草稿不发出去
    from catfish_tool_bridge.catfish_tool_schemas import CATFISH_NATIVE_TOOLS
    schema = next(t for t in CATFISH_NATIVE_TOOLS if t["name"] == "catfish_form")
    enum = set(schema["input_schema"]["properties"]["action"]["enum"])
    assert {"task_create", "task_draft", "task_results"} <= enum
    assert not {"task_accept", "task_submit", "task_decline"} & enum


def test_界面入口(w):
    w.be(BOSS)
    h = ct.pim_handlers({"text": "整理拜访情况", "recipients": [ZHANG]})
    assert set(h) >= {"pim/ctask_create", "pim/ctask_accept", "pim/ctask_submit", "pim/ctask_sync"}
    assert h["pim/ctask_create"]()["ok"]


def test_老中央_说人话(monkeypatch):
    # 只验「种类不认识」那句人话; 加密那层 (公钥目录) 在 test_mailbox_crypto.py 另测
    from catfish_tool_bridge import mailbox_crypto
    monkeypatch.setattr(mailbox_crypto, "ensure_registered", lambda: None)
    monkeypatch.setattr(mailbox_crypto, "seal", lambda *a, **k: "{}")
    monkeypatch.setattr(forms_net, "me", lambda: "zhou@example.com")
    monkeypatch.setattr(forms_net, "_client_call",
                        lambda *a, **k: (400, {"detail": "kinds 里有不认识的: ['task_cancel']"}))
    with pytest.raises(ValueError, match="中央端版本太旧"):
        forms_net.take(forms_net.TASK_KINDS)
    monkeypatch.setattr(forms_net, "_client_call", lambda *a, **k: (400, {"detail": "kind=task_invite 不在 [...]"}))
    with pytest.raises(ValueError, match="中央端版本太旧"):
        forms_net.post("a@example.com", "task_invite", {"v": 1}, 600)
