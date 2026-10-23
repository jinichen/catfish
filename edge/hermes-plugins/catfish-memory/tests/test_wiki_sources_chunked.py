"""10/23: 员工资料一份一份、一段一段读 (catfish_memory_wiki_sources)。

Windows 现场: 49 份 6.9MB 一次拼成 24K、分析只看最后 5K, 却全标成已摄取 → 只出 10 张薄卡。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import catfish_memory_wiki_sources as ws
from catfish_memory_wiki_provenance import Provenance, _normalize_sources


def _src(home: Path, name: str, body: str) -> Path:
    d = home / "wiki" / "raw" / "sources"
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text(body, encoding="utf-8")
    return p


def _run(home, pending, run_pass, **kw):
    done: list[str] = []
    stats = asyncio.run(ws.drain_sources(home, pending, run_pass, lambda h, names: done.extend(names), **kw))
    return stats, done


def test_大文件分段_每段都读_全读完才标已摄取(tmp_path):
    big = _src(tmp_path, "规划.md", ("第一章 新能源\n" * 1500) + ("第九章 量子科技\n" * 1500))
    seen: list[str] = []

    async def run_pass(text, path):
        seen.append(text)
        return (1, 0)

    stats, done = _run(tmp_path, [big], run_pass)
    assert len(seen) == stats["chunks"] >= 3
    assert all(len(t) <= ws.SOURCE_CHUNK_CHARS + 200 for t in seen), "一段不超过段长 (加段头)"
    assert "量子科技" in seen[-1] and "新能源" in seen[0], "开头和结尾都读到了"
    assert done == ["规划.md"]
    assert ws.read_progress(tmp_path) == {}


def test_一次一份_来源只记这一份(tmp_path):
    a = _src(tmp_path, "a.md", "甲文件内容")
    b = _src(tmp_path, "b.md", "乙文件内容")
    fed: list[tuple[str, str]] = []

    async def run_pass(text, path):
        fed.append((path.name, text))
        return (0, 1)

    _, done = _run(tmp_path, [a, b], run_pass)
    assert [n for n, _ in fed] == ["a.md", "b.md"]
    assert "乙文件" not in fed[0][1], "两份不混在一次分析里"
    assert done == ["a.md", "b.md"]


def test_中途失败从断点接着读_不标已摄取(tmp_path):
    big = _src(tmp_path, "长.md", "\n".join(f"第{i}行 内容" for i in range(4000)))
    calls = {"n": 0}

    async def flaky(text, path):
        calls["n"] += 1
        return None if calls["n"] == 2 else (1, 0)

    stats, done = _run(tmp_path, [big], flaky)
    assert stats["chunks"] == 1 and done == []
    pos = ws.read_progress(tmp_path)["长.md"]
    assert pos > 0

    resumed: list[str] = []

    async def ok(text, path):
        resumed.append(text)
        return (1, 0)

    _, done = _run(tmp_path, [big], ok)
    assert done == ["长.md"]
    assert big.read_text(encoding="utf-8")[pos:pos + 20].strip()[:8] in resumed[0], "从断点那段接着读"


def test_同一段连续失败三次就跳过_不卡死(tmp_path):
    f = _src(tmp_path, "怪.md", "怪内容")

    async def always_fail(text, path):
        return None

    for _ in range(ws.MAX_CHUNK_FAILS - 1):
        _, done = _run(tmp_path, [f], always_fail)
        assert done == []
    _, done = _run(tmp_path, [f], always_fail)
    assert done == ["怪.md"]


def test_到时间就停_下次接着(tmp_path):
    files = [_src(tmp_path, f"{i}.md", f"内容{i}") for i in range(3)]

    async def run_pass(text, path):
        return (0, 0)

    stats, done = _run(tmp_path, files, run_pass, time_budget=-1)
    assert stats["chunks"] == 0 and done == []


def test_只读资料时_编的聊天日期丢掉_改记原文件():
    prov = Provenance.from_run("", [Path("/x/wiki/raw/sources/1785-规划.md")])
    content = '---\ntitle: 规划\nsources: ["journal:2026-10-23"]\n---\n\n正文\n'
    out = _normalize_sources("wiki/concepts/规划.md", content, prov)
    assert "journal:2026-10-23" not in out
    assert "raw/sources/1785-规划" in out


def test_二进制乱码不读_标掉不卡队列(tmp_path):
    junk = _src(tmp_path, "扫描件.md", "---\ntype: source\n---\n" + "\x00\x02\ufffd\ufffdJFIF" * 2000)
    good = _src(tmp_path, "好.md", "正常文字内容")
    fed: list[str] = []

    async def run_pass(text, path):
        fed.append(path.name)
        return (0, 0)

    _, done = _run(tmp_path, [junk, good], run_pass)
    assert fed == ["好.md"], "乱码不喂模型"
    assert done == ["扫描件.md", "好.md"]


def test_超大文件只读前若干段(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "MAX_CHUNKS_PER_FILE", 2)
    big = _src(tmp_path, "表格导出.md", "一行数据\n" * 20000)
    n = {"c": 0}

    async def run_pass(text, path):
        n["c"] += 1
        return (0, 0)

    _, done = _run(tmp_path, [big], run_pass)
    assert n["c"] == 2 and done == ["表格导出.md"]


def test_资料要点卡_把分析出的事实一条不落():
    analysis = (
        "## Entities\n- 福建省 | org | 省\n\n"
        "## Facts\n"
        "- 福建十五五规划 | 2030年战新产业占比约35% | 「工业战略性新兴产业产值占规模以上工业产值比重达35%左右」\n"
        "- 福建十五五规划 | 培育小巨人企业426家 | “培育国家级专精特新小巨人企业426家”\n"
        "- 坏行没有摘录 | 只有两段\n\n"
        "## Decisions\n- (无)\n"
    )
    path, content = ws.digest_card(analysis, "福建十五五规划", "raw/sources/1792700000-福建十五五规划")
    assert path == "wiki/concepts/福建十五五规划（资料要点）.md"
    assert "- 培育小巨人企业426家 〔原文:「培育国家级专精特新小巨人企业426家」〕" in content
    assert content.count("〔原文") == 2
    assert 'sources: ["raw/sources/1792700000-福建十五五规划"]' in content
    assert ws.digest_card("## Entities\n- x | org | y\n", "a", "b") is None
    assert ws.source_title_of(Path("/x/1792700000-福建十五五规划.md")) == "福建十五五规划"
