"""Read-only credential/MCP diagnostics. Never print token values or secrets.

Run with the affected Hermes venv's python.exe on Windows. This does not refresh
credentials, modify files, contact the gateway, or change running processes.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import sys
import time


def token_summary(value):
    if not isinstance(value, str) or not value.strip():
        return {"present": False}
    result = {"present": True, "jwt": False}
    parts = value.strip().split(".")
    if len(parts) != 3:
        return result
    try:
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
        result["jwt"] = True
        exp = payload.get("exp")
        if isinstance(exp, (int, float)):
            result["expires_in_seconds"] = int(exp - time.time())
        # Unverified claims are only diagnostics, never authorization decisions.
        result["expiry_verified"] = False
    except (ValueError, TypeError, AttributeError):
        result["payload_decodable"] = False
    return result


def main():
    default = Path(os.environ["LOCALAPPDATA"]) / "hermes" if os.name == "nt" else Path.home() / ".hermes"
    root = Path(os.environ.get("HERMES_HOME") or default)
    print("Python:", sys.version.split()[0])
    values = {}
    try:
        from dotenv import dotenv_values
        values = dict(dotenv_values(root / ".env", interpolate=False))
    except Exception as exc:
        print("env_read_error:", type(exc).__name__)
    try:
        import yaml
        config = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8-sig")) or {}
        model = config.get("model") or {}
        if isinstance(model, dict):
            values["model.api_key"] = model.get("api_key")
            print("provider:", model.get("provider"))
    except Exception as exc:
        print("config_read_error:", type(exc).__name__)
    keys = ("model.api_key", "OPENAI_API_KEY", "HERMES_SERVICE_TOKEN")
    for key in keys:
        print(key, json.dumps(token_summary(values.get(key))))
    print("config_matches_env_key:", bool(values.get(keys[0])) and values.get(keys[0]) == values.get(keys[1]))
    print("env_key_matches_service_token:", bool(values.get(keys[1])) and values.get(keys[1]) == values.get(keys[2]))
    try:
        from mcp import ClientSession, StdioServerParameters  # noqa: F401
        from mcp.client.stdio import stdio_client  # noqa: F401
        print("MCP SDK import OK")
    except Exception as exc:
        # ImportError.name is a module name, unlike an arbitrary exception string
        # which could include config values. Keep the diagnostic secret-free.
        print("MCP SDK import failed:", type(exc).__name__, getattr(exc, "name", None))


if __name__ == "__main__":
    main()
