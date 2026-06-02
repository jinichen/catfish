"""BL-LEARN-RECMODE V2 #67 (5/15) — 14 天自动删录屏 隐私单测.

跑法: cd central/llm-gateway && PYTHONPATH=src python -m pytest tests/test_recmode_cleanup.py -q
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from catfish_tool_bridge.recmode import cleanup as cl


def _mk_session(root: Path, sid: str, started_at: float, *, keep_forever: bool = False, with_meta: bool = True):
    """造一个 fake session dir 含 meta.json"""
    sd = root / sid
    sd.mkdir(parents=True)
    if with_meta:
        (sd / "meta.json").write_text(
            json.dumps({"session_id": sid, "started_at": started_at}),
            encoding="utf-8",
        )
    if keep_forever:
        (sd / ".keep_forever").touch()
    # 加一个截图占位 (算大小)
    (sd / "screenshots").mkdir()
    (sd / "screenshots" / "kf_001.png").write_bytes(b"fake_png_data" * 100)
    return sd


@pytest.fixture
def recordings_root(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    root = tmp_path / "recordings"
    root.mkdir()
    return root


def test_cleanup_empty(recordings_root):
    """没 sessions → 0 deleted, 不挂"""
    stats = cl.cleanup_old_recordings()
    assert stats["scanned"] == 0
    assert stats["deleted"] == 0


def test_cleanup_no_root(monkeypatch, tmp_path):
    """recordings 目录不存在 → 不挂返空 stats"""
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path / "nope"))
    stats = cl.cleanup_old_recordings()
    assert stats["scanned"] == 0


def test_cleanup_old_session_deleted(recordings_root):
    """超 14 天的 session 删"""
    old_ts = time.time() - 20 * 24 * 3600  # 20 天前
    _mk_session(recordings_root, "rec_old", old_ts)
    stats = cl.cleanup_old_recordings()
    assert stats["deleted"] == 1
    assert "rec_old" in stats["deleted_session_ids"]
    assert not (recordings_root / "rec_old").exists()


def test_cleanup_fresh_session_kept(recordings_root):
    """7 天内 session 留"""
    fresh_ts = time.time() - 7 * 24 * 3600
    _mk_session(recordings_root, "rec_fresh", fresh_ts)
    stats = cl.cleanup_old_recordings()
    assert stats["still_fresh"] == 1
    assert stats["deleted"] == 0
    assert (recordings_root / "rec_fresh").exists()


def test_cleanup_keep_forever_skipped(recordings_root):
    """超 14 天但标 .keep_forever → 跳过"""
    old_ts = time.time() - 30 * 24 * 3600
    _mk_session(recordings_root, "rec_keep", old_ts, keep_forever=True)
    stats = cl.cleanup_old_recordings()
    assert stats["kept_forever"] == 1
    assert stats["deleted"] == 0
    assert (recordings_root / "rec_keep" / ".keep_forever").exists()


def test_cleanup_meta_keep_forever_in_skill_draft(recordings_root):
    """skill_draft 里的 recmode_meta.json 标 _keep_forever 也跳"""
    old_ts = time.time() - 30 * 24 * 3600
    sd = _mk_session(recordings_root, "rec_meta_keep", old_ts)
    skill = sd / "skill_draft" / "personal" / "x"
    skill.mkdir(parents=True)
    (skill / "recmode_meta.json").write_text(
        json.dumps({"_keep_forever": True, "session_id": "rec_meta_keep"}),
        encoding="utf-8",
    )
    stats = cl.cleanup_old_recordings()
    assert stats["kept_forever"] == 1


def test_cleanup_dry_run(recordings_root):
    """dry_run=True 看不删"""
    old_ts = time.time() - 30 * 24 * 3600
    _mk_session(recordings_root, "rec_dry", old_ts)
    stats = cl.cleanup_old_recordings(dry_run=True)
    assert stats["deleted"] == 1  # 计数有
    assert (recordings_root / "rec_dry").exists()  # 但实际没删


def test_cleanup_custom_ttl(recordings_root):
    """传 ttl_seconds 覆盖默认 14 天"""
    five_days_ago = time.time() - 5 * 24 * 3600
    _mk_session(recordings_root, "rec_5d", five_days_ago)
    # 默认 14 天: 不删
    stats = cl.cleanup_old_recordings()
    assert stats["deleted"] == 0
    # ttl=3 天: 删
    stats = cl.cleanup_old_recordings(ttl_seconds=3 * 24 * 3600)
    assert stats["deleted"] == 1


# ── 6/2 BL-RECMODE-AUTO-CLEAN-RAW: cleanup_consumed_raw 测试 ─────────────


def _mk_session_with_full_raw(
    root: Path,
    sid: str,
    *,
    keep_forever: bool = False,
    with_draft_skill: tuple[str, str] | None = ("personal", "demo_skill"),
):
    """造 session 含完整原料 (events.jsonl + screenshots/*.png + transcripts.jsonl)
    + 可选 skill_draft/<ns>/<name>/SKILL.md."""
    sd = root / sid
    sd.mkdir(parents=True)
    (sd / "meta.json").write_text(
        json.dumps({"session_id": sid, "started_at": time.time()}), encoding="utf-8",
    )
    if keep_forever:
        (sd / ".keep_forever").touch()
    # 训练原料 3 类
    (sd / "events.jsonl").write_text('{"e": 1}\n{"e": 2}\n', encoding="utf-8")
    (sd / "screenshots").mkdir()
    (sd / "screenshots" / "001.png").write_bytes(b"png" * 1000)  # ~3 KB
    (sd / "screenshots" / "002.png").write_bytes(b"png" * 1000)
    (sd / "transcripts.jsonl").write_text('{"t": "hello"}\n', encoding="utf-8")
    # skill_draft
    if with_draft_skill:
        ns, name = with_draft_skill
        draft = sd / "skill_draft" / ns / name
        draft.mkdir(parents=True)
        (draft / "SKILL.md").write_text("# demo", encoding="utf-8")
        (draft / "main.py").write_text("def run(): pass\n", encoding="utf-8")
        (draft / "recmode_meta.json").write_text(
            json.dumps({"namespace": ns, "name": name}), encoding="utf-8",
        )
    return sd


def test_cleanup_consumed_raw_skipped_when_no_saved_skill(recordings_root, tmp_path):
    """没真"保存"到 skills_root → 不删任何东西."""
    sd = _mk_session_with_full_raw(recordings_root, "rec_unsaved")
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    # 故意不 copy 到 skills_root
    result = cl.cleanup_consumed_raw(sd, skills_root=skills_root)
    assert result["ok"] is False
    assert result["has_saved_skill"] is False
    # 原料 3 类全留
    assert (sd / "events.jsonl").exists()
    assert (sd / "screenshots").exists()
    assert (sd / "transcripts.jsonl").exists()


def test_cleanup_consumed_raw_deletes_when_skill_saved(recordings_root, tmp_path):
    """skill 真在 ~/.catfish/skills/<ns>/<name>/ → 删 3 类原料, 保留 meta + draft."""
    sd = _mk_session_with_full_raw(recordings_root, "rec_saved")
    skills_root = tmp_path / "skills"
    saved = skills_root / "personal" / "demo_skill"
    saved.mkdir(parents=True)
    (saved / "SKILL.md").write_text("# demo", encoding="utf-8")  # 模拟员工已保存
    result = cl.cleanup_consumed_raw(sd, skills_root=skills_root)
    assert result["ok"] is True
    assert result["has_saved_skill"] is True
    # 原料 3 类删
    assert not (sd / "events.jsonl").exists()
    assert not (sd / "screenshots").exists()
    assert not (sd / "transcripts.jsonl").exists()
    # 成果保留
    assert (sd / "meta.json").exists()
    assert (sd / "skill_draft" / "personal" / "demo_skill" / "SKILL.md").exists()
    # 释放字节数 > 0
    assert result["freed_bytes"] > 0


def test_cleanup_consumed_raw_keep_forever_skipped(recordings_root, tmp_path):
    """.keep_forever 标了 → 跳过 (员工 opt-out 主权)."""
    sd = _mk_session_with_full_raw(recordings_root, "rec_keep", keep_forever=True)
    skills_root = tmp_path / "skills"
    saved = skills_root / "personal" / "demo_skill"
    saved.mkdir(parents=True)
    (saved / "SKILL.md").write_text("# demo", encoding="utf-8")
    result = cl.cleanup_consumed_raw(sd, skills_root=skills_root)
    assert result["ok"] is False
    assert "keep_forever" in (result["error"] or "")
    assert (sd / "events.jsonl").exists()  # 没删


def test_cleanup_consumed_raw_dry_run(recordings_root, tmp_path):
    """dry_run=True 计数有但实际没删."""
    sd = _mk_session_with_full_raw(recordings_root, "rec_dry")
    skills_root = tmp_path / "skills"
    saved = skills_root / "personal" / "demo_skill"
    saved.mkdir(parents=True)
    (saved / "SKILL.md").write_text("# demo", encoding="utf-8")
    result = cl.cleanup_consumed_raw(sd, skills_root=skills_root, dry_run=True)
    assert result["ok"] is True
    assert result["freed_bytes"] > 0  # 计数仍有
    # 但实际文件没删
    assert (sd / "events.jsonl").exists()
    assert (sd / "screenshots").exists()


def test_cleanup_consumed_raw_no_draft(recordings_root, tmp_path):
    """session 没 skill_draft (录屏失败/没 aggregate) → has_saved_skill=False, 不删."""
    sd = _mk_session_with_full_raw(
        recordings_root, "rec_no_draft", with_draft_skill=None,
    )
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    result = cl.cleanup_consumed_raw(sd, skills_root=skills_root)
    assert result["ok"] is False
    assert result["has_saved_skill"] is False
    assert (sd / "events.jsonl").exists()


def test_cleanup_consumed_raw_session_dir_missing(tmp_path):
    """session_dir 不存在 → error 返, 不挂."""
    fake = tmp_path / "nope"
    result = cl.cleanup_consumed_raw(fake, skills_root=tmp_path / "skills")
    assert result["ok"] is False
    assert "不存在" in (result["error"] or "")


def test_cleanup_freed_bytes(recordings_root):
    """freed_bytes 算 session 文件总大小"""
    old_ts = time.time() - 30 * 24 * 3600
    _mk_session(recordings_root, "rec_size", old_ts)
    stats = cl.cleanup_old_recordings()
    # fake_png_data * 100 = 1300 bytes
    assert stats["freed_bytes"] >= 1000


def test_cleanup_no_meta_uses_mtime(recordings_root):
    """meta.json 不存在 → 用 dir mtime fallback"""
    sd = _mk_session(recordings_root, "rec_no_meta", 0, with_meta=False)
    # 把 mtime 改到 30 天前
    old_ts = time.time() - 30 * 24 * 3600
    os.utime(sd, (old_ts, old_ts))
    stats = cl.cleanup_old_recordings()
    assert stats["deleted"] == 1


def test_cleanup_skips_files_in_root(recordings_root):
    """recordings/ 根下散文件不动 (只删 session dir)"""
    (recordings_root / "junk.txt").write_text("hi")
    stats = cl.cleanup_old_recordings()
    assert (recordings_root / "junk.txt").exists()
    assert stats["scanned"] == 0


# ── BL-RECMODE-NO-AUTO-DELETE (5/25 鸿波) ──────────────────────────


def test_cleanup_daemon_is_removed():
    """5/25 BL-RECMODE-NO-AUTO-DELETE: cleanup_daemon 必须删, 防回归.

    哲学修正: catfish 不该后台自动删用户本机 ~/.catfish/recordings/.
    cleanup_old_recordings 仍可用作显式 utility (Dashboard / CLI 触发).
    """
    assert not hasattr(cl, "cleanup_daemon"), (
        "cleanup_daemon 必须删 — 不该后台自动删用户本机文件 "
        "(BL-RECMODE-NO-AUTO-DELETE 5/25 鸿波修正)"
    )
    # exports 干净
    assert "cleanup_daemon" not in cl.__all__
    # 仍保留 utility
    assert "cleanup_old_recordings" in cl.__all__
    assert "list_recordings_with_meta" in cl.__all__


def test_list_recordings_with_meta_empty():
    """没 recordings root → 返空 list, 不挂."""
    out = cl.list_recordings_with_meta()
    # 没 fixture, 不该有 recordings, 应空
    assert isinstance(out, list)


def test_list_recordings_with_meta_basic(recordings_root):
    """列出每个 session: id / started_at / size / kept_forever / skill_drafts / path."""
    started = time.time() - 3 * 24 * 3600
    sd = _mk_session(recordings_root, "rec_inv_1", started)
    # 加一个 skill_draft
    draft = sd / "skill_draft" / "productivity" / "weekly-report"
    draft.mkdir(parents=True)
    (draft / "SKILL.md").write_text("---\nname: weekly-report\n---\n", encoding="utf-8")

    out = cl.list_recordings_with_meta()
    assert len(out) == 1
    item = out[0]
    assert item["session_id"] == "rec_inv_1"
    assert item["started_at"] == pytest.approx(started, abs=1)
    assert item["size_bytes"] > 0
    assert item["kept_forever"] is False
    assert "productivity/weekly-report" in item["skill_drafts"]
    assert item["path"].endswith("rec_inv_1")


def test_list_recordings_with_meta_kept_forever_flag(recordings_root):
    """kept_forever flag 正确反映 .keep_forever 文件."""
    started = time.time() - 3 * 24 * 3600
    _mk_session(recordings_root, "rec_keep", started, keep_forever=True)
    _mk_session(recordings_root, "rec_normal", started)

    out = cl.list_recordings_with_meta()
    by_id = {item["session_id"]: item for item in out}
    assert by_id["rec_keep"]["kept_forever"] is True
    assert by_id["rec_normal"]["kept_forever"] is False


def test_list_recordings_sorted_newest_first(recordings_root):
    """新 session 排在前 (Dashboard 用户看的顺序)."""
    now = time.time()
    _mk_session(recordings_root, "rec_2025_01_05_old", now - 30 * 86400)
    _mk_session(recordings_root, "rec_2025_05_24_new", now - 1 * 86400)
    _mk_session(recordings_root, "rec_2025_05_10_mid", now - 15 * 86400)

    out = cl.list_recordings_with_meta()
    # session_id 字母倒序 (我们的 sort 是 reverse=True 按文件名 — 新日期 ID 更大)
    ids = [item["session_id"] for item in out]
    # 按 ID 字典序倒排, 最新 ID 在前 (假设 ID 是日期前缀格式)
    assert ids == sorted(ids, reverse=True)
