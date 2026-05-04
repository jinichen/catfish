"""session_meta — 给 LLM 一个"距上次 chat 多久 / 今天第几次"的元信息 (BL-E16 五一 sprint 5/3 晚).

# 为啥需要

employee_journal 给"工作内容". `inject_session_history` 给"session 列表".
但 LLM 还缺一个**时间感**: "员工跟我隔了 3 天没聊?", "今天他第 5 次找我了?".

有这个时间感 LLM 才能在合适时机说自然的关系性话 (BL-E16):
  - 跨天回来 → "好几天没找我了, 那个 X 项目卡住没?"
  - 同天频繁 → 不再每条都引用 "上次你...", 频率纪律生效

# 设计

`~/.catfish/session_meta.json` 维护:
  {
    "last_chat_at": "2026-05-03T10:35:00+08:00",  # ISO8601
    "today_count": 7,                               # 当天第几次
    "today_date": "2026-05-03"                      # 滚日期, 跨天清零 today_count
  }

每次 chat (gateway 收到 /v1/chat/completions) 末尾调 `tick()`:
  - last_chat_at = now
  - today_date 跟今天对比, 不一致 → reset today_count=1, 一致 → +1

每次 chat 开头调 `build_meta_block()`:
  - 读文件 → 算"距上次 N 小时 / N 天"
  - 拼 markdown 段返出

注入位置: identity_inject 拼最后一段 (在 employee_journal 后).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger("catfish.gateway.session_meta")

#: meta 文件路径 (默认 ~/.catfish/session_meta.json)
META_PATH = Path.home() / ".catfish" / "session_meta.json"


def meta_path() -> Path:
    """允许测试 monkeypatch."""
    return META_PATH


def _now() -> datetime:
    """允许测试 monkeypatch (不要直接 import datetime.now 进调用方)."""
    return datetime.now(timezone.utc).astimezone()


def _read() -> dict:
    p = meta_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("读 session_meta 失败 (要重建): %s", e)
        return {}


def _write(data: dict) -> None:
    p = meta_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def tick() -> None:
    """每次 chat 完调一次. 更新 last_chat_at + today_count.

    跨天自动 reset today_count=1; 同天 today_count + 1; 没记录从 1 起.
    写失败 logger.warning, 不影响 chat 主流程.
    """
    try:
        now = _now()
        today_str = now.date().isoformat()
        data = _read()
        if data.get("today_date") == today_str:
            data["today_count"] = int(data.get("today_count", 0)) + 1
        else:
            data["today_count"] = 1
            data["today_date"] = today_str
        data["last_chat_at"] = now.isoformat()
        _write(data)
    except Exception as e:
        logger.warning("session_meta tick 失败 (无关键路径): %s", e)


def build_meta_block() -> str:
    """生成给 LLM 看的 'Session Meta' markdown 段, 空则返空字符串.

    例输出:
        # Session Meta

        - 距上次找我: 3 天 4 小时前
        - 今天第 1 次找我

    第一次安装 (没文件) → 返空 (LLM 没"上次"概念). 不报错.
    """
    data = _read()
    if not data:
        return ""

    lines = ["# Session Meta", ""]
    last = data.get("last_chat_at")
    if last:
        try:
            last_dt = datetime.fromisoformat(last)
            now = _now()
            # 兼容 last 没带 tz (旧版本) → 假设当前时区
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=now.tzinfo)
            delta = now - last_dt
            human = _humanize_delta(delta)
            lines.append(f"- 距上次找我: {human}前")
        except Exception as e:
            logger.warning("parse last_chat_at 失败 %r: %s", last, e)

    today_count = data.get("today_count")
    today_date = data.get("today_date")
    if isinstance(today_count, int) and today_count >= 1 and today_date:
        # 如果 today_date 不是今天 (没 tick 过), today_count 别误报
        today_str = _now().date().isoformat()
        if today_date == today_str:
            lines.append(f"- 今天第 {today_count} 次找我")

    if len(lines) == 2:  # 只有 header + 空行, 没数据
        return ""
    return "\n".join(lines)


def _humanize_delta(delta: timedelta) -> str:
    """3 天 4 小时 / 4 小时 12 分 / 23 分钟 / 刚刚 — 给人看的"""
    total = int(delta.total_seconds())
    if total < 60:
        return "刚刚"
    if total < 3600:
        return f"{total // 60} 分钟"
    if total < 86_400:  # < 1 day
        h = total // 3600
        m = (total % 3600) // 60
        if m == 0:
            return f"{h} 小时"
        return f"{h} 小时 {m} 分"
    days = total // 86_400
    hours = (total % 86_400) // 3600
    if hours == 0:
        return f"{days} 天"
    return f"{days} 天 {hours} 小时"
