"""P12 / P40 在真 Hermes 会话库上: 补丁挂到的是 Hermes 真在调的那个方法 (0.23.2 的 SessionDB, 0.23.3 的 persistence)。

11/20 hermes v0.23.3 把会话库搬进 persistence/:
- P12 原来直接取 `SessionDB.update_system_prompt` → AttributeError → 整个插件 install 失败 (单测全过, 只有真网关测试抓到)
- P40 还装得上, 但写消息不再经过 `SessionDB.append_message`, 去重不起作用

跑法: `HERMES_ROOT=<某版 hermes 树> pytest tests/test_p12_p40_on_real_store.py` (没 hermes 跳过)。
"""
from __future__ import annotations

import importlib
import sqlite3
import time

import pytest

hermes_state = pytest.importorskip("hermes_state")


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hh"))
    monkeypatch.setenv("HERMES_STATE_DB_GUARD_BYPASS", "1")
    (tmp_path / "hh").mkdir()
    d = hermes_state.SessionDB(db_path=tmp_path / "state.db")
    yield d, tmp_path / "state.db"
    d.close()


def _patched():
    import plugin_misc
    import plugin_session
    importlib.reload(plugin_misc)
    importlib.reload(plugin_session)
    plugin_misc._patch_p12_update_system_prompt_safe()
    plugin_session._patch_p40_companion_user_message_dedup()
    return plugin_session


def _companion(path, sid):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE IF NOT EXISTS catfish_companion_sessions (session_id TEXT PRIMARY KEY)")
    con.execute("INSERT OR IGNORE INTO catfish_companion_sessions VALUES (?)", (sid,))
    con.commit()
    con.close()


def _user_rows(path, sid):
    con = sqlite3.connect(path)
    try:
        return con.execute("SELECT count(*) FROM messages WHERE session_id=? AND role='user' AND active=1",
                           (sid,)).fetchone()[0]
    finally:
        con.close()


def test_p12_装得上_并且会话行不存在时先补上(db):
    d, path = db
    _patched()  # 0.23.3 上原来这里就抛 AttributeError
    store = getattr(d, "_store", None)
    if store is not None and hasattr(store, "sessions"):  # 0.23.3+
        store.sessions.update_system_prompt("p12-missing", "你是小鲶")
    else:
        d.update_system_prompt("p12-missing", "你是小鲶")
    con = sqlite3.connect(path)
    try:
        assert con.execute("SELECT count(*) FROM sessions WHERE id='p12-missing'").fetchone()[0] == 1
    finally:
        con.close()


def test_p40_companion会话_同一条用户消息只存一次(db):
    d, path = db
    _patched()
    d.create_session("p40-c", "api_server")
    _companion(path, "p40-c")
    now = time.time()
    first = d.append_message("p40-c", "user", "帮我查一下明天的会", timestamp=now)
    second = d.append_message("p40-c", "user", "帮我查一下明天的会", timestamp=now + 3)
    assert second == first
    assert _user_rows(path, "p40-c") == 1


def test_p40_隔了30秒以上_或者不是companion会话_照常写(db):
    d, path = db
    _patched()
    now = time.time()
    d.create_session("p40-c2", "api_server")
    _companion(path, "p40-c2")
    d.append_message("p40-c2", "user", "继续", timestamp=now)
    d.append_message("p40-c2", "user", "继续", timestamp=now + 31)
    assert _user_rows(path, "p40-c2") == 2

    d.create_session("p40-wx", "weixin")
    d.append_message("p40-wx", "user", "继续", timestamp=now)
    d.append_message("p40-wx", "user", "继续", timestamp=now + 1)
    assert _user_rows(path, "p40-wx") == 2
