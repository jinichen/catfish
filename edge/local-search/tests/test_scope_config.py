"""BL-SEARCH-SCOPE-MIGRATION + BL-SEARCH-EXCLUDE-DIR-FIX + BL-SEARCH-TCC-SILENT-SKIP
(7/27 鸿波实盘挖出来的一串).

# 这批测试守的是什么

鸿波 7/27 报"文书风格完全不抽了"，往下挖出四个独立 bug，其中三个在这一层：

1. **exclude 从来没生效** —— `fnmatch('/x/node_modules/a.js', '**/node_modules')`
   是 False（fnmatch 全串匹配），而 rglob 又没法剪枝。后果：他索引库 60332 条里
   33206 条 .py + 10685 条 .h，全是 venv / node_modules 里的东西。

2. **整个目录读不了时静默跳过** —— 老代码 `except OSError: logger.debug(...)`
   吞掉一整棵目录，员工只看到"已索引 6 万条"，完全不知道最重要的目录一条没进。

3. **新增系统目录对老员工无效** —— DEFAULT_CONFIG 只在 yaml 不存在时写一次。
   鸿波的 ~/.catfish/output 塞满周报/汇报/对标报告，索引里一条没有。

对应的立场：索引范围**只有一个真相源**（search-scope.yaml，面板可见可改），
新增目录靠迁移追加进员工的实体文件，代码里不藏隐藏目录。
"""
from __future__ import annotations

import importlib
import os
import tempfile
from pathlib import Path

import pytest

from catfish_search import config as scope_config
from catfish_search import indexer
from catfish_search.config import SearchConfig
from catfish_search.indexer import _iter_files, _should_skip
from catfish_search.watcher import _bootstrap_missing_roots

# 鸿波 7/27 的真实 yaml 形态：有注释、有他自己加的 ~/person_task、没有 output
LEGACY_YAML = """# 鲶鱼本地文件搜索 · 索引范围配置
#
# 只索引员工明确同意的目录，不会扫全盘。

include:
  # 个人常用目录
  - ~/Documents
  - ~/Desktop

  # BL-FILE-SESSION-INDEX-V1 Phase 4 (5/30): 鲶鱼对话上传过的附件
  - ~/.catfish/uploads
  - ~/person_task

  # 按需打开下面这些（取消前面的 #）：
  # - ~/work
  # - ~/code

exclude:
  - "**/node_modules"

max_file_size_mb: 20

file_types:
  - .md
"""

_HOME_DIRS = (
    ".catfish/uploads",
    ".catfish/output",
    "Documents",
    "Desktop",
    "Downloads",
    "person_task",
)


@pytest.fixture
def scope(monkeypatch):
    """临时 HOME + reload config 模块，返回 (home, config 模块)。

    CATFISH_HOME / CONFIG_FILE 是 import 期就算好的常量，改 HOME 后必须 reload。
    """
    with tempfile.TemporaryDirectory() as home:
        monkeypatch.setenv("HOME", home)
        importlib.reload(scope_config)
        for d in _HOME_DIRS:
            (Path(home) / d).mkdir(parents=True, exist_ok=True)
        yield Path(home), scope_config
    # 还原真实 HOME 下的模块状态，免得污染同进程后续测试
    importlib.reload(scope_config)


# ── 迁移 ────────────────────────────────────────────────────


def test_migration_adds_output_to_legacy_yaml(scope):
    """老员工的 yaml 里没有 output → 迁移给他补上。

    这是 7/27 的核心症状：改 DEFAULT_CONFIG 对已经有 yaml 的人毫无作用。
    """
    home, cfgmod = scope
    cfgmod.CONFIG_FILE.write_text(LEGACY_YAML, encoding="utf-8")

    cfg = cfgmod.load_config()
    assert any(str(p) == str(home / ".catfish" / "output") for p in cfg.include)


def test_migration_preserves_comments_and_other_sections(scope):
    """行级插入，不是 safe_dump 重写 —— 员工的中文注释和自定义值必须原样保住。"""
    _, cfgmod = scope
    cfgmod.CONFIG_FILE.write_text(LEGACY_YAML, encoding="utf-8")
    cfgmod.load_config()

    txt = cfgmod.CONFIG_FILE.read_text(encoding="utf-8")
    assert "# 个人常用目录" in txt
    assert "# 按需打开下面这些（取消前面的 #）：" in txt
    assert "# - ~/work" in txt              # 注释掉的候选项没被当成条目
    assert "**/node_modules" in txt          # exclude 段没动
    assert "max_file_size_mb: 20" in txt     # 员工改过的值没被打回默认
    assert "- ~/person_task" in txt          # 员工自己加的目录还在


