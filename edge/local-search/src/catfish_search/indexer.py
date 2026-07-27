"""扫描文件并写入 SQLite FTS5 全文索引。

只记录**路径、标题、内容、元数据**。不记录任何对话、用户行为。
索引库完全在员工 Mac 本地（~/.catfish/search.db），不会上传。
"""
from __future__ import annotations

import fnmatch
import hashlib
import logging
import os
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
    """路径是否命中 exclude 列表。

    BL-SEARCH-EXCLUDE-DIR-FIX (7/27 鸿波实盘撞): 老代码只做 fnmatch(path, pat)，
    而 fnmatch 是**全串匹配** —— `**/node_modules` 翻成 `(?s:.*/node_modules)\\Z`，
    只命中目录条目自己，**不命中目录里的文件**:

        fnmatch('/x/proj/node_modules',           '**/node_modules') → True
        fnmatch('/x/proj/node_modules/lib/a.js',  '**/node_modules') → False  ← 漏

    而 _iter_files 用 pathlib.rglob（不支持剪枝），命中目录只是 `continue` 掉那一条
    目录条目，rglob 照样往里递归。结果 exclude 段列的 node_modules / venv / .git /
    dist 全部形同虚设 —— 鸿波库里 60332 条索引，33206 条 .py + 10685 条 .h，
    绝大多数来自 site-packages / node_modules。

    修：目录型 pattern 追加一条 `pat/*` 的判定（fnmatch 的 `*` 跨 `/` 匹配，
    一条就覆盖任意深度）。文件型 pattern（`**/*.zip`）加了也无害 —— `**/*.zip/*`
    匹配不到东西。
    """
    path_str = str(path)
    for pat in exclude_patterns:
        # 展开 ~ 到 home（注意不要改 pat 本身，ruff PLW2901）
        effective = str(Path(pat).expanduser()) if pat.startswith("~") else pat
        if fnmatch.fnmatch(path_str, effective):
            return True
        # 目录本身命中 → 目录**里面**的一切也要挡
        if fnmatch.fnmatch(path_str, effective.rstrip("/") + "/*"):
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


def _iter_files(root: Path, cfg: SearchConfig, errors: list[OSError] | None = None):
    """递归遍历目录，yield 合规的文件路径。遍历途中的 OSError 收进 `errors`。

    BL-SEARCH-EXCLUDE-DIR-FIX (7/27): 从 pathlib.rglob 换成 os.walk。
    rglob 没法剪枝 —— 老代码 `if _should_skip(p): if p.is_dir(): continue` 只是
    跳过那一条目录条目，rglob 照样往 node_modules / venv 里递归，几万个文件
    白扫一遍。os.walk 可以就地改 dirnames 列表来真剪枝。

    BL-SEARCH-TCC-SILENT-SKIP (7/27 鸿波实盘): 老代码把整个遍历包在
    `except (PermissionError, OSError): logger.debug(...)` 里 —— **整个目录**
    被静默丢掉，只留一条 debug 日志（默认级别根本不打印）。

    鸿波机器上的后果: search-scope.yaml 里 ~/Documents、~/Desktop、~/Downloads
    三个都配了，索引库里三个全是 0 条，而 ~/person_task 一个目录贡献了 60332 条。
    这三个恰好是 macOS TCC 保护目录 —— Companion spawn 的 Python 没拿到
    「文件与文件夹」授权，listdir 直接 EPERM。员工看 Dashboard 只看到
    「已索引 6 万条」，完全不知道自己最重要的三个目录一条没进。

    改成把错误收集起来交给 run_index，由 CLI / UI 明着报出来。
    """
    def _onerror(e: OSError) -> None:
        # os.walk 的 onerror 收到的异常带 .filename，比 root 精确
        logger.warning("遍历失败 %s: %s", getattr(e, "filename", root), e)
        if errors is not None:
            errors.append(e)

    # followlinks=False（默认）：不跟符号链接，防 ~/Documents 里一个指回 home
    # 的软链把整盘拖进来（也顺带防环）。
    for dirpath, dirnames, filenames in os.walk(root, onerror=_onerror):
        base = Path(dirpath)
        # 就地剪枝 —— 必须用切片赋值，os.walk 靠这个列表决定下一层走哪
        dirnames[:] = [
            d for d in dirnames if not _should_skip(base / d, cfg.exclude)
        ]
        for name in filenames:
            p = base / name
            if _should_index(p, cfg):
                yield p


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
    """完整扫描 + 索引一次。返回统计。

    BL-SEARCH-TCC-SILENT-SKIP (7/27): stats 多两项 ——
      per_root:      每个 include 根各索引到几个文件（0 = 这个根白配了）
      unreadable:    [(root, 原因)]，遍历时踩 PermissionError 的根
    这两项是给 CLI / Dashboard 明着报的。macOS 上 ~/Documents、~/Desktop、
    ~/Downloads 要「文件与文件夹」授权，没授权就是整棵目录 0 条，
    以前只有一条 debug 日志，员工根本发现不了。
    """
    conn = open_db()
    stats: dict = {
        "scanned": 0,
        "indexed": 0,
        "skipped": 0,
        "roots": len(cfg.include),
        "per_root": {},
        "unreadable": [],
    }
    start = time.time()

    try:
        for root in cfg.include:
            logger.info("scanning %s", root)
            before = stats["indexed"]
            walk_errors: list[OSError] = []
            for path in _iter_files(root, cfg, errors=walk_errors):
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

            stats["per_root"][str(root)] = stats["indexed"] - before
            if walk_errors:
                # 只记第一条原因就够了 —— 权限问题整棵目录同一个错
                first = walk_errors[0]
                stats["unreadable"].append((str(root), f"{type(first).__name__}: {first}"))
        conn.commit()
    finally:
        conn.close()

    stats["duration_sec"] = round(time.time() - start, 1)
    return stats


