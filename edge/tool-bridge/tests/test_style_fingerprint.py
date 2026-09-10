"""BL-MM8 style_fingerprint 单元测试.

覆盖:
- get / refresh / clear 三工具 happy path
- 数据源 = local_search 索引 (BL-STYLE-FP-USE-INDEX 7/27): 索引缺失 fail-loud /
  扩展名白名单 / 中文占比闸 / 太短跳过 / mtime 时间衰减 / 漏斗计数
- 抽取: 句子切分 / 词频 (含 jieba 退 char-fallback) / 标点 / 结构
- 时间衰减: 30/90/180 天分桶
- 损坏 fingerprint.json 返空 dict

7/27 删掉的测试 (对应能力已删, 留着是假绿):
- test_refresh_scans_explicit_source_dirs 等 args.source_dirs 一族
- test_refresh_skips_hidden_files —— 隐藏文件由 local_search 索引阶段决定,
  不再是 fingerprint 的事. 这条老测试恰恰**掩盖**了真 bug: 它用 tmp_path 造的
  可见目录, 永远测不到"扫描根**自己**是隐藏目录"那个致命分支 (~/.catfish/output).
- test_yaml_scan_dirs_* 一族 —— companion.yaml scan_dirs 整套已删
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest


# local_search indexer.py:SCHEMA 的副本. 故意抄一份而不 import catfish_search:
# tool-bridge 不依赖那个包 (pyproject 主路径 stdlib only), 而这份 schema 正是
# 两边约定的落盘契约 —— 抄在这里, 契约变了测试会红, 正好是我们要的告警.
_SEARCH_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS documents USING fts5(
    path UNINDEXED, title, content, file_type UNINDEXED, tokenize='trigram');
CREATE TABLE IF NOT EXISTS file_meta (
    path TEXT PRIMARY KEY, size_bytes INTEGER, mtime REAL,
    indexed_at REAL, content_hash TEXT);
"""

# 一段够长 (>200 字) 的中文公文, 中文占比远高于 MIN_CN_RATIO
GONGWEN = (
    "关于公司资质对标情况的分析报告\n\n"
    "一、总体情况\n"
    "经与中电系四家兄弟单位对标，公司现有资质共计八十三项，其中工程类资质十二项，"
    "服务类资质四十一项，认证类资质三十项。整体覆盖面处于中电系中游水平，但在施工"
    "总承包和低空经济两个新兴方向存在明显缺口。\n"
    "- 施工总承包资质缺口两项，影响投标范围\n"
    "- 系统集成资质需于三季度完成年审\n"
    "二、补强建议\n"
    "建议企发与风控部牵头，于三季度完成两项施工类资质申报；同步启动人员与业绩材料"
    "的归集工作，避免临期补件。"
)

# 英文技术文档 —— 该被中文占比闸整篇挡掉
ENGLISH_README = (
    "# Catfish Local Search\n\nA fast full text search engine built on SQLite FTS5 "
    "with trigram tokenizer. Install with pip and run the indexer to build a local "
    "index. Supports markdown, pdf, docx and more formats out of the box. "
) * 3


@pytest.fixture(autouse=True)
def isolated_fp(tmp_path: Path, monkeypatch):
    """每个测试用 tmp_path 隔离 fingerprint.json."""
    fake_fp = tmp_path / "style_fingerprint.json"
    monkeypatch.setattr(
        "catfish_tool_bridge.style_fingerprint.STYLE_FINGERPRINT_PATH",
        fake_fp,
    )
    # 自动刷新会检查索引签名；测试不能读取开发机真实 ~/.catfish/search.db。
    monkeypatch.setattr(
        "catfish_tool_bridge.style_fingerprint.SEARCH_DB_PATH",
        tmp_path / "search.db",
    )
    monkeypatch.setattr("catfish_tool_bridge.style_fingerprint._last_auto_refresh_at", 0.0)
    return fake_fp


@pytest.fixture
def sf():
    from catfish_tool_bridge import style_fingerprint
    return style_fingerprint


