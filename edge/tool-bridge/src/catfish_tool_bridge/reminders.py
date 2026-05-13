"""BL-REMINDER (5/13 鸿波"macOS 提醒联动") — catfish_create_reminder 模块.

跟 5/2 BL-E13 notify (macOS 通知中心右上角横幅消息) 互补:
- notify: 一次性弹窗, 几秒消失, 不持久, 不跨设备
- create_reminder: 真 to-do, 用户能勾完成, iCloud 同步到 iPhone/iPad

实现走 osascript + Reminders.app (跟 commands/system.rs Tauri command 同源).
tool-bridge 这边是给 LLM 调用用 (走 hermes adapter dispatch_native), Companion
里 Tauri command 是给 UI 直接调 (e.g. 仪表盘加"加 reminder" 按钮).

为什么两份实现? 因为 tool-bridge 跟 Companion 是不同进程, 各自需要 osascript 调用.

用例:
- 鸿波: "提醒我明早 9 点交月报" → LLM 调 catfish_create_reminder(title='交月报', due_date_iso='2026-05-14T09:00:00')
- 鸿波: "记下下周三给王总汇报" → LLM 调 catfish_create_reminder(title='给王总汇报', due_date_iso='...', body='Q2 进度')
- LLM 自己识别 "别忘了... " / "记得..." → 主动调
"""
from __future__ import annotations

import logging
import platform
import shlex
import subprocess
from typing import Any

logger = logging.getLogger("catfish.tool_bridge.reminders")


def _is_macos() -> bool:
    return platform.system() == "Darwin"


def _run_osascript(script: str, timeout_sec: float = 10.0) -> tuple[bool, str, str]:
    """跑一段 AppleScript, 返 (success, stdout, stderr).

    timeout_sec: 防卡住. Reminders.app 没启动的话首次会慢一点 (1-2 秒), 给 10 秒够.
    """
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
        return (
            result.returncode == 0,
            (result.stdout or "").strip(),
            (result.stderr or "").strip(),
        )
    except subprocess.TimeoutExpired:
        return (False, "", f"osascript 超 {timeout_sec}s")
    except FileNotFoundError:
        return (False, "", "osascript 命令不存在 (非 macOS?)")
    except Exception as e:  # noqa: BLE001
        return (False, "", f"osascript 调用异常: {e}")


