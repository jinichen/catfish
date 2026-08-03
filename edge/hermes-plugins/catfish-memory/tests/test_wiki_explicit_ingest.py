"""员工显式入库 (wiki/raw/sources/) 不受 auto_ingest 开关管。

# 这组测试守的是什么 (8/3)

6/16 鸿波说"对话自动入知识库会很乱", 于是加了 _wiki_enabled() 守门, 默认关。
要关的是「**聊天内容自动**变成 entity」。

但同一个门也罩住了 wiki/raw/sources/ —— 而那个目录里的东西是
catfish_wiki_ingest 放的, 那个工具的第一句描述就是「**员工显式**要求把 chat
附件存到知识库时才调」。两件相反的事共用一个开关。

后果: auto_ingest: false 的机器上, 员工明说"存进知识库", 文件写进
raw/sources/ 之后**没有任何东西会读它**。不是 24 小时后, 是永远。
ingested_state 永不更新, sources 无限堆积, 全程零报错、零日志。

拆开之后有个新风险, 比原来的 bug 更值得盯: 开了 sources 这条路时, 如果顺手
把 journal 一起喂给蒸馏 LLM, 聊天内容照样会被抽成 entity —— 等于从后门把
6/16 禁掉的事又打开了, 而且这次没人会发现。
test_journal_not_fed_when_auto_ingest_off 就是钉这一条的。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import catfish_memory
from catfish_memory import CatfishMemoryProvider


# ─────────────────────────────────────────────────────────────
# fixtures (跟 test_sync_turn.py 同款)
# ─────────────────────────────────────────────────────────────


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """假 ~/.catfish/ —— 跟 test_sync_turn.py 同款 (CATFISH_HOME 指过去)。"""
    d = tmp_path / ".catfish"
    (d / "wiki" / "raw" / "sources").mkdir(parents=True)
    (d / "wiki" / "entities").mkdir(parents=True)
    (d / "wiki" / "concepts").mkdir(parents=True)
    monkeypatch.setenv("CATFISH_HOME", str(d))
    return d


@pytest.fixture
def sync_thread(monkeypatch: pytest.MonkeyPatch):
    class _SyncThread:
        def __init__(self, target=None, name=None, daemon=None, **kwargs):
            self._target = target

        def start(self):
            if self._target:
                self._target()

        def join(self, timeout=None):
            pass

    monkeypatch.setattr(catfish_memory.threading, "Thread", _SyncThread)
    return _SyncThread


@pytest.fixture
def env_enable(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CATFISH_PLUGIN_SUMMARIZE", "1")
    monkeypatch.setenv("CATFISH_PLUGIN_SUMMARIZE_MODEL", "catfish-public-qwen-flash")
    monkeypatch.setenv("CATFISH_INTERNAL_DEV_TOKEN", "test-token")
    # 关掉自动入库 —— 这正是 8/3 现场的配置
    monkeypatch.setenv("CATFISH_WIKI_ENABLE", "0")


@pytest.fixture
def spy_llm(monkeypatch: pytest.MonkeyPatch):
    """记录蒸馏两步各自收到什么 input —— 断言喂进去的是什么, 不只是跑没跑。"""
    calls = {"summarize": [], "distill": [], "analysis": [], "generation": []}

    async def fake_summarize(pairs, model):
        calls["summarize"].append(model)
        return "### 测试主题\n\n这是一段总结"

    async def fake_distill(text, model):
        calls["distill"].append(text)
        return None

    async def fake_analysis(text, model):
        calls["analysis"].append(text)
        return "ANALYSIS-OUT"

    async def fake_generation(analysis, model):
        calls["generation"].append(analysis)
        return "GENERATION-OUT"

    monkeypatch.setattr(catfish_memory, "_call_summarize_llm", fake_summarize)
    monkeypatch.setattr(catfish_memory, "_call_distill_llm", fake_distill)
    monkeypatch.setattr(catfish_memory, "_call_analysis_llm", fake_analysis)
    monkeypatch.setattr(catfish_memory, "_call_generation_llm", fake_generation)
    monkeypatch.setattr(catfish_memory, "_parse_generation_output", lambda g: [])
    return calls


def _write_source(home: Path, name: str, body: str) -> Path:
    p = home / "wiki" / "raw" / "sources" / name
    p.write_text(
        f"---\ntype: source\nfilename: {name}\nkind: text\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return p


def _drive(provider: CatfishMemoryProvider, n: int = 6) -> None:
    """跑够轮数触发 bg sync。"""
    for i in range(n):
        provider.sync_turn(f"问题 {i}", f"回答 {i}", session_id="s1")


# ─────────────────────────────────────────────────────────────
# 核心: 关着 auto_ingest, 显式 source 仍然要被消费
# ─────────────────────────────────────────────────────────────


def test_explicit_source_distilled_even_when_auto_ingest_off(
    fake_home, sync_thread, env_enable, spy_llm
):
    """8/3 的 bug 本身: 员工显式入库的 source 必须被蒸馏。

    改之前这里 analysis 一次都不会被调 —— pending_sources 被
    `if _wiki_enabled() else []` 直接抹成空列表, 连目录都不看一眼。
    """
    _write_source(fake_home, "1785759315-中电系资质对标对齐矩阵.md", "153 项资质")

    p = CatfishMemoryProvider()
    p.initialize(session_id="s-explicit")
    _drive(p)

    assert spy_llm["analysis"], (
        "auto_ingest 关着时, 员工显式放进 raw/sources/ 的材料没有被蒸馏 —— "
        "这正是 8/3 那个'存进去了永远看不到'的 bug。"
    )
    fed = spy_llm["analysis"][0]
    assert "153 项资质" in fed, f"source 正文没被喂进 Analysis:\n{fed[:400]}"


def test_journal_not_fed_when_auto_ingest_off(
    fake_home, sync_thread, env_enable, spy_llm
):
    """拆开开关之后最要紧的一条: 不能顺带把聊天内容也蒸了。

    6/16 禁的就是「聊天内容自动变 entity」。如果 explicit_only 时还把 journal
    喂进 Analysis, 等于从后门恢复了那个行为, 而且没有任何人会察觉。
    """
    _write_source(fake_home, "1785759315-材料.md", "SOURCE-正文标记")

    p = CatfishMemoryProvider()
    p.initialize(session_id="s-nojournal")
    _drive(p)

    assert spy_llm["analysis"], "analysis 应该被调用"
    fed = spy_llm["analysis"][0]
    assert "SOURCE-正文标记" in fed, "source 该进去"
    assert "这是一段总结" not in fed, (
        "auto_ingest 关着时 journal 不该被喂进 wiki 蒸馏 —— "
        "那会让聊天内容变成 entity, 正是 6/16 要禁的事。\n"
        f"实际喂进去的:\n{fed[:600]}"
    )
    assert "问题 0" not in fed and "回答 0" not in fed, "对话原文更不该进去"


def test_nothing_runs_when_no_explicit_source(
    fake_home, sync_thread, env_enable, spy_llm
):
    """没有显式 source 时, 关着 auto_ingest 就该什么都不做。

    拆开开关不等于变相打开自动入库。
    """
    p = CatfishMemoryProvider()
    p.initialize(session_id="s-none")
    _drive(p)

    assert not spy_llm["analysis"], (
        "没有员工显式入库的材料时, auto_ingest 关着就不该跑 wiki 蒸馏 —— "
        f"实际跑了 {len(spy_llm['analysis'])} 次"
    )


def test_auto_ingest_on_still_feeds_journal(
    fake_home, sync_thread, env_enable, spy_llm, monkeypatch
):
    """开着 auto_ingest 时行为不变 —— 这次改动不该动到原来的路径。"""
    monkeypatch.setenv("CATFISH_WIKI_ENABLE", "1")
    _write_source(fake_home, "1785759315-材料.md", "SOURCE-正文标记")

    p = CatfishMemoryProvider()
    p.initialize(session_id="s-on")
    _drive(p)

    assert spy_llm["analysis"], "开着 auto_ingest 当然要跑"
    fed = spy_llm["analysis"][0]
    assert "SOURCE-正文标记" in fed
    assert "这是一段总结" in fed, "开着 auto_ingest 时 journal 本来就该进 Analysis"
