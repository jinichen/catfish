"""10/30: 中央端版本号接口 —— 门户顶栏靠它显示「中央端 20261030-xxxxxxxx」。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from catfish_gateway import central_version as cv
from catfish_gateway.app import app


def test_docker_env_wins(monkeypatch):
    monkeypatch.setenv("CATFISH_CENTRAL_VERSION", "20261030-abcdef12")
    assert cv.central_version() == {"version": "20261030-abcdef12", "source": "env"}


def test_source_tree_file_when_no_env(monkeypatch, tmp_path):
    monkeypatch.delenv("CATFISH_CENTRAL_VERSION", raising=False)
    f = tmp_path / "VERSION"
    f.write_text("20261030-0ef42c23\n")
    monkeypatch.setattr(cv, "_SOURCE_FILE", f)
    assert cv.central_version() == {"version": "20261030-0ef42c23", "source": "source-tree"}


def test_garbage_or_missing_file_is_unknown(monkeypatch, tmp_path):
    monkeypatch.delenv("CATFISH_CENTRAL_VERSION", raising=False)
    f = tmp_path / "VERSION"
    f.write_text("1.0")
    monkeypatch.setattr(cv, "_SOURCE_FILE", f)
    assert cv.central_version()["version"] == "unknown"
    monkeypatch.setattr(cv, "_SOURCE_FILE", tmp_path / "nope")
    assert cv.central_version()["version"] == "unknown"


def test_real_source_tree_path_points_at_central_version():
    assert cv._SOURCE_FILE.parent.name == "central" and cv._SOURCE_FILE.name == "VERSION"


def test_endpoint_needs_no_login(monkeypatch):
    monkeypatch.setenv("CATFISH_CENTRAL_VERSION", "20261030-abcdef12")
    r = TestClient(app).get("/api/central/version")
    assert r.status_code == 200
    assert r.json()["version"] == "20261030-abcdef12"
