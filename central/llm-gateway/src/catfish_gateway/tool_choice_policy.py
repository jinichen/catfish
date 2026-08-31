"""校验内部 source 的命名 tool_choice，防止绕过工具能力边界。"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from .tools_sanitizer_constants import SOURCE_TOOL_PROFILES, named_tool_choice_names


_STRUCTURED_OUTPUT_TOOLS: dict[str, frozenset[str]] = {
    "companion-profile": frozenset({"submit_profile"}),
    "companion-advisor-transform": frozenset({"submit_advisor_result"}),
    "companion-wiki-suggest": frozenset({"suggest_wikilinks"}),
}

# source profile 是无人值守能力边界：默认不允许强制点名；确有结构化输出合同的
# source 再显式加入上表。这里是调用协议，不是工具目录或模型参数。
_MANAGED_SOURCE_CHOICES: dict[str, frozenset[str]] = {
    source: frozenset() for source in SOURCE_TOOL_PROFILES
}
_MANAGED_SOURCE_CHOICES.update(_STRUCTURED_OUTPUT_TOOLS)


def _declared_tool_names(tools: Any) -> frozenset[str]:
    if not isinstance(tools, list):
        return frozenset()
    names: set[str] = set()
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function")
        name = function.get("name") if isinstance(function, dict) else None
        if isinstance(name, str) and name:
            names.add(name)
    return frozenset(names)


def permitted_pinned_tool_names(
    body: dict[str, Any],
    source_hint: str,
    declared_tools: Any = None,
) -> frozenset[str]:
    """只返回经过 source 合同校验的命名工具；违规请求直接 HTTP 400。"""
    requested = named_tool_choice_names(body)
    if not requested or source_hint not in _MANAGED_SOURCE_CHOICES:
        return requested

    allowed = _MANAGED_SOURCE_CHOICES[source_hint]
    forbidden = requested - allowed
    if forbidden:
        raise HTTPException(
            status_code=400,
            detail=(
                f"source '{source_hint}' 不允许该命名 tool_choice: "
                f"{', '.join(sorted(forbidden))}"
            ),
        )

    tools = body.get("tools") if declared_tools is None else declared_tools
    declared = _declared_tool_names(tools)
    missing = requested - declared
    if missing:
        raise HTTPException(
            status_code=400,
            detail=(
                "tool_choice 点名的工具不在 tools 数组中: "
                f"{', '.join(sorted(missing))}"
            ),
        )
    if source_hint in _STRUCTURED_OUTPUT_TOOLS:
        unexpected = declared - allowed
        if unexpected:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"结构化输出 source '{source_hint}' 包含未登记工具: "
                    f"{', '.join(sorted(unexpected))}"
                ),
            )
    return requested
