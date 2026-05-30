"""扫描文件并写入 SQLite FTS5 全文索引。

只记录**路径、标题、内容、元数据**。不记录任何对话、用户行为。
索引库完全在员工 Mac 本地（~/.catfish/search.db），不会上传。
"""
from __future__ import annotations

import fnmatch
import hashlib
import logging
import sqlite3
import time
from pathlib import Path

from .config import DB_FILE, SearchConfig
from .extractor import extract_text

logger = logging.getLogger("catfish.search.indexer")


# trigram 分词器对中英文都能工作（SQLite 3.34+）
SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS documents USING fts5(
    path UNINDEXED,
    title,
    content,
    file_type UNINDEXED,
    tokenize='trigram'
);

CREATE TABLE IF NOT EXISTS file_meta (
    path        TEXT PRIMARY KEY,
    size_bytes  INTEGER,
    mtime       REAL,
    indexed_at  REAL,
    content_hash TEXT
);

CREATE INDEX IF NOT EXISTS idx_meta_mtime ON file_meta(mtime);
"""


def open_db() -> sqlite3.Connection:
    """打开（或初始化）索引库。"""
    DB_FILE.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    conn.executescript(SCHEMA)
    return conn


def _should_skip(path: Path, exclude_patterns: list[str]) -> bool:
    """路径是否命中 exclude 列表。"""
    path_str = str(path)
    for pat in exclude_patterns:
        # 展开 ~ 到 home（注意不要改 pat 本身，ruff PLW2901）
        effective = str(Path(pat).expanduser()) if pat.startswith("~") else pat
        if fnmatch.fnmatch(path_str, effective):
            return True
    return False


def _should_index(path: Path, cfg: SearchConfig) -> bool:
    """判断文件是否应该被索引。"""
    if not path.is_file():
        return False
    if path.suffix.lower() not in cfg.file_types:
        return False
    try:
        if path.stat().st_size > cfg.max_file_size_bytes:
            return False
    except OSError:
        return False
    if _should_skip(path, cfg.exclude):
        return False
    return True


def _iter_files(root: Path, cfg: SearchConfig):
    """递归遍历目录，yield 合规的文件路径。"""
    try:
        for p in root.rglob("*"):
            if _should_skip(p, cfg.exclude):
                if p.is_dir():
                    continue
            if _should_index(p, cfg):
                yield p
    except (PermissionError, OSError) as e:
        logger.debug("skip %s: %s", root, e)


def _hash_content(text: str) -> str:
    """快速 hash 用于判断内容是否变化。"""
    return hashlib.md5(text.encode("utf-8", errors="ignore")).hexdigest()  # noqa: S324


def _needs_reindex(conn: sqlite3.Connection, path: Path) -> bool:
    """根据 mtime 判断是否需要重建索引。"""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return False

    row = conn.execute(
        "SELECT mtime FROM file_meta WHERE path = ?", (str(path),)
    ).fetchone()
    if row is None:
        return True
    return abs(row[0] - mtime) > 1e-3  # 文件时间不同就重建


def _index_one(conn: sqlite3.Connection, path: Path) -> bool:
    """索引单个文件。成功 True，跳过 False。"""
    if not _needs_reindex(conn, path):
        return False

    text = extract_text(path)
    if not text or not text.strip():
        return False

    # 标题简单取文件名（不含扩展名）
    title = path.stem
    # BL-FILE-SESSION-INDEX-V1 Phase 4 (5/30): ~/.catfish/uploads/ 下文件名带
    # <unix_secs>- 前缀 (Companion file_parse.rs 防同名冲突写的). 索引时 title
    # 去掉前缀, 让按原始文件名搜准确. 不去掉的话搜 "客户合同.pdf" 命中
    # "1748582400-客户合同.pdf" 但 title 显示前缀, 体验差.
    # path (绝对路径) 不动 — query.py 返参用 path, UI 仍能跳转到真文件.
    parts = str(path).split("/")
    if ".catfish" in parts and "uploads" in parts:
        import re as _re
        _m = _re.match(r"^\d{10,13}-(.+)$", title)
        if _m:
            title = _m.group(1)

    # 内容过长截断，防止单条超大
    content = text[:500_000]
    content_hash = _hash_content(content)
    mtime = path.stat().st_mtime
    size = path.stat().st_size
    file_type = path.suffix.lower()

    # 先删旧条目
    conn.execute("DELETE FROM documents WHERE path = ?", (str(path),))
    conn.execute(
        "INSERT INTO documents(path, title, content, file_type) VALUES (?, ?, ?, ?)",
        (str(path), title, content, file_type),
    )
    conn.execute(
        """
        INSERT INTO file_meta(path, size_bytes, mtime, indexed_at, content_hash)
        VALUES(?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            size_bytes = excluded.size_bytes,
            mtime      = excluded.mtime,
            indexed_at = excluded.indexed_at,
            content_hash = excluded.content_hash
        """,
        (str(path), size, mtime, time.time(), content_hash),
    )
    return True


def run_index(cfg: SearchConfig, on_progress=None) -> dict:
    """完整扫描 + 索引一次。返回统计。"""
    conn = open_db()
    stats = {"scanned": 0, "indexed": 0, "skipped": 0, "roots": len(cfg.include)}
    start = time.time()

    try:
        for root in cfg.include:
            logger.info("scanning %s", root)
            for path in _iter_files(root, cfg):
                stats["scanned"] += 1
                try:
                    if _index_one(conn, path):
                        stats["indexed"] += 1
                    else:
                        stats["skipped"] += 1
                except Exception as e:
                    logger.debug("index fail %s: %s", path, e)
                    stats["skipped"] += 1

                if on_progress and stats["scanned"] % 50 == 0:
                    on_progress(stats)

                if stats["scanned"] % 200 == 0:
                    conn.commit()
        conn.commit()
    finally:
        conn.close()

    stats["duration_sec"] = round(time.time() - start, 1)
    return stats


def index_path(path: Path) -> bool:
    """单文件增量索引（给 watcher 用）。成功入库 True，内容没变/抽不出文本 False。"""
    conn = open_db()
    try:
        ok = _index_one(conn, path)
        conn.commit()
        return ok
    finally:
        conn.close()


def remove_path(path: Path) -> bool:
    """从索引里删掉一个文件条目。返回是否确实删了。"""
    conn = open_db()
    try:
        cur = conn.execute("DELETE FROM file_meta WHERE path = ?", (str(path),))
        conn.execute("DELETE FROM documents WHERE path = ?", (str(path),))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def should_index(path: Path, cfg: SearchConfig) -> bool:
    """供外部（比如 watcher）判断某个路径是否需要索引。"""
    return _should_index(path, cfg)


def cleanup_missing(cfg: SearchConfig) -> int:
    """清理已删除文件的索引条目。返回清理数。"""
    conn = open_db()
    removed = 0
    try:
        rows = conn.execute("SELECT path FROM file_meta").fetchall()
        for (path_str,) in rows:
            if not Path(path_str).exists():
                conn.execute("DELETE FROM documents WHERE path = ?", (path_str,))
                conn.execute("DELETE FROM file_meta WHERE path = ?", (path_str,))
                removed += 1
        conn.commit()
    finally:
        conn.close()
    return removed
