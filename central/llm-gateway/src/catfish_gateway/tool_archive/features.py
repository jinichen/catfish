"""BL-Q3-ARCHIVE — 灰度开关 (5/11).

环境变量驱动 (跟 BL-FIX38 / BL-FIX39 同款), 不引第三方 feature flag 依赖.

- CATFISH_TOOL_ARCHIVE_ENABLED        全局开关 (默认 true — 鸿波 5/11 现在就开)
- CATFISH_TOOL_ARCHIVE_USERS          逗号分隔白名单 (空 = 所有人)
- CATFISH_TOOL_ARCHIVE_THRESHOLD      触发阈值字节 (默认 4000)
- CATFISH_TOOL_ARCHIVE_SUMMARY        摘要开关 (默认 true)
- CATFISH_TOOL_ARCHIVE_FALLBACK_FIX41 archive 写挂时降级 FIX41 硬切 (默认 true)
"""
from __future__ import annotations

import os


def _env_bool(key: str, default: bool) -> bool:
    v = os.environ.get(key, "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return default


def is_archive_enabled(user_email: str | None = None) -> bool:
    """全局开 + (白名单空 or user 在白名单内) = 启用."""
    if not _env_bool("CATFISH_TOOL_ARCHIVE_ENABLED", True):
        return False
    wl = os.environ.get("CATFISH_TOOL_ARCHIVE_USERS", "").strip()
    if not wl:
        return True  # 没设白名单 = 全员
    if not user_email:
        return False  # 设了白名单但没传 user → 拒
    allowed = {u.strip() for u in wl.split(",") if u.strip()}
    return user_email in allowed


def threshold_bytes() -> int:
    try:
        return max(1000, int(os.environ.get("CATFISH_TOOL_ARCHIVE_THRESHOLD", "4000")))
    except ValueError:
        return 4000


def summary_enabled() -> bool:
    return _env_bool("CATFISH_TOOL_ARCHIVE_SUMMARY", True)


def fallback_to_fix41() -> bool:
    """archive 写挂时降级 BL-FIX41 硬切."""
    return _env_bool("CATFISH_TOOL_ARCHIVE_FALLBACK_FIX41", True)


__all__ = [
    "is_archive_enabled",
    "threshold_bytes",
    "summary_enabled",
    "fallback_to_fix41",
]
