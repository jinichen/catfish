"""在**真 hermes 的 venv** 里跑: 把 P21 / P26 / P59 装到真 hermes 模块上, 验它们真的生效。

由 tests/test_cron_patches_on_real_hermes.py 用 `$HERMES_ROOT/venv/bin/python` 起子进程调用,
HERMES_HOME 指向临时目录 (不碰员工真数据)。结果按行打 JSON: {"check", "ok", "detail"}。

为什么要在真 hermes 里跑, 不用假模块: 这三个补丁失效的样子都是**不报错** ——
启动日志照样写「已装上」, 但 hermes 调的已经不是我们换掉的那个函数 (P27 就是这么
死的: 调度器启动时把 mark_job_run 绑成了本地名字, 换模块属性碰不到它, 半年没人发现)。
只有拿真 hermes 的调用方去解析名字, 才看得出"换上去的函数到底会不会被调到"。
"""
from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import pkgutil
import sys
import types
from pathlib import Path

HERMES_ROOT = Path(sys.argv[1]).resolve()
PLUGIN_DIR = Path(sys.argv[2]).resolve()
sys.path[:0] = [str(HERMES_ROOT), str(PLUGIN_DIR)]


def report(check: str, ok: bool, detail: str = "") -> None:
    print(json.dumps({"check": check, "ok": bool(ok), "detail": detail}, ensure_ascii=False), flush=True)


def import_package(pkg: str) -> list[str]:
    """把一个包下面的模块全 import 一遍 (模拟 hermes 运行时它们都已加载), 返回导不进的。"""
    failed = []
    root = importlib.import_module(pkg)
    for info in pkgutil.walk_packages(root.__path__, prefix=pkg + "."):
        if ".tests" in info.name or info.name.endswith("__main__"):
            continue
        try:
            importlib.import_module(info.name)
        except Exception as e:  # noqa: BLE001 — 个别平台专属模块导不进不影响判断
            failed.append(f"{info.name}: {type(e).__name__}")
    return failed


def stale_holders(original, name: str) -> list[str]:
    """补丁装上之后, 还有谁在模块层攥着**原函数**? 有就说明那条路径绕过了补丁。"""
    out = []
    for mod_name, mod in list(sys.modules.items()):
        if not isinstance(mod, types.ModuleType) or mod_name.startswith(("plugin", "_real_hermes")):
            continue
        try:
            if getattr(mod, name, None) is original:
                out.append(mod_name)
        except Exception:  # noqa: BLE001
            continue
    return out


def code_objects(fn):
    stack = [fn.__code__]
    while stack:
        co = stack.pop()
        yield co
        stack.extend(c for c in co.co_consts if isinstance(c, types.CodeType))


# ── P21: 定时任务跟 picker 选的模型走 ─────────────────────────────────────