@pytest.fixture
def fake_index(tmp_path: Path, monkeypatch):
    """造一个假的 local_search 索引库. 返回 add(path, file_type, content, age_days)."""
    db = tmp_path / "search.db"
    monkeypatch.setattr("catfish_tool_bridge.style_fingerprint.SEARCH_DB_PATH", db)
    conn = sqlite3.connect(db)
    conn.executescript(_SEARCH_SCHEMA)
    now = time.time()

    def add(path: str, file_type: str, content: str, age_days: float = 1.0):
        conn.execute(
            "INSERT INTO documents(path,title,content,file_type) VALUES(?,?,?,?)",
            (path, path.rsplit("/", 1)[-1], content, file_type),
        )
        conn.execute(
            "INSERT INTO file_meta(path,size_bytes,mtime,indexed_at,content_hash)"
            " VALUES(?,?,?,?,?)",
            (path, len(content), now - age_days * 86400, now, "hash"),
        )
        conn.commit()

    yield add
    conn.close()


# ── get ─────────────────────────────────────────────────────


def test_get_when_fingerprint_not_exists(sf):
    r = sf.style_fingerprint_get({})
    assert r["type"] == "result"
    assert r["result"]["exists"] is False
    assert "hint" in r["result"]


def test_get_returns_summary_after_refresh(sf, fake_index):
    fake_index("/h/out/对标报告.docx", ".docx", GONGWEN)
    fake_index("/h/out/周报.md", ".md", GONGWEN)

    sf.style_fingerprint_refresh({})
    r = sf.style_fingerprint_get({})
    assert r["type"] == "result"
    assert r["result"]["exists"] is True
    assert r["result"]["stats"]["total_docs"] == 2
    assert r["result"]["source_count"] == 2
    assert isinstance(r["result"]["top_words"], list)
    assert len(r["result"]["sample_sentences"]) <= 3


def test_get_automatically_refreshes_after_index_changes(sf, fake_index):
    """索引新增文档后，读取 fingerprint 应自动更新而不依赖按钮。"""
    fake_index("/h/out/旧报告.docx", ".docx", GONGWEN)
    first = sf.style_fingerprint_get({})["result"]
    assert first["source_count"] == 1

    fake_index("/h/out/新报告.docx", ".docx", GONGWEN)
    sf._last_auto_refresh_at = 0.0
    refreshed = sf.style_fingerprint_get({})["result"]
    assert refreshed["source_count"] == 2


def test_get_with_corrupted_file(sf, isolated_fp):
    isolated_fp.parent.mkdir(parents=True, exist_ok=True)
    isolated_fp.write_text("not json", encoding="utf-8")
    r = sf.style_fingerprint_get({})
    assert r["result"]["exists"] is False


# ── refresh: 数据源 = local_search 索引 (BL-STYLE-FP-USE-INDEX 7/27) ──


def test_refresh_fails_loud_when_index_missing(sf, tmp_path, monkeypatch, isolated_fp):
    """索引库不存在 → 明确报 index_unavailable, 不是静默 total_docs=0.

    军规: "数据源没通" 跟 "语料确实不够" 给员工的下一步动作完全不同,
    不能混成同一个 0 —— 5/20 到 7/27 鸿波就是被这个 0 卡住的.
    """
    monkeypatch.setattr(
        "catfish_tool_bridge.style_fingerprint.SEARCH_DB_PATH", tmp_path / "nope.db"
    )
    r = sf.style_fingerprint_refresh({})["result"]
    assert r["error"] == "index_unavailable"
    assert "nope.db" in r["hint"]
    assert r["total_docs"] == 0
    # 关键: 没写盘 —— 上一次的指纹不能被一个失败的 refresh 覆盖成空
    assert not isolated_fp.exists()


def test_refresh_does_not_clobber_fingerprint_on_index_error(
    sf, fake_index, tmp_path, monkeypatch
):
    """先成功抽一次, 再让索引消失 → 老指纹必须还在."""
    fake_index("/h/out/对标报告.docx", ".docx", GONGWEN)
    sf.style_fingerprint_refresh({})
    assert sf.style_fingerprint_get({})["result"]["stats"]["total_docs"] == 1

    monkeypatch.setattr(
        "catfish_tool_bridge.style_fingerprint.SEARCH_DB_PATH", tmp_path / "gone.db"
    )
    sf.style_fingerprint_refresh({})
    assert sf.style_fingerprint_get({})["result"]["stats"]["total_docs"] == 1


