"""按当前终端环境解析 Catfish 原生工具的实际可用性。"""
from __future__ import annotations

import platform
from typing import Any, Dict, Iterable, Mapping, Optional


def _platform_name(system_name: Optional[str] = None) -> str:
    return (system_name or platform.system()).strip().lower()


def with_runtime_availability(
    schema: Mapping[str, Any],
    *,
    system_name: Optional[str] = None,
) -> Dict[str, Any]:
    """复制 schema，并补上当前终端的 supported/available/reason_code。"""
    resolved = dict(schema)
    runtime = schema.get("x_catfish_runtime")
    platforms = runtime.get("platforms") if isinstance(runtime, Mapping) else None
    supported = not platforms or _platform_name(system_name) in {
        str(item).strip().lower() for item in platforms
    }
    configured_available = schema.get("available", True) is not False
    if supported and str(schema.get("name") or "").startswith("catfish_wechat_"):
        from . import wechat_archive  # noqa: PLC0415
        configured_available = configured_available and wechat_archive.runtime_available()

    resolved["supported"] = supported
    resolved["available"] = supported and configured_available
    if not supported:
        resolved["reason_code"] = "unsupported_platform"
    elif not configured_available:
        resolved["reason_code"] = "runtime_unavailable"
    else:
        resolved["reason_code"] = None
    return resolved


def unavailable_result(
    name: str,
    schemas: Iterable[Mapping[str, Any]],
    *,
    system_name: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """原生工具在当前终端不可用时返回稳定错误；未知或可用工具返回 None。"""
    schema = next((item for item in schemas if item.get("name") == name), None)
    if schema is None:
        return None
    resolved = with_runtime_availability(schema, system_name=system_name)
    if resolved["available"]:
        return None
    reason_code = resolved["reason_code"]
    message = (
        "当前终端不支持此工具"
        if reason_code == "unsupported_platform"
        else "当前终端暂时无法使用此工具"
    )
    return {
        "ok": False,
        "tool": name,
        "result": None,
        "error": message,
        "reason_code": reason_code,
    }
