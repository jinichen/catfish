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
from datetime import UTC, datetime, timedelta
from pathlib import Path

logger = logging.getLogger("catfish.gateway.session_meta")

#: meta 文件路径 (默认 ~/.catfish/session_meta.json)
META_PATH = Path.home() / ".catfish" / "session_meta.json"


def meta_path() -> Path:
    """允许测试 monkeypatch."""
    return META_PATH


def _now() -> datetime:
    """允许测试 monkeypatch (不要直接 import datetime.now 进调用方)."""
    return datetime.now(UTC).astimezone()


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
    """每次 chat 完调一次. 更新 last_chat_iso + today_count.

    跨天自动 reset today_count=1; 同天 today_count + 1; 没记录从 1 起.
    写失败 logger.warning, 不影响 chat 主流程.

    # 5/26 字段漂移修 (BL-E16 hidden bug)

    老 tick 写字段 `last_chat_at`, 但 catfish-memory plugin `_render_session_meta`
    (`edge/hermes-plugins/catfish-memory/catfish_memory.py:337`) 读的是
    `last_chat_iso`. 字段错位导致 plugin 端"🕒 时间感"长期渲染空 — LLM 不知道
    员工上次聊天是啥时候. 5/26 audit 抓到, 改 gateway 这边对齐写 `last_chat_iso`.

    兼容: 老字段 `last_chat_at` 无外部 caller (build_meta_block 死代码已删 5/26),
    直接改字段名 OK.
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
        data["last_chat_iso"] = now.isoformat()
        _write(data)
    except Exception as e:
        logger.warning("session_meta tick 失败 (无关键路径): %s", e)


# 5/26 砍 build_meta_block + _humanize_delta — 死代码 (0 真 caller).
# 注释里说 "SessionMetaProvider 接管" 是撒谎 (grep 全代码库类不存在).
# 真正接管 inject 的是 catfish-memory plugin _render_session_meta (它读
# 本文件 tick() 写的 session_meta.json, 自己渲染 "🕒 时间感" 段).
# gateway 这个 build_meta_block 从来没被调过, 直接删 ~60 行死代码.
