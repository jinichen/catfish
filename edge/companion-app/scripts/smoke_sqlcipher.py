#!/usr/bin/env python3
"""打包时用**打进包的那个解释器 + wheelhouse** 验一遍群聊本机库的加密组件 (11/6, docs/GROUP-CHAT-PLAN.md §4.3)。

用法: "<装好 wheelhouse 的 venv>/python" scripts/smoke_sqlcipher.py   退出码 0 = 通过。

Mac: build-mac-resources.sh (arm64 / Intel 各一次); Windows: smoke-windows-runtime.ps1 (MSI 流水线, 真 Windows 上跑)。
验: 能 import、带的 SQLite 不在 WAL 损坏区间 (跟 check-embedded-sqlite.py 同判据)、加密真生效 (文件里没明文、
没密钥打不开)、WAL / 中文 LIKE / VACUUM INTO 快照 / sqlcipher_export (明文库转加密) 都能用 —— 这些是
tool-bridge colleague_rooms_crypt.py 实际用到的。
"""
import importlib.util
import sqlite3
import sys
import tempfile
from pathlib import Path


def main() -> int:
    try:
        import sqlcipher3 as sq
    except ImportError as e:
        print(f"    ❌ sqlcipher3 装不上 / import 不了: {e}")
        return 1
    spec = importlib.util.spec_from_file_location("chk", Path(__file__).with_name("check-embedded-sqlite.py"))
    chk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(chk)
    if chk.is_vulnerable(tuple(int(x) for x in sq.sqlite_version.split("."))):
        print(f"    ❌ sqlcipher3 带的 SQLite {sq.sqlite_version} 有 WAL-reset 损坏漏洞, 换一个 sqlcipher3 版本")
        return 1
    key = "x'" + "5a" * 32 + "'"
    with tempfile.TemporaryDirectory(prefix="catfish-sqlcipher-smoke-", ignore_cleanup_errors=True) as tmp:
        d = Path(tmp)
        c = sq.connect(str(d / "rooms.db"))
        c.execute(f'PRAGMA key = "{key}"')
        cipher = c.execute("PRAGMA cipher_version").fetchone()[0]
        assert c.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        c.execute("CREATE TABLE m (seq INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT)")
        c.execute("INSERT INTO m (text) VALUES (?)", ("报价单周五前交 SMOKE-PLAINTEXT",))
        c.commit()
        assert c.execute("SELECT count(*) FROM m WHERE text LIKE ?", ("%周五%",)).fetchone()[0] == 1
        c.execute("VACUUM INTO ?", (str(d / "snap.db"),))
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        c.close()
        for f in ("rooms.db", "snap.db"):
            raw = (d / f).read_bytes()
            assert raw[:16] != b"SQLite format 3\x00" and b"SMOKE-PLAINTEXT" not in raw, f"{f} 是明文"
        try:
            sq.connect(str(d / "rooms.db")).execute("SELECT count(*) FROM m").fetchone()
            print("    ❌ 没给密钥也打开了加密库")
            return 1
        except sq.DatabaseError:
            pass
        p = sqlite3.connect(str(d / "plain.db"))
        p.execute("CREATE TABLE a (x)")
        p.execute("INSERT INTO a VALUES ('明文')")
        p.commit()
        p.close()
        e = sq.connect(str(d / "plain.db"))
        e.execute(f"ATTACH DATABASE '{d / 'enc.db'}' AS enc KEY \"{key}\"")
        e.execute("SELECT sqlcipher_export('enc')")
        e.execute("DETACH DATABASE enc")
        e.close()
        e2 = sq.connect(str(d / "enc.db"))
        e2.execute(f'PRAGMA key = "{key}"')
        assert e2.execute("SELECT x FROM a").fetchone()[0] == "明文"
        e2.close()
    print(f"    ✓ SQLCipher {cipher} / SQLite {sq.sqlite_version}: 加密、WAL、搜索、快照、明文转加密都正常")
    return 0


if __name__ == "__main__":
    sys.exit(main())
