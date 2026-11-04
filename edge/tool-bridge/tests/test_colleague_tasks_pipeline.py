"""派任务给同事 · 第三步: 多阶段 (docs/COLLEAGUE-TASK-PLAN.md §3)。假邮筒, 全部虚构, 邮箱都是 example.com。"""
from __future__ import annotations

import json

import pytest

from catfish_tool_bridge import colleague_tasks as ct
from catfish_tool_bridge import colleague_tasks_pipeline as pl
from catfish_tool_bridge import forms_net

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

    def _post(self, to, kind, env, ttl):
        self.box.append({"from": self.who, "to": to, "kind": kind, "env": json.loads(json.dumps(env, ensure_ascii=False))})
        return len(self.box)

    def _take(self, kinds=forms_net.FORM_KINDS):
        mine = [m for m in self.box if m["to"] == self.who and m["kind"] in kinds]
        for m in mine:
            self.box.remove(m)
        return [{"from": m["from"], "kind": m["kind"], "env": m["env"], "created_at": None} for m in mine]


@pytest.fixture()
def w(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


STAGES = [
    {"name": "收集", "text": "把你负责客户本周的拜访记录整理出来", "recipients": [ZHANG, LI]},
    {"name": "分析", "text": "根据大家的拜访记录写一段分析", "recipients": [WANG]},
    {"name": "定稿", "text": "把分析改成周报口径", "recipients": [ZHANG]},
]


def _do(w, who, result):
    w.be(who)
    ct.sync()
    tid = next(t["task_id"] for t in ct.list_all()["incoming"] if t["state"] in ("待处理", "在做"))
    ct.accept(tid)
    ct.submit(tid, result)
    return tid


def test_三段_并行收集_发起人放行_才交给下一段(w):
    w.be(BOSS)
    out = pl.create({"title": "本周客户周报", "stages": STAGES})
    pid = out["pipeline"]["pipeline_id"]
    assert sorted(out["sent"]) == [LI, ZHANG]
    assert not [m for m in w.box if m["to"] == WANG]          # 第二段的人还没收到任何东西
    assert ct.list_all()["outgoing"] == []                    # 每一段不在普通任务列表里
    assert ct.list_all()["pipelines"][0]["current"] == 1

    _do(w, ZHANG, "张三: 拜访甲、乙")
    w.be(BOSS)
    assert not [e for e in ct.sync()["events"] if e["kind"] == "stage_done"]   # 还差李四, 不提醒
    assert not pl.get(pid)["pipeline"]["stages"][0]["finished"]   # 李四还没交
    _do(w, LI, "李四: 拜访丙")
    w.be(BOSS)
    ev = [e for e in ct.sync()["events"] if e["kind"] == "stage_done"]
    assert len(ev) == 1 and ev[0]["period"] == 1
    assert [e for e in ct.sync()["events"] if e["kind"] == "stage_done"] == []   # 只提醒一次
    g = pl.get(pid)
    assert "【zhang@example.com】\n张三: 拜访甲、乙" in g["handoff"] and "李四: 拜访丙" in g["handoff"]
    assert ct.list_all()["pipelines"][0]["waiting_release"] is True

    # 发起人改了再放行: 王五只看到放行的那段话
    r = pl.release(pid, "本周共拜访 3 家: 甲、乙、丙 (已去掉客户电话)")
    assert r["stage"] == 2 and r["sent"] == [WANG]
    w.be(WANG)
    ct.sync()
    rec = ct.load_incoming(ct.list_all()["incoming"][0]["task_id"])
    assert rec["task"]["context"] == "本周共拜访 3 家: 甲、乙、丙 (已去掉客户电话)"
    assert "张三: 拜访甲" not in json.dumps(rec, ensure_ascii=False)
    assert rec["task"]["title"] == "本周客户周报 · 分析"

    _do(w, WANG, "分析: 甲客户有续约意向")
    w.be(BOSS)
    ct.sync()
    pl.release(pid, pl.get(pid)["handoff"])
    _do(w, ZHANG, "周报定稿")
    w.be(BOSS)
    ct.sync()
    done = pl.release(pid, "")
    assert done["done"] and pl.load(pid)["status"] == "done"
    assert ct.list_all()["pipelines"][0]["status"] == "done"


def test_不等了_直接放行_没交的人收到取消(w):
    w.be(BOSS)
    pid = pl.create({"title": "周报", "stages": STAGES})["pipeline"]["pipeline_id"]
    _do(w, ZHANG, "张三的")
    w.be(BOSS)
    ct.sync()
    w.box.clear()
    r = pl.release(pid, "只有张三的")
    assert r["cancelled"] == [LI]
    assert {(m["to"], m["kind"]) for m in w.box} == {(LI, "task_cancel"), (WANG, "task_invite")}


def test_请他重做_只动这一个人(w):
    w.be(BOSS)
    tid = ct.create({"text": "整理拜访", "recipients": [ZHANG, LI]})["task"]["task_id"]
    ct.send(tid)
    for who in (ZHANG, LI):
        w.be(who)
        ct.sync()
        ct.submit(tid, f"{who} 的结果")
    w.be(BOSS)
    ct.sync()
    w.box.clear()
    ct.redo(tid, ZHANG, "少了客户名字, 补上")
    assert [m["to"] for m in w.box] == [ZHANG]
    states = {r["email"]: r["state"] for r in ct.progress(ct.load_outgoing(tid))["rows"]}
    assert states == {ZHANG: "等回应", LI: "已完成"}
    w.be(ZHANG)
    assert [e["kind"] for e in ct.sync()["events"]] == ["changed"]
    rec = ct.load_incoming(tid)
    assert rec["redo_note"] == "少了客户名字, 补上" and not rec.get("done")
    assert rec["draft"]["result"] == "zhang@example.com 的结果"   # 上次交的当草稿, 在上面改
    assert ct.list_all()["incoming"][0]["state"] == "待处理"
    ct.accept(tid)
    ct.submit(tid, "补上了客户名字")
    w.be(BOSS)
    ct.sync()
    states = {r["email"]: r["state"] for r in ct.progress(ct.load_outgoing(tid))["rows"]}
    assert states == {ZHANG: "已完成", LI: "已完成"}
    with pytest.raises(ValueError):
        ct.redo(tid, ZHANG, "")


def test_校验(w):
    w.be(BOSS)
    with pytest.raises(ValueError, match="至少要两段"):
        pl.create({"title": "x", "stages": STAGES[:1]})
    with pytest.raises(ValueError, match="第 2 段要选谁"):
        pl.create({"title": "x", "stages": [STAGES[0], {"text": "分析"}]})
    pid = pl.create({"title": "x", "stages": STAGES})["pipeline"]["pipeline_id"]
    with pytest.raises(ValueError, match="内容是空的"):
        pl.release(pid, "  ")
    pl.cancel(pid)
    assert pl.load(pid)["status"] == "cancelled"
    with pytest.raises(ValueError):
        pl.release(pid, "x")
