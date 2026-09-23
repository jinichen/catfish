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
        assert "model" not in schema["input_schema"].get("properties", {})
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


@pytest.mark.parametrize("source_type", ["authorized_database", "notification_preview"])
def test_removed_source_configs_never_start_a_provider(
    configured_reader: Path, monkeypatch: pytest.MonkeyPatch, source_type: str,
) -> None:
    path = wechat_archive._config_path()
    config = json.loads(path.read_text(encoding="utf-8"))
    config["source_type"] = source_type
    config["provider_id"] = "catfish-wechat-authorized-macos"
    path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setattr(
        wechat_archive, "_run_helper_json",
        lambda *_args, **_kwargs: pytest.fail("旧配置不能启动 Provider"),
    )
    assert wechat_archive.runtime_available() is False
    assert wechat_archive.tool_wechat_sessions({})["ok"] is False


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


# ── 9/23: 微信 ZIP 导入库 ─────────────────────────────────────────────

_READER_SRC = Path(__file__).resolve().parents[2] / "wechat-reader" / "src"


def _library_config(path: Path, helper: Path, library: Path, model: str = "catfish-private-main") -> None:
    path.write_text(json.dumps({
        "version": 2, "enabled": True, "helper_path": str(helper),
        "source_type": "export_library", "source_path": str(library),
        "consented_picker_model": model, "consented_at": "2026-09-23T10:00:00+08:00",
    }), encoding="utf-8")


@pytest.fixture
def real_library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    """真 reader (源码树) + 一个合成的微信导出 ZIP 导进库。"""
    if not _READER_SRC.is_dir():
        pytest.skip("wechat-reader 源码不在旁边")
    import zipfile
    helper = tmp_path / "catfish-wechat-reader"
    helper.write_text(
        f"#!{sys.executable}\nimport sys\nsys.path.insert(0, {str(_READER_SRC)!r})\n"
        "from catfish_wechat_reader.__main__ import main\nsys.exit(main())\n",
        encoding="utf-8",
    )
    helper.chmod(0o700)
    export = tmp_path / "聊天记录.zip"
    body = ("·测试甲\n2026年9月14日 15:06\n材料发一下\n\n"
            "·测试乙\n2026年9月14日 15:07\n[文件] 底稿.rar\n\n"
            "·测试丙\n2026年9月14日 15:08\n收到\n\n")
    with zipfile.ZipFile(export, "w") as archive:
        archive.writestr("聊天记录.txt", body.encode("utf-8"))
    library = tmp_path / "wechat-exports"
    imported = wechat_archive._run_helper_json(helper, "import", [
        "--source", str(export), "--library", str(library), "--group-name", "年审群"])
    config = tmp_path / "wechat_archive.json"
    _library_config(config, helper, library)
    monkeypatch.setenv("CATFISH_WECHAT_ARCHIVE_CONFIG", str(config))
    monkeypatch.setattr(picker_state, "read_picker_model", lambda: "catfish-private-main")
    return library, imported["group_id"]


def test_library_source_end_to_end_with_real_reader(real_library) -> None:
    library, group_id = real_library
    sessions = wechat_archive.tool_wechat_sessions({})
    assert sessions["ok"] is True, sessions
    assert sessions["source_type"] == "export_library"
    assert sessions["items"] == [{
        "session_id": group_id, "name": "年审群", "type": "chat",
        "first_message_at": sessions["items"][0]["first_message_at"],
        "last_message_at": sessions["items"][0]["last_message_at"], "message_count": 3,
    }]
    assert sessions["items"][0]["first_message_at"].startswith("2026-09-14T15:06")

    found = wechat_archive.tool_wechat_search({
        "query": "底稿", "start_time": "2026-09-01T00:00:00+08:00",
        "end_time": "2026-09-30T00:00:00+08:00",
    })
    assert found["ok"] is True, found
    item = found["items"][0]
    assert (item["type"], item["attachment_name"], item["attachment_present"]) == (
        "file", "底稿.rar", False)


def test_library_import_does_not_require_reauthorization(real_library) -> None:
    library, _ = real_library
    (library / "unrelated-new-file.tmp").write_text("x", encoding="utf-8")
    assert wechat_archive.tool_wechat_sessions({})["ok"] is True


def test_reader_failure_reason_is_surfaced_not_swallowed(real_library) -> None:
    library, group_id = real_library
    for archive in library.glob("*.zip"):
        archive.unlink()
    result = wechat_archive.tool_wechat_history({
        "session_id": group_id, "start_time": "2026-09-01T00:00:00+08:00",
        "end_time": "2026-09-30T00:00:00+08:00",
    })
    assert result["ok"] is False
    assert result["reason_code"] == "source_missing"
    assert "导入库缺少原包" in result["error"]


def test_missing_library_is_reported_before_starting_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    helper = tmp_path / "reader"
    helper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    helper.chmod(0o700)
    config = tmp_path / "wechat_archive.json"
    _library_config(config, helper, tmp_path / "never-imported")
    monkeypatch.setenv("CATFISH_WECHAT_ARCHIVE_CONFIG", str(config))
    monkeypatch.setattr(picker_state, "read_picker_model", lambda: "catfish-private-main")
    monkeypatch.setattr(wechat_archive, "_run_helper_json",
                        lambda *_a, **_k: pytest.fail("库不存在时不应启动读取器"))
    result = wechat_archive.tool_wechat_sessions({})
    assert result["reason_code"] == "source_missing"


def test_unknown_reader_reason_codes_are_not_passed_through() -> None:
    error = wechat_archive._reader_failure(
        2, json.dumps({"ok": False, "error": "x" * 1000, "reason_code": "weird"}).encode())
    assert error.reason_code == "reader_failed"
    assert len(str(error)) == 300
    assert wechat_archive._reader_failure(2, b"not json").reason_code == "reader_failed"
