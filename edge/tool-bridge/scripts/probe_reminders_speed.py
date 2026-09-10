#!/usr/bin/env python3
"""9/10: 逐句量 Reminders AppleScript 哪一步慢 (早安页「RPC tools/dispatch 超时」真因排查).

    cd edge/tool-bridge && python3 scripts/probe_reminders_speed.py

不猜: 每个 osascript 语句单独跑、单独计时。
"""
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from catfish_tool_bridge import reminders  # noqa: E402


def run(label: str, script: str) -> str:
    t = time.time()
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=120)
    dt = time.time() - t
    out = (r.stdout or r.stderr).strip().replace("\n", " ")
    print(f"{dt:6.2f}s  {label:<44} {out[:70]}")
    return r.stdout.strip()


print("── 全局 ──")
run("count of lists", 'tell application "Reminders" to count of lists')
names = run("name of every list", 'tell application "Reminders" to name of every list')
run("count of reminders (app 级)", 'tell application "Reminders" to count of reminders')
run("name of every reminder (app 级, 一次事件)", 'tell application "Reminders" to name of every reminder')
run("properties of every reminder (app 级)", 'tell application "Reminders" to properties of every reminder')
run("id/name/completed/due 各一次 (app 级)",
    'tell application "Reminders"\n'
    'set a to id of every reminder\nset b to name of every reminder\n'
    'set c to completed of every reminder\nset d to due date of every reminder\n'
    'return count of a\nend tell')

print("── 逐清单 ──")
for n in [x.strip() for x in names.split(",") if x.strip()]:
    esc = n.replace('"', '\\"')
    run(f"count of reminders of list \"{n}\"", f'tell application "Reminders" to count of reminders of list "{esc}"')
    run(f"name of every reminder of list \"{n}\"", f'tell application "Reminders" to name of every reminder of list "{esc}"')
    run(f"properties of every reminder of list \"{n}\"", f'tell application "Reminders" to properties of every reminder of list "{esc}"')

print("── 现行整段脚本 ──")
for label, args in (
    ("全量(含已完成)", {"scope": "all", "include_completed": True, "limit": 500}),
    ("仅未完成", {"scope": "all", "include_completed": False, "limit": 500}),
):
    t = time.time()
    r = reminders.tool_list_reminders(args, timeout_sec=120.0)
    print(f"{time.time()-t:6.2f}s  {label:<44} ok={r.get('ok')} 条数={len(r.get('reminders', []))} {r.get('error') or ''}")
