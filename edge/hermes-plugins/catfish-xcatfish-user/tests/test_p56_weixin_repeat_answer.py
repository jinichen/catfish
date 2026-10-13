"""P56: 连着两个审批都回「1」, 第二个不能被微信内容去重吞掉; 通道晚到的重投不能替员工批准。

假 adapter 照抄 weixin.py:1477-1488 的两道去重 (message_id + 同人同文字 5 分钟), 不 import hermes。
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time

import plugin_approval_ux as ux


class _Dedup:
    """跟 gateway/platforms/helpers.py MessageDeduplicator 同语义的最小实现。"""

    def __init__(self, ttl=300):
        self._seen = {}
        self._ttl = ttl

    def is_duplicate(self, key):
        now = time.time()
        if key in self._seen and now - self._seen[key] < self._ttl:
            return True
        self._seen[key] = now
        return False


def _extract_text(items):
    return items[0]["text"] if items else ""


class _Adapter:
    def __init__(self):
        self._dedup = _Dedup()
        self.processed = []


async def _orig_process(self, message):
    mid = message.get("message_id")
    if mid and self._dedup.is_duplicate(mid):
        return
    text = _extract_text(message.get("item_list") or [])
    if text:
        key = f"content:{message['from_user_id']}:{hashlib.md5(text.encode()).hexdigest()}"
        if self._dedup.is_duplicate(key):
            return
    self.processed.append(text)


process = ux.wrap_process_message(_orig_process, _extract_text)


def _msg(mid, text, created=None):
    m = {"message_id": mid, "from_user_id": "u1", "item_list": [{"text": text}]}
    if created is not None:
        m["create_time_ms"] = int(created * 1000)
    return m


def _send(adapter, m):
    asyncio.run(process(adapter, m))


def test_1013_second_one_reaches_hermes(monkeypatch):
    a = _Adapter()
    now = time.time()
    _send(a, _msg("m1", "1", now))                         # 第一个审批: 回 1
    monkeypatch.setattr(ux, "_PROMPT_SENT_AT", time.time() + 0.01)   # 18 秒后第二个审批提示
    _send(a, _msg("m2", "1", time.time() + 0.02))          # 再回 1, 发送时间在新提示之后
    assert a.processed == ["1", "1"]


def test_late_redelivery_of_old_answer_cannot_approve_the_new_prompt(monkeypatch):
    a = _Adapter()
    t0 = time.time() - 30
    _send(a, _msg("m1", "1", t0))
    monkeypatch.setattr(ux, "_PROMPT_SENT_AT", time.time())         # 新审批
    _send(a, _msg("m1-redelivered", "1", t0))              # 通道重投: 换了 id, 发送时间还是旧的
    assert a.processed == ["1"]


def test_without_send_time_we_stay_safe_and_log(monkeypatch, caplog):
    a = _Adapter()
    _send(a, _msg("m1", "1"))
    monkeypatch.setattr(ux, "_PROMPT_SENT_AT", time.time() + 0.01)
    with caplog.at_level(logging.WARNING):
        _send(a, _msg("m2", "1"))
    assert a.processed == ["1"]
    assert "没有 create_time_ms" in caplog.text


def test_no_new_prompt_means_true_duplicate(monkeypatch):
    a = _Adapter()
    monkeypatch.setattr(ux, "_PROMPT_SENT_AT", time.time() - 60)    # 最近的审批在第一个「1」之前
    _send(a, _msg("m1", "1", time.time()))
    _send(a, _msg("m2", "1", time.time()))
    assert a.processed == ["1"]


def test_ordinary_chat_duplicates_and_same_message_id_untouched(monkeypatch):
    a = _Adapter()
    monkeypatch.setattr(ux, "_PROMPT_SENT_AT", time.time() + 5)
    _send(a, _msg("m1", "英伟达的股票分析", time.time()))
    _send(a, _msg("m2", "英伟达的股票分析", time.time() + 10))       # 不是审批回答 → 照旧去重
    _send(a, _msg("m3", "1", time.time()))
    _send(a, _msg("m3", "1", time.time() + 10))                       # 同一个 message_id → 照旧去重
    assert a.processed == ["英伟达的股票分析", "1"]


def test_decision_function():
    f = ux.should_bypass_content_dedup
    assert f(prev_seen=100.0, created_s=120.0, prompt_at=119.0)
    assert f(prev_seen=100.0, created_s=118.5, prompt_at=119.0)      # 时钟余量 2 秒内
    assert not f(prev_seen=100.0, created_s=110.0, prompt_at=119.0)  # 发送时间早于新提示 = 重投
    assert not f(prev_seen=120.0, created_s=130.0, prompt_at=119.0)  # 新提示早于上次回答 = 真重复
    assert not f(prev_seen=None, created_s=130.0, prompt_at=119.0)
    assert not f(prev_seen=100.0, created_s=None, prompt_at=119.0)


def test_prompt_records_send_time():
    before = time.time()
    ux.format_approval_prompt("execute_code <<'PY'\nimport requests\nPY", "x")
    assert ux._PROMPT_SENT_AT >= before
