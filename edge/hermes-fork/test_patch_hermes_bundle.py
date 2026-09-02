"""Tests for the build-time Hermes uv configuration repair."""
from __future__ import annotations

import io
import sys
import tarfile
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from patch_hermes_bundle import (  # noqa: E402
    _configure_utf8_stdio,
    assert_clean_pyproject,
    main,
    patch_archive,
    patch_pyproject,
)


class _LegacyWindowsStream:
    """Minimal stream that reproduces a cp1252-only Windows console."""

    def __init__(self) -> None:
        self.encoding = "cp1252"
        self.errors = "strict"
        self.text = ""

    def reconfigure(self, *, encoding: str, errors: str) -> None:
        self.encoding = encoding
        self.errors = errors

    def write(self, value: str) -> int:
        value.encode(self.encoding, errors=self.errors)
        self.text += value
        return len(value)

    def flush(self) -> None:
        pass


def test_patch_removes_expired_duration_and_is_idempotent() -> None:
    source = '[tool.uv]\nexclude-newer = "14 days"\nfoo = true\n'
    patched, changed = patch_pyproject(source)
    assert changed is True
    assert 'exclude-newer = "14 days"' not in patched
    assert "foo = true" in patched

    again, changed_again = patch_pyproject(patched)
    assert (again, changed_again) == (patched, False)
    assert_clean_pyproject(again)


def test_valid_absolute_date_is_preserved() -> None:
    source = '[tool.uv]\nexclude-newer = "2026-08-17"\n'
    patched, changed = patch_pyproject(source)
    assert (patched, changed) == (source, False)
    assert_clean_pyproject(patched)


def test_multiple_duration_settings_fail_closed() -> None:
    source = 'exclude-newer = "14 days"\nexclude-newer = "7 days"\n'
    with pytest.raises(ValueError, match="2 个"):
        patch_pyproject(source)


def test_archive_patch_changes_only_root_pyproject(tmp_path: Path) -> None:
    source_tar = tmp_path / "source.tar.gz"
    output_tar = tmp_path / "output.tar.gz"
    with tarfile.open(source_tar, "w:gz") as archive:
        pyproject = b'[tool.uv]\nexclude-newer = "14 days"\n'
        info = tarfile.TarInfo("hermes-agent-src/pyproject.toml")
        info.size = len(pyproject)
        archive.addfile(info, io.BytesIO(pyproject))
        other = b"keep me"
        info = tarfile.TarInfo("hermes-agent-src/README.md")
        info.size = len(other)
        archive.addfile(info, io.BytesIO(other))

    assert patch_archive(source_tar, output_tar) is True
    with tarfile.open(output_tar, "r:gz") as archive:
        assert b"14 days" not in archive.extractfile("hermes-agent-src/pyproject.toml").read()
        assert archive.extractfile("hermes-agent-src/README.md").read() == b"keep me"
        assert_clean_pyproject(
            archive.extractfile("hermes-agent-src/pyproject.toml").read().decode()
        )


def test_main_reconfigures_legacy_windows_console(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stdout = _LegacyWindowsStream()
    stderr = _LegacyWindowsStream()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)

    result = main(["--input", str(tmp_path / "missing.tar.gz"), "--check"])

    assert result == 1
    assert stdout.encoding == "utf-8"
    assert stderr.encoding == "utf-8"
    assert "归档不存在" in stderr.text


def test_configure_utf8_stdio_tolerates_stream_without_reconfigure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", stream)

    _configure_utf8_stdio()
