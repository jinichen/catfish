"""InMemoryStorage 单测 (KeyringStorage 集成测试要 keyring 装好, 留 mac 端跑)."""
from __future__ import annotations

from catfish_secret_broker.storage import InMemoryStorage, make_storage


def test_set_get(storage: InMemoryStorage):
    storage.set("foo", "bar")
    assert storage.get("foo") == "bar"


def test_get_unknown_returns_none(storage: InMemoryStorage):
    assert storage.get("nonexistent") is None


def test_set_overwrites(storage: InMemoryStorage):
    storage.set("foo", "v1")
    storage.set("foo", "v2")
    assert storage.get("foo") == "v2"


def test_delete(storage: InMemoryStorage):
    storage.set("foo", "bar")
    assert storage.delete("foo") is True
    assert storage.get("foo") is None


def test_delete_unknown(storage: InMemoryStorage):
    assert storage.delete("nonexistent") is False


def test_exists(storage: InMemoryStorage):
    assert storage.exists("foo") is False
    storage.set("foo", "bar")
    assert storage.exists("foo") is True


def test_backend_name(storage: InMemoryStorage):
    assert storage.backend_name == "memory"


def test_make_storage_memory_via_env(monkeypatch):
    monkeypatch.setenv("CATFISH_SECRET_BROKER_BACKEND", "memory")
    s = make_storage()
    assert s.backend_name == "memory"
