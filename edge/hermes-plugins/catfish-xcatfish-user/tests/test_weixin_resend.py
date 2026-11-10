"""微信补发 (weixin_resend.py): 推送被拒时留内容, 员工在微信里说话时补发。

hermes 的 cron 模块用桩代替; 装到真 hermes 上真调一遍的在 test_weixin_resend_on_real_hermes.py。
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import time
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent
NOT_READY = ("live adapter send failed: iLink sendmessage session not ready: ret=-2 errcode=None errmsg= "
             "— the user must send the bot a message first (or re-pair)")
CHAT = "wx-user-1"


def _iso(dt: datetime) -> str:
    return dt.astimezone().isoformat()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path / "catfish"))
    jobs: list[dict] = []
    sent: list[tuple[dict, str, bool]] = []
    replies: list = []  # 依次返回的投递结果; 空了就成功
    updates: list[tuple[str, dict]] = []
    out_dir = tmp_path / "output"

    def deliver(job, content, adapters=None, loop=None, *, for_failure=False):
        sent.append((job, content, for_failure))
        return replies.pop(0) if replies else None

    def resolve_targets(job):
        d = str(job.get("deliver") or "")
        out = []
        for part in d.split(","):
            part = part.strip()
            if part == "weixin" or part == "origin":
                out.append({"platform": "weixin", "chat_id": CHAT})
            elif part.startswith("weixin:"):
                out.append({"platform": "weixin", "chat_id": part.split(":", 1)[1]})
            elif part.startswith("telegram"):
                out.append({"platform": "telegram", "chat_id": "tg"})
        return out

    def archive_answer(text):
        return text.rpartition("## Response")[2].strip() or None

    def update_job(job_id, upd):
        updates.append((job_id, upd))
        for j in jobs:
            if j["id"] == job_id:
                j.update(upd)

    cron = types.ModuleType("cron")
    sched = types.ModuleType("cron.scheduler")
    sched._deliver_result = deliver
    cj = types.ModuleType("cron.jobs")
    cj.load_jobs = lambda: [dict(j) for j in jobs]
    cj.update_job = update_job
    cj.get_cron_output_dir = lambda: out_dir
    sd = types.ModuleType("cron.scheduler_delivery")
    sd._resolve_delivery_targets = resolve_targets
    sp = types.ModuleType("cron.scheduler_prompt")
    sp._archive_answer = archive_answer
    cron.scheduler, cron.jobs, cron.scheduler_delivery, cron.scheduler_prompt = sched, cj, sd, sp
    for name, mod in {"cron": cron, "cron.scheduler": sched, "cron.jobs": cj,
                      "cron.scheduler_delivery": sd, "cron.scheduler_prompt": sp}.items():
        monkeypatch.setitem(sys.modules, name, mod)

    spec = importlib.util.spec_from_file_location("_wr_under_test", PLUGIN_DIR / "weixin_resend.py")
    wr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wr)
    assert wr.install()
    return types.SimpleNamespace(wr=wr, sched=sched, jobs=jobs, sent=sent, replies=replies,
                                 updates=updates, out_dir=out_dir, store=tmp_path / "catfish" / "weixin_resend.json")


def _job(job_id="j1", name="AI六巨头日报", deliver="weixin", kind="cron", ran=None, next_run=None, err=NOT_READY):
    ran = ran or datetime.now()
    return {"id": job_id, "name": name, "deliver": deliver, "schedule": {"kind": kind},
            "last_run_at": _iso(ran), "next_run_at": _iso(next_run or ran + timedelta(days=1)),
            "last_status": "delivery_failed" if err else "ok", "last_delivery_error": err}


def _fail_once(env, job, content="今日日报正文"):
    env.jobs.append(job)
    env.replies.append(NOT_READY)
    assert env.sched._deliver_result(job, content, None, None) == NOT_READY
    env.sent.clear()


def test_会话过期的投递内容被留下_别的失败不留(env):
    _fail_once(env, _job())
    pending = json.loads(env.store.read_text())["pending"]
    assert pending["j1"]["content"] == "今日日报正文"

    env.replies.append("telegram: rate limited")
    env.sched._deliver_result(_job("j2", err="x"), "别的", None, None)
    assert "j2" not in json.loads(env.store.read_text())["pending"]


def test_同一任务之后投递成功_旧的待补发作废(env):
    _fail_once(env, _job())
    env.sched._deliver_result(_job(), "新一期", None, None)
    assert json.loads(env.store.read_text())["pending"] == {}


def test_员工在微信里说话_补发到这个微信号_带补发说明_清掉没送达状态(env):
    _fail_once(env, _job())
    done = env.wr.resend_for_chat(CHAT, adapters={"x": 1}, loop=None)
    assert done == ["j1"]
    job, content, _ = env.sent[0]
    assert job["deliver"] == f"weixin:{CHAT}"
    assert content.startswith("【补发】这条原定 ") and content.endswith("今日日报正文")
    assert env.updates == [("j1", {"last_delivery_error": None, "last_status": "ok"})]
    data = json.loads(env.store.read_text())
    assert data["pending"] == {}
    # 再说一句话不会再补一遍
    assert env.wr.resend_for_chat(CHAT, adapters={"x": 1}, loop=None) == []
    assert len(env.sent) == 1


def test_别人的微信号说话不补(env):
    _fail_once(env, _job())
    assert env.wr.resend_for_chat("someone-else", adapters={"x": 1}, loop=None) == []
    assert env.sent == []
    assert "j1" in json.loads(env.store.read_text())["pending"]


def test_多平台任务只补微信那一路(env):
    _fail_once(env, _job(deliver="telegram,weixin"))
    env.wr.resend_for_chat(CHAT, adapters={"x": 1}, loop=None)
    job, _, _ = env.sent[0]
    assert job["deliver"] == f"weixin:{CHAT}" and "failure_deliver" not in job


def test_补发又被拒_留着等下一句话(env):
    _fail_once(env, _job())
    env.replies.append(NOT_READY)
    assert env.wr.resend_for_chat(CHAT, adapters={"x": 1}, loop=None) == []
    assert "j1" in json.loads(env.store.read_text())["pending"]
    assert env.wr.resend_for_chat(CHAT, adapters={"x": 1}, loop=None) == ["j1"]


def test_别的失败补三次就放弃(env):
    _fail_once(env, _job())
    for _ in range(3):
        env.replies.append("network down")
        env.wr.resend_for_chat(CHAT, adapters={"x": 1}, loop=None)
    assert json.loads(env.store.read_text())["pending"] == {}
    assert len(env.sent) == 3


def test_过期不补_一次性任务1小时_周期任务24小时或下一次已经跑过(env):
    now = datetime.now()
    _fail_once(env, _job("once", kind="once", ran=now - timedelta(hours=2)))
    _fail_once(env, _job("day", ran=now - timedelta(hours=25), next_run=now + timedelta(hours=1)))
    _fail_once(env, _job("superseded", ran=now - timedelta(hours=3), next_run=now - timedelta(minutes=5)))
    _fail_once(env, _job("fresh", ran=now - timedelta(hours=3)))
    assert env.wr.resend_for_chat(CHAT, adapters={"x": 1}, loop=None) == ["fresh"]
    assert set(json.loads(env.store.read_text())["pending"]) == set()


def test_任务删了不补(env):
    _fail_once(env, _job())
    env.jobs.clear()
    assert env.wr.resend_for_chat(CHAT, adapters={"x": 1}, loop=None) == []


def test_升级前就失败的_从运行记录取回答补发_只补一次(env):
    job = _job("old")
    env.jobs.append(job)  # 没经过包装: 存储里没有它
    d = env.out_dir / "old"
    d.mkdir(parents=True)
    f = d / "2026-11-10_09-36-45.md"
    f.write_text("# Cron Job: x\n\n## Prompt\n\n问题\n\n## Response\n\n存档里的日报\n", encoding="utf-8")
    ts = datetime.fromisoformat(job["last_run_at"]).timestamp()
    import os
    os.utime(f, (ts, ts))
    assert env.wr.resend_for_chat(CHAT, adapters={"x": 1}, loop=None) == ["old"]
    assert env.sent[0][1].endswith("存档里的日报")
    # update_job 写失败的情况下 (状态还挂着) 也不会重复补: sent 里记着这次运行
    for j in env.jobs:
        j["last_delivery_error"] = NOT_READY
    assert env.wr.resend_for_chat(CHAT, adapters={"x": 1}, loop=None) == []


def test_脚本任务的记录不拿来补(env):
    job = _job("script")
    env.jobs.append(job)
    d = env.out_dir / "script"
    d.mkdir(parents=True)
    f = d / "a.md"
    f.write_text("# Cron Job: x\n\n**Mode:** no_agent\n\n输出\n", encoding="utf-8")
    assert env.wr.resend_for_chat(CHAT, adapters={"x": 1}, loop=None) == []


def test_钩子_微信消息才触发_后台补发_照常分发(env):
    _fail_once(env, _job())
    Platform = types.SimpleNamespace

    async def run(platform):
        ev = types.SimpleNamespace(source=types.SimpleNamespace(platform=Platform(value=platform), chat_id=CHAT))
        gw = types.SimpleNamespace(adapters={"weixin": object()})
        r = await env.wr.pre_gateway_dispatch_hook(event=ev, gateway=gw, session_store=None)
        assert r is None
        for _ in range(50):
            if not env.wr._tasks:
                break
            await asyncio.sleep(0.02)

    asyncio.run(run("telegram"))
    assert env.sent == []
    asyncio.run(run("weixin"))
    assert len(env.sent) == 1


def test_包装只装一次(env):
    first = env.sched._deliver_result
    assert env.wr.install()
    assert env.sched._deliver_result is first


# ── tool-bridge 直连推送 (Windows 提醒 / 工作流推送) ─────────────────────

class _FakeAdapter:
    def __init__(self, results):
        self.results, self.got = list(results), []

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        self.got.append((chat_id, content))
        r = self.results.pop(0) if self.results else "ok"
        return types.SimpleNamespace(success=r == "ok", error=None if r == "ok" else r)


def _direct(env, items):
    p = env.store.parent / "weixin_resend_direct.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items), encoding="utf-8")
    return p


def _run_with_loop(fn):
    import threading
    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    try:
        return fn(loop)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        t.join(2)


def test_直连推送_补发这个微信号的_过期的丢_别人的留着(env):
    now = time.time()
    p = _direct(env, [
        {"text": "⏰ 小鲶提醒: 交月报", "chat_id": CHAT, "kind": "reminder", "saved_at": now - 600},
        {"text": "⏰ 小鲶提醒: 早过了", "chat_id": CHAT, "kind": "reminder", "saved_at": now - 7200},
        {"text": "工作流结果", "chat_id": CHAT, "kind": "message", "saved_at": now - 7200},
        {"text": "别人的", "chat_id": "other", "kind": "message", "saved_at": now - 60},
        "坏行",
    ])
    ad = _FakeAdapter([])
    _run_with_loop(lambda loop: env.wr.resend_for_chat(CHAT, {"weixin": ad}, loop))
    assert [c for _, c in ad.got] and all(c.startswith("【补发】") for _, c in ad.got)
    assert sorted(c.split("\n\n", 1)[1] for _, c in ad.got) == ["⏰ 小鲶提醒: 交月报", "工作流结果"]
    left = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]
    assert [i["text"] for i in left] == ["别人的"]
    assert not list(p.parent.glob(".*.claim"))


def test_直连推送_又被拒留着_别的错三次放弃(env):
    p = _direct(env, [{"text": "a", "chat_id": CHAT, "kind": "message", "saved_at": time.time()}])
    ad = _FakeAdapter([NOT_READY, "boom", "boom", "boom"])
    for _ in range(4):
        _run_with_loop(lambda loop: env.wr.resend_for_chat(CHAT, {"weixin": ad}, loop))
    assert len(ad.got) == 4
    assert not p.exists() or p.read_text(encoding="utf-8").strip() == ""