def check_p21() -> None:
    import cron.scheduler as sched
    import plugin_cron

    import_package("cron")
    import gateway.session_cron  # noqa: F401 — 会话属主真正跑定时任务的地方
    real_run_job = sched.run_job

    # 模型在哪儿定: run_job 把**同一个** job dict 交给 _load_cron_job_config, 后者先看 job["model"]
    report("P21 run_job 把同一个 job 交给 _load_cron_job_config",
           "_load_cron_job_config(job," in inspect.getsource(real_run_job))
    loader_src = inspect.getsource(sched._load_cron_job_config)
    report("P21 _load_cron_job_config 先看 job['model']", 'model = job.get("model")' in loader_src)
    first = next(iter(inspect.signature(real_run_job).parameters), None)
    report("P21 run_job 第一个参数仍是 job", first == "job", f"实际: {first}")

    seen = {}

    def fake_orig(job, *a, **k):  # 代替真 run_job: 只走真的「定模型」那一步, 不起 agent
        seen["model"] = sched._load_cron_job_config(job, job["id"], "自检").model
        return True, "", "", None

    real_loader = sched._load_cron_job_config
    sched.run_job = fake_orig
    plugin_cron.model_authority = types.SimpleNamespace(read_picker_model=lambda: "picker-model-x")
    plugin_cron._patch_p21_cron_picker_integration()
    patched = sched.run_job
    report("P21 补丁装上了", patched is not fake_orig)

    # 两类调用方: 模块里按名字找 (LOAD_GLOBAL), 或函数体里 `from cron.scheduler import run_job`
    # (v0.22 起会话属主 gateway/session_cron.execute 就是这样拿的, 真正跑任务的是它)。
    # 两种都是调用那一刻才去查 —— 只要没人在模块层攥着原函数, 拿到的就是补丁。
    globals_callers, import_callers = [], []
    for mod_name, mod in list(sys.modules.items()):
        if not mod_name.startswith(("cron", "gateway")) or not isinstance(mod, types.ModuleType):
            continue
        for obj in list(vars(mod).values()):
            if not (inspect.isfunction(obj) and obj.__module__ == mod_name):
                continue
            names = set().union(*(co.co_names for co in code_objects(obj)))
            if "run_job" not in names:
                continue
            label = f"{mod_name}.{obj.__qualname__}"
            if "cron.scheduler" in names:
                import_callers.append(label)
            elif mod_name == sched.__name__:
                globals_callers.append((label, obj.__globals__.get("run_job") is patched))
    report("P21 调度器按名字调 run_job 时拿到的是补丁",
           bool(globals_callers) and all(ok for _, ok in globals_callers),
           ", ".join(f"{n}{'' if ok else ' ✗'}" for n, ok in globals_callers) or "调度器里找不到按名字调 run_job 的地方")
    report("P21 会话属主在调用时才 import run_job (拿到补丁)", "gateway.session_cron.execute" in import_callers,
           ", ".join(import_callers) or "gateway.session_cron.execute 不再这样拿 run_job —— 看看它改成了什么")
    holders = stale_holders(real_run_job, "run_job")
    report("P21 没有模块攥着原 run_job (P27 那种死法)", not holders, ", ".join(holders))

    from gateway.session_cron import execute as _owner_execute  # noqa: F401
    from cron.scheduler import run_job as at_call_time
    at_call_time({"id": "j1", "model": "job-old-model"})
    report("P21 经补丁跑一次, 真的定模型那一步拿到的是 picker 的", seen.get("model") == "picker-model-x",
           f"实际: {seen.get('model')!r}")
    # 会话属主建会话记录时 (gateway/session_cron._create) 不经过 run_job, 自己调定模型函数
    # 写 policy.model —— 这一步也得是 picker 的, 会话 / 历史里显示的才是实际用的模型
    import gateway.session_cron as sc
    create_names = set().union(*(co.co_names for co in code_objects(sc._create)))
    report("P21 会话属主建记录时在调用时才 import 定模型函数",
           {"cron.scheduler", "_load_cron_job_config"} <= create_names)
    holders = stale_holders(real_loader, "_load_cron_job_config")
    report("P21 没有模块攥着原定模型函数", not holders, ", ".join(holders))
    from cron.scheduler import _load_cron_job_config as loader_at_call_time
    job = {"id": "j2", "model": "job-old-model"}
    label = loader_at_call_time(job, "j2", "自检").model
    report("P21 会话记录的模型名是 picker 的 (不是任务原来配的)", label == "picker-model-x", f"实际: {label!r}")
    report("P21 定模型时不改调用方的 job (它随后原样冻进 request_json)", job["model"] == "job-old-model",
           f"实际: {job['model']!r}")


# ── P26: 卡片上的暂停 / 恢复 / 删除 ──────────────────────────────────────

class _Req:
    def __init__(self, job_id, body=None):
        self.match_info = {"job_id": job_id}
        self._body = body or {}

    async def json(self):
        return self._body


