"""pytest fixtures (BL-D3 Phase 1)."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from catfish_mcp_registry.app import app
from catfish_mcp_registry.loader import ManifestRegistry


@pytest.fixture
def manifests_dir() -> Path:
    """真 manifests/ 目录 — 让单测跑真 4 个 yaml."""
    return (Path(__file__).resolve().parent.parent / "manifests").resolve()


@pytest.fixture
def registry(manifests_dir: Path) -> ManifestRegistry:
    r = ManifestRegistry(manifests_dir)
    r.load_all()
    return r


@pytest.fixture
def client(registry: ManifestRegistry):
    """FastAPI TestClient — registry 直接挂到 app.state, 跳 lifespan."""
    app.state.registry = registry
    return TestClient(app)
