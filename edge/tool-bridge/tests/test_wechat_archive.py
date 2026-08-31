"""微信历史读取只允许经员工授权的导出文件读取器。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

import pytest

from catfish_tool_bridge import catfish_tools, picker_state, tool_availability, wechat_archive


def _write_config(
    path: Path,
    helper: Path,
    source: Path,
    *,
    enabled: bool = True,
    model: str = "catfish-private-main",
) -> None:
    path.write_text(
        json.dumps(
            {
                "version": 2,
                "enabled": enabled,
                "helper_path": str(helper),
                "source_type": "export_file",
                "source_path": str(source),
                "source_size": source.stat().st_size,
                "source_modified_ns": source.stat().st_mtime_ns,
                "consented_picker_model": model,
                "consented_at": "2026-08-28T10:00:00+08:00",
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture
def configured_reader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    helper = tmp_path / "catfish-wechat-reader"
    helper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    helper.chmod(0o700)
    source = tmp_path / "wechat.jsonl"
    source.write_text(
        json.dumps(
            {
                "message_id": "m1",
                "session_id": "s1",
                "timestamp": "2026-08-28T09:00:00+08:00",
                "text": "测试",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    config = tmp_path / "wechat_archive.json"
    _write_config(config, helper, source)
    monkeypatch.setenv("CATFISH_WECHAT_ARCHIVE_CONFIG", str(config))
    monkeypatch.setattr(picker_state, "read_picker_model", lambda: "catfish-private-main")
    return helper


def _doctor_ok() -> dict[str, object]:
    return {
        "protocol_version": 1,
        "read_only": True,
        "secure_key_store": True,
        "ephemeral_plaintext_cache": True,
        "modifies_wechat_app": False,
    }


def _schema(name: str) -> dict:
    return next(item for item in catfish_tools.CATFISH_NATIVE_TOOLS if item["name"] == name)


def test_schemas_never_accept_a_model_override() -> None:
    for name in (
        "catfish_wechat_sessions",
        "catfish_wechat_history",
        "catfish_wechat_search",
    ):
        schema = _schema(name)
        assert "model" not in schema["parameters"].get("properties", {})
        assert schema["x_catfish_runtime"] == {"platforms": ["darwin", "windows"]}


def test_disabled_reader_is_rejected_before_process_start(
    configured_reader: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = Path(wechat_archive._config_path())
    source = configured_reader.parent / "wechat.jsonl"
    _write_config(config, configured_reader, source, enabled=False)
    monkeypatch.setattr(
        wechat_archive,
        "_run_helper_json",
        lambda *_args, **_kwargs: pytest.fail("未授权时不应启动读取器"),
    )

    result = wechat_archive.tool_wechat_sessions({})

    assert result["ok"] is False
    assert result["reason_code"] == "not_authorized"
    resolved = tool_availability.with_runtime_availability(
        _schema("catfish_wechat_sessions"), system_name="Darwin"
    )
    assert resolved["available"] is False


def test_authorized_reader_is_visible_to_hermes(configured_reader: Path) -> None:
    resolved = tool_availability.with_runtime_availability(
        _schema("catfish_wechat_sessions"), system_name="Darwin"
    )

    assert resolved["available"] is True
    assert resolved["reason_code"] is None


def test_picker_change_invalidates_previous_authorization(
    configured_reader: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(picker_state, "read_picker_model", lambda: "catfish-public-qwen-flash")
    monkeypatch.setattr(
        wechat_archive,
        "_run_helper_json",
        lambda *_args, **_kwargs: pytest.fail("Picker 变化后不应读取聊天"),
    )

    result = wechat_archive.tool_wechat_sessions({})

    assert result["ok"] is False
    assert result["reason_code"] == "picker_changed"
    assert result["current_picker_model"] == "catfish-public-qwen-flash"


def test_missing_or_changed_export_file_is_rejected(
    configured_reader: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = configured_reader.parent / "wechat.jsonl"
    source.write_text(source.read_text(encoding="utf-8") + "{}\n", encoding="utf-8")
    monkeypatch.setattr(
        wechat_archive,
        "_run_helper_json",
        lambda *_args, **_kwargs: pytest.fail("数据源变化后不应启动读取器"),
    )

    changed = wechat_archive.tool_wechat_sessions({})
    assert changed["ok"] is False
    assert changed["reason_code"] == "source_changed"

    source.unlink()
    missing = wechat_archive.tool_wechat_sessions({})
    assert missing["ok"] is False
    assert missing["reason_code"] == "source_missing"


def test_insecure_reader_is_rejected(
    configured_reader: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doctor = _doctor_ok()
    doctor["secure_key_store"] = False
    monkeypatch.setattr(
        wechat_archive,
        "_run_helper_json",
        lambda _helper, command, _args: doctor if command == "doctor" else {},
    )

    result = wechat_archive.tool_wechat_sessions({})

    assert result["ok"] is False
    assert result["reason_code"] == "unsafe_reader"


def test_history_is_bounded_and_returns_only_safe_fields(
    configured_reader: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, list[str]]] = []

    def fake_run(_helper: Path, command: str, args: list[str]) -> object:
        calls.append((command, args))
        if command == "doctor":
            return _doctor_ok()
        return {
            "items": [
                {
                    "message_id": "m1",
                    "session_id": "s1",
                    "sender_name": "张三",
                    "timestamp": "2026-08-28T09:00:00+08:00",
                    "text": "项目进度正常",
                    "db_path": "/private/secret.db",
                    "encryption_key": "must-not-leak",
                }
            ]
        }

    monkeypatch.setattr(wechat_archive, "_run_helper_json", fake_run)
    result = wechat_archive.tool_wechat_history(
        {
            "session_id": "s1",
            "start_time": "2026-08-25T00:00:00+08:00",
            "end_time": "2026-08-28T23:59:59+08:00",
            "limit": 9999,
        }
    )

    assert result["ok"] is True
    assert result["picker_model"] == "catfish-private-main"
    assert result["items"] == [
        {
            "message_id": "m1",
            "session_id": "s1",
            "sender_name": "张三",
            "timestamp": "2026-08-28T09:00:00+08:00",
            "text": "项目进度正常",
        }
    ]
    assert calls[-1][0] == "history"
    assert calls[-1][1][-4:-2] == ["--limit", "200"]
    assert calls[-1][1][-2] == "--source"
    assert calls[-1][1][-1].endswith("wechat.jsonl")


def test_search_requires_explicit_time_range(configured_reader: Path) -> None:
    result = wechat_archive.tool_wechat_search({"query": "预算"})

    assert result["ok"] is False
    assert result["reason_code"] == "invalid_scope"


def test_helper_process_does_not_receive_model_or_api_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    helper = tmp_path / "reader"
    helper.write_text(
        f"#!{sys.executable}\n"
        "import json, os\n"
        "print(json.dumps({'api_key': os.environ.get('OPENAI_API_KEY'), "
        "'model': os.environ.get('MODEL'), 'tmp': os.environ.get('TMPDIR')}))\n",
        encoding="utf-8",
    )
    helper.chmod(0o700)
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    monkeypatch.setenv("MODEL", "must-not-leak")

    payload = wechat_archive._run_helper_json(helper, "doctor", [])

    assert payload["api_key"] is None
    assert payload["model"] is None
    assert payload["tmp"]
    assert not os.path.exists(payload["tmp"]), "调用结束后临时目录必须被清理"
