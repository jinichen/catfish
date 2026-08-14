"""「重建索引」必须顺带清掉不在范围内的旧数据 (8/14)。

# 病历

Companion「搜索范围」卡的文案写着:

    「改了 exclude / file_types 或**想清掉旧数据**, 点右边"重建索引"」

而"重建索引"按钮走的是 `catfish_search.cli index`, 它**从来不调 cleanup**。
`cleanup_missing` 只有 `clean` 子命令能触发, 前端又只在**删目录**时调它
(LocalSearchScopeCard.onRemove)。文案承诺的事按钮没做。

实测后果: 鸿波库里 10,517 条有 6,792 条在声明范围之外 —— 整个
~/person_task/catfish 源码仓 + ~/Downloads 里的合同发票。按入库时间看全是
7/27 那天进的, 也就是 BL-SEARCH-STALE-SCOPE 修好**之前**; 他早就在面板上删过
那些目录了, 但删的时候那次修复还没上线, 之后再没有任何路径会清掉它们。

索引范围是员工的知情同意边界。UI 说"只索引这 3 个目录", 就不该有第 4 个目录的
内容留在搜索结果和 style_fingerprint 语料里。

# ⚠ 这个文件真正要防的是修复本身的副作用

`cmd_index` 的 `--only` 分支会把 `cfg.include` 换成**单个根**。如果收尾的
cleanup 拿这份改窄的 cfg 去跑, 它会把"不在这一个根下面"的条目全判成越界删掉
—— 员工加个新目录, 其它目录的索引全没了, 而且悄无声息。

所以下面第二条比第一条重要。
"""
from __future__ import annotations

import sqlite3
import sys
import types
from pathlib import Path

import pytest

from catfish_search import cli as climod
from catfish_search import indexer
from catfish_search.config import SearchConfig


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """两个真实目录 + 一个指向临时库的 indexer。

    返回 (in_scope_dir, out_of_scope_dir, db_path)。
    """
    in_scope = tmp_path / "keep"
    other = tmp_path / "keep2"
    gone = tmp_path / "dropped"
    for d in (in_scope, other, gone):
        d.mkdir()
        (d / "a.md").write_text("一些中文正文内容用来占位", encoding="utf-8")

    db = tmp_path / "search.db"
    monkeypatch.setattr(indexer, "DB_FILE", db)
    return in_scope, other, gone, db


def _seed(db: Path, paths: list[Path]) -> None:
    """直接往库里塞条目 —— 模拟"7/27 之前留下的历史数据"。"""
    indexer.open_db().close()  # 建表
    conn = sqlite3.connect(db)
    for p in paths:
        conn.execute(
            "INSERT INTO documents(path,title,content,file_type) VALUES(?,?,?,?)",
            (str(p), p.name, "一些中文正文内容用来占位", ".md"),
        )
        conn.execute("INSERT INTO file_meta(path,mtime) VALUES(?,?)", (str(p), 1.0))
    conn.commit()
    conn.close()


def _paths_in_db(db: Path) -> set[str]:
    conn = sqlite3.connect(db)
    try:
        return {r[0] for r in conn.execute("SELECT path FROM file_meta")}
    finally:
        conn.close()


def _run_index(monkeypatch, cfg: SearchConfig, only: str | None = None) -> int:
    monkeypatch.setattr(climod, "load_config", lambda: cfg)
    args = types.SimpleNamespace(quiet=True, only=only)
    return climod.cmd_index(args)


def test_全量重建会清掉范围外的旧数据(sandbox, monkeypatch, capsys):
    """★★ 就是鸿波那 6,792 条的场景。"""
    in_scope, other, gone, db = sandbox
    _seed(db, [in_scope / "a.md", gone / "a.md"])
    assert str(gone / "a.md") in _paths_in_db(db)

    cfg = SearchConfig(include=[in_scope, other], file_types={".md"})
    assert _run_index(monkeypatch, cfg) == 0

    left = _paths_in_db(db)
    assert str(gone / "a.md") not in left, (
        f"范围外的条目还在 —— 「重建索引」还是没兑现'清掉旧数据'的文案。剩: {left}"
    )
    assert str(in_scope / "a.md") in left, "把该留的也清了"
    assert "顺带清掉" in capsys.readouterr().out


def test_only_模式绝不能清掉其它根的数据(sandbox, monkeypatch):
    """★★★ 防修复本身的副作用 —— 这条比上面那条重要。

    `--only` 会把 cfg.include 改窄成单个根。拿那份去 cleanup, 其它根的条目全被
    判成"不在范围内"删掉。触发路径极其日常: 员工在面板上**加一个目录**,
    Companion 就会调 `index --only <新目录>` (LocalSearchScopeCard.onAdd)。

    也就是说: 加一个目录 → 其它目录的索引全没。而且没有任何报错。
    """
    in_scope, other, gone, db = sandbox
    _seed(db, [in_scope / "a.md", other / "a.md", gone / "a.md"])

    cfg = SearchConfig(include=[in_scope, other], file_types={".md"})
    assert _run_index(monkeypatch, cfg, only=str(in_scope)) == 0

    left = _paths_in_db(db)
    assert str(other / "a.md") in left, (
        "--only 模式把**其它根**的数据清掉了 —— cleanup 拿到了被改窄的 cfg。"
        f"剩: {left}"
    )
    # 真正越界的那条仍然该走 (它在任何一个 include 根下面都不是)
    assert str(gone / "a.md") not in left, f"越界条目没清掉: {left}"


def test_include_为空时不清空整个库(sandbox, monkeypatch):
    """cleanup_missing 自己有这条保护, 这里确认走 cmd_index 时也在。

    配置读坏 / 还没配的时候按"什么都不在范围内"处理会把整库清空。
    cmd_index 在 include 为空时直接返回 1, 根本走不到 cleanup —— 钉住这个前提,
    哪天有人调整了提前返回的位置, 这条会红。
    """
    in_scope, other, gone, db = sandbox
    _seed(db, [in_scope / "a.md"])
    cfg = SearchConfig(include=[], file_types={".md"})
    assert _run_index(monkeypatch, cfg) == 1
    assert str(in_scope / "a.md") in _paths_in_db(db), "空配置把库清了"
