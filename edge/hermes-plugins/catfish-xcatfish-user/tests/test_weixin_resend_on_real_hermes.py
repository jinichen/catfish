"""微信补发装到**真 hermes** 上走一遍: 推送被拒 → 内容留下 → 员工说话 → 补发 → 清掉没送达状态。

包的是 cron.scheduler._deliver_result。要是哪天 hermes 让某个模块攥住了原函数 (P27 就是这么死的),
或者改了 session not ready 的报错原话, 这里会红。升级 hermes 前跑:
`HERMES_ROOT=<新版树> pytest tests/test_weixin_resend_on_real_hermes.py`。没有带 venv 的 hermes 树就跳过。
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
DRIVER = Path(__file__).resolve().parent / "_real_hermes_weixin_resend_driver.py"

pytestmark = pytest.mark.skipif(
    not (HERMES_PY.exists() and (HERMES_ROOT / "cron" / "scheduler.py").exists()),
    reason=f"没有带 venv 的 hermes 树可对照 ({HERMES_ROOT}) —— CI/沙箱里跳过",
)

MUST_RUN = (
    "装上了: cron.scheduler._deliver_result 换成了包装",
    "没有模块攥着原 _deliver_result (投递都经过包装)",
    "真投递: 假微信按 iLink 原话拒绝, 返回的错误认得出是会话过期",
    "内容留下了",
    "员工说话后补发成功",
    "补发内容带补发说明和原文",
    "真 update_job 清掉了没送达状态",
    "再说一句话不重复补发",
    "提醒推送补发: 经网关微信通道发出, 文件清空",
    "pre_gateway_dispatch 是 hermes 认的钩子",
    "驱动跑完",
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
    assert not bad, "hermes 这一版上微信补发不再生效:\n" + "\n".join(bad)


@pytest.mark.parametrize("name", MUST_RUN)
def test_key_check_actually_ran(results, name):
    assert name in {r["check"] for r in results}, f"这条没跑到 (驱动中途挂了?): {name}"
