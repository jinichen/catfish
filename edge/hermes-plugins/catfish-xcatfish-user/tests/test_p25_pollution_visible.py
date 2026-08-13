"""P25 接住污染的那一刻必须在生产日志里看得见。

# 背景

P25 (6/24) 治的是 hermes cron 把 `HERMES_CRON_SESSION` 写进 `os.environ` 不清、
污染整个 daemon 进程的 bug —— 之后任何 chat / api 调 execute_code 都会被
`cron_mode=deny` 误拦。P25 用 threadlocal 精准判定"本线程真在 cron 里", 非 cron
线程就把污染的 env 清掉再放行。

那个"清掉再放行"的时刻是**整个 P25 唯一能证明自己还有用的证据**。8/13 之前它是
`logger.debug`, 而 agent.log 里 DEBUG 一条都没有 (9347 INFO / 304 WARNING) ——
三个月没人看得见。

# 为什么这条日志现在是个实验

hermes 上游已经把 cron session 从 os.environ 改成 ContextVar + token 还原
(`cron/scheduler.py:3124`, 注释原话 "one cron job cannot taint unrelated
gateway/API/TUI turns in the same process")。也就是 P25 当初治的病, 新版 hermes
自己治了。

所以这条日志的出现与否是判断 P25 能否退役的**唯一现场证据**:
  · 打出来 → 本机还有走真 env 的路径, P25 必要
  · 长期不打 → 可以考虑退役 (但要先确认所有部署的 hermes 版本)

一个"只在 debug 级打"的证据等于没有证据。这个文件钉住它不能再掉回去。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_DIR))

_SRC = (_DIR / "plugin_cron.py").read_text(encoding="utf-8")


def _guard_wrapper_node():
    """取 P25 里那个 `_patched_check_execute_code_guard` 嵌套函数。"""
    outer = next(n for n in ast.parse(_SRC).body
                 if getattr(n, "name", None) == "_patch_p25_cron_env_isolation")
    for sub in ast.walk(outer):
        if (isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef))
                and sub.name == "_patched_check_execute_code_guard"):
            return sub
    pytest.fail("找不到 _patched_check_execute_code_guard —— P25 结构变了")


def test_pollution_rescue_is_not_logged_at_debug():
    """★ 那条日志不能是 debug —— 生产日志里 DEBUG 是关的。

    实测: agent.log 9347 INFO / 304 WARNING / **0 DEBUG**。用 debug 记一个
    "只在此刻发生的关键事件", 等于把唯一的证据写进一个没人读的地方。
    """
    node = _guard_wrapper_node()
    levels = [
        c.func.attr
        for c in ast.walk(node)
        if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
        and isinstance(c.func.value, ast.Name) and c.func.value.id == "logger"
    ]
    assert levels, "P25 的 guard wrapper 里一条日志都没有 —— 污染事件将完全不可见"
    assert "debug" not in levels, (
        f"污染接住的那一刻又掉回 debug 了 (当前: {levels})。"
        "生产日志 DEBUG 是关的, 这等于没记。"
    )
    assert any(lv in ("warning", "error", "info") for lv in levels)


def test_rescue_log_carries_the_polluted_value():
    """日志要带上被清掉的值 —— 光说"发生了"不够, 要能判断是谁留下的。"""
    node = _guard_wrapper_node()
    for c in ast.walk(node):
        if (isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                and isinstance(c.func.value, ast.Name) and c.func.value.id == "logger"):
            # 必须有格式化参数, 不能是一句干巴巴的常量
            assert len(c.args) >= 2, "日志没带上被清掉的 env 值"
            assert any(isinstance(a, ast.Name) and a.id == "_prev_env" for a in c.args[1:])
            return
    pytest.fail("没找到那条日志")


def test_rescue_log_fires_only_when_pollution_existed():
    """只在 `_prev_env is not None` 时打 —— 每次 execute_code 都打会淹掉日志。

    execute_code 是高频调用。今天翻 gateway.log 时几百行 `/v1/catalog` 已经吃过
    这个亏: 无差别记录会让真正的事件找不到。
    """
    node = _guard_wrapper_node()
    guarded = False
    for sub in ast.walk(node):
        if not isinstance(sub, ast.If):
            continue
        cond = ast.unparse(sub.test)
        if "_prev_env" not in cond:
            continue
        has_log = any(
            isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
            and isinstance(c.func.value, ast.Name) and c.func.value.id == "logger"
            for c in ast.walk(sub)
        )
        if has_log:
            guarded = True
            assert "is not None" in cond or "!=" in cond, f"判据可疑: {cond}"
    assert guarded, "那条日志不在 `_prev_env is not None` 的保护下 —— 会每次都打"


def test_logic_untouched_only_the_level_changed():
    """这次只准改日志, 不准动判定逻辑。

    P25 管的是 execute_code 会不会被误拦, 改错了的表现是员工偶发被拦 ——
    6/24 那次就是这么熬出来的。所以把三个关键行为钉住:
      · 在 cron 线程 → 直接走原函数, 不 pop
      · 非 cron 线程 → pop 之后再调
      · *args/**kwargs 透传 (7/7 踩过 hermes v0.18 加第 3 个参数)
    """
    node = _guard_wrapper_node()
    seg = ast.get_source_segment(_SRC, node)

    assert "in_cron" in seg, "threadlocal 判据没了"
    assert 'os.environ.pop("HERMES_CRON_SESSION", None)' in seg, "非 cron 分支的 pop 没了"
    assert node.args.vararg is not None and node.args.kwarg is not None, (
        "*args/**kwargs 透传没了 —— hermes 再加参数就 TypeError (7/7 P3.5.192)"
    )
    # 原函数必须被调到两次 (cron 分支 + 非 cron 分支)
    calls = [c for c in ast.walk(node)
             if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
             and c.func.id == "_orig_check"]
    assert len(calls) == 2, f"_orig_check 调用点应为 2 处 (cron / 非 cron), 实际 {len(calls)}"


def test_retirement_note_points_at_the_real_hazard():
    """退役前要读的那段注释必须还在。

    这不是"注释要写全"的洁癖 —— 非 cron 分支那个 pop 是**不恢复**的, 而 hermes
    现在把 os.environ 留作 standalone cron 入口和测试的合法兜底。谁哪天来退役
    P25, 必须先看到这一条, 否则会把"防误拦"改成"漏放行"。
    """
    outer_seg = next(
        ast.get_source_segment(_SRC, n) for n in ast.parse(_SRC).body
        if getattr(n, "name", None) == "_patch_p25_cron_env_isolation"
    )
    assert "ContextVar" in outer_seg, "没记 hermes 上游已改用 ContextVar 这件事"
    assert "不恢复" in outer_seg or "standalone" in outer_seg, "没记那个 pop 不恢复的风险"
