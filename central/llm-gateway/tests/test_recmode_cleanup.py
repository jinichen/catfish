"""BL-LEARN-RECMODE V2 #67 (5/15) — 14 天自动删录屏 隐私单测.

跑法: cd central/llm-gateway && PYTHONPATH=src python -m pytest tests/test_recmode_cleanup.py -q
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from catfish_gateway.recmode import cleanup as cl


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
