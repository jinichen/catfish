"""群聊本机库整库加密 (11/6, docs/GROUP-CHAT-PLAN.md §4.3): `rooms.db` 用 SQLCipher 存盘。

# 密钥从哪来

32 字节随机数, 存在**系统钥匙串** (macOS 钥匙串 / Windows 凭据管理器), 由 Companion 建、只有 Companion 读
(tool-bridge 读钥匙串在 macOS 上会弹看不见的授权框, 见 companion_secrets.py 文件头)。
Companion 每次调群聊 / 派任务的接口时把它带在参数 `_rooms_key` 里 (server.py 进门就摘掉, 不进日志、不往下传),
这里记在内存里。tool-bridge 重启后, 下一次 Companion 调用就又带上了。

# 什么时候是明文

- 加密组件 `sqlcipher3` 没装上 (老机器升级后启动自检会补装, hermes-extra-packages.txt), 或者
- 还没拿到密钥 (tool-bridge 起来以后 Companion 还没调过)

这两种情况下库**还是明文**时照常用 (界面「记录保留」里显示「本机记录还没加密」和原因);
一旦拿到密钥 + 组件在, 第一次打开就把明文库原样转成加密库 (`sqlcipher_export`, 核对条数后替换), 明文的旧文件和
老格式的备份目录 (`*.migrated`) 一起删掉。**已经加密的库**缺密钥 / 缺组件就打不开 —— 报 RoomsLocked, 不退回明文。

# 换电脑

迁移包 (age 口令加密) 里带上这把密钥, 新电脑导入时写进它的钥匙串 (device_migration)。
导出时库快照由这里做 (`snapshot`: `VACUUM INTO`, 出来的快照也是加密的、同一把密钥)。
"""
from __future__ import annotations

import re
import shutil
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from typing import Any

_KEY_RE = re.compile(r"[0-9a-f]{64}")
PLAIN_HEADER = b"SQLite format 3\x00"

_key: str | None = None
_mode: dict[str, str] = {}        # 库路径 → "encrypted" / "plain" (这个进程里判过的)
_why_plain: dict[str, str] = {}
_gen = 0                          # 换钥 / 首次拿到钥时 +1, 库那边据此重新判一次
_cond = threading.Condition()
_active = 0                       # 正开着的连接数 (转加密前要等它们关掉)
_converting = False


class RoomsLocked(ValueError):
    """本机群聊记录已经加密, 但这会儿打不开 (没密钥 / 组件没装 / 密钥对不上)。"""


NO_KEY = "本机群聊记录是加密的, 要打开鲶鱼 Companion 才能读 (密钥在系统钥匙串里, 只有 Companion 取得到)"
NO_LIB = "本机群聊记录是加密的, 但这台电脑的加密组件没装上 —— 重启 Companion 会自动补装"
BAD_KEY = "本机群聊记录打不开: 钥匙串里的密钥跟库对不上 (换电脑没带迁移包里的密钥?)"


def lib() -> Any | None:
    try:
        import sqlcipher3  # noqa: PLC0415
    except ImportError:
        return None
    return sqlcipher3


def set_key(value: Any) -> None:
    """Companion 带来的密钥 (64 位小写十六进制)。不合规的直接忽略 (不报错, 免得一个坏参数把整个请求拦掉)。"""
    global _key, _gen
    k = str(value or "").strip().lower()
    if not _KEY_RE.fullmatch(k) or k == _key:
        return
    with _cond:
        _key = k
        _gen += 1
        _mode.clear()
        _why_plain.clear()


def generation() -> int:
    return _gen


