"""定时任务推微信没推出去 → 等员工在微信里说话时补发 (11/10)。

# 为什么

微信 (腾讯 iLink) 只让机器人在员工最近说过话之后主动发消息。员工一阵子没在微信里跟小鲶说话,
定时任务 (日报、周扫描、参会提醒) 推过去就被拒: `iLink sendmessage session not ready`。
hermes 认定这种失败「重试没用」, 不重试也不进它的重投队列 —— 对的, 隔多久重试都一样被拒,
只有员工在微信里发一句话才会好。

之前机器上的补救是小鲶自己建的「股票日报-失败补推watcher」: 每 30 分钟盲推一次, 10 次就放弃。
11/06、11/09、11/10 三天都是 10 次全失败, 白打腾讯接口 (11/10 网关日志 485 条这个错), 而且只管那一份日报。

# 做法

1. 包住 `cron.scheduler._deliver_result` (定时任务所有投递都走它): 返回的错误是 session not ready
   → 把这次要发的内容按任务存进 ~/.catfish/weixin_resend.json (同一个任务只留最新一次)。
   这个任务后来有一次投递成功 → 删掉它的待补发 (有更新的了)。
2. `pre_gateway_dispatch` 钩子: 员工在微信里发来消息 (那一刻 hermes 已经存下新的会话凭证)
   → 后台把发给这个微信号的待补发逐条补上, 开头注明是补发、原定几点。补上了就把任务的
   「没送达」状态清掉, Companion 不再挂「没发到微信」。
3. 过期不补: 一次性任务 (参会提醒之类) 过了 1 小时不补; 周期任务 24 小时内、并且下一次还没跑。
4. 升级前就已经失败的: 存储里没有内容, 从 hermes 存的那次运行记录 (cron/output) 里取回答补发。

5. 不走定时任务的推送 (Windows 提醒同步到微信、工作流的「推送到微信」, 都在 tool-bridge
   wechat_push.py 里直连 iLink): 被拒的由 tool-bridge 追加进 ~/.catfish/weixin_resend_direct.jsonl,
   这里一起补 (提醒 1 小时内, 其余 24 小时)。

限流 / 断线这类会自己好的失败不归这里, hermes 自己按时间重试 (delivery_ledger)。
"""
from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger("catfish.xcatfish_user.weixin_resend")

_NOT_READY_RE = re.compile(r"session not ready|must send the bot a message", re.I)
ONCE_MAX_AGE_S = 3600
RECURRING_MAX_AGE_S = 24 * 3600
#: 不是「会话过期」的失败, 同一条最多再试几次
MAX_OTHER_FAILURES = 3

_lock = threading.Lock()
_running = threading.Lock()
_orig_deliver: Optional[Callable[..., Optional[str]]] = None
_tasks: set = set()


def _home() -> Path:
    return Path(os.environ.get("CATFISH_HOME") or (Path.home() / ".catfish"))


def _store_path() -> Path:
    return _home() / "weixin_resend.json"


def _direct_path() -> Path:
    """tool-bridge wechat_push.py 写的 (文件名两边要一致)。"""
    return _home() / "weixin_resend_direct.jsonl"


DIRECT_MAX_AGE_S = {"reminder": ONCE_MAX_AGE_S}


def is_session_not_ready(err: Any) -> bool:
    return isinstance(err, str) and bool(_NOT_READY_RE.search(err))


def _load() -> dict:
    try:
        data = json.loads(_store_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"pending": {}, "sent": {}}
    if not isinstance(data, dict):
        return {"pending": {}, "sent": {}}
    data.setdefault("pending", {})
    data.setdefault("sent", {})
    return data


def _save(data: dict) -> None:
    path = _store_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError as e:
        logger.warning("微信补发: 写 %s 失败 (%s)", path, e)


# ── 1. 投递结果 → 记下 / 清掉 ──────────────────────────────────────────

