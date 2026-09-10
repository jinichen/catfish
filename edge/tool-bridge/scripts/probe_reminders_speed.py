#!/usr/bin/env python3
"""9/10: 量一下 Reminders 全量读取现在要几秒 (早安页「RPC tools/dispatch 超时」真因排查).

    cd edge/tool-bridge && python3 scripts/probe_reminders_speed.py

打印 全量(含已完成) / 仅未完成 两种读法的耗时与条数。目标: 全量 < 5s。
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from catfish_tool_bridge import reminders  # noqa: E402

for label, args in (
    ("全量(含已完成, task_library 导入用)", {"scope": "all", "include_completed": True, "limit": 500}),
    ("仅未完成", {"scope": "all", "include_completed": False, "limit": 500}),
):
    t = time.time()
    r = reminders.tool_list_reminders(args, timeout_sec=60.0)
    dt = time.time() - t
    print(f"{label}: {dt:.1f}s  ok={r.get('ok')}  条数={len(r.get('reminders', []))}  {r.get('error') or ''}")
