"""catfish_search_files 单测 —— 重点钉"会静默给错答案"的那几处。

# 病历 (8/14)

鸿波让小鲶找一份"AI 场景梳理"文件, 它两轮都答「本地文件搜索环境无法启动」。
而 Companion 仪表盘同时显示索引好好的 (10,518 个文件, 2 分钟前刚入库), 那份
文件也确实在库里 (~/.catfish/uploads/1785827576-业务场景梳理清单.xlsx)。

真因: **LLM 手上没有任何能查这个索引的工具**。
  · catfish-local-search 没注册成 MCP server (config.yaml 里只有 catfish-tools)
  · catfish-search 二进制没装
  · skills.rs:946 禁止员工自加 catfish-* 的 MCP, 想补也补不了
模型只有 search_docs / search_attachments 够不着, 于是自己编了个说辞。

# 这个文件钉什么

不是"能搜到东西"这么松。三条判据都是**静默失败**的形状 —— 出错时长得跟
"文件不存在"一模一样, 只靠人眼看结果永远发现不了:

  1. trigram 少于 3 字符 → MATCH 返 0 且不报错
  2. FTS5 表上的 LIKE → 返 0 且不报错 (换成 OR 又能返, 更阴; 全表扫还会 Segfault)
  3. 空结果的措辞 → 不能诱导 LLM 断言"文件不存在"
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from catfish_tool_bridge import search_files as sf


# ── 造一个跟真库同构的小库 ─────────────────────────────────────


@pytest.fixture()
def fake_home(tmp_path, monkeypatch):
    """建一个 schema 跟 ~/.catfish/search.db 一致的索引库。

    ⚠ 建表语句是从真库 `SELECT sql FROM sqlite_master` 抄的, 特别是
      `tokenize='trigram'` 和 `path UNINDEXED` —— 这两个正是被测行为的来源,
      夹具偏一点点测试就测了个不存在的东西。
    """
    db = tmp_path / "search.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE VIRTUAL TABLE documents USING fts5(
            path UNINDEXED, title, content, file_type UNINDEXED,
            tokenize='trigram'
        );
        CREATE TABLE file_meta (
            path TEXT PRIMARY KEY, size_bytes INTEGER,
            mtime REAL, indexed_at REAL, content_hash TEXT
        );
        """
    )
    rows = [
        ("/h/.catfish/uploads/业务场景梳理清单.xlsx", "清单", "这是业务场景梳理的清单正文", ".xlsx", 300.0),
        ("/h/.catfish/output/周报-陈鸿波-20260813.xlsx", "周报", "本周完成了资质对标", ".xlsx", 200.0),
        ("/h/.catfish/notes/AI笔记.md", "AI笔记", "关于人工智能的一些想法", ".md", 100.0),
        # ⚠ 这份是**专门用来隔离全文那一路**的: 文件名里一个关键词都没有, 只有正文有。
        #    没有它, FTS 挂掉时文件名 LIKE 那一路也会把结果捞回来, 测试照绿。
        #
        # ⚠⚠ 正文里的两个词必须**都 ≥3 字**。第一版写的是"业务场景 … 梳理",
        #     而「梳理」只有 2 个字 —— 它会被 _split_terms 当短词剔掉, usable 只
        #     剩一个词, 于是所有"多词怎么拼"的判据全部落空 (对单元素列表,
        #     " ".join 和 " AND ".join 结果一样)。当时的变异测试因此 0 红,
        #     而我据此得出了一个**错误结论**写进源码注释, 见 search_files.py 坑 2。
        ("/h/.catfish/misc/z9.md", "z9", "业务场景 和 梳理清单 都只出现在正文里", ".md", 50.0),
    ]
    for p, t, c, ft, mt in rows:
        conn.execute("INSERT INTO documents(path,title,content,file_type) VALUES(?,?,?,?)", (p, t, c, ft))
        conn.execute("INSERT INTO file_meta(path,mtime) VALUES(?,?)", (p, mt))
    conn.commit()
    conn.close()
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    return tmp_path


# ── 坑 1: trigram 的 3 字符下限 ────────────────────────────────


def test_短词不能伪装成没找到(fake_home):
    """★★★ 少于 3 字符的词, trigram 会**静默返 0 行**。

    这是本工具存在的一半理由: 不说出来的话, "这词搜不了"和"文件不存在"在现场
    长得一模一样, 员工只会得到一句"没找到"。
    """
    r = sf.tool_search_files({"query": "AI"})
    assert r.get("short_terms") == ["AI"], (
        f"2 字符的词没被标出来 —— LLM 会把'搜不到'当成'不存在'。返回: {r}"
    )
    assert "short_terms_note" in r


def test_短词仍然走文件名匹配(fake_home):
    """★★ 短词搜不了正文, 但**文件名照样能匹配** —— 不能整个放弃。

    LIKE 是子串匹配, 没有长度下限, 正好补上 trigram 够不着的那部分。
    """
    r = sf.tool_search_files({"query": "AI"})
    paths = [m["path"] for m in r["matches"]]
    assert any("AI笔记" in p for p in paths), f"短词连文件名都没匹配: {r}"
    assert all(m["matched_by"] == "filename" for m in r["matches"])


def test_全是短词时的措辞不能说文件不存在(fake_home):
    r = sf.tool_search_files({"query": "xy"})
    assert r["count"] == 0
    s = r["summary"]
    assert "不等于文件不存在" in s, f"空结果的措辞会诱导 LLM 乱断言: {s}"


# ── 坑 2: FTS5 表上的 LIKE 静默返 0 ───────────────────────────