def note_delivery(job: dict, content: str, for_failure: bool, err: Optional[str]) -> None:
    job_id = str((job or {}).get("id") or "")
    if not job_id:
        return
    with _lock:
        data = _load()
        if err is None:
            if data["pending"].pop(job_id, None) is not None:
                _save(data)
            return
        if not is_session_not_ready(err) or not isinstance(content, str) or not content.strip():
            return
        data["pending"][job_id] = {
            "name": job.get("name") or job_id,
            "run_at": job.get("last_run_at") or datetime.now().astimezone().isoformat(),
            "saved_at": time.time(),
            "content": content,
            "for_failure": bool(for_failure),
            "failures": 0,
        }
        _save(data)
    logger.info("微信补发: 「%s」因微信会话过期没推出去, 等员工在微信里说话时补发", job.get("name") or job_id)


def wrap_deliver_result(orig: Callable[..., Optional[str]]) -> Callable[..., Optional[str]]:
    @functools.wraps(orig)
    def _patched(job, content, adapters=None, loop=None, *, for_failure=False, **kwargs):
        err = orig(job, content, adapters, loop, for_failure=for_failure, **kwargs)
        try:
            note_delivery(job, content, for_failure, err)
        except Exception:  # noqa: BLE001 —— 记不下来只是少补一次, 不能影响投递
            logger.warning("微信补发: 记录投递结果失败", exc_info=True)
        return err

    _patched._catfish_weixin_resend = True
    return _patched


def install() -> bool:
    """把 cron.scheduler._deliver_result 换成包装。scheduler.py 里所有投递都按模块全局名调它
    (调用时才查), 换模块属性能生效; 没有别的模块攥着原函数 (tests 里用真 hermes 钉住)。"""
    global _orig_deliver
    try:
        from cron import scheduler as _sched  # noqa: PLC0415
    except ImportError as e:
        logger.warning("微信补发: hermes cron 模块导不进来, 不装 (%s)", e)
        return False
    cur = getattr(_sched, "_deliver_result", None)
    if cur is None:
        logger.warning("微信补发: cron.scheduler._deliver_result 不在 (hermes 改了?), 不装")
        return False
    if getattr(cur, "_catfish_weixin_resend", False):
        return True
    _orig_deliver = cur
    _sched._deliver_result = wrap_deliver_result(cur)
    logger.info("微信补发: 定时任务投递已包上 (session not ready 的内容会留着等补发) ✓")
    return True


# ── 2. 员工在微信里说话 → 补发 ───────────────────────────────────────

def _parse_ts(value: Any) -> Optional[float]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except ValueError:
        return None


def _expired(job: Optional[dict], run_ts: float, now: float) -> bool:
    once = (((job or {}).get("schedule") or {}).get("kind") == "once")
    if now - run_ts > (ONCE_MAX_AGE_S if once else RECURRING_MAX_AGE_S):
        return True
    next_ts = _parse_ts((job or {}).get("next_run_at"))
    # 下一次已经跑过 (它自己会推最新的) —— 旧的不补
    return next_ts is not None and next_ts <= now and not once


def _archived_answer(job_id: str, run_ts: float) -> Optional[str]:
    """升级前就失败的任务: 存储里没有, 从 hermes 那次运行的记录里取回答。"""
    try:
        from cron.jobs import get_cron_output_dir  # noqa: PLC0415
        from cron.scheduler_prompt import _archive_answer  # noqa: PLC0415
        files = sorted((get_cron_output_dir() / job_id).glob("*.md"))
    except Exception:  # noqa: BLE001
        return None
    if not files:
        return None
    latest = files[-1]
    try:
        if abs(latest.stat().st_mtime - run_ts) > 1800:
            return None  # 不是失败的那一次
        text = latest.read_text(encoding="utf-8")
    except OSError:
        return None
    if "## Response" not in text:
        return None  # 脚本任务的记录是整份文档, 不拿来发
    return _archive_answer(text)