def _escape_applescript_string(s: str) -> str:
    """AppleScript 字符串里 " 和 \\ 都要 escape."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _convert_iso_to_applescript_date(iso: str) -> str:
    """ISO 8601 (e.g. '2026-05-14T09:00:00') → AppleScript 'YYYY-MM-DD HH:MM:SS'.

    AppleScript 接受 'date "YYYY-MM-DD HH:MM:SS"' 形式 (本地时区).
    简单转换: 替换 T 为空格, 砍掉时区后缀.
    """
    s = iso.replace("T", " ")
    # 砍掉 +08:00 / +0800 / Z 之类时区后缀
    for sep in ("+", "-"):
        # 找 HH:MM 之后的 +/-, 不是日期里的 -
        idx = s.rfind(sep)
        if idx > 10:  # 日期 yyyy-mm-dd 有 - 在前 10 字符内
            s = s[:idx]
            break
    if s.endswith("Z"):
        s = s[:-1]
    return s.strip()


def tool_create_reminder(args: dict[str, Any]) -> dict[str, Any]:
    """catfish_create_reminder tool 入口.

    顺序: 输入校验 → 平台校验 → osascript.
    校验放前面是为了 LLM 错调时不论什么平台都能拿到清晰错误 (e.g. 忘传 title).
    """
    title = (args.get("title") or "").strip()
    if not title:
        return {"ok": False, "error": "title 不能空 (catfish_create_reminder 必填字段)"}

    body = (args.get("body") or "").strip()
    due_date_iso = (args.get("due_date_iso") or "").strip()
    list_name = (args.get("list_name") or "提醒事项").strip()
    priority_raw = args.get("priority")

    # priority 校验提前 (LLM 传错可能性高), 平台校验之前
    priority_int: int | None = None
    if priority_raw is not None:
        try:
            priority_int = max(0, min(9, int(priority_raw)))
        except (TypeError, ValueError):
            return {"ok": False, "error": f"priority 应是 0-9 整数, got: {priority_raw!r}"}

    if not _is_macos():
        return {
            "ok": False,
            "error": "create_reminder 只 macOS 支持 (走 Reminders.app). 当前平台: "
                     + platform.system(),
        }

    # 拼 AppleScript properties record
    safe_title = _escape_applescript_string(title)
    safe_list = _escape_applescript_string(list_name)
    props = [f'name:"{safe_title}"']

    if body:
        safe_body = _escape_applescript_string(body)
        props.append(f'body:"{safe_body}"')

    if due_date_iso:
        try:
            applescript_date = _convert_iso_to_applescript_date(due_date_iso)
            safe_date = _escape_applescript_string(applescript_date)
            props.append(f'due date:date "{safe_date}"')
        except Exception as e:  # noqa: BLE001
            return {
                "ok": False,
                "error": f"due_date_iso 格式错 (期望 'YYYY-MM-DDTHH:MM:SS'): {e}",
            }

    if priority_int is not None:
        props.append(f"priority:{priority_int}")

    script = f'''tell application "Reminders"
    set targetList to first list whose name is "{safe_list}"
    set newReminder to make new reminder at end of targetList with properties {{{", ".join(props)}}}
    return name of newReminder
end tell'''

    ok, stdout, stderr = _run_osascript(script)
    if not ok:
        # 常见错误友好化
        if "Not authorized" in stderr or "权限" in stderr or "not allowed" in stderr.lower():
            return {
                "ok": False,
                "error": (
                    "Reminders.app 权限未给 — 系统设置 → 隐私与安全性 → 提醒事项 → "
                    "勾上 Catfish Companion (或 Terminal / Python). 然后再试. "
                    f"原始错误: {stderr}"
                ),
                "needs_permission": True,
            }
        if "Can’t get list" in stderr or "can’t get" in stderr.lower() or "doesn’t exist" in stderr.lower():
            return {
                "ok": False,
                "error": (
                    f"list \"{list_name}\" 不存在. 可用 list 调 catfish_list_reminder_lists 看. "
                    f"中文系统默认 '提醒事项', 英文系统 'Reminders'. 原始错误: {stderr}"
                ),
                "list_not_found": list_name,
            }
        return {"ok": False, "error": f"osascript 失败: {stderr}"}

    logger.info("BL-REMINDER: 创建提醒 '%s' (list=%s, due=%s)", title, list_name, due_date_iso or "无")
    return {
        "ok": True,
        "reminder_name": stdout or title,
        "list_name": list_name,
        "due_date_iso": due_date_iso or None,
        "summary": f"⏰ 已在 Reminders.app 「{list_name}」list 创建提醒 「{title}」"
                   + (f" — {due_date_iso}" if due_date_iso else " (无截止)")
                   + ". iCloud 同步到 iPhone/iPad. 用户在 Reminders.app 里能勾完成.",
    }


def tool_list_reminder_lists(args: dict[str, Any]) -> dict[str, Any]:
    """catfish_list_reminder_lists tool 入口."""
    if not _is_macos():
        return {
            "ok": False,
            "error": "list_reminder_lists 只 macOS 支持. 当前平台: " + platform.system(),
            "list_names": [],
        }

    script = '''tell application "Reminders"
    set lst to {}
    repeat with L in lists
        set end of lst to name of L
    end repeat
    return lst
end tell'''

    ok, stdout, stderr = _run_osascript(script)
    if not ok:
        if "Not authorized" in stderr or "权限" in stderr:
            return {
                "ok": False,
                "error": "Reminders.app 权限未给 — 系统设置 → 隐私与安全性 → 提醒事项",
                "needs_permission": True,
                "list_names": [],
            }
        return {"ok": False, "error": f"osascript 失败: {stderr}", "list_names": []}

    # osascript list 返回是 ", " 分隔字符串
    lists = [s.strip() for s in stdout.split(", ") if s.strip()]
    return {
        "ok": True,
        "list_names": lists,
        "count": len(lists),
        "summary": f"📋 macOS Reminders.app 有 {len(lists)} 个 list: {', '.join(lists) if lists else '(无)'}",
    }


__all__ = [
    "tool_create_reminder",
    "tool_list_reminder_lists",
    "_convert_iso_to_applescript_date",  # 给单测
    "_escape_applescript_string",  # 给单测
]
