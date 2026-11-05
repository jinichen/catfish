"""派任务给同事 · 第二步: 定时 / 周期 (docs/COLLEAGUE-TASK-PLAN.md §2)。

假邮筒 + 假 hermes 定时任务 (create_job / get_job / remove_job)。全部虚构, 邮箱都是 example.com。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from catfish_tool_bridge import colleague_tasks as ct
from catfish_tool_bridge import colleague_tasks_periodic as per
from catfish_tool_bridge import colleague_tasks_schedule as sched
from catfish_tool_bridge import forms, forms_net, forms_tool

BOSS, ZHANG, LI = "zhou@example.com", "zhang@example.com", "li@example.com"


class FakeJobs:
    def __init__(self):
        self.jobs: dict[str, dict] = {}
        self.n = 0

    def create_job(self, prompt, schedule, repeat=None, name=None):
        self.n += 1
        jid = f"job{self.n}"
        self.jobs[jid] = {"id": jid, "prompt": prompt, "schedule": schedule, "repeat": repeat, "name": name,
                          "next_run_at": "2099-01-01T09:00:00+08:00"}
        return self.jobs[jid]

    def get_job(self, jid):
        return self.jobs.get(jid)

    def remove_job(self, jid):
        return self.jobs.pop(jid, None) is not None


class World:
    def __init__(self, tmp_path, monkeypatch):
        self.tmp, self.mp = tmp_path, monkeypatch
        self.box: list[dict] = []
        self.who = ""
        self.hermes: dict[str, FakeJobs] = {}
        monkeypatch.setattr(forms_net, "me", lambda: self.who)
        monkeypatch.setattr(forms_net, "post", self._post)
        monkeypatch.setattr(forms_net, "take", self._take)
        monkeypatch.setattr(sched, "_jobs", lambda: self.hermes.setdefault(self.who, FakeJobs()))

    def be(self, email):
        self.who = email
        self.mp.setenv("CATFISH_HOME", str(self.tmp / email.split("@")[0]))

    def jobs(self, email):
        return self.hermes.setdefault(email, FakeJobs()).jobs

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


WEEKLY = {"kind": "cron", "expr": "0 9 * * 1", "times": 3}


def _send(w, schedule, recipients=(ZHANG, LI)):
    w.be(BOSS)
    tid = ct.create({"text": "每周一把上周客户拜访整理成三行", "recipients": list(recipients), "schedule": schedule})["task"]["task_id"]
    ct.send(tid)
    return tid


def _owner_states(w, tid):
    w.be(BOSS)
    ct.sync()
    return {r["email"]: r["state"] for r in ct.progress(ct.load_outgoing(tid))["rows"]}


def test_周期_接下建定时_每期核对交回_做满收尾(w):
    tid = _send(w, WEEKLY)
    w.be(ZHANG)
    ct.sync()
    rec = ct.load_incoming(tid)
    assert rec["task"]["schedule"] == {"kind": "cron", "expr": "0 9 * * 1", "until": None, "times": 3}
    assert ct.list_all()["incoming"][0]["schedule"] == "每周一 09:00, 最多 3 次"
    r = ct.accept(tid)
    job = w.jobs(ZHANG)[r["job_id"]]
    assert job["schedule"] == "0 9 * * 1" and job["repeat"] == 3 and f"task_id={tid}" in job["prompt"]
    assert "不要发邮件、发消息、派任务、删东西" in job["prompt"] and "<<<\n每周一把" in job["prompt"]
    assert _owner_states(w, tid) == {ZHANG: "已安排", LI: "等回应"}

    w.be(ZHANG)   # 第一期: 小鲶到点做完存结果, 默认等本人核对
    out = forms_tool.dispatch("catfish_form", {"action": "task_period", "task_id": tid, "result": "拜访 2 家"})
    assert out["ok"] and out["state"] == "待核对" and out["period"] == 1
    assert not [m for m in w.box if m["kind"] == "task_reply" and m["env"]["status"] == "period"]
    ev = ct.sync()["events"]
    assert [e["kind"] for e in ev] == ["review"] and ev[0]["period"] == 1
    assert ct.sync()["events"] == []          # 只提醒一次
    assert ct.list_all()["incoming"][0]["state"] == "待核对"
    per.submit_period(tid, 1, "拜访 2 家: 甲 / 乙")
    assert ct.list_all()["incoming"][0]["state"] == "进行中"

    assert _owner_states(w, tid)[ZHANG] == "进行中"
    res = ct.results(tid)
    zhang = next(p for p in res["people"] if p["同事"] == ZHANG)
    assert zhang["每期结果"][0]["结果"] == "拜访 2 家: 甲 / 乙" and res["schedule"] == "每周一 09:00, 最多 3 次"

    w.be(ZHANG)
    per.record_period(tid, "第 2 期")
    per.record_period(tid, "第 3 期")
    assert w.jobs(ZHANG) == {}                # 做满 3 次, 本机定时任务删掉
    with pytest.raises(ValueError):
        per.record_period(tid, "第 4 期")
    for n in (2, 3):
        per.submit_period(tid, n, None)
    assert ct.list_all()["incoming"][0]["state"] == "已结束"
    assert _owner_states(w, tid)[ZHANG] == "已结束"


def test_每期直接交回(w):
    tid = _send(w, WEEKLY, recipients=(ZHANG,))
    w.be(ZHANG)
    ct.sync()
    ct.accept(tid, auto_return=True)
    per.record_period(tid, "拜访 1 家")
    assert any(m["env"].get("status") == "period" for m in w.box)
    assert ct.sync()["events"] == []          # 自动交回的不用提醒核对
    w.be(BOSS)
    ev = ct.sync()["events"]
    assert [e["kind"] for e in ev] == ["accepted", "period"] and ev[1]["period"] == 1


def test_一次性_到点电脑没开_补跑一次_还不行就告诉本人(w):
    at = (datetime.now() + timedelta(hours=1)).isoformat(timespec="minutes")
    tid = _send(w, {"kind": "once", "at": at}, recipients=(ZHANG,))
    w.be(ZHANG)
    ct.sync()
    jid = ct.accept(tid)["job_id"]
    assert w.jobs(ZHANG)[jid]["schedule"] == at and w.jobs(ZHANG)[jid]["repeat"] == 1
    # 关机错过: hermes 把过期的一次性任务直接丢了; 时间往回拨到 3 小时前
    w.jobs(ZHANG).clear()
    rec = ct.load_incoming(tid)
    rec["task"]["schedule"]["at"] = (datetime.now() - timedelta(hours=3)).isoformat(timespec="minutes")
    forms._write(ct._in_path(tid), rec)
    assert ct.sync()["events"] == []
    assert len(w.jobs(ZHANG)) == 1            # 补了一个 1 分钟后的
    w.jobs(ZHANG).clear()                     # 补的也没做成
    ev = ct.sync()["events"]
    assert [e["kind"] for e in ev] == ["missed"] and ct.list_all()["incoming"][0]["state"] == "没做成"
    assert ct.sync()["events"] == []          # 不重复告诉
    # 本人点「现在让小鲶做」后小鲶存了结果: 标晚了多久
    out = per.record_period(tid, "补做了")
    assert out["late_minutes"] >= 170
    per.submit_period(tid, 1, None)
    assert ct.list_all()["incoming"][0]["state"] == "已结束"
    assert _owner_states(w, tid) == {ZHANG: "已完成"}


def test_发起人改了_要重新同意_定时先停(w):
    tid = _send(w, WEEKLY, recipients=(ZHANG,))
    w.be(ZHANG)
    ct.sync()
    ct.accept(tid)
    assert len(w.jobs(ZHANG)) == 1
    w.be(BOSS)
    ct.sync()
    until = (datetime.now() + timedelta(days=60)).date().isoformat()
    up = ct.update(tid, {"schedule": {"kind": "cron", "expr": "0 17 * * 5", "until": until}})
    assert up["sent"] == [ZHANG]
    assert _owner_states(w, tid)[ZHANG] == "等重新同意"
    w.be(ZHANG)
    ev = ct.sync()["events"]
    assert [e["kind"] for e in ev] == ["changed"]
    assert w.jobs(ZHANG) == {} and ct.list_all()["incoming"][0]["state"] == "要重新同意"
    ct.accept(tid)
    assert next(iter(w.jobs(ZHANG).values()))["schedule"] == "0 17 * * 5"
    assert _owner_states(w, tid)[ZHANG] == "已安排"


def test_退出_和_取消_都删本机定时(w):
    tid = _send(w, WEEKLY)
    for who in (ZHANG, LI):
        w.be(who)
        ct.sync()
        ct.accept(tid)
    w.be(ZHANG)
    per.quit(tid, "调岗了")
    assert w.jobs(ZHANG) == {} and ct.list_all()["incoming"][0]["state"] == "已退出"
    assert _owner_states(w, tid) == {ZHANG: "已退出", LI: "已安排"}
    w.box.clear()
    ct.cancel(tid)
    assert {m["to"] for m in w.box} == {LI}   # 退出的不再通知
    w.be(LI)
    ct.sync()
    assert w.jobs(LI) == {} and ct.list_all()["incoming"][0]["state"] == "已取消"
    with pytest.raises(ValueError):
        per.record_period(tid, "还在跑?")


def test_定时任务被删了_告诉本人_重新接下就补上(w):
    tid = _send(w, WEEKLY, recipients=(ZHANG,))
    w.be(ZHANG)
    ct.sync()
    ct.accept(tid)
    w.jobs(ZHANG).clear()                     # 员工在「定时任务」里手动删了
    assert [e["kind"] for e in ct.sync()["events"]] == ["job_lost"]
    assert ct.list_all()["incoming"][0]["state"] == "定时没了"
    ct.accept(tid)
    assert len(w.jobs(ZHANG)) == 1 and ct.list_all()["incoming"][0]["state"] == "已安排"


def test_校验_必须有结束_时间不能已过():
    now = datetime(2026, 11, 4, 10, 0)
    with pytest.raises(ValueError, match="结束"):
        sched.normalize({"kind": "cron", "expr": "0 9 * * 1"}, now=now)
    with pytest.raises(ValueError, match="已经过了"):
        sched.normalize({"kind": "once", "at": "2026-11-03T09:00"}, now=now)
    with pytest.raises(ValueError):
        sched.normalize({"kind": "cron", "expr": "0 9 * * 1; rm -rf ~", "times": 2}, now=now)
    with pytest.raises(ValueError):
        sched.normalize({"kind": "cron", "expr": "0 9 * * 1", "times": 500}, now=now)
    assert sched.normalize({"kind": "once", "at": "2026-11-03T09:00"}, now=now, strict=False)["at"] == "2026-11-03T09:00"
    assert sched.describe({"kind": "cron", "expr": "30 8 * * 1-5", "until": "2026-12-31", "times": None}) == \
        "工作日 08:30, 到 2026-12-31 为止"
    assert sched.describe(None) == "马上做"


def test_立即任务调task_period当草稿存(w):
    w.be(BOSS)
    tid = ct.create({"text": "整理拜访", "recipients": [ZHANG]})["task"]["task_id"]
    ct.send(tid)
    w.be(ZHANG)
    ct.sync()
    assert per.record_period(tid, "草稿")["ok"]
    assert ct.load_incoming(tid)["draft"]["result"] == "草稿"


def test_要确认的步骤没做_待确认_回来接着做(w):
    tid = _send(w, WEEKLY, recipients=(ZHANG,))
    w.be(ZHANG)
    ct.sync()
    jid = ct.accept(tid, auto_return=True)["job_id"]
    assert "skipped" in w.jobs(ZHANG)[jid]["prompt"]
    w.box.clear()
    # 到点无人值守: 汇总做好了, 发邮件那步要人确认没做 → 不交回 (即使勾了直接交回)
    out = forms_tool.dispatch("catfish_form", {"action": "task_period", "task_id": tid, "result": "汇总: 拜访 2 家",
                                               "skipped": ["把汇总发给销售部邮件组"]})
    assert out["state"] == "待确认" and out["skipped"] == ["把汇总发给销售部邮件组"]
    assert [m["env"]["status"] for m in w.box] == ["waiting"]
    ev = ct.sync()["events"]
    assert [e["kind"] for e in ev] == ["confirm_needed"] and ev[0]["period"] == 1
    assert ct.list_all()["incoming"][0]["state"] == "待确认" and ct.list_all()["incoming"][0]["to_confirm"] == 1

    assert _owner_states(w, tid)[ZHANG] == "等对方确认"
    zhang = next(p for p in ct.results(tid)["people"] if p["同事"] == ZHANG)
    assert zhang["每期结果"][0]["还差几步等对方确认"] == 1

    # 本人回来点「我在了, 接着做」: 小鲶带 period 补完, 不新开一期; 勾了直接交回 → 这回发出去
    w.be(ZHANG)
    out = forms_tool.dispatch("catfish_form", {"action": "task_period", "task_id": tid, "period": 1,
                                               "result": "汇总: 拜访 2 家 (已发给销售部邮件组)"})
    rec = ct.load_incoming(tid)
    assert out["state"] == "已交回" and len(rec["periods"]) == 1 and rec["periods"][0]["skipped"] == []
    assert _owner_states(w, tid)[ZHANG] == "进行中"
    w.be(BOSS)
    zhang = next(p for p in ct.results(tid)["people"] if p["同事"] == ZHANG)
    assert zhang["每期结果"][0]["还差几步等对方确认"] == 0 and "已发给" in zhang["每期结果"][0]["结果"]


def test_待确认的也可以不补_直接交回(w):
    tid = _send(w, WEEKLY, recipients=(ZHANG,))
    w.be(ZHANG)
    ct.sync()
    ct.accept(tid)
    per.record_period(tid, "汇总好了", skipped=["发邮件"])
    per.submit_period(tid, 1, "汇总好了 (邮件我自己发)")
    assert ct.list_all()["incoming"][0]["state"] == "进行中"
    assert _owner_states(w, tid)[ZHANG] == "进行中"


# ── 11/5: 一次性任务接下就让小鲶开工 ───────────────────────────────

def test_一次性任务_接下就让小鲶在后台开工_做好了通知一次_待核对(w):
    tid = _send(w, None, (ZHANG,))
    w.be(ZHANG)
    ct.sync()
    out = ct.accept(tid)
    assert out["started"] is True
    job = list(w.jobs(ZHANG).values())[0]
    assert job["repeat"] == 1 and "无人值守" in job["prompt"] and f"task_id={tid}" in job["prompt"]
    assert "每周一把上周客户拜访整理成三行" in job["prompt"]
    assert ct.list_all()["incoming"][0]["state"] == "在做"
    ct.accept(tid)                                   # 重复接下不重复开工
    assert len(w.jobs(ZHANG)) == 1

    per.record_period(tid, "拜访 3 家", skipped=["把汇总发给周总"])   # 小鲶跑完存草稿 (一次性任务没有「期」)
    rec = ct.load_incoming(tid)
    assert rec["draft"]["result"].startswith("拜访 3 家") and "1. 把汇总发给周总" in rec["draft"]["result"]
    assert not rec.get("done")                       # 不自己交回
    ev = [e for e in ct.sync()["events"] if e["kind"] == "drafted"]
    assert [e["title"] for e in ev] == ["每周一把上周客户拜访整理成三行"]
    assert [e for e in ct.sync()["events"] if e["kind"] == "drafted"] == []   # 只通知一次
    assert ct.list_all()["incoming"][0]["state"] == "待核对"


def test_接下就开工_超时没出来_提醒一次_hermes起不来不影响接下(w, monkeypatch):
    tid = _send(w, None, (ZHANG,))
    w.be(ZHANG)
    ct.sync()
    ct.accept(tid)
    rec = ct.load_incoming(tid)
    rec["auto_started_at"] = (datetime.now() - timedelta(minutes=ct.START_TIMEOUT_MIN + 5)).isoformat(timespec="seconds")
    forms._write(ct._in_path(tid), rec)
    w.jobs(ZHANG).clear()                            # hermes 跑完删掉了 (或丢了), 没存草稿
    assert [e["kind"] for e in ct.sync()["events"] if e["kind"] == "start_failed"] == ["start_failed"]
    assert [e for e in ct.sync()["events"] if e["kind"] == "start_failed"] == []

    tid2 = _send(w, None, (LI,))
    w.be(LI)
    ct.sync()
    def boom():
        raise RuntimeError("hermes 没装")
    monkeypatch.setattr(sched, "_jobs", boom)
    out = ct.accept(tid2)
    assert out["started"] is False and "让小鲶做" in out["start_error"]
    assert ct.load_incoming(tid2)["accepted_at"]
    assert ct.accept(tid2, start=False)["started"] is False


def test_界面接下_start_false_不开工(w):
    tid = _send(w, None, (ZHANG,))
    w.be(ZHANG)
    ct.sync()
    ct.pim_handlers({"task_id": tid, "start": False})["pim/ctask_accept"]()
    assert w.jobs(ZHANG) == {}