def _candidates(data: dict, jobs: list, now: float) -> list[tuple[str, dict, Optional[dict]]]:
    by_id = {str(j.get("id")): j for j in jobs if isinstance(j, dict)}
    out: list[tuple[str, dict, Optional[dict]]] = []
    for job_id, entry in list(data["pending"].items()):
        job = by_id.get(job_id)
        run_ts = _parse_ts(entry.get("run_at")) or float(entry.get("saved_at") or 0)
        if job is None or _expired(job, run_ts, now):
            data["pending"].pop(job_id, None)
            continue
        out.append((job_id, entry, job))
    for job_id, job in by_id.items():
        if job_id in data["pending"] or not is_session_not_ready(job.get("last_delivery_error")):
            continue
        run_at = job.get("last_run_at")
        run_ts = _parse_ts(run_at)
        if run_ts is None or data["sent"].get(job_id) == run_at or _expired(job, run_ts, now):
            continue
        answer = _archived_answer(job_id, run_ts)
        if answer:
            out.append((job_id, {"name": job.get("name") or job_id, "run_at": run_at, "saved_at": run_ts,
                                 "content": answer, "for_failure": False, "failures": 0}, job))
    return out


def _weixin_chat_ids(job: dict) -> set[str]:
    from cron.scheduler_delivery import _resolve_delivery_targets  # noqa: PLC0415
    return {str(t.get("chat_id")) for t in _resolve_delivery_targets(job)
            if str(t.get("platform") or "").lower() == "weixin" and not t.get("thread_id")}


def _banner(entry: dict) -> str:
    ts = _parse_ts(entry.get("run_at")) or float(entry.get("saved_at") or time.time())
    when = datetime.fromtimestamp(ts).strftime("%m/%d %H:%M")
    return f"【补发】这条原定 {when} 推送，当时微信会话过期没发出去，现在补上。\n\n"


def _weixin_adapter(adapters: Any) -> Any:
    for key, adapter in dict(adapters or {}).items():
        if str(getattr(key, "value", key)).lower() == "weixin":
            return adapter
    return None


