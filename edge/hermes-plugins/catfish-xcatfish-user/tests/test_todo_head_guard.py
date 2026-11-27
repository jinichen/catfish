"""todo_head_guard (11/27): 脚本改完提醒事项, 开头状态旧的当场接在工具结果后面。"""
import importlib.util
import json
from datetime import date
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("thg_under_test", Path(__file__).parents[1] / "todo_head_guard.py")
thg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(thg)

TODAY = date(2026, 11, 27)
STALE = ("【当前状态 2026-11-20】公示≠已发证。" + "通稿改动记录。" * 60
         + "\n2026-11-27 09:08 官网挂网取证：ITSS-YW-1-350020260016。")
FIXED = "【当前状态 2026-11-27 09:08】已取证 ITSS-YW-1-350020260016。\n【历史】" + STALE


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    monkeypatch.setattr(thg, "date", type("D", (), {"today": staticmethod(lambda: TODAY)}))
    box = {"reminders": [{"id": "r1", "name": "ITSS 一级", "body": STALE}], "reads": 0}

    def read():
        box["reads"] += 1
        return box["reminders"]
    monkeypatch.setattr(thg, "read_reminders", read)
    return box


def test_脚本改了提醒事项_开头旧_接在结果后面(env):
    out = thg.tool_execution_middleware("execute_code", {"code": "subprocess.run(['remindctl','edit',rid,'--notes',n])"},
                                        lambda a: '{"status": "success", "output": "ok"}')
    r = json.loads(out)
    assert r["output"] == "ok" and "ITSS 一级" in r["todo_head_notice"] and "2026-11-27 09:08" in r["todo_head_notice"]


def test_同一份正文不重复提醒_改好了不提醒(env):
    run = lambda: thg.tool_execution_middleware("terminal", {"command": "remindctl edit X --notes ..."}, lambda a: "done")
    assert "⚠️" in run()
    assert run() == "done"
    env["reminders"][0]["body"] = FIXED
    assert run() == "done"


def test_没提到提醒事项的脚本不读(env):
    assert thg.tool_execution_middleware("execute_code", {"code": "print(1)"}, lambda a: "1") == "1"
    assert thg.tool_execution_middleware("read_file", {"path": "Reminders.md"}, lambda a: "x") == "x"
    assert env["reads"] == 0


def test_读不到提醒事项_结果原样(env, monkeypatch):
    monkeypatch.setattr(thg, "read_reminders", lambda: None)
    assert thg.tool_execution_middleware("terminal", {"command": "remindctl list"}, lambda a: "ok") == "ok"
