"""基础冒烟测试：建索引 + 查询在临时目录跑通。"""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest import mock

from catfish_search.config import SearchConfig
from catfish_search.indexer import cleanup_missing, run_index
from catfish_search.query import search, stats_summary


def _make_cfg(root: Path) -> SearchConfig:
    return SearchConfig(
        include=[root],
        exclude=[],
        max_file_size_mb=10,
        file_types={".md", ".txt"},
    )


def test_index_and_query():
    """端到端：写几个文件、索引、查询命中、清理。

    覆盖三类查询：
        - 2 字中文（走 LIKE 降级）
        - 4 字中文（走 FTS5 trigram）
        - 英文短词（走 LIKE 降级）
    """
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "a.md").write_text("鲶鱼项目的设计文档：三大支柱之一是浏览器 Agent。")
        (root / "b.txt").write_text("This is about cats and fish.")
        (root / "c.md").write_text("无关内容。记录今天的天气。")

        with mock.patch(
            "catfish_search.indexer.DB_FILE",
            Path(td) / "search.db",
        ), mock.patch(
            "catfish_search.query.open_db",
            new=lambda: __import__("sqlite3").connect(Path(td) / "search.db"),
        ):
            stats = run_index(_make_cfg(root))
            assert stats["indexed"] >= 3, f"expected >=3, got {stats}"

            # 2 字中文：走 LIKE 降级
            hits = search("鲶鱼")
            assert len(hits) >= 1
            assert any("a.md" in h.path for h in hits)

            # 4 字中文：走 FTS5 trigram
            hits = search("设计文档")
            assert any("a.md" in h.path for h in hits)

            # 英文短词（<3）也应该能找到
            hits = search("cat")
            assert any("b.txt" in h.path for h in hits)

            # 英文长词走 FTS5
            hits = search("about")
            assert any("b.txt" in h.path for h in hits)


def test_cleanup_missing():
    """删除的文件应该从索引里被清理。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        f = root / "will-delete.md"
        f.write_text("临时内容")

        with mock.patch(
            "catfish_search.indexer.DB_FILE",
            Path(td) / "search.db",
        ):
            run_index(_make_cfg(root))
            f.unlink()
            removed = cleanup_missing(_make_cfg(root))
            assert removed == 1


def test_stats_summary():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "one.md").write_text("内容")
        with mock.patch(
            "catfish_search.indexer.DB_FILE",
            Path(td) / "search.db",
        ), mock.patch(
            "catfish_search.query.open_db",
            new=lambda: __import__("sqlite3").connect(Path(td) / "search.db"),
        ):
            run_index(_make_cfg(root))
            summary = stats_summary()
            assert summary["total_files"] >= 1