def test_migration_inserts_after_last_real_item(scope):
    """插在最后一个真实条目后面，不是整段末尾。

    段末尾是"# 按需打开下面这些"那堆注释掉的候选项，插它们后面读起来
    像是那组的一员，很怪。
    """
    _, cfgmod = scope
    cfgmod.CONFIG_FILE.write_text(LEGACY_YAML, encoding="utf-8")
    cfgmod.load_config()

    lines = cfgmod.CONFIG_FILE.read_text(encoding="utf-8").splitlines()
    out_at = next(i for i, ln in enumerate(lines) if ln.strip() == "- ~/.catfish/output")
    hint_at = next(i for i, ln in enumerate(lines) if "按需打开下面这些" in ln)
    assert out_at < hint_at


def test_migration_is_idempotent(scope):
    """跑多少次都只有一条，文件字节不变。"""
    _, cfgmod = scope
    cfgmod.CONFIG_FILE.write_text(LEGACY_YAML, encoding="utf-8")
    cfgmod.load_config()
    first = cfgmod.CONFIG_FILE.read_text(encoding="utf-8")

    for _ in range(3):
        cfgmod.load_config()
    again = cfgmod.CONFIG_FILE.read_text(encoding="utf-8")

    assert first == again
    assert again.count("~/.catfish/output") == 1


def test_migration_respects_employee_deletion(scope):
    """员工在面板上删掉这条 → 不能被下次 load_config 塞回去。

    marker 记在 _applied_migrations 里就是为了这个：迁移是一次性的，
    不是每次启动都强推。
    """
    _, cfgmod = scope
    cfgmod.CONFIG_FILE.write_text(LEGACY_YAML, encoding="utf-8")
    cfgmod.load_config()

    txt = cfgmod.CONFIG_FILE.read_text(encoding="utf-8")
    cfgmod.CONFIG_FILE.write_text(
        "\n".join(ln for ln in txt.splitlines() if "~/.catfish/output" not in ln) + "\n",
        encoding="utf-8",
    )

    cfg = cfgmod.load_config()
    assert not any(str(p).endswith("/.catfish/output") for p in cfg.include)


def test_migration_skips_when_already_present(scope):
    """员工自己手写过 → 不插重复的，但 marker 照样记上。"""
    _, cfgmod = scope
    cfgmod.CONFIG_FILE.write_text(
        LEGACY_YAML.replace(
            "  - ~/person_task", "  - ~/person_task\n  - ~/.catfish/output"
        ),
        encoding="utf-8",
    )
    cfgmod.load_config()

    txt = cfgmod.CONFIG_FILE.read_text(encoding="utf-8")
    assert txt.count("~/.catfish/output") == 1
    assert "catfish-output-2026-07" in txt


def test_migration_not_marked_done_when_no_include_section(scope):
    """认不出配置形态时不硬塞，也不能标成"做完了"把这条永久吞掉。"""
    _, cfgmod = scope
    cfgmod.CONFIG_FILE.write_text("exclude: []\n", encoding="utf-8")
    cfgmod.load_config()

    assert "_applied_migrations" not in cfgmod.CONFIG_FILE.read_text(encoding="utf-8")


def test_fresh_install_gets_output_from_default_config(scope):
    """全新员工走 DEFAULT_CONFIG，不该因为迁移又插一条重复的。"""
    home, cfgmod = scope
    cfg = cfgmod.load_config()

    assert any(str(p) == str(home / ".catfish" / "output") for p in cfg.include)
    assert cfgmod.CONFIG_FILE.read_text(encoding="utf-8").count("~/.catfish/output") == 1


def test_load_config_dedupes_include(scope):
    """员工写了两条指向同一处的路径（~/x 和绝对路径）→ 只扫一遍。"""
    home, cfgmod = scope
    cfgmod.CONFIG_FILE.write_text(
        f"include:\n  - ~/Documents\n  - {home}/Documents\n"
        "exclude: []\nfile_types: [.md]\n",
        encoding="utf-8",
    )
    cfg = cfgmod.load_config()

    assert sum(1 for p in cfg.include if p == home / "Documents") == 1


# ── exclude 真的挡住目录里的文件 ────────────────────────────


