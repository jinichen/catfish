"""delivery/catfish-poc/tools/build_component_manifest.py 单测.

跑法 (仓库根): python3 -m pytest scripts/tests/test_build_component_manifest.py -q
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "build_component_manifest",
    Path(__file__).resolve().parents[2] / "delivery" / "catfish-poc" / "tools" / "build_component_manifest.py",
)
bcm = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bcm)


@pytest.mark.parametrize("name,expected", [
    ("meeting-asr-1.0.0-mac-arm64.tar.gz", ("meeting-asr", "1.0.0", "mac-arm64")),
    ("bge-m3-2.10.3-windows-x64.tar.gz", ("bge-m3", "2.10.3", "windows-x64")),
    ("meeting-asr-1.0-mac-arm64.tar.gz", None),       # 版本号要三段
    ("meeting-asr-1.0.0-linux-x64.tar.gz", None),     # 未知平台
    ("meeting-asr-1.0.0-mac-arm64.zip", None),
    ("Meeting-1.0.0-mac-arm64.tar.gz", None),         # 大写
])
def test_parse_filename(name, expected):
    meta = bcm.parse_filename(name)
    if expected is None:
        assert meta is None
    else:
        assert (meta["name"], meta["version"], meta["platform"]) == expected


def test_manifest_lists_newest_version_per_platform_with_sha256(tmp_path):
    (tmp_path / "meeting-asr-1.0.0-mac-arm64.tar.gz").write_bytes(b"old")
    (tmp_path / "meeting-asr-1.10.0-mac-arm64.tar.gz").write_bytes(b"new")   # 1.10 > 1.9, 不是字符串比较
    (tmp_path / "meeting-asr-1.9.0-mac-arm64.tar.gz").write_bytes(b"mid")
    (tmp_path / "meeting-asr-1.0.0-windows-x64.tar.gz").write_bytes(b"win")
    (tmp_path / "manifest.json").write_text("{}")   # 自己不算组件
    (tmp_path / ".DS_Store").write_bytes(b"x")

    m = bcm.build_manifest(tmp_path)
    assert m["schema"] == 1
    got = {(c["name"], c["platform"]): c for c in m["components"]}
    assert set(got) == {("meeting-asr", "mac-arm64"), ("meeting-asr", "windows-x64")}
    mac = got[("meeting-asr", "mac-arm64")]
    assert mac["version"] == "1.10.0"
    assert mac["file"] == "meeting-asr-1.10.0-mac-arm64.tar.gz"
    assert mac["size"] == 3
    assert mac["sha256"] == hashlib.sha256(b"new").hexdigest()


def test_misnamed_file_is_an_error_not_silently_skipped(tmp_path):
    (tmp_path / "meeting-asr-1.0.0-mac-arm64.tar.gz").write_bytes(b"ok")
    (tmp_path / "meeting_asr_v1.tar.gz").write_bytes(b"bad")
    with pytest.raises(ValueError, match="meeting_asr_v1.tar.gz"):
        bcm.build_manifest(tmp_path)


def test_main_writes_manifest_atomically(tmp_path, capsys):
    (tmp_path / "meeting-asr-1.0.0-mac-arm64.tar.gz").write_bytes(b"abc")
    assert bcm.main([str(tmp_path)]) == 0
    data = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert data["components"][0]["name"] == "meeting-asr"
    assert not (tmp_path / ".manifest.json.tmp").exists()
    assert bcm.main([str(tmp_path / "nope")]) == 1
