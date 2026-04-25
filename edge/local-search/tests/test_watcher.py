"""watcher 核心逻辑冒烟测试。

不启动真的 observer 线程，只测试：
    - 去抖队列的 mark / drain_ready
    - _flush 能正确把事件落到索引库
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path
from unittest import mock

from catfish_search import indexer, watcher
from catfish_search.config import SearchConfig


def _make_cfg(root: Path) -> SearchConfig:
    return SearchConfig(
        include=[root],
        exclude=[],
        max_file_size_mb=10,
        file_types={".md", ".txt"},
    )


def test_pending_debounce():
    """未到期的不会被 drain，超过 deadline 的才 drain。"""
    p = watcher._Pending()
    p.mark("/tmp/a.md", "upsert")

    # 刚打的标记还在 deadline 内，应拿不到任何。
    assert p.drain_ready(time.time()) == []

    # 强制 drain 能拿到。
    got = p.drain_ready(time.time(), force=True)
    assert got == [("/tmp/a.md", "upsert")]
    # drain 完就空了。
    assert p.drain_ready(time.time(), force=True) == []


def test_flush_upsert_and_delete():
    """事件驱动的增量：创建 -> upsert；删除 -> delete。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        f = root / "note.md"
        f.write_text("增量索引测试：鲶鱼项目")

        cfg = _make_cfg(root)

        with mock.patch(
            "catfish_search.indexer.DB_FILE",
            Path(td) / "search.db",
        ):
            # 先全量建一遍，拿到基线。
            indexer.run_index(cfg)

            # 制造一个 upsert 事件：改文件内容。
            f.write_text("增量索引测试：鲶鱼项目 v2 新增一行")
            pending = watcher._Pending()
            pending.mark(str(f), "upsert")
            result = watcher._flush(pending, cfg, force=True)
            assert result["upsert"] == 1
            assert result["delete"] == 0

            # 制造一个 delete 事件。
            f.unlink()
            pending.mark(str(f), "delete")
            result = watcher._flush(pending, cfg, force=True)
            assert result["delete"] == 1


def test_flush_skips_filtered_files():
    """不符合规则的路径（扩展名不对、文件太大）应走 skip 分支。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        bad = root / "ignore-me.bin"
        bad.write_text("shouldn't be indexed")

        cfg = _make_cfg(root)  # 只允许 md / txt
        with mock.patch(
            "catfish_search.indexer.DB_FILE",
            Path(td) / "search.db",
        ):
            pending = watcher._Pending()
            pending.mark(str(bad), "upsert")
            result = watcher._flush(pending, cfg, force=True)
            assert result["skip"] == 1
            assert result["upsert"] == 0