def test_refresh_skips_non_doc_file_types(sf, fake_index):
    """扩展名白名单: 代码 / 表格不进语料.

    索引里 82% 是 .py/.h/.js (鸿波库 60332 条里 33206 条 .py), 不挡住会把
    公文风格喂成技术文档味.
    """
    fake_index("/h/out/报告.docx", ".docx", GONGWEN)
    fake_index("/h/code/a.py", ".py", "导入模块并处理数据。" * 40)   # 中文也不收
    fake_index("/h/data/表.xlsx", ".xlsx", GONGWEN)                  # 表格不算文书
    fake_index("/h/data/清单.csv", ".csv", GONGWEN)

    r = sf.style_fingerprint_refresh({})["result"]
    assert r["total_docs"] == 1
    assert r["funnel"]["indexed_doc_type"] == 1  # 只有 .docx 进了漏斗


def test_refresh_skips_short_docs(sf, fake_index):
    fake_index("/h/out/ok.md", ".md", GONGWEN)
    fake_index("/h/out/short.md", ".md", "只有几个字")

    r = sf.style_fingerprint_refresh({})["result"]
    assert r["total_docs"] == 1
    assert r["funnel"]["too_short"] == 1


def test_refresh_skips_english_docs_by_cn_ratio(sf, fake_index):
    """中文占比闸: 英文 README 整篇不要.

    光靠 _word_freq 只留中文词不够 —— 英文文档虽然贡献不了高频词, 照样污染
    句长 / 标点 / 结构比例 / 样本句这四项.
    """
    fake_index("/h/out/报告.docx", ".docx", GONGWEN)
    fake_index("/h/code/README.md", ".md", ENGLISH_README)

    r = sf.style_fingerprint_refresh({})["result"]
    assert r["total_docs"] == 1
    assert r["funnel"]["not_chinese"] == 1


def test_refresh_empty_index_gives_actionable_hint(sf, fake_index):
    """索引建了但一篇文书类都没有 → hint 指向"加目录", 不是干瘪的 0."""
    fake_index("/h/code/a.py", ".py", "print(1)\n" * 50)
    r = sf.style_fingerprint_refresh({})["result"]
    assert r["total_docs"] == 0
    assert r.get("error") is None  # 索引是通的, 只是没料
    assert "搜索范围" in r["hint"]


def test_refresh_all_filtered_hint_mentions_funnel(sf, fake_index):
    """有文书类但全被筛掉 → hint 说清是"太短"还是"中文占比不足"."""
    fake_index("/h/code/README.md", ".md", ENGLISH_README)
    r = sf.style_fingerprint_refresh({})["result"]
    assert r["total_docs"] == 0
    assert "中文占比不足" in r["hint"]


def test_refresh_uses_file_meta_mtime_for_decay(sf, fake_index):
    """时间衰减读的是索引里的 file_meta.mtime, 不是文件系统 stat."""
    fake_index("/h/out/新.md", ".md", GONGWEN, age_days=3)
    fake_index("/h/out/旧.md", ".md", GONGWEN, age_days=300)

    sf.style_fingerprint_refresh({})
    fp = json.loads(sf.STYLE_FINGERPRINT_PATH.read_text(encoding="utf-8"))
    now = time.time()
    weights = {
        Path(s["path"]).name: sf._decay_weight(s["mtime"], now) for s in fp["sources"]
    }
    assert weights["新.md"] == 1.0
    assert weights["旧.md"] == 0.1


def test_refresh_caps_at_max_docs_newest_first(sf, fake_index, monkeypatch):
    """超过上限时取 mtime 最新的那批 (跟时间衰减同向)."""
    monkeypatch.setattr("catfish_tool_bridge.style_fingerprint.MAX_DOCS_TO_SCAN", 3)
    for i in range(6):
        fake_index(f"/h/out/doc{i}.md", ".md", GONGWEN, age_days=i + 1)

    sf.style_fingerprint_refresh({})
    fp = json.loads(sf.STYLE_FINGERPRINT_PATH.read_text(encoding="utf-8"))
    names = sorted(Path(s["path"]).name for s in fp["sources"])
    assert names == ["doc0.md", "doc1.md", "doc2.md"]


