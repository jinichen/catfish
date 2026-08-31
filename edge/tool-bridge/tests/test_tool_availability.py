"""终端工具能力必须由 Tool Bridge 按当前运行环境判定。"""
from __future__ import annotations

from catfish_tool_bridge import catfish_tools, tool_availability


MACOS_ONLY_TOOLS = {
    "catfish_create_reminder",
    "catfish_list_reminders",
    "catfish_list_reminder_lists",
    "catfish_create_calendar_event",
    "catfish_list_calendars",
}


def _native_schema(name: str) -> dict:
    return next(
        schema
        for schema in catfish_tools.CATFISH_NATIVE_TOOLS
        if schema["name"] == name
    )


def test_macos_tools_declare_runtime_platform() -> None:
    for name in MACOS_ONLY_TOOLS:
        assert _native_schema(name)["x_catfish_runtime"] == {
            "platforms": ["darwin"],
        }


def test_windows_marks_macos_tools_unavailable_without_mutating_schema() -> None:
    original = _native_schema("catfish_list_reminders")
    resolved = tool_availability.with_runtime_availability(
        original,
        system_name="Windows",
    )

    assert resolved is not original
    assert resolved["available"] is False
    assert resolved["supported"] is False
    assert resolved["reason_code"] == "unsupported_platform"
    assert original["available"] is True
    assert "supported" not in original


def test_darwin_keeps_macos_tools_available() -> None:
    for name in MACOS_ONLY_TOOLS:
        resolved = tool_availability.with_runtime_availability(
            _native_schema(name),
            system_name="Darwin",
        )
        assert resolved["available"] is True
        assert resolved["supported"] is True
        assert resolved["reason_code"] is None


def test_dispatch_guard_returns_stable_unsupported_result() -> None:
    result = tool_availability.unavailable_result(
        "catfish_list_reminders",
        catfish_tools.CATFISH_NATIVE_TOOLS,
        system_name="Windows",
    )

    assert result == {
        "ok": False,
        "tool": "catfish_list_reminders",
        "result": None,
        "error": "当前终端不支持此工具",
        "reason_code": "unsupported_platform",
    }


def test_dispatch_guard_ignores_supported_and_unknown_tools() -> None:
    assert tool_availability.unavailable_result(
        "catfish_list_reminders",
        catfish_tools.CATFISH_NATIVE_TOOLS,
        system_name="Darwin",
    ) is None
    assert tool_availability.unavailable_result(
        "not_a_native_tool",
        catfish_tools.CATFISH_NATIVE_TOOLS,
        system_name="Windows",
    ) is None
