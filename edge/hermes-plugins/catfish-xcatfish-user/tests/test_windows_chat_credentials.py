import pytest
import windows_chat_credentials as credentials


@pytest.fixture
def configured(tmp_path, monkeypatch):
    monkeypatch.setattr(credentials.sys, "platform", "win32")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("CATFISH_GATEWAY_URL", "http://127.0.0.1:8999")
    (tmp_path / "config.yaml").write_text(
        'model:\n  provider: openai-api\n  base_url: http://127.0.0.1:8999/v1\n  api_key: fresh-employee\n'
    )
    return {"base_url": "http://127.0.0.1:8999/v1", "api_key": "stale-env", "credential_pool": object()}


def test_windows_chat_uses_current_config(configured):
    result = credentials.select_chat_credentials(configured, "companion-chat")
    assert result["api_key"] == "fresh-employee"
    assert result["credential_pool"] is None
    assert configured["api_key"] == "stale-env"


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_other_platforms_unchanged(configured, monkeypatch, platform):
    monkeypatch.setattr(credentials.sys, "platform", platform)
    assert credentials.select_chat_credentials(configured, "companion-chat") is configured


@pytest.mark.parametrize("source", [None, "", "companion-advisor", "cron"])
def test_background_identity_unchanged(configured, source):
    assert credentials.select_chat_credentials(configured, source) is configured


@pytest.mark.parametrize("url", ["https://example.org/v1", "http://127.0.0.1:8999/other", "http://127.0.0.1:8999.attacker.test/v1"])
def test_no_cross_endpoint_credential_leak(configured, url):
    configured["base_url"] = url
    assert credentials.select_chat_credentials(configured, "companion-chat") is configured


def test_refresh_is_read_on_next_call(configured, tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(path.read_text().replace("fresh-employee", "refreshed-employee"))
    assert credentials.select_chat_credentials(configured, "companion-chat")["api_key"] == "refreshed-employee"


def test_missing_config_preserves_existing_behavior(configured, tmp_path):
    (tmp_path / "config.yaml").unlink()
    assert credentials.select_chat_credentials(configured, "companion-chat") is configured


def test_hook_passes_key_before_client_creation(configured, monkeypatch):
    import sys
    from types import SimpleNamespace
    import plugin_runtime

    captured = {}
    def original(agent, **kwargs):
        captured.update(kwargs)
        return "initialized"
    module = SimpleNamespace(init_agent=original)
    monkeypatch.setitem(sys.modules, "agent", SimpleNamespace(agent_init=module))
    monkeypatch.setattr(plugin_runtime, "_sib", lambda name: credentials)
    monkeypatch.setattr(plugin_runtime, "_ctx", lambda: SimpleNamespace(
        CV_CF_SOURCE=SimpleNamespace(get=lambda: "companion-chat")))
    plugin_runtime._patch_p1_agent_init()
    assert module.init_agent(SimpleNamespace(), **configured) == "initialized"
    assert captured["api_key"] == "fresh-employee"
    assert captured["credential_pool"] is None