def test_refresh_ignores_legacy_source_dirs_arg(sf, fake_index):
    """老调用方可能还传 source_dirs —— 忽略即可, 不能抛."""
    fake_index("/h/out/报告.docx", ".docx", GONGWEN)
    r = sf.style_fingerprint_refresh({"source_dirs": ["/tmp/whatever"]})["result"]
    assert r["total_docs"] == 1


# ── _cn_ratio ───────────────────────────────────────────────


def test_cn_ratio_separates_gongwen_from_english(sf):
    assert sf._cn_ratio(GONGWEN) >= sf.MIN_CN_RATIO
    assert sf._cn_ratio(ENGLISH_README) < sf.MIN_CN_RATIO
    assert sf._cn_ratio("") == 0.0


# ── 抽取细节 ────────────────────────────────────────────────


def test_split_sentences_zh_en_mixed(sf):
    text = "这是第一句。This is sentence 2! 第三句？最后一句."
    sents = sf._split_sentences(text)
    assert len(sents) == 4
    assert "这是第一句" in sents


def test_word_freq_filters_stopwords(sf):
    text = "的 一 是 在 资质 资质 风控"
    freq = sf._word_freq(text)
    # stopwords 应该被滤掉
    assert "的" not in freq
    assert "一" not in freq


def test_word_freq_returns_dict_even_no_jieba(sf, monkeypatch):
    """即便没装 jieba, 也能 fallback 到 char n-gram, 不挂."""
    monkeypatch.setitem(__import__("sys").modules, "jieba", None)
    text = "公司资质管理办法资质资质" * 10
    freq = sf._word_freq(text)
    # 不强制必须有 jieba, 保证不抛 + 返 dict 即可
    assert isinstance(freq, dict)


# ── BL-STYLE-FP-NOISE-FILTER (2026-06-03) regression ──


def test_filter_noise_removes_code_blocks(sf):
    """``` code blocks ``` 真整段去 (Python/bash 真英文 alpha 真大量 noise)."""
    text = "公司资质管理\n```python\nimport os\nprint('hello')\n```\n推进 ISO 审核"
    out = sf._filter_noise(text)
    assert "import os" not in out
    assert "print" not in out
    assert "公司资质管理" in out
    assert "推进 ISO 审核" in out


def test_filter_noise_removes_inline_code(sf):
    """`inline code` 真去 (函数名/变量名 noise)."""
    text = "调 `catfish_memory_dedupe` 真合并重复 entry"
    out = sf._filter_noise(text)
    assert "catfish_memory_dedupe" not in out
    assert "真合并重复" in out


def test_filter_noise_removes_urls(sf):
    """https?://URL 真去 (Top 高频词 https/com/github 主源)."""
    text = "详见 https://github.com/catfish/repo 的 BACKLOG 章节"
    out = sf._filter_noise(text)
    assert "https" not in out
    assert "github.com" not in out
    assert "详见" in out
    assert "章节" in out


def test_filter_noise_preserves_markdown_link_text(sf):
    """[text](url) 真留 text (一般是中文标题)."""
    text = "看 [资质建设方案](https://docs.example.com/qual.md) 详情"
    out = sf._filter_noise(text)
    assert "资质建设方案" in out
    assert "https" not in out
    assert "docs.example.com" not in out


def test_filter_noise_removes_html_tags(sf):
    """<html tags> 真去."""
    text = "<div class='note'>请注意资质评审时间</div>"
    out = sf._filter_noise(text)
    assert "<div" not in out
    assert "class='note'" not in out
    assert "请注意资质评审时间" in out


def test_filter_noise_preserves_structure_markers(sf):
    """保留 # / - / 数字. 真 _structure_pref 真识别 list/heading."""
    text = "## 标题\n\n- 列表项 1\n- 列表项 2\n\n1. 编号项"
    out = sf._filter_noise(text)
    # markers 真保留 (_structure_pref 真识别需要)
    assert "## 标题" in out
    assert "- 列表项 1" in out
    assert "1. 编号项" in out


