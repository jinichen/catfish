from __future__ import annotations

import json
from pathlib import Path

import pytest

from catfish_wechat_reader.__main__ import main

from wechat_fixtures import GROUP_A, GROUP_A_MEDIA, make_export

WIDE = ["--start", "2026-01-01T00:00:00+08:00", "--end", "2026-12-31T00:00:00+08:00"]


def _run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, dict]:
    code = main(list(args))
    output = capsys.readouterr()
    assert output.err == ""
    return code, json.loads(output.out)


def _import(capsys, source: Path, library: Path, *extra: str) -> dict:
    code, payload = _run(capsys, "import", "--json", "--source", str(source),
                         "--library", str(library), *extra)
    assert code == 0, payload
    return payload


@pytest.fixture
def library(tmp_path: Path) -> Path:
    return tmp_path / "wechat-exports"


def test_doctor_advertises_wechat_zip_without_breaking_safety_contract(capsys) -> None:
    code, payload = _run(capsys, "doctor", "--json")
    assert code == 0
    # Tool Bridge / Companion / Windows 安装脚本都校验这四项, 不能动
    assert payload["read_only"] is True
    assert payload["secure_key_store"] is True
    assert payload["ephemeral_plaintext_cache"] is True
    assert payload["modifies_wechat_app"] is False
    assert "wechat_zip" in payload["formats"]
    assert payload["persists_plaintext"] is False


def test_inspect_does_not_write_anything(tmp_path: Path, library: Path, capsys) -> None:
    source = make_export(tmp_path / "a.zip", GROUP_A, GROUP_A_MEDIA)
    code, payload = _run(capsys, "inspect", "--json", "--source", str(source),
                         "--library", str(library))
    assert code == 0
    assert payload["message_count"] == len(GROUP_A)
    assert payload["attachments"] == {"in_archive": 2, "referenced": 3, "missing": 1}
    assert payload["matched_group_id"] is None
    assert payload["suggested_name"] == "测试甲、测试乙等 5 人"
    assert not library.exists()


def test_import_needs_a_group_when_nothing_matches(tmp_path: Path, library: Path, capsys) -> None:
    source = make_export(tmp_path / "a.zip", GROUP_A, GROUP_A_MEDIA)
    code, payload = _run(capsys, "import", "--json", "--source", str(source),
                         "--library", str(library))
    assert code == 2
    assert payload["reason_code"] == "group_required"


def test_import_is_idempotent_and_self_must_be_a_sender(tmp_path: Path, library: Path, capsys) -> None:
    source = make_export(tmp_path / "a.zip", GROUP_A, GROUP_A_MEDIA)
    code, payload = _run(capsys, "import", "--json", "--source", str(source), "--library",
                         str(library), "--group-name", "年审群", "--self-name", "路人")
    assert code == 2 and payload["reason_code"] == "invalid_scope"
    first = _import(capsys, source, library, "--group-name", "年审群", "--self-name", "我自己")
    again = _import(capsys, source, library, "--group-name", "另一个名字")
    assert first["imported"] is True and again["already_imported"] is True
    assert again["group_id"] == first["group_id"]
    assert len(list(library.glob("*.zip"))) == 1


def test_overlapping_exports_merge_without_duplicates(tmp_path: Path, library: Path, capsys) -> None:
    first = make_export(tmp_path / "1.zip", GROUP_A[:5], GROUP_A_MEDIA)
    second = make_export(tmp_path / "2.zip", GROUP_A[3:], GROUP_A_MEDIA)
    group = _import(capsys, first, library, "--group-name", "年审群")
    # 发送人重合够多, 自动归到同一个群; 「我」只在第二个包里出现, 这时才记
    auto = _import(capsys, second, library, "--self-name", "我自己")
    assert auto["group_id"] == group["group_id"]
    code, payload = _run(capsys, "history", "--json", "--source", str(library),
                         "--session-id", group["group_id"], *WIDE, "--limit", "200")
    assert code == 0
    assert payload["count"] == len(GROUP_A)
    texts = [item["text"] for item in payload["items"]]
    assert texts == [item[2].replace("\u2005", " ") for item in GROUP_A]
    assert [item["is_self"] for item in payload["items"]].count(True) == 1


def test_same_minute_repeats_are_not_collapsed(tmp_path: Path, library: Path, capsys) -> None:
    repeated = [("测试甲", "2026年9月1日 09:00", "好"), ("测试甲", "2026年9月1日 09:00", "好"),
                ("测试乙", "2026年9月1日 09:01", "嗯")]
    source = make_export(tmp_path / "r.zip", repeated)
    group = _import(capsys, source, library, "--group-name", "重复")
    _, payload = _run(capsys, "history", "--json", "--source", str(library),
                      "--session-id", group["group_id"], *WIDE)
    assert payload["count"] == 3


