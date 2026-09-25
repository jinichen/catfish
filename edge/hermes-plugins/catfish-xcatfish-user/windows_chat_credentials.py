"""Use Companion's current employee credential for Windows chat only.

Never change process environment or service credentials. Bind the credential to
the exact configured gateway endpoint; unrelated providers retain their keys.
"""
from pathlib import Path
from urllib.parse import urlsplit
import os
import sys


def _endpoint(value):
    try:
        url = urlsplit(value)
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password:
            return None
        return (url.scheme, url.hostname, url.port or (443 if url.scheme == "https" else 80), url.path.rstrip("/"))
    except (TypeError, ValueError):
        return None


def select_chat_credentials(kwargs, source):
    if sys.platform != "win32" or source != "companion-chat":
        return kwargs
    import yaml

    root = os.environ.get("HERMES_HOME")
    if not root:
        local = os.environ.get("LOCALAPPDATA")
        if not local:
            return kwargs
        root = str(Path(local) / "hermes")
    try:
        config = yaml.safe_load((Path(root) / "config.yaml").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, yaml.YAMLError):
        return kwargs
    model = config.get("model") if isinstance(config, dict) else None
    if not isinstance(model, dict) or model.get("provider") != "openai-api":
        return kwargs
    endpoint = _endpoint(model.get("base_url"))
    gateway = os.environ.get("CATFISH_GATEWAY_URL", "http://127.0.0.1:8999").rstrip("/")
    if not gateway.endswith("/v1"):
        gateway += "/v1"
    if endpoint is None or endpoint != _endpoint(gateway) or endpoint != _endpoint(kwargs.get("base_url")):
        return kwargs
    key = model.get("api_key")
    if not isinstance(key, str) or not key.strip():
        return kwargs
    # The gateway still validates signature, expiry, audience and permissions.
    # Do not let an old environment-backed pool replace this explicit identity.
    return {**kwargs, "api_key": key.strip(), "credential_pool": None}
