"""P15 SSE 审批桥必须同时兼容 Hermes 新旧队列。"""
from __future__ import annotations

from pathlib import Path

import plugin_approval
import plugin_verify


class _LegacyQueue:
    def __init__(self) -> None:
        self.items = []

    def put(self, item) -> None:
        self.items.append(item)


class _ThreadSafeQueue:
    def __init__(self) -> None:
        self.items = []
        self.legacy_put_called = False

    def put_threadsafe(self, item) -> None:
        self.items.append(item)

    def put(self, _item) -> None:
        self.legacy_put_called = True
        raise AssertionError("ThreadSafeAsyncQueue 必须优先走 put_threadsafe")


def test_enqueue_stream_event_supports_legacy_queue():
    queue = _LegacyQueue()
    event = ("__tool_progress__", {"status": "approval_pending"})

    plugin_approval._enqueue_stream_event(queue, event)

    assert queue.items == [event]


def test_enqueue_stream_event_prefers_threadsafe_queue():
    queue = _ThreadSafeQueue()
    event = ("__tool_progress__", {"status": "approval_pending"})

    plugin_approval._enqueue_stream_event(queue, event)

    assert queue.items == [event]
    assert queue.legacy_put_called is False


def _write_api_server(root: Path, queue_call: str) -> None:
    target = root / "gateway" / "platforms" / "api_server.py"
    target.parent.mkdir(parents=True)
    target.write_text(
        "async def _handle_chat_completions(self, request):\n"
        "    _stream_q = object()\n"
        "    def _on_delta(delta):\n"
        f"        _stream_q.{queue_call}(delta)\n",
        encoding="utf-8",
    )


def test_verify_accepts_hermes_0200_queue(tmp_path, monkeypatch):
    _write_api_server(tmp_path, "put")
    monkeypatch.setenv("HERMES_ROOT", str(tmp_path))

    assert plugin_verify._check_p15_stream_q_in_source() is True


def test_verify_accepts_hermes_0206_threadsafe_queue(tmp_path, monkeypatch):
    _write_api_server(tmp_path, "put_threadsafe")
    monkeypatch.setenv("HERMES_ROOT", str(tmp_path))

    assert plugin_verify._check_p15_stream_q_in_source() is True


def test_verify_rejects_queue_without_supported_producer(tmp_path, monkeypatch):
    _write_api_server(tmp_path, "put_nowait")
    monkeypatch.setenv("HERMES_ROOT", str(tmp_path))

    assert plugin_verify._check_p15_stream_q_in_source() is False
