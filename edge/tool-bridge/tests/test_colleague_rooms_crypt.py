"""群聊本机库整库加密 (11/6, docs/GROUP-CHAT-PLAN.md §4.3): SQLCipher, 密钥由 Companion 带来。邮箱都是 example.com。"""
from __future__ import annotations

import asyncio
import json

import pytest

from catfish_tool_bridge import colleague_rooms as cr
from catfish_tool_bridge import colleague_rooms_crypt as crypt
from tests.test_colleague_rooms import BOSS, ZHANG, World, _room_all_joined

pytest.importorskip("sqlcipher3")

K1 = "ab" * 32
K2 = "cd" * 32


def _forget():
    """模拟 tool-bridge 重启: 内存里的密钥和判过的状态都没了。"""
    crypt._key = None
    crypt._gen += 1
    crypt._mode.clear()
    crypt._why_plain.clear()


@pytest.fixture(autouse=True)
def _clean():
    _forget()
    yield
    _forget()


@pytest.fixture()
def w(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


def _texts(rid):
    return [m["text"] for m in cr.messages(rid) if m["kind"] == "text"]


def test_明文库拿到密钥就转加密_记录和未读都在_老备份删掉(w):
    rid = _room_all_joined(w, (ZHANG,))
    w.be(BOSS)
    cr.send(rid, "报价单周五前交")
    w.sync(ZHANG)
    assert crypt.is_plain_file(cr.store.db_path())
    unread = cr.list_rooms()["rooms"][0]["unread"]
    (cr.rooms_dir() / f"{rid}.migrated").mkdir()
    (cr.rooms_dir() / f"{rid}.migrated" / "messages.jsonl").write_text("明文备份")
    (cr.rooms_dir() / "_sync.json.migrated").write_text("{}")
    assert cr.store.encryption()["encrypted"] is False

    crypt.set_key(K1)
    assert _texts(rid) == ["报价单周五前交"]
    assert cr.list_rooms()["rooms"][0]["unread"] == unread
    raw = cr.store.db_path().read_bytes()
    assert not crypt.is_plain_file(cr.store.db_path()) and "报价单".encode() not in raw
    assert not (cr.rooms_dir() / f"{rid}.migrated").exists() and not (cr.rooms_dir() / "_sync.json.migrated").exists()
    assert cr.store.encryption() == {"encrypted": True}
    # 加密以后照常收发
    w.be(BOSS)
    cr.send(rid, "收到")
    w.sync(ZHANG)
    assert _texts(rid) == ["报价单周五前交", "收到"]


def test_已加密的库_没密钥_密钥不对_组件没装_都打不开_不退回明文(w, monkeypatch):
    crypt.set_key(K1)
    rid = _room_all_joined(w, (ZHANG,))
    w.be(ZHANG)
    assert not crypt.is_plain_file(cr.store.db_path())

    _forget()
    with pytest.raises(crypt.RoomsLocked, match="打开鲶鱼 Companion"):
        cr.list_rooms()
    crypt.set_key(K2)
    with pytest.raises(crypt.RoomsLocked, match="对不上"):
        cr.list_rooms()
    _forget()
    crypt.set_key(K1)
    monkeypatch.setattr(crypt, "lib", lambda: None)
    with pytest.raises(crypt.RoomsLocked, match="加密组件没装上"):
        cr.list_rooms()
    monkeypatch.undo()
    w.be(ZHANG)
    _forget()
    crypt.set_key(K1)
    assert cr.load(rid)["name"]


def test_组件没装上_明文库照常用_说明原因(w, monkeypatch):
    monkeypatch.setattr(crypt, "lib", lambda: None)
    crypt.set_key(K1)
    rid = _room_all_joined(w, (ZHANG,))
    w.be(ZHANG)
    assert cr.load(rid) and crypt.is_plain_file(cr.store.db_path())
    st = cr.store.encryption()
    assert st["encrypted"] is False and "加密组件" in st["why"]


def test_快照也是加密的_只许写到本机目录下(w, tmp_path):
    crypt.set_key(K1)
    rid = _room_all_joined(w, (ZHANG,))
    w.be(ZHANG)
    dest = cr.wiki_files._catfish_home() / ".migration-tmp" / "rooms.db"
    out = cr.pim_handlers({"dest": str(dest)})["pim/croom_snapshot"]()
    assert out["ok"] and out["encrypted"] is True
    assert dest.exists() and not crypt.is_plain_file(dest)
    import sqlcipher3
    con = sqlcipher3.connect(str(dest))
    con.execute(crypt._pragma_key(K1))
    assert con.execute("SELECT count(*) FROM rooms WHERE room_id = ?", (rid,)).fetchone()[0] == 1
    con.close()
    for bad in (str(tmp_path / "elsewhere.db"), str(dest.with_suffix(".txt")), "rooms.db"):
        with pytest.raises(ValueError, match="快照位置不对"):
            cr.pim_handlers({"dest": bad})["pim/croom_snapshot"]()


def test_密钥进门就摘掉_不往下传(w, monkeypatch):
    from catfish_tool_bridge import server
    seen = {}

    def handlers(params):
        seen.update(params)
        return {"pim/croom_list": lambda: {"ok": True}}

    monkeypatch.setattr(cr, "pim_handlers", handlers)
    resp = asyncio.run(server._handle_request({"id": 1, "method": "pim/croom_list", "params": {"_rooms_key": K1, "x": 1}}))
    assert resp["result"] == {"ok": True}
    assert seen == {"x": 1} and crypt._key == K1
    assert K1 not in json.dumps(resp)


def test_不合规的密钥忽略():
    for bad in ("", None, "abc", "AB" * 31, "zz" * 32):
        crypt.set_key(bad)
        assert crypt._key is None
    crypt.set_key(("AB" * 32))
    assert crypt._key == "ab" * 32


def test_打进包的sqlcipher3版本跟这里一致_带的SQLite没有WAL损坏漏洞():
    """版本钉在两处: 安装包清单 (hermes-extra-packages.txt) 和这里的 dev 依赖。换版本要两处一起换, 且新版本得过 WAL 漏洞闸。"""
    import importlib.util
    import re
    from pathlib import Path

    import sqlcipher3

    root = Path(__file__).resolve().parents[2]
    extra = (root / "companion-app/src-tauri/hermes-extra-packages.txt").read_text(encoding="utf-8")
    dev = (root / "tool-bridge/pyproject.toml").read_text(encoding="utf-8")
    pin = re.search(r"^sqlcipher3==(\S+)", extra, re.M).group(1)
    assert f'"sqlcipher3=={pin}"' in dev
    spec = importlib.util.spec_from_file_location("chk", root / "companion-app/scripts/check-embedded-sqlite.py")
    chk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(chk)
    assert not chk.is_vulnerable(tuple(int(x) for x in sqlcipher3.sqlite_version.split(".")))