def test_should_skip_matches_files_inside_excluded_dir():
    """BL-SEARCH-EXCLUDE-DIR-FIX: fnmatch 是全串匹配，只写 `**/node_modules`
    命中不了里面的文件。这条红过就是 7/27 那 33206 条 .py 的成因。
    """
    ex = ["**/node_modules", "**/venv", "~/Library"]
    assert _should_skip(Path("/x/p/node_modules"), ex) is True
    assert _should_skip(Path("/x/p/node_modules/lib/a.js"), ex) is True
    assert _should_skip(Path("/x/p/venv/lib/py3.12/site-packages/mod.py"), ex) is True
    assert _should_skip(Path("/x/p/src/main.py"), ex) is False


def test_iter_files_prunes_excluded_dirs():
    """os.walk 就地剪枝：excluded 目录整棵不进，不是"走完再逐个丢弃"。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "docs").mkdir()
        (root / "docs" / "报告.md").write_text("正文", encoding="utf-8")
        (root / "node_modules" / "a" / "b").mkdir(parents=True)
        (root / "node_modules" / "a" / "b" / "junk.js").write_text("x")
        (root / "venv" / "lib").mkdir(parents=True)
        (root / "venv" / "lib" / "pkg.py").write_text("x")

        cfg = SearchConfig(
            include=[root],
            exclude=["**/node_modules", "**/venv"],
            max_file_size_mb=10,
            file_types={".md", ".js", ".py"},
        )
        got = sorted(str(p.relative_to(root)) for p in _iter_files(root, cfg))

    assert got == ["docs/报告.md"]


# ── 读不了的目录要喊出来 ────────────────────────────────────


def test_run_index_reports_unreadable_roots(monkeypatch):
    """BL-SEARCH-TCC-SILENT-SKIP: 整棵目录读不了时必须出现在 stats.unreadable，
    不能只留一条 debug 日志。

    鸿波看到的是"已索引 60332 条"，实际最重要的几个目录一条没进。
    """
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        good = root / "good"
        good.mkdir()
        (good / "报告.md").write_text("正文内容", encoding="utf-8")
        denied = root / "denied"
        denied.mkdir()
        (denied / "x.md").write_text("secret", encoding="utf-8")
        empty = root / "empty"
        empty.mkdir()
        os.chmod(denied, 0o000)

        monkeypatch.setattr(indexer, "DB_FILE", root / "t.db")
        cfg = SearchConfig(
            include=[good, denied, empty],
            exclude=[],
            max_file_size_mb=10,
            file_types={".md"},
        )
        try:
            stats = indexer.run_index(cfg)
        finally:
            os.chmod(denied, 0o755)

        assert stats["per_root"][str(good)] == 1
        assert stats["per_root"][str(denied)] == 0
        assert stats["per_root"][str(empty)] == 0
        # 权限被拒的进 unreadable；单纯的空目录不该误报
        assert [r for r, _ in stats["unreadable"]] == [str(denied)]


# ── 首次索引 (bootstrap) ────────────────────────────────────


def test_roots_needing_full_index_tracks_per_root(monkeypatch):
    """BL-SEARCH-BOOTSTRAP-LEDGER: 按根记账，不是看"库空不空"。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        a, b = root / "a", root / "b"
        a.mkdir()
        b.mkdir()
        (a / "x.md").write_text("正文内容", encoding="utf-8")
        (b / "y.md").write_text("正文内容", encoding="utf-8")
        monkeypatch.setattr(indexer, "DB_FILE", root / "t.db")

        cfg_a = SearchConfig(include=[a], exclude=[], max_file_size_mb=10,
                             file_types={".md"})
        assert indexer.roots_needing_full_index(cfg_a) == [a]
        indexer.run_index(cfg_a)
        assert indexer.roots_needing_full_index(cfg_a) == []

        # 新加一个根：库是**满的**，但 b 没扫过 —— 空库判据在这里会漏
        cfg_ab = SearchConfig(include=[a, b], exclude=[], max_file_size_mb=10,
                              file_types={".md"})
        assert indexer.roots_needing_full_index(cfg_ab) == [b]


