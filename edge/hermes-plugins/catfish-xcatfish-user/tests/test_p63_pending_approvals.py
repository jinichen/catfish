"""P63: 等员工批准的步骤列表 —— 只读 adapter._run_statuses, 只给还在等的。"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent / "plugin_pending_approvals.py"
_spec = importlib.util.spec_from_file_location("plugin_pending_approvals", _PATH)
mod = importlib.util.module_from_spec(_spec)
sys.modules["plugin_pending_approvals"] = mod
_spec.loader.exec_module(mod)


class _Adapter:
    def __init__(self, statuses):
        self._run_statuses = statuses


def test_只给还在等批准的_带回执要的字段():
    a = _Adapter({
        "run-b": {"status": "waiting_for_approval", "created_at": 20, "session_id": "s2",
                  "approval": {"request_id": "r2", "execution_generation": 1,
                               "command": "rm x", "description": "删除文件", "session_id": "s2"}},
        "run-a": {"status": "waiting_for_approval", "created_at": 10,
                  "approval": {"request_id": "r1", "command": "curl", "description": "联网"}},
        "run-c": {"status": "running", "created_at": 5},
        "run-d": {"status": "completed", "created_at": 1, "approval": {"request_id": "old"}},
    })
    got = mod.snapshot(a)
    assert [x["run_id"] for x in got] == ["run-a", "run-b"]
    assert got[1] == {"run_id": "run-b", "created_at": 20, "request_id": "r2", "execution_generation": 1,
                      "command": "rm x", "description": "删除文件", "session_id": "s2"}


def test_没有adapter或格式不对_返回空():
    assert mod.snapshot(None) == []
    assert mod.snapshot(_Adapter({"x": "坏数据"})) == []
