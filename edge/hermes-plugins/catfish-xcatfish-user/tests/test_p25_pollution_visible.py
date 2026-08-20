"""P25 退役之后, 我们依赖的那个上游行为得钉住。

# 这个文件的来历 (保留, 因为它是一次做对了的实验)

P25 (6/24) 治的是: hermes cron 把 `HERMES_CRON_SESSION` 写进 `os.environ` 不清、
污染整个 daemon 进程 —— 之后任何 chat / api 调 execute_code 都会被
`cron_mode=deny` 误拦。P25 用 threadlocal 精准判定"本线程真在 cron 里", 非 cron
线程就把污染的 env 清掉再放行。

这个文件**原来**钉的是那条"清掉再放行"的日志不能掉回 `logger.debug` ——
因为它是 P25 唯一能证明自己还有用的证据, 而 8/13 之前它就是 debug 级,
agent.log 里 DEBUG 一条都没有 (9347 INFO / 304 WARNING / 0 DEBUG), 三个月没人
看得见。当时写下的退役判据是:

    · 打出来 → 本机还有走真 env 的路径, P25 必要
    · 长期不打 → 可以考虑退役 (但要先确认所有部署的 hermes 版本)

**8/19 这个实验跑完了**:

    日志窗口                gateway.log 8/08 → 8/20 (12 天)
    同期 cron job           340 次
    同期 execute_code       494 次
    对照组「P25 wrap」      1810 次   ← 证明 patch 真装上了, 0 才有意义
    「P25 接住一次污染」    0
    「P25 装载时清掉污染」  0

加上鸿波确认员工机 hermes 统一 0.20 (大版本升级一起升), 两个条件都满足, P25
退役。**把探针从 debug 提到 warning 这一步是关键** —— 没有它, 这个实验永远
跑不出结论。

# 所以这个文件现在钉什么

钉**我们新依赖的那个上游行为**。P25 删了之后, 保护完全来自 hermes 自己的
ContextVar 化; 它要是改回 os.environ, 故障形状跟当年一模一样 (execute_code
被 cron_mode=deny 误拦), 而且**不报错**。

判据故意宽一格: 只验"这个行为还在", 不验实现细节 (变量名 / 行号)。上游重构
是常态, 这条不该因为改了个局部名字就红 —— 它只在**行为消失**时红。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

HERMES_ROOT = Path(
    os.environ.get("HERMES_ROOT", Path.home() / ".hermes" / "hermes-agent")
)

pytestmark = pytest.mark.skipif(
    not (HERMES_ROOT / "cron" / "scheduler.py").exists(),
    reason=f"没有 hermes 树可对照 ({HERMES_ROOT}) —— CI/沙箱里跳过",
)


def _read(rel: str) -> str:
    return (HERMES_ROOT / rel).read_text(encoding="utf-8", errors="replace")


def test_cron_把_session_标记放进_contextvar_而不是全局env():
    """上游 cron 仍然用 ContextVar 标记 cron session。

    改回 `os.environ[...] = "1"` 的话, 整个 daemon 又会被污染。
    """
    src = _read("cron/scheduler.py")

    assert "set_session_vars" in src, (
        "上游 cron 不再用 session_context —— cron 的 session 标记可能回到全局 env。\n"
        "P25 (threadlocal 隔离 + 非 cron 线程 pop env) 8/19 就是因为它才退役的,\n"
        "现在要重新评估: execute_code 会被 cron_mode=deny 误拦, 而且不报错。\n"
        "见 plugin_cron.py 尾部的 P25 墓碑。"
    )
    assert re.search(r'_VAR_MAP\[\s*[\'"]HERMES_CRON_SESSION[\'"]\s*\]', src), (
        "上游不再从 _VAR_MAP 取 HERMES_CRON_SESSION 的 ContextVar —— "
        "重新确认它是不是回到 os.environ 了。"
    )


def test_cron_跑完会还原那个标记():
    """set 了必须 reset —— 只 set 不 reset 等于换个地方污染。"""
    src = _read("cron/scheduler.py")
    assert re.search(r"_cron_session_(var|token)", src), "cron session token 机制不见了"
    assert ".reset(" in src or "clear_session_vars" in src, (
        "上游 cron 不再还原 session ContextVar —— 一个 job 的标记会漏给后续的 turn。"
    )


def test_审批判定优先读_session_作用域():
    """approval 仍然优先走 session 作用域, 而不是直接读进程 env。"""
    src = _read("tools/approval.py")
    assert "get_session_env" in src, (
        "tools/approval.py 不再用 get_session_env —— 审批判定可能回到进程级 env。"
    )
    # 上游 docstring 里那句承诺也钉一下: 它是这条依赖的来源
    assert re.search(r"cron job cannot taint unrelated", src), (
        "上游不再承诺「一个 cron job 不会污染无关的 turn」—— 契约变了, "
        "去读实现确认 P25 要不要复活。"
    )


def test_没有人往全局env写这个变量():
    """全树扫一遍: 不该再有 `os.environ["HERMES_CRON_SESSION"] = ...`。

    这是 P25 病因的**直接判据** —— 有人写, 病就回来了。
    """
    offenders: list[str] = []
    pat = re.compile(
        r"""os\.environ\[\s*['"]HERMES_CRON_SESSION['"]\s*\]\s*=|"""
        r"""putenv\(\s*['"]HERMES_CRON_SESSION['"]|"""
        r"""setdefault\(\s*['"]HERMES_CRON_SESSION['"]"""
    )
    for py in HERMES_ROOT.rglob("*.py"):
        s = str(py)
        if "/tests/" in s or ".hermes-runtime" in s:
            continue
        try:
            txt = py.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, line in enumerate(txt.splitlines(), 1):
            if pat.search(line):
                offenders.append(f"{py.relative_to(HERMES_ROOT)}:{i}: {line.strip()[:80]}")
    assert not offenders, (
        "上游又开始往全局 env 写 HERMES_CRON_SESSION —— P25 的病回来了:\n"
        + "\n".join(offenders)
        + "\n见 plugin_cron.py 尾部的 P25 墓碑。"
    )


def test_p25_确实已经从插件里删干净了():
    """墓碑留着, 代码不留。"""
    plugin_dir = Path(__file__).resolve().parent.parent
    live: list[str] = []
    for py in plugin_dir.glob("*.py"):
        txt = py.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(txt.splitlines(), 1):
            st = line.strip()
            if st.startswith("#") or not st:
                continue
            if "_patch_p25" in st or "_CATFISH_CRON_THREAD_LOCAL" in st:
                live.append(f"{py.name}:{i}: {st[:70]}")
    assert not live, "P25 还有活引用:\n" + "\n".join(live)
