"""P21 / P26 / P59 装到**真 hermes** 上, 验它们真的被调到 —— 不是验"装上了"。

# 为什么要有这个文件

这三个补丁失效时都不报错: 启动日志照样写「已装上」, 但 hermes 调的已经不是我们换上去
的那个函数。P27 (cron 失败重试) 就是这么死的 —— 调度器启动时把 mark_job_run 绑成了
本地名字, 换模块属性碰不到它, 又赶上 v0.22 加了关键字参数, 从装上那天起就没生效过,
10/14 审计才发现。

所以这里拿真 hermes 的调用方去解析名字, 再真调一次:
  · P21  定时任务跟 Companion 里选的模型走: 调度器 / 会话属主调 run_job 时拿到的是补丁,
         并且 hermes 真正「定模型」的那一步读到的是 picker 的模型
  · P26  卡片上的暂停 / 恢复 / 删除: 用我们的 handler 对真 cron.jobs 建一条任务再操作
  · P59  定时任务里的「始终允许」: 三个包装点都换上了、没有模块攥着原函数、定时任务的会话
         标记下 hermes 认得出"无人值守"
  · 另外钉住: Mac / Windows 定时任务一律在 gateway 进程里跑 (补丁才在场)

升级 hermes 前跑: `HERMES_ROOT=<新版树> pytest tests/test_cron_patches_on_real_hermes.py`。
它要用那棵树自己的 venv (`$HERMES_ROOT/venv/bin/python`), 没有就跳过 (CI / 沙箱)。
HERMES_HOME 指向临时目录, 不碰员工真数据。静态锚点在 audit_hermes_compat.sh Section 21。
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

HERMES_ROOT = Path(os.environ.get("HERMES_ROOT", Path.home() / ".hermes" / "hermes-agent"))
HERMES_PY = HERMES_ROOT / "venv" / "bin" / "python"
PLUGIN_DIR = Path(__file__).resolve().parent.parent
DRIVER = Path(__file__).resolve().parent / "_real_hermes_cron_driver.py"

pytestmark = pytest.mark.skipif(
    not (HERMES_PY.exists() and (HERMES_ROOT / "cron" / "scheduler.py").exists()),
    reason=f"没有带 venv 的 hermes 树可对照 ({HERMES_ROOT}) —— CI/沙箱里跳过",
)

# 每一类至少要有这些检查真的跑出结果 —— 防止驱动半路挂了、少报几条也显示全绿
MUST_RUN = (
    "P21 经补丁跑一次, 真的定模型那一步拿到的是 picker 的",
    "P21 会话属主在调用时才 import run_job (拿到补丁)",
    "P21 没有模块攥着原 run_job (P27 那种死法)",
    "P26 暂停: 接口 200 且任务真停了",
    "P26 恢复: 接口 200 且任务重新启用",
    "P26 删除: 接口 200 且任务真没了",
    "P59 没有模块攥着原 check_execute_code_guard (绕过补丁)",
    "P59 定时任务里 _unattended_contexts() 为真 (删除类在定时任务里才会被拦)",
    "非 Linux 一律进程内跑定时任务 (补丁在 gateway 进程里生效)",
)


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    home = tmp_path_factory.mktemp("hermes-home")
    env = {"HOME": str(home), "HERMES_HOME": str(home / ".hermes"), "PATH": "/usr/bin:/bin"}
    proc = subprocess.run(
        [str(HERMES_PY), "-I", str(DRIVER), str(HERMES_ROOT), str(PLUGIN_DIR)],
        cwd=home, env=env, capture_output=True, text=True, timeout=600,
    )
    rows = [json.loads(line) for line in proc.stdout.splitlines() if line.startswith("{")]
    assert rows, f"驱动没产出结果 (exit={proc.returncode})\n{proc.stderr[-3000:]}"
    return rows


def test_every_check_passes(results):
    bad = [f"✗ {r['check']}  {r['detail']}" for r in results if not r["ok"]]
    assert not bad, "hermes 这一版上补丁不再生效:\n" + "\n".join(bad)


@pytest.mark.parametrize("name", MUST_RUN)
def test_key_check_actually_ran(results, name):
    assert name in {r["check"] for r in results}, f"这条没跑到 (驱动中途挂了?): {name}"
