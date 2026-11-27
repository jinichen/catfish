"""脚本里直接改提醒事项后, 待办正文开头还是旧状态 → 当场告诉小鲶改开头 (11/27)。

为什么: 见 tool-bridge task_notes.py。小鲶改待办正文两条路 —— catfish_create_task (77 次, tool-bridge 那边管) 和
execute_code / terminal 里直接 `remindctl edit --notes` / osascript (273 次, 这里管)。脚本里正文是变量拼的,
执行前看不出写成什么样, 所以挂 tool_execution 中间件: 脚本提到提醒事项的, 跑完后读一遍未完成的提醒
(JXA 一次批量取, 约 6 秒), 正文跟上次不一样、而且开头状态比后面的记录旧的, 把提醒接在工具结果后面。

不撤回: 撤回会把刚写进去的新进展弄丢, 比开头旧更糟。没改的, catfish_list_tasks 每次都会标 (head_stale),
早安那头另有一道保险 (latestProgress 带上日期最新的一行)。
只在 macOS 有提醒事项; Windows 上待办只走 catfish_create_task。

stale_head 跟 tool-bridge task_notes.stale_head 是同一份判据 (插件不能 import tool-bridge),
tool-bridge tests/test_task_notes.py 钉住两份结果一致 —— 改一边要改另一边。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("catfish-xcatfish-user.todo_head_guard")

_TOOLS = ("execute_code", "terminal")
_MENTIONS = re.compile(r"remindctl|Reminders|x-apple-reminder|EKReminder", re.I)
_JXA = ('var r=Application("Reminders").reminders.whose({completed:false});'
        'var i=r.id(),n=r.name(),b=r.body();'
        'JSON.stringify(i.map(function(x,k){return {id:x,name:n[k],body:b[k]||""}}))')

HEAD_CHARS = 300
_FULL = re.compile(r"(20\d{2})[-./年](\d{1,2})[-./月](\d{1,2})日?(?:\s*(\d{1,2}):(\d{2}))?")
_LEAD = re.compile(r"^\s*(?:[-·•*]\s*)?(\d{1,2})(?:/|月)(\d{1,2})日?(?:\s+(\d{1,2}):(\d{2}))?(?![\d/%])")
_TAG = re.compile(r"\[catfish-task:[^\]]*\]")


def _key(y: int, m: int, d: int, hh: int = 0, mm: int = 0) -> int:
    return ((y * 100 + m) * 100 + d) * 10000 + hh * 100 + mm


def latest_date_key(text: str, today: date, *, lead: bool = False) -> int:
    limit = _key(today.year, today.month, today.day, 23, 59)
    best = 0
    for m in _FULL.finditer(text):
        k = _key(int(m[1]), int(m[2]), int(m[3]), int(m[4] or 0), int(m[5] or 0))
        if best < k <= limit and 1 <= int(m[2]) <= 12 and 1 <= int(m[3]) <= 31:
            best = k
    if lead:
        m = _LEAD.match(text)
        if m and 1 <= int(m[1]) <= 12 and 1 <= int(m[2]) <= 31:
            k = _key(today.year, int(m[1]), int(m[2]), int(m[3] or 0), int(m[4] or 0))
            if best < k <= limit:
                best = k
    return best


def _fmt(k: int) -> str:
    d, hm = divmod(k, 10000)
    y, md = divmod(d, 10000)
    return f"{y}-{md // 100:02d}-{md % 100:02d}" + (f" {hm // 100:02d}:{hm % 100:02d}" if hm else "")


def stale_head(body: Any, today: Optional[date] = None) -> Optional[dict]:
    today = today or date.today()
    raw = _TAG.sub("", str(body or ""))
    text = re.sub(r"\s+", " ", raw).strip()
    if len(text) <= HEAD_CHARS:
        return None
    head_key = latest_date_key(text[:HEAD_CHARS], today)
    if not head_key:
        return None
    newest_key, newest = head_key, ""
    for line in raw.splitlines():
        k = latest_date_key(line, today, lead=True)
        if head_key % 10000 == 0 and k // 10000 == head_key // 10000:
            continue  # 开头只写了日期没写时分: 同一天的记录不算更新
        if k > newest_key:
            newest_key, newest = k, re.sub(r"\s+", " ", line).strip()
    if not newest or newest in text[:HEAD_CHARS]:
        return None
    return {"head_date": _fmt(head_key), "newest_date": _fmt(newest_key), "newest_line": newest[:200]}


def stale_notice(title: str, info: dict) -> str:
    return (f"⚠️ 待办「{title}」正文开头的状态是 {info['head_date']} 的, 后面却有 {info['newest_date']} 的新进展"
            f"（「{info['newest_line'][:80]}」）。早安卡片 / 周报 / 问小鲶只读正文开头, 会按旧状态说话。"
            "现在就改这条待办的正文: 开头换成最新的【当前状态 日期】和【下一步】, 旧状态挪到后面的【历史】; 事情办完了就标完成。")


# ── 读提醒事项 / 记上次的样子 ─────────────────────────────────────────

def _state_path() -> Path:
    env = os.environ.get("CATFISH_HOME", "").strip()
    return (Path(env).expanduser() if env else Path.home() / ".catfish") / "todo_head_state.json"


def read_reminders() -> Optional[list[dict]]:
    if sys.platform != "darwin":
        return None
    try:
        out = subprocess.run(["osascript", "-l", "JavaScript", "-e", _JXA], capture_output=True, text=True, timeout=30)
        return json.loads(out.stdout) if out.returncode == 0 else None
    except Exception as e:  # noqa: BLE001
        logger.debug("todo_head_guard: 读提醒事项失败: %s", e)
        return None


def check(reminders: list[dict], today: Optional[date] = None) -> list[str]:
    """正文跟上次记下的不一样、且开头状态旧的 → 提醒语; 顺手把这次的样子记下。"""
    path = _state_path()
    try:
        seen = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        seen = {}
    now, notices = {}, []
    for r in reminders:
        body = str(r.get("body") or "")
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
        now[str(r.get("id"))] = digest
        if seen.get(str(r.get("id"))) == digest:
            continue
        info = stale_head(body, today)
        if info:
            notices.append(stale_notice(str(r.get("name") or ""), info))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(now), encoding="utf-8")
    except OSError:
        pass
    return notices[:3]


def tool_execution_middleware(tool_name: str = "", args: Optional[dict] = None, next_call: Any = None, **_kw: Any) -> Any:
    args = args if isinstance(args, dict) else {}
    result = next_call(args)
    try:
        if str(tool_name or "") not in _TOOLS or not _MENTIONS.search(json.dumps(args, ensure_ascii=False)):
            return result
        reminders = read_reminders()
        notices = check(reminders) if reminders else []
        if not notices:
            return result
        note = "\n".join(notices)
        try:
            parsed = json.loads(result) if isinstance(result, str) else None
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            parsed["todo_head_notice"] = note
            return json.dumps(parsed, ensure_ascii=False)
        return f"{result}\n\n{note}" if isinstance(result, str) else result
    except Exception as e:  # noqa: BLE001 — 这道检查出错不能影响工具结果
        logger.warning("todo_head_guard: %s", e)
        return result
