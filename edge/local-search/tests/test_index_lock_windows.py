"""全量索引锁在 Windows 上也要互斥 (10/2)。"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from catfish_search import indexer


def test_full_index_lock_windows_branch_is_exclusive(monkeypatch):
    """10/2: Windows 没有 fcntl, 原来直接放行 → 两个 watcher 一起全量扫。

    这里在 mac / Linux 上模拟 Windows: fcntl 导入失败, msvcrt 换成按文件记锁的假实现
    (真 msvcrt.locking 同一文件的第二个句柄锁同一字节也会失败, 语义一样)。
    """
    import sys
    import types

    held: set = set()

    def locking(fd, mode, nbytes):
        st = os.fstat(fd)
        key = (st.st_dev, st.st_ino)  # 同一个文件 = 同一把锁
        if mode == fake.LK_NBLCK:
            if key in held:
                raise OSError(36, "Resource deadlock avoided")
            held.add(key)
        elif mode == fake.LK_UNLCK:
            held.discard(key)

    fake = types.SimpleNamespace(LK_NBLCK=2, LK_UNLCK=0, locking=locking)
    monkeypatch.setitem(sys.modules, "fcntl", None)  # import fcntl → ImportError
    monkeypatch.setitem(sys.modules, "msvcrt", fake)

    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setattr(indexer, "DB_FILE", Path(td) / "t.db")
        with indexer.full_index_lock() as first:
            assert first is True
            with indexer.full_index_lock() as second:
                assert second is False, "Windows 上第二个也必须拿不到锁"
        with indexer.full_index_lock() as again:
            assert again is True
