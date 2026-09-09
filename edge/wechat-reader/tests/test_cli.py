from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from catfish_wechat_reader.__main__ import main


def _run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, dict]:
    code = main(list(args))
    output = capsys.readouterr()
    assert output.err == ""
    return code, json.loads(output.out)


def _messages() -> list[dict[str, object]]:
    return [
        {
            "message_id": "m1",
            "session_id": "team",
            "session_name": "项目群",
            "sender_id": "u1",
            "sender_name": "张三",
            "timestamp": "2026-08-27T09:00:00+08:00",
            "type": "text",
            "text": "预算已提交",
            "is_self": False,
        },
        {
            "message_id": "m2",
            "session_id": "team",
            "session_name": "项目群",
            "sender_id": "me",
            "sender_name": "我",
            "timestamp": "2026-08-28T10:30:00+08:00",
            "type": "text",
            "text": "请确认合同",
            "is_self": True,
        },
        {
            "message_id": "m3",
            "session_id": "finance",
            "session_name": "财务",
            "sender_id": "u2",
            "sender_name": "李四",
            "timestamp": "2026-08-28T11:00:00+08:00",
            "type": "text",
            "text": "合同金额无误",
            "is_self": False,
        },
    ]


def test_doctor_reports_safe_export_reader(capsys: pytest.CaptureFixture[str]) -> None:
    code, payload = _run(capsys, "doctor", "--json")

    assert code == 0
    assert payload["protocol_version"] == 1
    assert payload["read_only"] is True
    assert payload["secure_key_store"] is True
    assert payload["ephemeral_plaintext_cache"] is True
    assert payload["modifies_wechat_app"] is False
    assert payload["source_types"] == ["export_file"]
    assert set(payload["formats"]) == {"json", "jsonl", "csv"}


@pytest.mark.parametrize("command", [
    "provider-authorize", "provider-run", "provider-plan", "authorized-access-plan",
])
def test_removed_provider_commands_are_not_available(command: str) -> None:
    with pytest.raises(SystemExit) as error:
        main([command, "--json"])
    assert error.value.code == 2


@pytest.mark.parametrize("suffix", ["db", "sqlite", "sqlite3"])
def test_database_sources_are_rejected_without_modification(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], suffix: str,
) -> None:
    source = tmp_path / f"messages.{suffix}"
    original = b"SQLite format 3\x00not-a-chat-export"
    source.write_bytes(original)
    code, payload = _run(capsys, "sessions", "--source", str(source))
    assert code == 2
    assert payload["reason_code"] == "unsupported_format"
    assert source.read_bytes() == original


@pytest.mark.parametrize("shape", ["array", "object"])
def test_json_sessions_aggregate_without_persisting_index(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    shape: str,
) -> None:
    source = tmp_path / "wechat.json"
    body: object = _messages() if shape == "array" else {"messages": _messages()}
    source.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")

    code, payload = _run(
        capsys, "sessions", "--json", "--source", str(source), "--limit", "10"
    )

    assert code == 0
    assert payload["count"] == 2
    assert payload["items"][0] == {
        "session_id": "finance",
        "name": "财务",
        "type": "chat",
        "last_message_at": "2026-08-28T11:00:00+08:00",
        "message_count": 1,
    }
    assert not list(tmp_path.glob("*.db"))


def test_jsonl_history_filters_time_and_session(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "wechat.jsonl"
    source.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in _messages()) + "\n",
        encoding="utf-8",
    )

    code, payload = _run(
        capsys,
        "history",
        "--json",
        "--source",
        str(source),
        "--session-id",
        "team",
        "--start",
        "2026-08-28T00:00:00+08:00",
        "--end",
        "2026-08-28T23:59:59+08:00",
        "--limit",
        "20",
    )

    assert code == 0
    assert [item["message_id"] for item in payload["items"]] == ["m2"]
    assert "session_name" not in payload["items"][0]


def test_csv_accepts_documented_chinese_headers_and_searches(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "wechat.csv"
    with source.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["消息ID", "会话ID", "会话名称", "发送人", "时间", "内容", "是否本人"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "消息ID": "m9",
                "会话ID": "sales",
                "会话名称": "销售部",
                "发送人": "王五",
                "时间": "2026-08-28T12:00:00+08:00",
                "内容": "客户合同等待盖章",
                "是否本人": "否",
            }
        )

    code, payload = _run(
        capsys,
        "search",
        "--json",
        "--source",
        str(source),
        "--query",
        "合同",
        "--start",
        "2026-08-28T00:00:00+08:00",
        "--end",
        "2026-08-28T23:59:59+08:00",
        "--limit",
        "20",
    )

    assert code == 0
    assert payload["items"][0]["text"] == "客户合同等待盖章"
    assert payload["items"][0]["sender_name"] == "王五"


def test_unknown_extension_and_missing_required_fields_fail_closed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    unknown = tmp_path / "chat.txt"
    unknown.write_text("hello", encoding="utf-8")
    code, payload = _run(
        capsys, "sessions", "--json", "--source", str(unknown), "--limit", "10"
    )
    assert code == 2
    assert payload["ok"] is False
    assert payload["reason_code"] == "unsupported_format"

    broken = tmp_path / "broken.jsonl"
    broken.write_text('{"text":"没有会话和时间"}\n', encoding="utf-8")
    code, payload = _run(
        capsys, "sessions", "--json", "--source", str(broken), "--limit", "10"
    )
    assert code == 2
    assert payload["reason_code"] == "invalid_record"


def test_limit_is_capped_at_200(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "many.jsonl"
    rows = []
    for index in range(250):
        item = _messages()[0].copy()
        item["message_id"] = f"m{index}"
        item["timestamp"] = f"2026-08-28T09:{index % 60:02d}:00+08:00"
        rows.append(json.dumps(item, ensure_ascii=False))
    source.write_text("\n".join(rows), encoding="utf-8")

    code, payload = _run(
        capsys,
        "history",
        "--json",
        "--source",
        str(source),
        "--session-id",
        "team",
        "--start",
        "2026-08-28T00:00:00+08:00",
        "--end",
        "2026-08-28T23:59:59+08:00",
        "--limit",
        "9999",
    )
    assert code == 0
    assert payload["count"] == 200