def is_plain_file(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(16) == PLAIN_HEADER
    except OSError:
        return False


def _pragma_key(key: str) -> str:
    return f"PRAGMA key = \"x'{key}'\""


def _open(path: Path, mode: str):
    if mode == "encrypted":
        L = lib()
        con = L.connect(str(path), timeout=10)
        con.execute(_pragma_key(_key))
        con.row_factory = L.Row
    else:
        con = sqlite3.connect(str(path), timeout=10)
        con.row_factory = sqlite3.Row
    return con


def prepare(path: Path) -> str:
    """第一次打开这个库: 判明文 / 加密, 该转加密就转。返回 mode。调用方 (store._conn) 持锁, 一个库只走一次。"""
    key = str(path)
    if key in _mode:
        return _mode[key]
    L = lib()
    exists = path.exists() and path.stat().st_size > 0
    if not exists or is_plain_file(path):
        if L and _key:
            if exists:
                _convert(path)
            mode = "encrypted"
        else:
            mode = "plain"
            _why_plain[key] = ("加密组件还没装上 (重启 Companion 会自动补装)" if not L
                               else "还没从系统钥匙串取到密钥 (打开 Companion 后会自动加密)")
    else:
        if not L:
            raise RoomsLocked(NO_LIB)
        if not _key:
            raise RoomsLocked(NO_KEY)
        try:
            with closing(_open(path, "encrypted")) as con:
                con.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except L.DatabaseError:
            raise RoomsLocked(BAD_KEY) from None
        mode = "encrypted"
    _mode[key] = mode
    return mode


def _counts(con) -> tuple[int, ...]:
    out = []
    for t in ("rooms", "messages", "meta"):
        try:
            out.append(con.execute(f"SELECT count(*) FROM {t}").fetchone()[0])
        except Exception:  # noqa: BLE001 —— 老库可能还没这张表 (两个库的 DatabaseError 不是同一个类)
            out.append(-1)
    return tuple(out)


def _convert(path: Path) -> None:
    """明文库 → 加密库 (原地)。等正开着的连接都关了再换文件 (Windows 上换不了开着的文件; Mac 上换了旧连接会写丢)。"""
    global _converting
    L = lib()
    tmp = path.with_name(path.name + ".enc-tmp")
    with _cond:
        _converting = True
        _cond.wait_for(lambda: _active == 0, timeout=30)
    try:
        tmp.unlink(missing_ok=True)
        with closing(sqlite3.connect(str(path), timeout=10)) as p:
            p.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            before = _counts(p)
        with closing(L.connect(str(path), timeout=10)) as c:
            target = str(tmp).replace("'", "''")
            c.execute(f"ATTACH DATABASE '{target}' AS enc KEY \"x'{_key}'\"")
            c.execute("SELECT sqlcipher_export('enc')")
            c.execute("DETACH DATABASE enc")
        with closing(_open(tmp, "encrypted")) as e:
            after = _counts(e)
        if after != before:
            raise ValueError(f"本机群聊记录转加密时核对不上 ({before} → {after}), 先不换, 下次再试")
        for side in ("-wal", "-shm"):
            Path(str(path) + side).unlink(missing_ok=True)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
        with _cond:
            _converting = False
            _cond.notify_all()


def connect(path: Path):
    """开一个连接 (按 prepare 判过的 mode)。用完要 release()。"""
    global _active
    with _cond:
        _cond.wait_for(lambda: not _converting, timeout=60)
        _active += 1
    try:
        return _open(path, _mode.get(str(path), "plain"))
    except BaseException:
        release()
        raise


def release() -> None:
    global _active
    with _cond:
        _active = max(0, _active - 1)
        _cond.notify_all()


def drop_plain_leftovers(rooms_dir: Path) -> None:
    """库加密以后, 明文的老格式备份 (`r_*.migrated/`、`_sync.json.migrated`) 留着就白加密了 —— 删掉。"""
    for d in rooms_dir.glob("r_*.migrated"):
        shutil.rmtree(d, ignore_errors=True)
    (rooms_dir / "_sync.json.migrated").unlink(missing_ok=True)


def status(path: Path) -> dict[str, Any]:
    mode = _mode.get(str(path))
    if mode is None:
        return {"encrypted": None}
    return {"encrypted": mode == "encrypted", **({"why": _why_plain[str(path)]} if str(path) in _why_plain else {})}
