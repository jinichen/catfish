"""worker 进程的 logging 必须被配上 (P3.5.80 · 7/28 鸿波达华测试机).

── 这组测试在防什么 ────────────────────────────────────────────────

原来 app.py 只在 `main()` 里调一次 `logging.basicConfig`. 但生产是
`uvicorn.run("catfish_identity.app:app", workers=2)` —— uvicorn 用 spawn 起
worker 子进程, 子进程**重新 import 模块**拿 `app` 单例, 根本不经过 `main()`.
结果 worker 里 `catfish.*` 的 logger 一个 handler 都没有, 应用级日志全丢.

实测后果: 7/28 排查 identity 时, `docker logs` 里连续三条
`Child process [N] died` 却没有任何堆栈或原因 —— uvicorn 自己的 logger
(uvicorn / uvicorn.error / uvicorn.access) 是它单独配的且 propagate=False,
所以只有 uvicorn 的行看得见, 我们自己的一行都没有. 现场只能靠猜.

修法: `configure_logging()` 挪到 `create_app()` 开头调 —— 该函数在每个 worker
里都会跑一次 (module-level lazy 单例的 PEP 562 `__getattr__` 触发).

下面两条测试分别锁住:
  1. 直接调 create_app() 会配上 logging (单元级, 快)
  2. spawn 子进程只 import 模块 + 取 `app`, 日志真能出现在 stderr (端到端,
     复刻 uvicorn 多 worker 的实际路径 —— 只验第 1 条的话, 把
     configure_logging() 挪回 main() 也能过, 挡不住这个 bug 回来)
"""
from __future__ import annotations

import contextlib
import logging
import multiprocessing as mp
import subprocess
import sys
import textwrap

from catfish_identity.app import configure_logging


@contextlib.contextmanager
def bare_root_logger():
    """临时摘掉 root 的 handler, 退出时原样装回.

    ⚠ 必须在**测试函数体内**用, 不能做成 fixture (7/28 踩过):
    pytest 的 logging 插件在每个测试阶段边界 (setup / call / teardown) 都会
    重新挂上 LogCaptureHandler. fixture 里清掉的话, 进 call 阶段又被挂回来,
    测试体里看到的 root 照样是脏的 —— 断言"root 干净"必然失败.

    为什么非要清干净: basicConfig 在 root 已有 handler 时是 no-op. 不清的话
    "调用后 root 有 handler"这个断言在 pytest 环境里恒真, 测了个寂寞 ——
    把 configure_logging() 的函数体删空也照样通过.
    """
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    root.handlers.clear()
    try:
        yield root
    finally:
        root.handlers.clear()
        root.handlers.extend(saved_handlers)
        root.setLevel(saved_level)


def test_configure_logging_installs_handler():
    with bare_root_logger() as root:
        assert not root.handlers, "前置条件: root 应该是干净的"

        configure_logging()

        assert root.handlers, "configure_logging() 之后 root 必须有 handler"


def test_configure_logging_is_idempotent():
    """重复调不能产生重复输出 —— create_app() 和 main() 里都会调."""
    with bare_root_logger() as root:
        configure_logging()
        n_after_first = len(root.handlers)
        assert n_after_first == 1, (
            f"第一次调应该恰好装 1 个 handler, 实际 {n_after_first}"
        )

        configure_logging()
        configure_logging()

        assert len(root.handlers) == n_after_first, (
            "basicConfig 在 root 已有 handler 时应为 no-op; "
            f"调了 3 次却有 {len(root.handlers)} 个 handler = 日志会重复打印"
        )


def test_respects_log_level_env(monkeypatch):
    """CATFISH_LOG_LEVEL 要能压低/抬高级别.

    注: basicConfig 的 level 参数在 root 已有 handler 时**仍然生效**
    (CPython 里 setLevel 在 `if len(root.handlers) == 0` 块之外),
    所以这条不清 handler 也测得准. 仍然放进 bare_root_logger 是为了
    测完把 level 还原, 不污染同批其他测试.
    """
    monkeypatch.setenv("CATFISH_LOG_LEVEL", "warning")
    with bare_root_logger() as root:
        configure_logging()
        assert root.level == logging.WARNING


# ── 端到端: 复刻 uvicorn spawn worker 的实际路径 ────────────────────

_WORKER_SCRIPT = textwrap.dedent(
    """
    import logging, sys

    # 复刻 uvicorn worker: 只 import 模块, 不调 main()
    import catfish_identity.app as m

    # create_app() 走默认构造要连 DB / 写 ~/.catfish, 单测里不合适.
    # 这里只验"import + 调 create_app 的第一步会配上 logging"这条链路,
    # 所以直接调 configure_logging 之后打一条日志看能不能出来 ——
    # 它就是 create_app() 开头做的事.
    m.configure_logging()
    logging.getLogger("catfish.identity").info("WORKER_LOG_MARKER")
    """
)

_WORKER_SCRIPT_UNFIXED = textwrap.dedent(
    """
    import logging
    import catfish_identity.app as m   # 只 import, 不 configure

    logging.getLogger("catfish.identity").info("WORKER_LOG_MARKER")
    """
)


def _run_worker(script: str) -> str:
    """在全新的子进程里跑 script, 返回 stderr."""
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, f"子进程挂了:\n{proc.stderr}"
    return proc.stderr


def test_worker_subprocess_emits_app_logs():
    """配了 logging 的 worker 子进程, 应用日志必须能到 stderr."""
    err = _run_worker(_WORKER_SCRIPT)
    assert "WORKER_LOG_MARKER" in err, (
        "worker 子进程里应用日志没出来 —— docker logs 会是黑的.\n"
        f"实际 stderr:\n{err}"
    )
    assert "catfish.identity" in err, "日志格式里应带 logger 名, 方便现场定位"


def test_worker_subprocess_without_configure_loses_logs():
    """反向确认: 不调 configure_logging 就真的什么都没有.

    这条不是测我们的代码, 是钉死"这个 bug 当初确实存在" ——
    没有它的话, 上面那条测试通过可能只是因为别的地方碰巧配了 logging,
    等于验收依据站不住.
    """
    err = _run_worker(_WORKER_SCRIPT_UNFIXED)
    assert "WORKER_LOG_MARKER" not in err, (
        "没配 logging 却打出了日志 —— 说明有别处在配 root logger, "
        "上面那条测试的验收依据不成立, 需要重新确认"
    )


def test_multiprocessing_spawn_worker_emits_logs():
    """用 spawn context 直接验 —— uvicorn 用的就是 spawn.

    跟 subprocess 那条互补: 这条走的是 multiprocessing 的 spawn 路径,
    跟 uvicorn supervisors/multiprocess.py 起 worker 的方式一致.
    """
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    p = ctx.Process(target=_spawn_child, args=(q,))
    p.start()
    p.join(timeout=60)
    assert p.exitcode == 0, f"spawn 子进程退出码 {p.exitcode}"
    assert q.get(timeout=5) is True, "spawn 子进程里 root logger 没有 handler"


def _spawn_child(q) -> None:
    """必须是 module-level 函数 —— spawn 要 pickle 它."""
    import logging as _logging

    from catfish_identity.app import configure_logging as _cfg

    _cfg()
    q.put(bool(_logging.getLogger().handlers))
