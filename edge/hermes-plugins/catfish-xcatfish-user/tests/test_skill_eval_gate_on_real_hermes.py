"""技能验证题门槛装到**真 hermes** 上走一遍 (真 model_tools → tool_execution 中间件 → 真 skill_manage)。

升级 hermes 前跑: `HERMES_ROOT=<新版树> pytest tests/test_skill_eval_gate_on_real_hermes.py`。没有带 venv 的 hermes 树就跳过。
要是哪天 hermes 改了中间件的参数 (tool_call_id / session_id / next_call) 或 skill_manage 的返回, 这里会红。
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
DRIVER = Path(__file__).resolve().parent / "_real_hermes_skill_eval_driver.py"

pytestmark = pytest.mark.skipif(
    not (HERMES_PY.exists() and (HERMES_ROOT / "tools" / "skill_manager_tool.py").exists()),
    reason=f"没有带 venv 的 hermes 树可对照 ({HERMES_ROOT}) —— CI/沙箱里跳过",
)

MUST_RUN = (
    "真 skill_manage 改丢口径: 工具结果是「已撤回」",
    "真 skill_manage 改丢口径: 技能恢复成改之前",
    "没碰口径的改动照常生效",
    "作答拖 35 秒 (过钩子 30 秒限时): 工具等验完才返回, 结果是撤回",
    "删有员工口径的技能 (没写并进哪): 拦下, 技能还在",
    "门槛日志记了撤回和拦删",
    "驱动跑完",
)


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    home = tmp_path_factory.mktemp("hermes-home")
    env = {"HOME": str(home), "HERMES_HOME": str(home / ".hermes"), "PATH": "/usr/bin:/bin"}
    (home / ".hermes").mkdir()
    proc = subprocess.run(
        [str(HERMES_PY), "-I", str(DRIVER), str(HERMES_ROOT), str(PLUGIN_DIR)],
        cwd=home, env=env, capture_output=True, text=True, timeout=600,
    )
    rows = [json.loads(line) for line in proc.stdout.splitlines() if line.startswith("{")]
    assert rows, f"驱动没产出结果 (exit={proc.returncode})\n{proc.stderr[-3000:]}"
    return {r["check"]: r for r in rows}


@pytest.mark.parametrize("check", MUST_RUN)
def test_真hermes上(results, check):
    r = results.get(check)
    assert r is not None, f"这一项没跑到: {check}\n已跑: {list(results)}"
    assert r["ok"], f"{check}: {r['detail']}"
