"""Catfish Policy Plugin for Hermes.

Registers a pre_tool_call hook that checks each tool invocation against a
YAML-defined red-line ruleset. If a rule triggers, the tool is blocked and
the agent receives an explainable message instead of silently failing.

Design notes
------------
- Rules live in `rules.yaml` next to this file. Easy to read / audit / diff.
- Matcher logic is in `matchers.py`, pure functions, unit-testable without
  spinning up Hermes.
- The plugin is fail-open: if the YAML can't be parsed, we log a warning and
  skip policy checks. This is debatable but aligns with MVP philosophy --
  better to let the agent work than brick it.
- No logging of tool args content. Log lines only mention rule id + tool name.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore

from .matchers import match_rules

logger = logging.getLogger("catfish.policy")

_RULES_CACHE: dict[str, Any] | None = None


def _load_rules() -> dict[str, Any]:
    global _RULES_CACHE
    if _RULES_CACHE is not None:
        return _RULES_CACHE

    if yaml is None:
        logger.warning("PyYAML not installed -- policy plugin disabled")
        _RULES_CACHE = {"rules": []}
        return _RULES_CACHE

    rules_file = Path(__file__).parent / "rules.yaml"
    try:
        with open(rules_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        n = len(data.get("rules", []))
        logger.info("catfish-policy: loaded %d rule(s) from %s", n, rules_file)
        _RULES_CACHE = data
    except Exception as exc:
        logger.warning(
            "catfish-policy: failed to load rules.yaml (%s) -- running with no rules",
            exc,
        )
        _RULES_CACHE = {"rules": []}
    return _RULES_CACHE


def _policy_disabled() -> bool:
    return os.environ.get("CATFISH_POLICY_DISABLED", "").lower() in (
        "1", "true", "yes", "on"
    )


def pre_tool_call(
    tool_name: str,
    args: dict[str, Any] | None = None,
    **_kwargs: Any,
) -> dict[str, Any] | None:
    """Return block directive if any rule triggers.

    Hermes' plugin loader expects one of:
      - None                            -> proceed
      - {"action": "block", "message"}  -> abort, show message to agent
    """
    if _policy_disabled():
        return None

    call_args = args if isinstance(args, dict) else {}
    rules = _load_rules()
    hit = match_rules(tool_name, call_args, rules)
    if not hit:
        return None

    rule_id = hit.get("id", "<unnamed>")
    reason = hit.get("reason", "Tool call blocked by Catfish policy.").strip()

    # Log only the rule id + tool name. Never log args (could contain content).
    logger.warning(
        "catfish-policy: blocked tool '%s' via rule '%s'", tool_name, rule_id
    )

    return {
        "action": "block",
        "message": f"[鲶鱼策略 · {rule_id}]\n{reason}",
    }


def register(ctx) -> None:
    """Entry point called by Hermes' plugin loader."""
    # Eager-load rules so any parse errors surface at startup, not mid-turn
    _load_rules()
    ctx.register_hook("pre_tool_call", pre_tool_call)
    logger.info("catfish-policy: registered pre_tool_call hook")
