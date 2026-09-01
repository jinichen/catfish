"""Select model parameter overrides by request shape.

Model rows are persisted independently from the caller. A provider override
that is valid for one request shape must therefore declare its scope instead
of being encoded as a model-name exception in the gateway.
"""

from __future__ import annotations

from typing import Any

from .thinking_guard import is_forced_tool_choice

_AUTO_SCOPE = "auto"
_ALL_SCOPE = "all"
_FORCED_SCOPE = "forced_tool_choice"


def _has_thinking_toggle(overrides: dict[str, Any]) -> bool:
    """Return whether overrides contain a request-sensitive thinking toggle.

    ``thinking.type`` is intentionally not included: that is the legacy
    DeepSeek compatibility setting and remains universal unless an admin
    explicitly changes its scope. DashScope and self-hosted Qwen toggles are
    the two forms whose behavior depends on forced structured output.
    """
    if "enable_thinking" in overrides:
        return True
    extra_body = overrides.get("extra_body")
    if not isinstance(extra_body, dict):
        return False
    if "enable_thinking" in extra_body:
        return True
    template = extra_body.get("chat_template_kwargs")
    return isinstance(template, dict) and "enable_thinking" in template


def effective_scope(upstream: Any) -> str:
    """Resolve the configured scope, including safe legacy compatibility."""
    overrides = getattr(upstream, "param_overrides", None) or {}
    configured = getattr(upstream, "param_overrides_scope", _AUTO_SCOPE)
    if configured in {_ALL_SCOPE, _FORCED_SCOPE}:
        return configured
    if _has_thinking_toggle(overrides):
        return _FORCED_SCOPE
    return _ALL_SCOPE


def applicable_overrides(upstream: Any, tool_choice: Any) -> dict[str, Any]:
    """Return the model overrides that apply to this request.

    The returned mapping is the original mapping when all overrides apply and
    a shallow copy when a scoped override must be skipped. Callers can keep
    their existing merge semantics without mutating model configuration.
    """
    overrides = getattr(upstream, "param_overrides", None) or {}
    if not overrides:
        return {}
    if effective_scope(upstream) == _FORCED_SCOPE and not is_forced_tool_choice(tool_choice):
        return {}
    return overrides


__all__ = ["applicable_overrides", "effective_scope"]