def test_roots_needing_full_index_survives_db_recreation(monkeypatch):
    """鸿波实盘打脸的那个场景：删了 search.db，老 watcher 立刻把库重建并写进
    1 条 —— 空库判据当场失效，按根记账不受影响。
    """
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        docs = root / "docs"
        docs.mkdir()
        (docs / "存量.md").write_text("从没被改动过的存量正文", encoding="utf-8")
        monkeypatch.setattr(indexer, "DB_FILE", root / "t.db")
        cfg = SearchConfig(include=[docs], exclude=[], max_file_size_mb=10,
                           file_types={".md"})
        indexer.run_index(cfg)

        # 模拟员工 rm search.db
        (root / "t.db").unlink()
        # 模拟老 watcher 随手一个增量写入把库重建了
        (docs / "刚改的.md").write_text("刚被编辑器保存的正文", encoding="utf-8")
        indexer.index_path(docs / "刚改的.md")

        conn = indexer.open_db()
        n = conn.execute("SELECT COUNT(*) FROM file_meta").fetchone()[0]
        conn.close()

        assert n == 1, "库确实非空了（空库判据会在这里跳过 bootstrap）"
        assert indexer.roots_needing_full_index(cfg) == [docs]


def test_roots_needing_full_index_forgets_removed_roots(monkeypatch):
    """员工删掉一个目录又加回来 → 得重新全量，不能沿用旧记账。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        a, b = root / "a", root / "b"
        a.mkdir()
        b.mkdir()
        (a / "x.md").write_text("正文内容", encoding="utf-8")
        monkeypatch.setattr(indexer, "DB_FILE", root / "t.db")
        def mk(inc):
            return SearchConfig(include=inc, exclude=[], max_file_size_mb=10,
                                file_types={".md"})

        indexer.run_index(mk([a]))
        assert indexer.roots_needing_full_index(mk([a])) == []
        indexer.roots_needing_full_index(mk([b]))          # a 被移出 include → 忘掉
        assert indexer.roots_needing_full_index(mk([a])) == [a]


def test_bootstrap_indexes_preexisting_files(monkeypatch):
    """watcher 只吃变化事件，存量文件得靠 bootstrap。

    鸿波实盘：yaml 里配了 ~/Documents 等四个目录，索引库里各 0 条 ——
    因为那些文件从没被改动过，watcher 收不到任何事件，而 Companion
    从来只 spawn watch、不 spawn index。
    """
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        docs = root / "docs"
        docs.mkdir()
        for i in range(3):
            (docs / f"报告{i}.md").write_text(f"第{i}份存量公文正文", encoding="utf-8")

        monkeypatch.setattr(indexer, "DB_FILE", root / "t.db")
        cfg = SearchConfig(include=[docs], exclude=[], max_file_size_mb=10,
                           file_types={".md"})

        _bootstrap_missing_roots(cfg)

        conn = indexer.open_db()
        n = conn.execute("SELECT COUNT(*) FROM file_meta").fetchone()[0]
        conn.close()

    assert n == 3


def test_bootstrap_only_touches_unindexed_roots(monkeypatch):
    """扫过的根不重扫 —— 每开一次 Companion 都全量 stat 判重太慢且白跑。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        a, b = root / "a", root / "b"
        a.mkdir()
        b.mkdir()
        (a / "x.md").write_text("正文内容", encoding="utf-8")
        (b / "y.md").write_text("正文内容", encoding="utf-8")
        monkeypatch.setattr(indexer, "DB_FILE", root / "t.db")

        indexer.run_index(SearchConfig(include=[a], exclude=[], max_file_size_mb=10,
                                       file_types={".md"}))

        seen = []
        real = indexer.run_index

        def spy(c):
            seen.append(list(c.include))
            return real(c)

        monkeypatch.setattr("catfish_search.watcher.run_index", spy)
        _bootstrap_missing_roots(
            SearchConfig(include=[a, b], exclude=[], max_file_size_mb=10,
                         file_types={".md"})
        )

    assert seen == [[b]]


def test_bootstrap_retries_root_that_hit_permission_error(monkeypatch):
    """踩权限错的根不记账 —— 员工去系统设置授权后，下次启动自动补建，
    不用他记得回来手点一次。
    """
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        denied = root / "denied"
        denied.mkdir()
        (denied / "x.md").write_text("secret", encoding="utf-8")
        os.chmod(denied, 0o000)
        monkeypatch.setattr(indexer, "DB_FILE", root / "t.db")
        cfg = SearchConfig(include=[denied], exclude=[], max_file_size_mb=10,
                           file_types={".md"})
        try:
            indexer.run_index(cfg)
            still = indexer.roots_needing_full_index(cfg)
        finally:
            os.chmod(denied, 0o755)

    assert still == [denied]