def _resend_direct(chat_id: str, adapters: Any, loop: Any, now: float) -> int:
    """tool-bridge 留下的直连推送: 整份取走 (rename, 不跟追加写抢), 补发这个微信号的, 剩下的写回去。"""
    path = _direct_path()
    if not path.exists():
        return 0
    claim = path.with_name(f".{path.name}.{os.getpid()}.claim")
    try:
        os.replace(path, claim)
        lines = claim.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    keep: list[dict] = []
    sent = 0
    adapter = _weixin_adapter(adapters)
    for raw in lines:
        try:
            item = json.loads(raw)
            text, saved = str(item["text"]), float(item["saved_at"])
        except (ValueError, KeyError, TypeError):
            continue
        if now - saved > DIRECT_MAX_AGE_S.get(item.get("kind"), RECURRING_MAX_AGE_S):
            continue
        if str(item.get("chat_id")) != str(chat_id) or adapter is None:
            keep.append(item)
            continue
        err: Optional[str]
        try:
            fut = asyncio.run_coroutine_threadsafe(
                adapter.send(chat_id, _banner({"saved_at": saved}) + text), loop)
            res = fut.result(timeout=60)
            err = None if getattr(res, "success", False) else str(getattr(res, "error", None) or "send failed")
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"
        if err is None:
            sent += 1
            continue
        if not is_session_not_ready(err):
            item["failures"] = int(item.get("failures") or 0) + 1
            if item["failures"] >= MAX_OTHER_FAILURES:
                logger.warning("微信补发: 一条直连推送补发 %d 次都失败 (%s), 不再补", item["failures"], err)
                continue
        keep.append(item)
    try:
        if keep:
            with path.open("a", encoding="utf-8") as f:
                f.write("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in keep))
        claim.unlink()
    except OSError:
        logger.warning("微信补发: 写回没补上的直连推送失败", exc_info=True)
    if sent:
        logger.info("微信补发: 补发了 %d 条提醒 / 工作流推送", sent)
    return sent


def resend_for_chat(chat_id: str, adapters: Any, loop: Any, now: Optional[float] = None) -> list[str]:
    """把发给这个微信号的待补发都补上。在后台线程里跑 (投递会阻塞等事件循环)。返回补上的任务 id。"""
    if not _running.acquire(blocking=False):
        return []  # 上一条消息触发的补发还在跑
    try:
        try:
            _resend_direct(str(chat_id), adapters, loop, time.time() if now is None else now)
        except Exception:  # noqa: BLE001
            logger.warning("微信补发: 直连推送补发失败", exc_info=True)
        from cron.jobs import load_jobs, update_job  # noqa: PLC0415
        deliver = _orig_deliver
        if deliver is None:
            from cron import scheduler as _sched  # noqa: PLC0415
            deliver = _sched._deliver_result
        now = time.time() if now is None else now
        with _lock:
            data = _load()
            todo = _candidates(data, load_jobs(), now)
            _save(data)
        done: list[str] = []
        for job_id, entry, job in todo:
            try:
                if str(chat_id) not in _weixin_chat_ids(job):
                    continue
            except Exception:  # noqa: BLE001
                logger.warning("微信补发: 解析「%s」的投递目标失败", entry.get("name"), exc_info=True)
                continue
            one = dict(job, deliver=f"weixin:{chat_id}")
            one.pop("failure_deliver", None)
            err = deliver(one, _banner(entry) + entry["content"], adapters, loop,
                          for_failure=bool(entry.get("for_failure")))
            with _lock:
                data = _load()
                if err is None:
                    data["pending"].pop(job_id, None)
                    data["sent"][job_id] = entry.get("run_at")
                    done.append(job_id)
                elif not is_session_not_ready(err):
                    entry["failures"] = int(entry.get("failures") or 0) + 1
                    if entry["failures"] >= MAX_OTHER_FAILURES:
                        data["pending"].pop(job_id, None)
                        logger.warning("微信补发: 「%s」补发 %d 次都失败 (%s), 不再补",
                                       entry.get("name"), entry["failures"], err)
                    else:
                        data["pending"][job_id] = entry
                elif job_id not in data["pending"]:
                    data["pending"][job_id] = entry  # 取自运行记录的, 也留着等下一次
                _save(data)
            if err is None:
                logger.info("微信补发: 「%s」已补发到微信", entry.get("name"))
                if job.get("last_run_at") == entry.get("run_at"):
                    try:
                        update_job(job_id, {"last_delivery_error": None, "last_status": "ok"})
                    except Exception:  # noqa: BLE001
                        logger.warning("微信补发: 清「%s」的没送达状态失败", entry.get("name"), exc_info=True)
            else:
                logger.warning("微信补发: 「%s」补发失败: %s", entry.get("name"), err)
        return done
    finally:
        _running.release()


def _is_weixin(source: Any) -> bool:
    platform = getattr(source, "platform", None)
    return str(getattr(platform, "value", platform) or "").lower() == "weixin"


async def pre_gateway_dispatch_hook(event: Any = None, gateway: Any = None, **_: Any) -> None:
    """员工在微信里发来消息: 后台补发, 不挡这条消息正常处理。永远返回 None (照常分发)。"""
    try:
        source = getattr(event, "source", None)
        if not _is_weixin(source) or not getattr(source, "chat_id", None):
            return None
        adapters = getattr(gateway, "adapters", None)
        if not adapters:
            return None
        loop = asyncio.get_running_loop()
        chat_id = str(source.chat_id)
        task = loop.create_task(asyncio.to_thread(resend_for_chat, chat_id, adapters, loop))
        _tasks.add(task)  # 事件循环只弱引用任务, 不留一份可能半路被回收
        task.add_done_callback(_tasks.discard)
    except Exception:  # noqa: BLE001
        logger.warning("微信补发: 触发失败", exc_info=True)
    return None

