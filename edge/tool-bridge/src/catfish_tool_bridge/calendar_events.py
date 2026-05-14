"""BL-CALENDAR (5/14 0:30 鸿波拍板) — catfish_create_calendar_event 模块.

跟 5/13 BL-REMINDER (Reminders.app to-do) 互补 —

| 工具 | 场景 | 数据落点 |
|---|---|---|
| `notify` (5/2 BL-E13) | 一次性弹窗 (几秒消失) | macOS 通知中心 (不持久) |
| `catfish_create_reminder` (5/13 BL-REMINDER) | 待办 (用户能勾完成) | Reminders.app + iCloud |
| **`catfish_create_calendar_event` (本模块)** | **时间锚定的事件** (会议 / 现场审核 / 行程, 带 location + 时长) | **Calendar.app + iCloud** |

# 触发场景 (鸿波 5/14 ISO 现场审核会议踩坑后加)

- "5/18-5/22 上午 8:40 在 409 会议室开 ISO 现场审核会"  → catfish_create_calendar_event 5 次
- "明天下午 3 点跟王总评审 Q2 进度, 12 楼 1201"        → 一次, 带 location
- "下周一中午 12:30 跟客户吃饭, 苏州工业园区 XX 餐厅"  → 一次, 带 location

跟 reminder 区别 (LLM 选 tool 时按这个判断):
- 有**明确开始结束时间** + 通常带 **location** → calendar_event
- 有**截止时间但只是提醒**, 没固定时长 → reminder
- 完全没时间 ("记得给王总打电话") → reminder (无 due_date)

# 实现走 osascript + Calendar.app

跟 reminders.py 同模式. macOS 首次调用弹 TCC 权限申请 (隐私与安全性 → 日历).

# 为啥两份实现 (Python tool-bridge + Rust Companion Tauri)

- tool-bridge: 给 LLM 调用 (走 hermes adapter dispatch_native)
- Companion: 给 UI 直接调 (e.g. 仪表盘"加日历事件" 按钮)
- 不同进程各自需要 osascript 调用, 不能互相代劳
"""
from __future__ import annotations

import logging
import platform
import subprocess
from typing import Any

logger = logging.getLogger("catfish.tool_bridge.calendar_events")


def _is_macos() -> bool:
    return platform.system() == "Darwin"


