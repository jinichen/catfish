import asyncio
import base64
import json
import time
from unittest.mock import AsyncMock

import hermes_token_renewal as renewal


def token():
    body = base64.urlsafe_b64encode(json.dumps({"exp": time.time() + 30 * 86400}).encode()).decode().rstrip("=")
    return f"header.{body}.signature"


def setup(monkeypatch, tmp_path):
    for key in ("HERMES_SERVICE_TOKEN", "CATFISH_HERMES_CLIENT_SECRET", "CLIENT_SECRET"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HERMES_ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.setattr(renewal, "_RENEW_LOCK", None)


def test_missing_token_can_be_minted(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    monkeypatch.setenv("CATFISH_HERMES_CLIENT_SECRET", "test-secret")
    fresh = token()
    mint = AsyncMock(return_value=fresh)
    monkeypatch.setattr(renewal, "mint_service_token", mint)
    assert asyncio.run(renewal.get_fresh_service_token()) == fresh
    mint.assert_awaited_once()
    assert fresh in (tmp_path / ".env").read_text()


def test_windows_reads_late_provision_without_changing_employee_key(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    monkeypatch.setattr(renewal.sys, "platform", "win32")
    monkeypatch.setenv("OPENAI_API_KEY", "employee")
    fresh = token()
    (tmp_path / ".env").write_text(f"HERMES_SERVICE_TOKEN={fresh}\nOPENAI_API_KEY=other\n")
    mint = AsyncMock()
    monkeypatch.setattr(renewal, "mint_service_token", mint)
    assert asyncio.run(renewal.get_fresh_service_token()) == fresh
    assert renewal.os.environ["OPENAI_API_KEY"] == "employee"
    mint.assert_not_awaited()


def test_macos_keeps_process_credentials(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    monkeypatch.setattr(renewal.sys, "platform", "darwin")
    fresh = token()
    monkeypatch.setenv("HERMES_SERVICE_TOKEN", fresh)
    (tmp_path / ".env").write_text("HERMES_SERVICE_TOKEN=stale\n")
    assert asyncio.run(renewal.get_fresh_service_token()) == fresh
