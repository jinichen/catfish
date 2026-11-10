"""在**真 hermes 的 venv** 里跑: 把微信补发装到真 hermes 上, 走一遍「推送被拒 → 员工说话 → 补发」。

由 tests/test_weixin_resend_on_real_hermes.py 用 `$HERMES_ROOT/venv/bin/python` 起子进程调用,
HERMES_HOME 指向临时目录。微信适配器用假的 (第一次按 iLink 的原话拒绝, 之后收下), 其余都是真 hermes:
真的 cron.jobs 建任务、真的 _deliver_result 投递 (live 通道 → standalone 兜底)、真的 update_job。
结果按行打 JSON: {"check", "ok", "detail"}。
"""
from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import os
import sys
import threading
import types
from pathlib import Path

HERMES_ROOT = Path(sys.argv[1]).resolve()
PLUGIN_DIR = Path(sys.argv[2]).resolve()
sys.path[:0] = [str(HERMES_ROOT)]
CHAT = "o9cq80_fake_user@im.wechat"


def report(check: str, ok: bool, detail: str = "") -> None:
    print(json.dumps({"check": check, "ok": bool(ok), "detail": detail}, ensure_ascii=False), flush=True)


def main() -> None:
    os.environ["CATFISH_HOME"] = str(Path(os.environ["HERMES_HOME"]).parent / ".catfish")
    from gateway.config import Platform
    from gateway.platforms.base import SendResult
    from cron import scheduler as sched
    from cron import jobs as cj

    original = sched._deliver_result
    spec = importlib.util.spec_from_file_location("_wr_real", PLUGIN_DIR / "weixin_resend.py")
    wr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wr)
    report("装上了: cron.scheduler._deliver_result 换成了包装", wr.install()
           and getattr(sched._deliver_result, "_catfish_weixin_resend", False))

    # 还有谁在模块层攥着原函数? 有 = 那条投递路径绕过了包装 (P27 那种死法)
    for pkg in ("cron", "gateway"):
        root = importlib.import_module(pkg)
        import pkgutil
        for info in pkgutil.walk_packages(root.__path__, prefix=pkg + "."):
            if ".tests" in info.name or info.name.endswith("__main__"):
                continue
            try:
                importlib.import_module(info.name)
            except Exception:  # noqa: BLE001
                pass
    holders = [f"{n}.{a}" for n, m in list(sys.modules.items()) if isinstance(m, types.ModuleType)
               and not n.startswith("_wr") for a, v in list(vars(m).items()) if v is original
               and not (n == "cron.scheduler_delivery" and a == "_deliver_result")]
    report("没有模块攥着原 _deliver_result (投递都经过包装)", not holders, ", ".join(holders))

    calls: list[str] = []

    class FakeWeixin:
        platform = Platform.WEIXIN
        name = "weixin"
        is_connected = True

        async def send(self, chat_id, content, reply_to=None, metadata=None):
            calls.append(content)
            if len(calls) == 1:
                return SendResult(success=False, error=(
                    "iLink sendmessage session not ready: ret=-2 errcode=None errmsg=unknown error"
                    " — the user must send the bot a message first (or re-pair)"))
            return SendResult(success=True, message_id=f"m{len(calls)}")

        def __getattr__(self, item):  # 其余 hermes 可能探的属性: 没有
            raise AttributeError(item)

    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    adapters = {Platform.WEIXIN: FakeWeixin()}

    job = cj.create_job(prompt="写日报", schedule="30 9 * * 1-5", name="测试日报", deliver=f"weixin:{CHAT}")
    cj.update_job(job["id"], {"last_run_at": __import__("datetime").datetime.now().astimezone().isoformat(),
                              "last_status": "delivery_failed"})
    job = cj.get_job(job["id"])
    err = sched._deliver_result(job, "日报正文 ABC", adapters, loop)
    report("真投递: 假微信按 iLink 原话拒绝, 返回的错误认得出是会话过期",
           wr.is_session_not_ready(err), str(err)[:300])
    cj.update_job(job["id"], {"last_delivery_error": err})
    store = json.loads(Path(os.environ["CATFISH_HOME"], "weixin_resend.json").read_text())
    report("内容留下了", store["pending"].get(job["id"], {}).get("content") == "日报正文 ABC", json.dumps(store)[:300])

    done = wr.resend_for_chat(CHAT, adapters, loop)
    report("员工说话后补发成功", done == [job["id"]], f"done={done} calls={len(calls)}")
    report("补发内容带补发说明和原文", len(calls) == 2 and calls[1].count("【补发】") == 1 and "日报正文 ABC" in calls[1],
           (calls[1][:200] if len(calls) > 1 else ""))
    after = cj.get_job(job["id"])
    report("真 update_job 清掉了没送达状态", after.get("last_delivery_error") is None and after.get("last_status") == "ok",
           f"{after.get('last_status')} {after.get('last_delivery_error')}")
    again = wr.resend_for_chat(CHAT, adapters, loop)
    report("再说一句话不重复补发", again == [] and len(calls) == 2, f"{again} {len(calls)}")

    # tool-bridge 直连推送 (Windows 提醒) 被拒后留下的, 走网关的微信通道补发
    import time as _t
    qf = Path(os.environ["CATFISH_HOME"], "weixin_resend_direct.jsonl")
    qf.write_text(json.dumps({"text": "⏰ 小鲶提醒: 交月报", "chat_id": CHAT, "kind": "reminder",
                              "saved_at": _t.time() - 60}, ensure_ascii=False) + "\n", encoding="utf-8")
    n = len(calls)
    wr.resend_for_chat(CHAT, adapters, loop)
    report("提醒推送补发: 经网关微信通道发出, 文件清空",
           len(calls) == n + 1 and calls[-1].endswith("⏰ 小鲶提醒: 交月报") and "【补发】" in calls[-1]
           and (not qf.exists() or qf.read_text(encoding="utf-8").strip() == ""),
           calls[-1][:120])

    # 钩子名在 hermes 的合法清单里, 并且 hermes 会 await async 回调
    from hermes_cli.plugins import VALID_HOOKS
    report("pre_gateway_dispatch 是 hermes 认的钩子", "pre_gateway_dispatch" in VALID_HOOKS)
    loop.call_soon_threadsafe(loop.stop)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        import traceback
        report("驱动跑完", False, traceback.format_exc()[-1500:])
    else:
        report("驱动跑完", True)
