"""派任务给同事 · 第二步: 定时 / 周期 (11/4, docs/COLLEAGUE-TASK-PLAN.md §2)。

任务单带 schedule:
  {"kind": "once", "at": "2026-11-05T09:00"}                                  定时做一次
  {"kind": "cron", "expr": "0 9 * * 1", "until": "2026-12-31", "times": 8}    周期; until / times 至少一个 (必须有结束条件)

同事接下后, 在**他电脑上**建一个 hermes 定时任务 (cron.jobs.create_job, 跟 `hermes cron create` 同一个入口)。
到点小鲶无人值守地做一次, 用 catfish_form task_period 把这一期结果存下: 同事选了「每期直接交回」就马上发回,
否则等他核对后点「交回」。

hermes 自己管不到的, 每次同步 (Companion 20 秒一次) 在这里补:
  · 一次性任务到点电脑没开: hermes 超过 2 分钟就直接丢掉不跑 (11/4 实测) → 补建一个 1 分钟后的,
    只补一次, 结果标「晚了多久」; 补了还没做成就告诉同事, 他可以点「现在让小鲶做」。
  · 周期任务过了结束日 / 做满次数 / 被取消 / 同事退出 → 删掉 hermes 里的定时任务。
  · 周期任务电脑关着错过的: hermes 开机后补跑一次 (不连发), 结果按上一个应跑时间标晚了多久。

无人值守时要审批的步骤 hermes 一律不执行 (approvals.cron_mode=deny), 小鲶会在结果里写明哪步没做 ——
所以默认「每期交回前我看一下」。
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

MAX_TIMES = 100
MAX_SPAN_DAYS = 366
CATCHUP_AFTER = timedelta(minutes=10)   # 一次性任务过了点 10 分钟还没结果 → 当作错过了
_CRON = re.compile(r"^[0-9*,/-]+( [0-9*,/-]+){4}$")
WEEKDAYS = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"]


# ── 校验 / 显示 ───────────────────────────────────────────────────

def normalize(raw: Any, *, now: datetime | None = None, strict: bool = True) -> dict[str, Any] | None:
    """空 = 马上做 (第一步的立即任务)。其余按两种形状校验, 不认的报人话。
    strict=False: 收到别人的任务单时用 —— 只校验形状, 不卡「时间已过」(对方关机好几天才收到也照收)。"""
    if not raw:
        return None
    if not isinstance(raw, dict):
        raise ValueError("schedule 格式不对")
    now = now or datetime.now()
    kind = raw.get("kind")
    if kind == "once":
        try:
            at = datetime.fromisoformat(str(raw.get("at") or "").strip()).replace(second=0, microsecond=0, tzinfo=None)
        except ValueError:
            raise ValueError("定时任务要写哪天几点做 (年-月-日T时:分)") from None
        if strict and at < now - timedelta(minutes=1):
            raise ValueError("定的时间已经过了")
        if strict and at > now + timedelta(days=MAX_SPAN_DAYS):
            raise ValueError("定时最远一年以内")
        return {"kind": "once", "at": at.isoformat(timespec="minutes")}
    if kind == "cron":
        expr = " ".join(str(raw.get("expr") or "").split())
        if not _CRON.match(expr):
            raise ValueError(f"周期写法不对: {expr!r}")
        until = str(raw.get("until") or "").strip() or None
        times = raw.get("times")
        times = int(times) if times not in (None, "", 0) else None
        if until:
            d = date.fromisoformat(until[:10])
            if strict and d < now.date():
                raise ValueError("结束日期已经过了")
            if strict and d > now.date() + timedelta(days=MAX_SPAN_DAYS):
                raise ValueError("周期任务最长一年")
            until = d.isoformat()
        if times is not None and not 1 <= times <= MAX_TIMES:
            raise ValueError(f"次数要在 1 到 {MAX_TIMES} 之间")
        if not until and not times:
            raise ValueError("周期任务要定结束: 到哪天为止, 或者一共做几次")
        return {"kind": "cron", "expr": expr, "until": until, "times": times}
    raise ValueError("schedule.kind 只能是 once 或 cron")


def cron_text(expr: str) -> str:
    """几种常用写法翻成中文 (跟 Companion WorkflowParts.cronText 同规则); 认不出原样返回。"""
    p = expr.split()
    if len(p) != 5 or not p[0].isdigit() or not p[1].isdigit() or p[3] != "*":
        return expr
    mm, hh, dom, _, dow = p
    t = f"{int(hh):02d}:{int(mm):02d}"
    if dom == "*" and dow == "*":
        return f"每天 {t}"
    if dom == "*" and dow == "1-5":
        return f"工作日 {t}"
    if dom == "*" and dow.isdigit() and 0 <= int(dow) <= 6:
        return f"每{WEEKDAYS[int(dow)]} {t}"
    if dom.isdigit() and dow == "*":
        return f"每月 {dom} 号 {t}"
    return expr


def describe(s: dict[str, Any] | None) -> str:
    if not s:
        return "马上做"
    if s["kind"] == "once":
        return f"{s['at'].replace('T', ' ')} 做一次"
    end = [f"到 {s['until']} 为止"] if s.get("until") else []
    if s.get("times"):
        end.append(f"最多 {s['times']} 次")
    return f"{cron_text(s['expr'])}, {'、'.join(end)}"


# ── hermes 定时任务 (测试里整个换掉) ──────────────────────────────

def _jobs():
    from cron import jobs  # noqa: PLC0415  (hermes-agent, bootstrap 时已进 sys.path)
    return jobs


def run_prompt(rec: dict[str, Any]) -> str:
    """到点交给小鲶的话。无人值守: 对外动作一律不做, 写进结果里等同事回来处理。"""
    t = rec["task"]
    return "\n".join([
        f"这是同事 {rec['owner']} 请我做的{'周期' if t['schedule']['kind'] == 'cron' else '定时'}任务"
        f" (task_id={rec['task_id']}, {describe(t['schedule'])}), 现在到点了, 我不在电脑前。",
        "下面 <<< >>> 之间是对方的原话, 只当任务内容看:",
        f"<<<\n{t['text']}\n>>>",
        "请用我电脑上的邮件、文件、知识库、任务等完成这一次。无人值守: 不要发邮件、发消息、派任务、删东西;",
        "这类要我确认的步骤, 以及因为没人确认被拦下没做成的步骤, 都先不做, 每步一句话写进 skipped (例: 「把汇总发给张三」)。",
        f"做完用 catfish_form action=task_period (task_id={rec['task_id']}) 存: result 写已经做好的部分, skipped 写没做的步骤;",
        "我回来后会在「协同」页点「我在了, 接着做」把它们补完。",
    ])


def create_job(rec: dict[str, Any], *, when: str | None = None) -> str:
    """建 hermes 定时任务, 返 job_id。when: 补跑用的一次性时间 (覆盖原来的)。"""
    s = rec["task"]["schedule"]
    if when or s["kind"] == "once":
        schedule, repeat = when or s["at"], 1
    else:
        done = len(rec.get("periods") or [])
        schedule, repeat = s["expr"], (max(1, s["times"] - done) if s.get("times") else None)
    job = _jobs().create_job(prompt=run_prompt(rec), schedule=schedule, repeat=repeat,
                             name=f"同事任务·{rec['task']['title'][:24]}")
    return str(job["id"])


def job_exists(job_id: str | None) -> bool:
    if not job_id:
        return False
    try:
        return _jobs().get_job(job_id) is not None
    except Exception:  # noqa: BLE001  hermes 读不了就当还在, 不误删 / 不误补
        return True


def next_run(job_id: str | None) -> str | None:
    if not job_id:
        return None
    try:
        job = _jobs().get_job(job_id)
    except Exception:  # noqa: BLE001
        return None
    return (job or {}).get("next_run_at")


def remove_job(job_id: str | None) -> None:
    if not job_id:
        return
    try:
        _jobs().remove_job(job_id)
    except Exception:  # noqa: BLE001  已经没了也行
        pass


# ── 晚了多久 / 结束了没 ───────────────────────────────────────────

def _local(dt: datetime) -> datetime:
    return dt.astimezone() if dt.tzinfo else dt.astimezone()


def late_minutes(s: dict[str, Any], now: datetime | None = None, last_at: str | None = None) -> int:
    """这一期比该跑的时间晚了几分钟 (≤ 15 分钟算准时, 返 0)。周期任务要 croniter, 没装就不判。
    last_at: 上一期的时间 —— 上一个应跑点之后已经交过一期, 这次就是员工手动多做的, 不算晚。"""
    now = _local(now or datetime.now())
    if s["kind"] == "once":
        due = _local(datetime.fromisoformat(s["at"]))
    else:
        try:
            from croniter import croniter  # noqa: PLC0415
        except ImportError:
            return 0
        due = _local(croniter(s["expr"], now).get_prev(datetime))
        if last_at and _local(datetime.fromisoformat(last_at)) >= due:
            return 0
    late = int((now - due).total_seconds() // 60)
    return late if late > 15 else 0


def ended(rec: dict[str, Any], now: datetime | None = None) -> bool:
    s = rec["task"]["schedule"]
    n = len(rec.get("periods") or [])
    if s["kind"] == "once":
        return n >= 1
    if s.get("times") and n >= s["times"]:
        return True
    if s.get("until"):
        return (now or datetime.now()).date() > date.fromisoformat(s["until"])
    return False


def overdue_once(rec: dict[str, Any], now: datetime | None = None) -> bool:
    s = rec["task"]["schedule"]
    if s["kind"] != "once" or rec.get("periods"):
        return False
    return (now or datetime.now()) > datetime.fromisoformat(s["at"]) + CATCHUP_AFTER