def test_self_is_excluded_from_group_matching(tmp_path: Path, library: Path, capsys) -> None:
    # 两个群只有「我」重合 —— 不能认成同一个群
    a = make_export(tmp_path / "a.zip", [("我自己", "2026年9月1日 09:00", "a"),
                                         ("测试甲", "2026年9月1日 09:01", "b"),
                                         ("测试乙", "2026年9月1日 09:02", "c")])
    b = make_export(tmp_path / "b.zip", [("我自己", "2026年9月2日 09:00", "a"),
                                         ("测试丙", "2026年9月2日 09:01", "b"),
                                         ("测试丁", "2026年9月2日 09:02", "c")])
    _import(capsys, a, library, "--group-name", "甲群", "--self-name", "我自己")
    code, payload = _run(capsys, "inspect", "--json", "--source", str(b), "--library", str(library))
    assert payload["matched_group_id"] is None
    assert payload["known_self_name"] == "我自己"  # 别的群里记过的「我」, 这里自动认出来
    assert payload["suggested_name"] == "测试丙、测试丁的聊天"


def test_private_chat_matches_on_exact_pair_only(tmp_path: Path, library: Path, capsys) -> None:
    first = make_export(tmp_path / "p1.zip", [("我自己", "2026年9月1日 09:00", "a"),
                                              ("测试甲", "2026年9月1日 09:01", "b")])
    later = make_export(tmp_path / "p2.zip", [("测试甲", "2026年9月3日 09:00", "c"),
                                              ("我自己", "2026年9月3日 09:01", "d")])
    group = _import(capsys, first, library, "--group-name", "与测试甲", "--self-name", "我自己")
    assert _import(capsys, later, library)["group_id"] == group["group_id"]


def test_ambiguous_match_is_left_to_the_employee(tmp_path: Path, library: Path, capsys) -> None:
    people = ["测试甲", "测试乙", "测试丙"]
    for index, name in enumerate(["一群", "二群"]):
        src = make_export(tmp_path / f"{index}.zip", [
            (p, f"2026年9月{index + 1}日 09:0{i}", f"{name}{i}") for i, p in enumerate(people)])
        _import(capsys, src, library, "--group-name", name)
    probe = make_export(tmp_path / "probe.zip", [
        (p, "2026年9月9日 09:0{}".format(i), "新消息") for i, p in enumerate(people)])
    _, payload = _run(capsys, "inspect", "--json", "--source", str(probe), "--library", str(library))
    assert payload["matched_group_id"] is None
    assert {c["name"] for c in payload["candidates"]} == {"一群", "二群"}


def test_groups_update_and_remove(tmp_path: Path, library: Path, capsys) -> None:
    source = make_export(tmp_path / "a.zip", GROUP_A, GROUP_A_MEDIA)
    group = _import(capsys, source, library, "--group-name", "年审群")
    code, payload = _run(capsys, "update-group", "--json", "--library", str(library),
                         "--group-id", group["group_id"], "--name", "年审-材料收集",
                         "--self-name", "我自己")
    assert code == 0 and payload["self_name"] == "我自己"
    _, listing = _run(capsys, "groups", "--json", "--library", str(library))
    assert listing["items"][0]["name"] == "年审-材料收集"
    assert listing["items"][0]["message_count"] == len(GROUP_A)
    _, sessions = _run(capsys, "sessions", "--json", "--source", str(library))
    assert sessions["items"][0]["name"] == "年审-材料收集"
    _, removed = _run(capsys, "remove-group", "--json", "--library", str(library),
                      "--group-id", group["group_id"])
    assert removed["removed_exports"] == 1
    assert sorted(p.name for p in library.iterdir()) == ["groups.json"]


def test_history_exposes_attachment_fields(tmp_path: Path, library: Path, capsys) -> None:
    source = make_export(tmp_path / "a.zip", GROUP_A, GROUP_A_MEDIA)
    group = _import(capsys, source, library, "--group-name", "年审群")
    _, payload = _run(capsys, "search", "--json", "--source", str(library),
                      "--query", "原始底稿", *WIDE)
    item = payload["items"][0]
    assert (item["type"], item["attachment_name"], item["attachment_present"]) == (
        "file", "原始底稿.rar", False)
    assert item["session_id"] == group["group_id"]


def test_single_zip_can_be_queried_without_a_library(tmp_path: Path, capsys) -> None:
    source = make_export(tmp_path / "a.zip", GROUP_A, GROUP_A_MEDIA)
    code, payload = _run(capsys, "sessions", "--json", "--source", str(source))
    assert code == 0
    assert payload["items"][0]["session_id"].startswith("zip-")
    assert payload["items"][0]["message_count"] == len(GROUP_A)


def test_orphans_are_ignored_but_missing_archives_fail(tmp_path: Path, library: Path, capsys) -> None:
    source = make_export(tmp_path / "a.zip", GROUP_A, GROUP_A_MEDIA)
    group = _import(capsys, source, library, "--group-name", "年审群")
    (library / f"{'0' * 64}.zip").write_bytes(b"orphan without sidecar")
    code, payload = _run(capsys, "sessions", "--json", "--source", str(library))
    assert code == 0 and payload["count"] == 1
    for archive in library.glob("*.zip"):
        if archive.stem != "0" * 64:
            archive.unlink()
    code, payload = _run(capsys, "history", "--json", "--source", str(library),
                         "--session-id", group["group_id"], *WIDE)
    assert code == 2 and payload["reason_code"] == "source_missing"