def check_p26() -> None:
    import cron.jobs as jobs
    import plugin_cron
    from gateway.platforms.api_server import APIServerAdapter

    report("P26 APIServerAdapter._check_auth 还在 (三个接口都靠它鉴权)", hasattr(APIServerAdapter, "_check_auth"))
    for name, params in (("pause_job", ["job_id", "reason"]), ("resume_job", ["job_id"]),
                         ("remove_job", ["job_id"])):
        fn = getattr(jobs, name, None)
        actual = list(inspect.signature(fn).parameters) if fn else None
        report(f"P26 cron.jobs.{name} 签名不变", actual == params, f"实际: {actual}")

    job = jobs.create_job("catfish 自检", "0 9 * * 1", name="catfish-p26-selfcheck")
    me = types.SimpleNamespace(_check_auth=lambda request: None)

    def call(handler, job_id, body=None):
        resp = asyncio.run(handler(me, _Req(job_id, body)))
        return resp.status, json.loads(resp.body)

    st, body = call(plugin_cron._handle_cron_pause, job["id"], {"reason": "自检"})
    after = jobs.get_job(job["id"]) if hasattr(jobs, "get_job") else None
    report("P26 暂停: 接口 200 且任务真停了",
           st == 200 and body.get("ok") and after and after.get("enabled") is False and after.get("state") == "paused",
           f"status={st} state={after and after.get('state')}")
    st, body = call(plugin_cron._handle_cron_resume, job["id"])
    after = jobs.get_job(job["id"]) if hasattr(jobs, "get_job") else None
    report("P26 恢复: 接口 200 且任务重新启用",
           st == 200 and body.get("ok") and after and after.get("enabled") is True,
           f"status={st} enabled={after and after.get('enabled')}")
    st, body = call(plugin_cron._handle_cron_delete, job["id"])
    gone = jobs.get_job(job["id"]) if hasattr(jobs, "get_job") else "未知"
    report("P26 删除: 接口 200 且任务真没了", st == 200 and body.get("ok") and gone is None, f"status={st}")
    st, _ = call(plugin_cron._handle_cron_pause, "no-such-job")
    report("P26 不存在的任务回 404", st == 404, f"status={st}")


# ── P59: 定时任务里的「始终允许」 ───────────────────────────────────────

P59_TARGETS = ("check_execute_code_guard", "is_approved", "_human_decision")


def check_p59() -> None:
    import tools.approval as approval
    import plugin_approval_always as p59

    for name, want in (("_unattended_contexts", []), ("_is_permanently_approved", ["pattern_key"])):
        fn = getattr(approval, name, None)
        actual = list(inspect.signature(fn).parameters) if fn else None
        report(f"P59 tools.approval.{name} 还在且签名不变", actual == want, f"实际: {actual}")
    guard = getattr(approval, "check_execute_code_guard", None)
    gp = list(inspect.signature(guard).parameters)[:3] if guard else None
    report("P59 check_execute_code_guard 前三个参数不变", gp == ["code", "env_type", "has_host_access"], f"实际: {gp}")

    # 先把会调这三个函数的模块都导进来 (hermes 运行时它们早已加载), 再装补丁, 再看有没有人攥着原函数
    import_package("tools")
    originals = {n: getattr(approval, n, None) for n in P59_TARGETS}
    missing = [n for n, f in originals.items() if f is None]
    report("P59 三个包装点都在", not missing, ", ".join(missing))
    p59._patch_p59_always_means_always()
    for n, orig in originals.items():
        if orig is None:
            continue
        report(f"P59 {n} 已换成补丁", getattr(getattr(approval, n), "_catfish_p59", False))
        holders = stale_holders(orig, n)
        report(f"P59 没有模块攥着原 {n} (绕过补丁)", not holders, ", ".join(holders))

    # 无人值守判定真的认得出定时任务: 在 cron 的会话标记下, _unattended_contexts() 必须为真
    try:
        from gateway.session_context import _VAR_MAP
        tok = _VAR_MAP["HERMES_CRON_SESSION"].set("1")
        try:
            unattended = bool(approval._unattended_contexts())
        finally:
            _VAR_MAP["HERMES_CRON_SESSION"].reset(tok)
        report("P59 定时任务里 _unattended_contexts() 为真 (删除类在定时任务里才会被拦)", unattended)
    except Exception as e:  # noqa: BLE001
        report("P59 定时任务里 _unattended_contexts() 为真 (删除类在定时任务里才会被拦)", False, repr(e))


# ── 子进程跑定时任务的那条路: 只在 Linux + systemd 托管时走, Mac / Windows 一律进程内 ──

def check_dispatch() -> None:
    from tools import process_registry as pr

    src = inspect.getsource(pr.restart_safe_gateway_child_argv)
    report("非 Linux 一律进程内跑定时任务 (补丁在 gateway 进程里生效)",
           "if not _IS_LINUX" in src and '"in_process"' in src)
    import cron.scheduler as sched
    ext = "".join(inspect.getsource(f) for n, f in vars(sched).items()
                  if inspect.isfunction(f) and "external" in n and f.__module__ == sched.__name__)
    report("Linux 子进程跑定时任务时仍会加载插件 (discover_plugins)", "discover_plugins()" in ext)


for fn in (check_p21, check_p26, check_p59, check_dispatch):
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        report(f"{fn.__name__} 整段跑挂了", False, f"{type(e).__name__}: {e}")