def test_word_freq_excludes_pure_english(sf):
    """BL-STYLE-FP-NOISE-FILTER: 只留含中文 word, 禁纯英文 (the/com/of/and 等).

    真兼容两种 mode: 有 jieba (生产) → 留中文 word 真有数据;
    无 jieba (sandbox) → fallback char n-gram 真也**不**含英文 (空也满足条件).
    """
    text = "the catfish 项目管理 hermes 的 com 和 github 资质评审 推进 审核"
    freq = sf._word_freq(text)
    # 真禁纯英文 (两 mode 都必须满足)
    assert "the" not in freq
    assert "catfish" not in freq
    assert "hermes" not in freq
    assert "com" not in freq
    assert "github" not in freq
    # 所有 key 真都含中文 (真禁纯英文 真验)
    for k in freq:
        # 真 key 真至少含 1 个中文字符 (U+4E00-U+9FFF)
        assert any("一" <= c <= "鿿" for c in k), f"key {k!r} 真不含中文真不该进 freq"


def test_punctuation_pref_counts(sf):
    text = "句子一, 句子二, 句子三; 句子四！句子五？"
    p = sf._punctuation_pref(text)
    assert p["comma_en"] >= 2
    assert p["semicolon_en"] >= 1
    assert p["exclamation"] >= 1
    assert p["question"] >= 1


def test_structure_pref_detects_list(sf):
    text = "- 列表项一\n- 列表项二\n- 列表项三"
    s = sf._structure_pref(text)
    assert s["list_ratio"] == 1.0


def test_structure_pref_detects_table(sf):
    text = "| a | b | c |\n| 1 | 2 | 3 |"
    s = sf._structure_pref(text)
    assert s["table_ratio"] == 1.0


def test_structure_pref_detects_prose(sf):
    text = "这是一段散文.\n再来一段散文.\n第三段散文。"
    s = sf._structure_pref(text)
    assert s["prose_ratio"] > 0.5


def test_decay_weight_buckets(sf):
    now = 1_700_000_000.0
    # 30 天内 = 1.0
    assert sf._decay_weight(now - 10 * 86400, now) == 1.0
    # 60 天 = 0.5 桶
    assert sf._decay_weight(now - 60 * 86400, now) == 0.5
    # 100 天 → 0.25 桶 (DECAY_BUCKETS: 30/1.0, 90/0.5, 180/0.25)
    assert sf._decay_weight(now - 100 * 86400, now) == 0.25
    # 老于 180 天 = default 0.1
    assert sf._decay_weight(now - 200 * 86400, now) == 0.1


def test_sample_sentences_picks_medium_length(sf):
    """sample_sentences 应该挑中等长度 (15-80 字), 跳过太短/太长."""
    docs = [
        (Path("a.md"), time.time(), "短.\n这是一个中等长度的句子, 适合采样作为样本.\n" + ("超长" * 100)),
    ]
    samples = sf._sample_sentences(docs, n=3)
    # 至少应该有那个中等长度的
    assert any("中等长度" in s for s in samples)


# ── clear ───────────────────────────────────────────────────


def test_clear_deletes_file(sf, isolated_fp, fake_index):
    fake_index("/h/out/报告.docx", ".docx", GONGWEN)
    sf.style_fingerprint_refresh({})
    assert isolated_fp.exists()
    r = sf.style_fingerprint_clear({})
    assert r["type"] == "result"
    assert not isolated_fp.exists()


def test_clear_when_not_exists(sf, isolated_fp):
    assert not isolated_fp.exists()
    r = sf.style_fingerprint_clear({})
    assert r["type"] == "result"


# ── BL-STYLE-FP-NAN-FIX (5/20): 空 docs 不返 NaN ────────────────


def test_empty_fingerprint_has_zero_ratio_not_empty_dict(sf):
    """空 docs → structure_pref 返 {list_ratio:0, table_ratio:0, prose_ratio:0}.

    前端拿 None ratio (NaN * 100).toFixed(0) === "NaN" 显 NaN%. 给 0 就显 0%.
    """
    fp = sf._build_fingerprint([])
    assert "structure_pref" in fp
    assert fp["structure_pref"] == {
        "list_ratio": 0.0,
        "table_ratio": 0.0,
        "prose_ratio": 0.0,
    }
    # stats 也得齐, 不然前端 avg_sentence_length 等字段 undefined
    assert fp["stats"]["total_docs"] == 0
    assert fp["stats"]["total_chars"] == 0
    assert fp["stats"]["avg_sentence_length"] == 0.0
    assert fp["stats"]["sentence_count"] == 0