def _run_osascript(script: str, timeout_sec: float = 10.0) -> tuple[bool, str, str]:
    """跑一段 AppleScript, 返 (success, stdout, stderr).

    跟 reminders._run_osascript 同实现 (本来想抽公用 helper, 但跨模块 import
    增加耦合, 这俩各 30 行重复 OK).
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
    """AppleScript 字符串里 " 和 \\ 都要 escape.

    顺序: 先 escape \\ (替换成 \\\\), 再 escape " (替换成 \\"). 反过来会破坏
    \\" 变成 \\\\\\". 跟 reminders._escape_applescript_string 同实现.
    """
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _convert_iso_to_applescript_date(iso: str) -> str:
    """ISO 8601 (e.g. '2026-05-18T08:40:00') → AppleScript 'YYYY-MM-DD HH:MM:SS'.

    AppleScript 的 `date "..."` coercion 接受这个格式 (本地时区).
    简单转换: 替换 T 为空格, 砍掉时区后缀 (Z / +08:00 / -05:00).

    跟 reminders._convert_iso_to_applescript_date 同实现 (复制保持模块独立).
    """
    s = iso.replace("T", " ")
    # 砍掉 +08:00 / +0800 / Z 之类时区后缀
    for sep in ("+", "-"):
        idx = s.rfind(sep)
        if idx > 10:  # 日期 yyyy-mm-dd 有 - 在前 10 字符内
            s = s[:idx]
            break
    if s.endswith("Z"):
        s = s[:-1]
    return s.strip()


def _default_end_iso_from_start(start_iso: str, hours: float = 1.0) -> str:
    """没传 end_iso 时, 默认从 start + N 小时计算 end.

    用 datetime parse 不引入新依赖 (stdlib).
    """
    from datetime import datetime, timedelta
    # 砍 Z + 时区, 跟 _convert_iso_to_applescript_date 一致 (再用 fromisoformat)
    s = start_iso.rstrip("Z")
    # +/- 时区后缀 fromisoformat 自带支持 (Python 3.11+), 但保守起见砍
    for sep in ("+", "-"):
        idx = s.rfind(sep)
        if idx > 10:
            s = s[:idx]
            break
    dt = datetime.fromisoformat(s)
    end_dt = dt + timedelta(hours=hours)
    return end_dt.isoformat()


def _normalize_alarms(raw: Any) -> list[int]:
    """alarm_minutes_before 接受 int / list[int] / None, 统一返 list[int].

    None → 默认 [15] (事件前 15 分钟提醒一次, 这是 macOS Calendar 默认值的常见配置).
    [] / 0 / [0] → 空 list (不加 alarm). 显式禁用要传 0 或空 list.
    int → [int]
    list[int] → 去重, 排序 (从小到大), 钳到 [0, 40320] (28 天内, Calendar 上限).
    """
    if raw is None:
        return [15]  # 默认 15 min 前
    # 单值 int 包成 list
    if isinstance(raw, int):
        raw = [raw]
    if not isinstance(raw, list):
        return [15]
    out: list[int] = []
    for v in raw:
        try:
            iv = int(v)
        except (TypeError, ValueError):
            continue
        # 钳: 0 = 事件开始时, 40320 = 28 天前 (Calendar 业务上限)
        iv = max(0, min(40320, iv))
        out.append(iv)
    # 去重 + 升序
    return sorted(set(out))


def tool_create_calendar_event(args: dict[str, Any]) -> dict[str, Any]:
    """catfish_create_calendar_event tool 入口.

    顺序: 输入校验 → 平台校验 → osascript (跟 reminders 同模式).

    必填:
    - title: 事件标题
    - start_iso: ISO 8601 开始时间

    可选:
    - end_iso: ISO 8601 结束时间 (默认 start + 1h)
    - location: 地点字符串 (会议室 / 餐厅 / 等)
    - description: 详情备注
    - calendar_name: 哪个日历 (默认 '工作'; 中文系统通常有 '工作' / '家庭' / '我的日历')
    - alarm_minutes_before: int 或 list[int], 事件前几分钟弹通知 (默认 [15] —
      事件前 15 分钟提醒一次. iCloud 同步到 iPhone 后, 到时间会震动+弹通知.).
      传 [] 或 [0] = 显式不提醒 (rare). 传 [15, 60, 1440] = 15min/1h/1天前 三次提醒.
    """
    title = (args.get("title") or "").strip()
    if not title:
        return {"ok": False, "error": "title 不能空 (catfish_create_calendar_event 必填)"}

    start_iso = (args.get("start_iso") or "").strip()
    if not start_iso:
        return {"ok": False, "error": "start_iso 不能空 (ISO 8601, e.g. '2026-05-18T08:40:00')"}

    # 校验 start_iso 真能 parse — 提前报错避免 osascript 神秘失败
    try:
        from datetime import datetime
        datetime.fromisoformat(start_iso.rstrip("Z").split("+")[0])
    except (TypeError, ValueError) as e:
        return {
            "ok": False,
            "error": f"start_iso 格式错 (期望 'YYYY-MM-DDTHH:MM:SS'): {e}",
        }

    end_iso = (args.get("end_iso") or "").strip()
    if not end_iso:
        # 默认 1h 后
        try:
            end_iso = _default_end_iso_from_start(start_iso, hours=1.0)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"start_iso parse 失败无法算默认 end: {e}"}
    else:
        # 校验 end_iso 也 parse OK
        try:
            from datetime import datetime
            datetime.fromisoformat(end_iso.rstrip("Z").split("+")[0])
        except (TypeError, ValueError) as e:
            return {
                "ok": False,
                "error": f"end_iso 格式错 (期望 'YYYY-MM-DDTHH:MM:SS'): {e}",
            }

    location = (args.get("location") or "").strip()
    description = (args.get("description") or "").strip()
    calendar_name = (args.get("calendar_name") or "工作").strip()
    alarms_min = _normalize_alarms(args.get("alarm_minutes_before"))

    if not _is_macos():
        return {
            "ok": False,
            "error": "create_calendar_event 只 macOS 支持 (走 Calendar.app). 当前平台: "
                     + platform.system(),
        }

    # 拼 AppleScript properties record — **必须压一行**, AppleScript 多行 record
    # 不允许 (5/14 鸿波 ISO 审核脚本踩的坑就是多行 record).
    safe_title = _escape_applescript_string(title)
    safe_cal = _escape_applescript_string(calendar_name)
    safe_start = _escape_applescript_string(_convert_iso_to_applescript_date(start_iso))
    safe_end = _escape_applescript_string(_convert_iso_to_applescript_date(end_iso))

    props = [
        f'summary:"{safe_title}"',
        f'start date:date "{safe_start}"',
        f'end date:date "{safe_end}"',
    ]
    if location:
        props.append(f'location:"{_escape_applescript_string(location)}"')
    if description:
        props.append(f'description:"{_escape_applescript_string(description)}"')

    # alarm 段 — 创建 event 后, 进 event 上下文 make new display alarm.
    # trigger interval 单位是分钟, **负数 = 前 N 分钟提醒**, 正数 = 后 (没人用).
    # iCloud 同步后 iPhone 到时间震动 + 弹通知.
    alarm_lines = []
    if alarms_min:
        alarm_lines.append("    tell newEvent")
        for m in alarms_min:
            # m=0 → trigger interval:0 (事件开始时); m=15 → -15 (前 15 min)
            trigger = -m if m > 0 else 0
            alarm_lines.append(
                f'        make new display alarm at end of display alarms with properties {{trigger interval:{trigger}}}'
            )
        alarm_lines.append("    end tell")
    alarm_segment = ("\n" + "\n".join(alarm_lines)) if alarm_lines else ""

    # 注意: AppleScript Calendar 的 event 对象字段是 `summary` (不是 reminder 的 `name`)
    script = f'''tell application "Calendar"
    set targetCal to first calendar whose name is "{safe_cal}"
    set newEvent to make new event at targetCal with properties {{{", ".join(props)}}}{alarm_segment}
    return summary of newEvent
end tell'''

    ok, stdout, stderr = _run_osascript(script)
    if not ok:
        # 常见错误友好化
        if "Not authorized" in stderr or "权限" in stderr or "not allowed" in stderr.lower():
            return {
                "ok": False,
                "error": (
                    "Calendar.app 权限未给 — 系统设置 → 隐私与安全性 → 日历 → "
                    "勾上 Catfish Companion (或 Terminal / Python). 然后再试. "
                    f"原始错误: {stderr}"
                ),
                "needs_permission": True,
            }
        if "Can’t get calendar" in stderr or "can’t get" in stderr.lower() or "doesn’t exist" in stderr.lower():
            return {
                "ok": False,
                "error": (
                    f"calendar \"{calendar_name}\" 不存在. 可用 calendar 调 "
                    f"catfish_list_calendars 看. 中文系统常见 '工作' / '家庭' / "
                    f"'我的日历', 英文 'Work' / 'Home' / 'Calendar'. 原始错误: {stderr}"
                ),
                "calendar_not_found": calendar_name,
            }
        return {"ok": False, "error": f"osascript 失败: {stderr}"}

    logger.info(
        "BL-CALENDAR: 创建事件 '%s' (cal=%s, start=%s, end=%s, loc=%s, alarms=%s)",
        title, calendar_name, start_iso, end_iso, location or "无", alarms_min or "无",
    )
    summary_parts = [f"📅 已在 Calendar.app 「{calendar_name}」日历创建事件 「{title}」"]
    summary_parts.append(f"  开始: {start_iso}")
    summary_parts.append(f"  结束: {end_iso}")
    if location:
        summary_parts.append(f"  地点: {location}")
    if alarms_min:
        # 把分钟转人话: 15→"15 分钟前", 60→"1 小时前", 1440→"1 天前"
        def _fmt(m: int) -> str:
            if m == 0:
                return "事件开始时"
            if m < 60:
                return f"{m} 分钟前"
            if m < 1440:
                return f"{m // 60} 小时{f' {m % 60} 分钟' if m % 60 else ''}前"
            return f"{m // 1440} 天{f' {(m % 1440) // 60} 小时' if (m % 1440) // 60 else ''}前"
        summary_parts.append(f"  提醒: {', '.join(_fmt(m) for m in alarms_min)} (iPhone 会震动+弹通知)")
    else:
        summary_parts.append("  ⚠ 无 alarm — iPhone 不会响 (传 alarm_minutes_before=[15] 加 15min 前提醒)")
    summary_parts.append("iCloud 同步到 iPhone/iPad/Apple Watch.")
    return {
        "ok": True,
        "event_summary": stdout or title,
        "calendar_name": calendar_name,
        "start_iso": start_iso,
        "end_iso": end_iso,
        "location": location or None,
        "alarm_minutes_before": alarms_min or None,
        "summary": " ".join(summary_parts),
    }


def tool_list_calendars(args: dict[str, Any]) -> dict[str, Any]:
    """catfish_list_calendars tool 入口."""
    if not _is_macos():
        return {
            "ok": False,
            "error": "list_calendars 只 macOS 支持. 当前平台: " + platform.system(),
            "calendar_names": [],
        }

    script = '''tell application "Calendar"
    set lst to {}
    repeat with C in calendars
        set end of lst to name of C
    end repeat
    return lst
end tell'''

    ok, stdout, stderr = _run_osascript(script)
    if not ok:
        if "Not authorized" in stderr or "权限" in stderr:
            return {
                "ok": False,
                "error": "Calendar.app 权限未给 — 系统设置 → 隐私与安全性 → 日历",
                "needs_permission": True,
                "calendar_names": [],
            }
        return {"ok": False, "error": f"osascript 失败: {stderr}", "calendar_names": []}

    cals = [s.strip() for s in stdout.split(", ") if s.strip()]
    return {
        "ok": True,
        "calendar_names": cals,
        "count": len(cals),
        "summary": f"📅 macOS Calendar.app 有 {len(cals)} 个日历: {', '.join(cals) if cals else '(无)'}",
    }


__all__ = [
    "tool_create_calendar_event",
    "tool_list_calendars",
    "_convert_iso_to_applescript_date",  # 给单测
    "_escape_applescript_string",  # 给单测
    "_default_end_iso_from_start",  # 给单测
    "_normalize_alarms",  # 给单测
]