def test_cleanup_purges_now_excluded_entries(monkeypatch):
    """exclude 修好之前索引库里堆的 node_modules 条目，文件还在磁盘上，
    光靠 exists() 永远清不掉 —— cleanup 要按当前 exclude 重判一遍。
    """
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "docs").mkdir()
        keep = root / "docs" / "报告.md"
        keep.write_text("正文内容", encoding="utf-8")
        (root / "node_modules").mkdir()
        (root / "node_modules" / "junk.md").write_text("垃圾内容", encoding="utf-8")

        monkeypatch.setattr(indexer, "DB_FILE", root / "t.db")
        # 先用空 exclude 建索引，模拟"修好之前"的库
        lax = SearchConfig(
            include=[root], exclude=[], max_file_size_mb=10, file_types={".md"}
        )
        indexer.run_index(lax)
        conn = indexer.open_db()
        assert conn.execute("SELECT count(*) FROM file_meta").fetchone()[0] == 2
        conn.close()

        # 现在带上 exclude 跑 cleanup
        strict = SearchConfig(
            include=[root],
            exclude=["**/node_modules"],
            max_file_size_mb=10,
            file_types={".md"},
        )
        removed = indexer.cleanup_missing(strict)

        conn = indexer.open_db()
        left = [r[0] for r in conn.execute("SELECT path FROM file_meta")]
        conn.close()

    assert removed == 1
    assert left == [str(keep)]


# ── 并发 (BL-SEARCH-DB-LOCKED) ──────────────────────────────


def test_open_db_uses_wal_and_busy_timeout(monkeypatch):
    """老代码 sqlite3.connect() 不带 timeout，busy_timeout 默认 0，一撞锁立刻抛。

    增量写只占几毫秒时没暴露；加了 bootstrap 全量索引（持续写几分钟）之后，
    任何并发访问当场 `sqlite3.OperationalError: database is locked`。
    """
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setattr(indexer, "DB_FILE", Path(td) / "t.db")
        conn = indexer.open_db()
        try:
            assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
            assert conn.execute("PRAGMA busy_timeout").fetchone()[0] >= 1000
        finally:
            conn.close()


def test_open_db_survives_concurrent_write(monkeypatch):
    """一个连接开着写事务时，另一个 open_db 不该当场抛 locked。

    老代码每次 open_db 都无条件 executescript(SCHEMA)，等于每次都抢一下写锁 ——
    鸿波那个崩栈就停在 `conn.executescript(SCHEMA)` 这行。
    """
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setattr(indexer, "DB_FILE", Path(td) / "t.db")
        writer = indexer.open_db()
        try:
            writer.execute("BEGIN IMMEDIATE")
            writer.execute(
                "INSERT INTO indexed_roots(root, finished_at, file_count) "
                "VALUES('/x', 1.0, 1)"
            )
            reader = indexer.open_db()  # 老代码在这里抛 locked
            try:
                reader.execute("SELECT COUNT(*) FROM file_meta").fetchone()
            finally:
                reader.close()
        finally:
            writer.rollback()
            writer.close()


def test_full_index_lock_is_exclusive(monkeypatch):
    """同一时刻只让一个进程做全量索引。

    鸿波日志里 20:25:13 和 20:25:42 两个 watcher 各自宣布"先补一次"，
    一起扫同一批目录 —— 白干还互相抢写锁。
    """
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setattr(indexer, "DB_FILE", Path(td) / "t.db")
        with indexer.full_index_lock() as first:
            assert first is True
            with indexer.full_index_lock() as second:
                assert second is False, "第二个应该拿不到锁"
        # 释放后能重新拿到
        with indexer.full_index_lock() as again:
            assert again is True


def test_bootstrap_skips_when_another_process_holds_lock(monkeypatch):
    """抢不到锁就跳过 —— 干活的那个做完了活也就干了，这里等没意义。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        docs = root / "docs"
        docs.mkdir()
        (docs / "存量.md").write_text("存量正文内容", encoding="utf-8")
        monkeypatch.setattr(indexer, "DB_FILE", root / "t.db")
        cfg = SearchConfig(
            include=[docs], exclude=[], max_file_size_mb=10, file_types={".md"}
        )

        called = []

        def fake_run_index(c):
            called.append(c)
            return {"scanned": 0, "indexed": 0, "skipped": 0, "duration_sec": 0.0,
                    "per_root": {}, "unreadable": []}

        monkeypatch.setattr("catfish_search.watcher.run_index", fake_run_index)
        with indexer.full_index_lock():  # 模拟另一个进程正拿着
            _bootstrap_missing_roots(cfg)

        assert called == []
        # 锁释放后照样该补
        _bootstrap_missing_roots(cfg)
        assert len(called) == 1