def index_is_empty() -> bool:
    """索引库里一条都没有（含库文件不存在）。

    BL-SEARCH-NO-BOOTSTRAP (7/27 鸿波实盘): Companion 只 spawn
    `catfish_search.cli watch`，而 run_watch 只处理**文件变化事件**，全文没有
    一处调 run_index —— 也就是说 local_search 从装上那天起就没做过全量索引。

    鸿波库里那 60332 条全是 watcher 运行期间碰巧被改动过的文件：~/person_task
    是活跃开发目录（git checkout / pip install -e / npm ci 天天动文件）贡献了
    全部，而 ~/Documents、~/Desktop、~/Downloads、~/.catfish/uploads 里的文件
    没人动，一条都没进。他 yaml 里明明配了这四个。

    (autostart.rs 老注释写着"启动慢 5-15s (初始 reconcile)" —— 代码里没有
     reconcile，那句是假的，7/27 一并改掉。)
    """
    if not DB_FILE.exists():
        return True
    conn = open_db()
    try:
        return conn.execute("SELECT COUNT(*) FROM file_meta").fetchone()[0] == 0
    except sqlite3.Error:
        return True  # 库损坏当空处理，重建一次总比不建强
    finally:
        conn.close()


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
    """清理已删除、或现在已被 exclude 排除的索引条目。返回清理数。

    BL-SEARCH-EXCLUDE-DIR-FIX (7/27): 加了 exclude 判定。原因 —— exclude 修好
    之前索引库里已经堆了几万条 node_modules / venv 里的文件，它们**还在磁盘上**，
    光靠 exists() 永远清不掉，会一直污染搜索结果和 style_fingerprint 的语料。
    改成"不存在 or 现在该被排除"两条都清。
    """
    conn = open_db()
    removed = 0
    try:
        rows = conn.execute("SELECT path FROM file_meta").fetchall()
        for (path_str,) in rows:
            p = Path(path_str)
            if p.exists() and not _should_skip(p, cfg.exclude):
                continue
            conn.execute("DELETE FROM documents WHERE path = ?", (path_str,))
            conn.execute("DELETE FROM file_meta WHERE path = ?", (path_str,))
            removed += 1
        conn.commit()
    finally:
        conn.close()
    return removed
