"""FTS5 查询 + 结果整理。

查询策略：
    长度 >= 3：走 FTS5 trigram 匹配（快，支持 bm25 排序）
    长度 <  3：降级到 LIKE 子串匹配（慢但能命中"鲶鱼""合同"这类短词）
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .indexer import open_db


@dataclass
class SearchHit:
    path: str
    title: str
    file_type: str
    snippet: str
    score: float


FTS_MIN_LEN = 3


def _escape_fts(query: str) -> str:
    """FTS5 查询包双引号避免语法字符。"""
    q = query.strip().replace('"', ' ')
    return f'"{q}"'


def _make_snippet(content: str, keyword: str, window: int = 80) -> str:
    """从全文里抠出关键词附近一小段。"""
    if not content:
        return ""
    lc = content.lower()
    pos = lc.find(keyword.lower())
    if pos < 0:
        return content[:window * 2]
    start = max(0, pos - window)
    end = min(len(content), pos + len(keyword) + window)
    prefix = "... " if start > 0 else ""
    suffix = " ..." if end < len(content) else ""
    return f"{prefix}{content[start:end]}{suffix}"


def _search_fts(conn: sqlite3.Connection, query: str, limit: int) -> list[SearchHit]:
    rows = conn.execute(
        """
        SELECT
            path,
            title,
            file_type,
            snippet(documents, 2, '[[', ']]', ' ... ', 20) AS snippet,
            bm25(documents) AS score
        FROM documents
        WHERE documents MATCH ?
        ORDER BY score
        LIMIT ?
        """,
        (_escape_fts(query), limit),
    ).fetchall()
    return [_row_to_hit(r) for r in rows]


def _search_like(conn: sqlite3.Connection, query: str, limit: int) -> list[SearchHit]:
    """短查询降级：LIKE 子串匹配，命中后手工抠 snippet。"""
    rows = conn.execute(
        """
        SELECT path, title, file_type, content
        FROM documents
        WHERE content LIKE ? OR title LIKE ?
        LIMIT ?
        """,
        (f"%{query}%", f"%{query}%", limit),
    ).fetchall()
    return [
        SearchHit(
            path=r["path"],
            title=r["title"],
            file_type=r["file_type"],
            snippet=_make_snippet(r["content"] or "", query),
            score=0.0,
        )
        for r in rows
    ]


def _row_to_hit(r: sqlite3.Row) -> SearchHit:
    return SearchHit(
        path=r["path"],
        title=r["title"],
        file_type=r["file_type"],
        snippet=(r["snippet"] or "").replace("\n", " "),
        score=float(r["score"]),
    )


def search(query: str, limit: int = 10) -> list[SearchHit]:
    """跑一次查询。短查询自动降级 LIKE。"""
    q = query.strip()
    if not q:
        return []

    conn = open_db()
    try:
        conn.row_factory = sqlite3.Row
        if len(q) >= FTS_MIN_LEN:
            return _search_fts(conn, q, limit)
        return _search_like(conn, q, limit)
    finally:
        conn.close()


def stats_summary() -> dict:
    """索引库统计信息。"""
    conn = open_db()
    try:
        total = conn.execute("SELECT COUNT(*) FROM file_meta").fetchone()[0]
        by_type = conn.execute(
            """
            SELECT file_type, COUNT(*) AS n
            FROM documents
            GROUP BY file_type
            ORDER BY n DESC
            """
        ).fetchall()
        size_sum = conn.execute(
            "SELECT COALESCE(SUM(size_bytes), 0) FROM file_meta"
        ).fetchone()[0]
    finally:
        conn.close()

    return {
        "total_files": total,
        "by_type": [dict(ft=t, count=n) for t, n in by_type],
        "total_size_mb": round(size_sum / (1024 * 1024), 1),
    }
