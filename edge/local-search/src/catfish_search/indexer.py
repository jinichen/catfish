"""扫描文件并写入 SQLite FTS5 全文索引。

只记录**路径、标题、内容、元数据**。不记录任何对话、用户行为。
索引库完全在员工 Mac 本地（~/.catfish/search.db），不会上传。
"""
from __future__ import annotations

import contextlib
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

-- BL-SEARCH-BOOTSTRAP-LEDGER (7/27 二次修): 记「哪些 include 根做过全量索引」。
--
-- 第一版用 "索引库里一条都没有" 当判据，鸿波实盘当场打脸：他 rm 掉 search.db 后
-- **老 watcher 还在跑**，随手一个文件变动就 index_path → open_db() 把库重建了并
-- 写进 1 条。等新 watcher 起来，库已经"非空"，bootstrap 直接跳过。
--
-- 而且"库空不空"本来也回答不了真正的问题：员工新加一个目录时，库是满的，
-- 但那个新根一个文件都没全量扫过。按根记账两个场景一起解决。
CREATE TABLE IF NOT EXISTS indexed_roots (
    root        TEXT PRIMARY KEY,
    finished_at REAL,
    file_count  INTEGER
);
"""


# 撞锁时最多等这么久再放弃（秒）。
#
# BL-SEARCH-DB-LOCKED (7/27 鸿波实盘): 老代码 sqlite3.connect() 不带 timeout，
# 而 sqlite 的 busy_timeout 默认是 **0** —— 一撞锁立刻抛 OperationalError，
# 不重试。以前 watcher 只做几毫秒的增量写，撞上的概率低到没暴露；加了 bootstrap
# 全量索引（一跑几分钟持续写）之后，任何并发访问当场炸：
#
#   sqlite3.OperationalError: database is locked
#     File ".../indexer.py", line 62, in open_db
#       conn.executescript(SCHEMA)
#
# 谁会并发？Companion 的 autostart 每次启动 pkill+respawn watcher，员工自己
# 又可能在终端前台跑一个；再加上 tool-bridge 的 style_fingerprint 和
# Companion 的 local_search_stats 都会来读这个库。
DB_BUSY_TIMEOUT_SEC = 30.0

# SCHEMA 里建的表，用来判断要不要跑 DDL
_EXPECTED_TABLES = ("documents", "file_meta", "indexed_roots")


def open_db() -> sqlite3.Connection:
    """打开（或初始化）索引库。"""
    DB_FILE.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_FILE, timeout=DB_BUSY_TIMEOUT_SEC)

    # WAL: 让读不被写阻塞。全量索引期间 Dashboard 查统计 / style_fingerprint
    # 抽语料都还能正常读，不会卡住或报 locked。（默认 rollback journal 下，
    # 写事务会把所有读者挡在门外。）
    # 库在只读介质 / 网络盘上时 WAL 可能设不了，设不了就算了，busy_timeout 还在。
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.DatabaseError:
        pass

    # 只在表确实缺的时候跑 DDL。executescript 会先隐式 COMMIT 再执行，
    # 每次开库都跑等于每次都抢一下写锁 —— 上面那个 locked 崩栈就停在这行。
    have = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if not all(t in have for t in _EXPECTED_TABLES):
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


def _index_one(conn: sqlite3.Connection, path: Path) -> str:
    """索引单个文件。返回 'indexed' / 'unchanged' / 'no_text'。

    BL-SEARCH-STATS-MISLEADING (7/27 鸿波实盘): 老签名返 bool，两种完全不同的
    False 混成一个 —— "内容没变不用重建"（正常，占绝大多数）和"抽不出文本"
    （异常）。上层统一记进 stats["skipped"]，于是一次健康的全量重跑打出
    "扫 6830 / 新索引 4 / 跳过 6826"，看着像 99.9% 都出错了。
    """
    if not _needs_reindex(conn, path):
        return "unchanged"

    text = extract_text(path)
    if not text or not text.strip():
        return "no_text"

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
    return "indexed"


def _count_under(conn: sqlite3.Connection, root: Path) -> int:
    """索引库里这个根底下现有多少条。

    BL-SEARCH-STATS-MISLEADING (7/27): per_root 原来数的是"本次新增几条"。
    库已经建好之后再跑一次全量，所有文件都走 _needs_reindex 判定"没变"，
    新增 0 —— 于是六个健康目录全打 ⚠️ 0 条，还触发"一个文件都没索引到"的告警。
    员工看到会以为索引坏了。改成数索引库里实际有多少。
    """
    prefix = str(root).rstrip("/") + "/"
    # LIKE 的通配符转义: 路径里可能有 % 或 _（_ 在中文路径里很常见）
    esc = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return conn.execute(
        "SELECT COUNT(*) FROM file_meta WHERE path LIKE ? ESCAPE '\\'", (esc + "%",)
    ).fetchone()[0]


def run_index(cfg: SearchConfig, on_progress=None) -> dict:
    """完整扫描 + 索引一次。返回统计。

    stats 各项:
      scanned / indexed / unchanged / failed  —— 见 BL-SEARCH-STATS-MISLEADING
      per_root:    每个 include 根**索引库里现有**多少条（0 = 这个根白配了）
      unreadable:  [(root, 原因)]，遍历时踩 PermissionError 的根

    BL-SEARCH-TCC-SILENT-SKIP (7/27): per_root / unreadable 是给 CLI / Dashboard
    明着报的。macOS 上 ~/Documents、~/Desktop、~/Downloads 要「文件与文件夹」
    授权，没授权就是整棵目录 0 条，以前只有一条 debug 日志，员工根本发现不了。
    """
    conn = open_db()
    stats: dict = {
        "scanned": 0,
        "indexed": 0,      # 真写进库了（新文件 / 内容变了）
        "unchanged": 0,    # mtime 没变，不用重建 —— 正常，重跑时占绝大多数
        "failed": 0,       # 抽不出文本 / 抛异常
        "roots": len(cfg.include),
        "per_root": {},
        "unreadable": [],
    }
    start = time.time()

    try:
        for root in cfg.include:
            logger.info("scanning %s", root)
            walk_errors: list[OSError] = []
            for path in _iter_files(root, cfg, errors=walk_errors):
                stats["scanned"] += 1
                try:
                    outcome = _index_one(conn, path)
                except Exception as e:
                    logger.debug("index fail %s: %s", path, e)
                    outcome = "failed"
                if outcome == "indexed":
                    stats["indexed"] += 1
                elif outcome == "unchanged":
                    stats["unchanged"] += 1
                else:  # no_text / failed
                    stats["failed"] += 1

                if on_progress and stats["scanned"] % 50 == 0:
                    on_progress(stats)

                if stats["scanned"] % 200 == 0:
                    conn.commit()

            conn.commit()  # 先落盘，_count_under 才数得到这一轮写的
            stats["per_root"][str(root)] = _count_under(conn, root)
            if walk_errors:
                # 只记第一条原因就够了 —— 权限问题整棵目录同一个错
                first = walk_errors[0]
                stats["unreadable"].append((str(root), f"{type(first).__name__}: {first}"))
            else:
                # BL-SEARCH-BOOTSTRAP-LEDGER: 走完整棵且没踩错才算"全量过了"。
                # 踩了权限错就不记 —— 员工去系统设置授权后，下次启动会自动补建，
                # 不用他记得回来手点一次。
                conn.execute(
                    "INSERT INTO indexed_roots(root, finished_at, file_count) "
                    "VALUES(?, ?, ?) ON CONFLICT(root) DO UPDATE SET "
                    "finished_at = excluded.finished_at, file_count = excluded.file_count",
                    (str(root), time.time(), stats["per_root"][str(root)]),
                )
        conn.commit()
    finally:
        conn.close()

    stats["duration_sec"] = round(time.time() - start, 1)
    return stats


@contextlib.contextmanager
def full_index_lock():
    """跨进程互斥锁，保证同一时刻只有一个进程在做全量索引。

    BL-SEARCH-DB-LOCKED (7/27 鸿波实盘): 他日志里 20:25:13 和 20:25:42 两个
    watcher 各自宣布"这些目录还没做过全量索引，先补一次"，然后一起扫同一批
    目录 —— Companion autostart 起了一个，他自己在终端又前台跑了一个。
    重复劳动之外还互相抢写锁。

    yield True = 拿到锁；yield False = 别人正在跑，调用方应该跳过（不是等，
    等几分钟没意义，那个进程做完了活也就干了）。

    用 fcntl.flock：进程崩了/被 kill 内核自动释放，不会留下需要人工清的死锁文件。
    Windows 没有 flock —— 那边直接放行（Companion 目前只发 macOS，
    daemon_windows.py 是给单进程 daemon 场景的，不存在这个并发）。
    """
    try:
        import fcntl  # noqa: PLC0415
    except ImportError:
        yield True
        return

    DB_FILE.parent.mkdir(exist_ok=True)
    lock_path = DB_FILE.with_suffix(".index.lock")
    fh = open(lock_path, "w")  # noqa: SIM115
    try:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
    finally:
        fh.close()


def roots_needing_full_index(cfg: SearchConfig) -> list[Path]:
    """cfg.include 里还没做过全量索引的根。

    BL-SEARCH-NO-BOOTSTRAP (7/27 鸿波实盘): Companion 只 spawn
    `catfish_search.cli watch`，而 run_watch 只处理**文件变化事件**，全文没有
    一处调 run_index —— 也就是说 local_search 从装上那天起就没做过全量索引。

    鸿波库里那 60332 条全是 watcher 运行期间碰巧被改动过的文件：~/person_task
    是活跃开发目录（git checkout / pip install -e / npm ci 天天动文件）贡献了
    全部，而 ~/Documents、~/Desktop、~/Downloads、~/.catfish/uploads 里的文件
    没人动，一条都没进。他 yaml 里明明配了这四个。

    BL-SEARCH-BOOTSTRAP-LEDGER (7/27 二次修): 判据从"索引库空不空"换成
    "这个根做过全量没有"（indexed_roots 表）。原因见 SCHEMA 里的注释 ——
    空库判据被"老 watcher 把删掉的库重建了"当场打脸，而且也覆盖不了
    "员工新加一个目录"这个场景（库是满的，新根却一条没扫）。

    (autostart.rs 老注释写着"启动慢 5-15s (初始 reconcile)" —— 代码里没有
     reconcile，那句是假的，7/27 一并改掉。)
    """
    conn = open_db()
    try:
        done = {row[0] for row in conn.execute("SELECT root FROM indexed_roots")}
        # 顺手忘掉已经不在 include 里的根。不然员工"删掉目录 → 又加回来"时，
        # 记账还在，会被当成扫过的而跳过 bootstrap。
        stale = done - {str(p) for p in cfg.include}
        if stale:
            conn.executemany(
                "DELETE FROM indexed_roots WHERE root = ?", [(r,) for r in stale]
            )
            conn.commit()
            done -= stale
    except sqlite3.Error:
        done = set()  # 库损坏 / 老 schema：当成全都没做过，重建一次总比不建强
    finally:
        conn.close()
    return [p for p in cfg.include if str(p) not in done]


def index_path(path: Path) -> bool:
    """单文件增量索引（给 watcher 用）。成功入库 True，内容没变/抽不出文本 False。"""
    conn = open_db()
    try:
        ok = _index_one(conn, path) == "indexed"
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
