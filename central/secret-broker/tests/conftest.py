"""pytest fixtures (BL-G6 Phase 2)."""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

# 强制内存后端 (单测不污染 mac Keychain)
os.environ.setdefault("CATFISH_SECRET_BROKER_BACKEND", "memory")

from catfish_secret_broker.app import app  # noqa: E402
from catfish_secret_broker.storage import InMemoryStorage  # noqa: E402


@pytest.fixture
def storage() -> InMemoryStorage:
    return InMemoryStorage()


@pytest.fixture
def client(storage: InMemoryStorage):
    """每个测试拿独立 storage, 不互相污染."""
    app.state.storage = storage
    return TestClient(app)


@pytest.fixture
def auth_headers() -> dict[str, str]:
    """gateway 注入的 user header (单测模拟 gateway 已验证 JWT)."""
    return {"X-Catfish-User-Sub": "alice@catfish.dev"}