def test_文件名查询没有查_documents_表(fake_home):
    """★★★ 这条钉的是实现方式, 不是结果 —— 因为错误实现"看起来能用"。

    `documents` 是 FTS5 虚拟表, path 是 UNINDEXED。在它上面写 LIKE, 约束被下推
    给 FTS5 而它处理不了 → **返 0 行且不报错**。实测真库里
    `WHERE path LIKE '%uploads%'` 返 0, 同条件在 file_meta 上返 115。

    更阴的是它不稳定: 写成 `LIKE ? OR LIKE ?` 时 SQLite 改走全表扫又能返出东西
    (我最初就是这么误判"LIKE 能用"的), 而那个全表扫要读整个 586MB FTS5 内容,
    当场把进程搞成 Segfault。

    所以钉源码: 文件名那一路必须查 file_meta。
    """
    src = Path(sf.__file__).read_text(encoding="utf-8")
    # 去掉注释再判 —— 注释里正当地写着"不要查 documents", 会误伤 (今天第 5 次了)
    code = "\n".join(
        line.split("#")[0] for line in src.splitlines()
        if not line.strip().startswith("#")
    )
    assert "FROM file_meta WHERE" in code, "文件名查询没走 file_meta"
    import re
    bad = re.findall(r"FROM documents[^\"']*?LIKE", code, re.S)
    assert not bad, f"还在 FTS5 表上用 LIKE —— 会静默返 0: {bad}"


def test_文件名匹配真的能命中(fake_home):
    """行为层反证: 上面钉了实现, 这条确认实现是对的。"""
    r = sf.tool_search_files({"query": "周报"})
    assert r["count"] >= 1, f"文件名搜不到 —— LIKE 那一路挂了: {r}"
    assert "周报" in r["matches"][0]["path"]


# ── 正常路径 ──────────────────────────────────────────────────


def test_鸿波那句原话能找到目标文件(fake_home):
    """★★ 端到端: 就是 8/14 那次失败的输入。"""
    r = sf.tool_search_files({"query": "AI 场景梳理"})
    paths = [m["path"] for m in r["matches"]]
    assert any("业务场景梳理清单" in p for p in paths), f"还是找不到: {r}"
    assert r.get("short_terms") == ["AI"]


def test_多词是_AND_不是_OR(fake_home):
    """空格分隔的词之间是 AND —— 全都命中才算。"""
    r = sf.tool_search_files({"query": "业务场景 梳理清单"})
    assert r["count"] >= 1
    r2 = sf.tool_search_files({"query": "业务场景 完全不相干的词组"})
    assert r2["count"] == 0, f"AND 变成 OR 了: {r2}"


def test_正文命中这一路必须独立可用(fake_home):
    """★★★ 隔离 FTS 那一路 —— 用一份**文件名里没有关键词**的文档。

    这条是补出来的。原来只有上面那条 test_多词是_AND_不是_OR, 它的注释声称
    "顺带钉住 trigram 下不能用 AND 关键字", 但变异测试证明它没有:
    把 `_fts_expr` 的并列改成 ` AND ` 拼接 (trigram 下失效, 真库实测返 0 行),
    11 条测试**一条都没红** —— 因为夹具里那份文件的文件名也含关键词,
    LIKE 那一路把结果捞回来了。

    判据被另一条通路掩盖, 是今天反复踩的同一个形状: 测到的是"总能出结果",
    不是"这一路是通的"。

    查询用的两个词都 ≥3 字 —— 否则短的那个被剔掉后只剩单词, 这条就退化成
    "单词能不能搜到", 钉不住多词拼接 (见 fixture 里那段说明)。
    """
    r = sf.tool_search_files({"query": "业务场景 梳理清单"})
    hits = [m for m in r["matches"] if m["path"].endswith("z9.md")]
    assert hits, (
        "文件名里不含关键词的文档没被找到 —— 说明全文 MATCH 那一路是断的。"
        f"返回: {[m['path'] for m in r['matches']]}"
    )
    assert hits[0]["matched_by"] == "content", hits[0]


def test_按扩展名过滤(fake_home):
    r = sf.tool_search_files({"query": "资质", "file_types": ["md"]})
    assert all(m["file_type"] == ".md" for m in r["matches"]), r


def test_没有索引库时给的是建索引指引_不是没找到(fake_home, monkeypatch, tmp_path):
    """★★ error='index_unavailable' 要区别于"搜过了没有"。"""
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path / "空目录"))
    r = sf.tool_search_files({"query": "任意"})
    assert r.get("error") == "index_unavailable"
    assert "建索引" in r["summary"] or "搜索范围" in r["summary"]


def test_出错时明说是工具出错(fake_home, monkeypatch):
    """★ 异常不能变成"没找到" —— 那会让 LLM 断言文件不存在。"""
    monkeypatch.setattr(sf, "search_files", lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    r = sf.tool_search_files({"query": "x"})
    assert r["count"] == 0
    assert "工具出错" in r["summary"] or "别说" in r["summary"], r["summary"]


def test_工具已接进_dispatch(fake_home):
    """★★ 光有实现没接 dispatch = LLM 还是调不到 —— 正是这次 bug 的形状。"""
    from catfish_tool_bridge import catfish_tools as ct
    names = {t["name"] for t in ct.NATIVE_TOOLS} if hasattr(ct, "NATIVE_TOOLS") else set()
    if not names:
        for attr in dir(ct):
            v = getattr(ct, attr)
            if isinstance(v, list) and v and isinstance(v[0], dict) and "name" in v[0]:
                names = {t["name"] for t in v}
                break
    assert "catfish_search_files" in names, "没进工具清单, LLM 看不见"
    out = ct._dispatch_native_inner("catfish_search_files", {"query": "周报"})
    assert out.get("count", 0) >= 1, f"dispatch 没接上: {out}"
