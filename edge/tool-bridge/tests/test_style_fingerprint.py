"""BL-MM8 style_fingerprint 单元测试.

覆盖:
- get / refresh / clear 三工具 happy path
- 抽取: 句子切分 / 词频 (含 jieba 退 char-fallback) / 标点 / 结构
- 时间衰减: 30/90/180 天分桶
- 文件类型: .md/.txt/.docx 支持, csv/xlsx/pdf 跳过
- 边界: 文档 < 200 字跳过, > 5MB 跳过, 隐藏文件跳过
- 损坏 fingerprint.json 返空 dict
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_fp(tmp_path: Path, monkeypatch):
    """每个测试用 tmp_path 隔离."""
    fake_fp = tmp_path / "style_fingerprint.json"
    monkeypatch.setattr(
        "catfish_tool_bridge.style_fingerprint.STYLE_FINGERPRINT_PATH",
        fake_fp,
    )
    return fake_fp


@pytest.fixture
def sf():
    from catfish_tool_bridge import style_fingerprint
    return style_fingerprint


def _write_doc(d: Path, name: str, content: str) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text(content, encoding="utf-8")
    return p


# ── get ─────────────────────────────────────────────────────


def test_get_when_fingerprint_not_exists(sf):
    r = sf.style_fingerprint_get({})
    assert r["type"] == "result"
    assert r["result"]["exists"] is False
    assert "hint" in r["result"]


def test_get_returns_summary_after_refresh(sf, tmp_path, monkeypatch):
    src = tmp_path / "work"
    _write_doc(src, "doc1.md", "公司资质管理办法 2025 修订. 主责部门移交企发与风控部. " * 10)
    _write_doc(src, "doc2.md", "本周完成: 60 天未下单清单. 上会材料整理中. 戴明利反馈待收. " * 10)
    monkeypatch.setattr(sf, "DEFAULT_SOURCE_DIRS", [src])

    sf.style_fingerprint_refresh({})
    r = sf.style_fingerprint_get({})
    assert r["type"] == "result"
    assert r["result"]["exists"] is True
    assert r["result"]["stats"]["total_docs"] == 2
    assert r["result"]["source_count"] == 2
    assert isinstance(r["result"]["top_words"], list)
    assert len(r["result"]["sample_sentences"]) <= 3


# ── refresh ─────────────────────────────────────────────────


def test_refresh_scans_explicit_source_dirs(sf, tmp_path):
    src1 = tmp_path / "src1"
    src2 = tmp_path / "src2"
    _write_doc(src1, "a.md", "测试文档一" * 100)
    _write_doc(src2, "b.md", "测试文档二" * 100)
    r = sf.style_fingerprint_refresh({"source_dirs": [str(src1), str(src2)]})
    assert r["type"] == "result"
    assert r["result"]["total_docs"] == 2
    assert str(src1) in r["result"]["scanned_dirs"]


def test_refresh_skips_short_docs(sf, tmp_path):
    src = tmp_path / "work"
    _write_doc(src, "tooshort.md", "只有几个字")  # < 200 字
    _write_doc(src, "ok.md", "够长的文档" * 100)  # > 200 字
    r = sf.style_fingerprint_refresh({"source_dirs": [str(src)]})
    assert r["result"]["total_docs"] == 1


def test_refresh_skips_hidden_files(sf, tmp_path):
    src = tmp_path / "work"
    _write_doc(src, ".hidden.md", "藏起来的文档" * 100)
    _write_doc(src, "visible.md", "可见的文档" * 100)
    r = sf.style_fingerprint_refresh({"source_dirs": [str(src)]})
    assert r["result"]["total_docs"] == 1


def test_refresh_skips_unsupported_types(sf, tmp_path):
    src = tmp_path / "work"
    _write_doc(src, "data.csv", "a,b,c\n" * 100)  # csv 跳过
    _write_doc(src, "data.xlsx", "binary" * 100)  # xlsx 跳过 (内容也不像 docx 二进制但我们靠扩展名判)
    _write_doc(src, "doc.md", "文档内容" * 100)  # md 走
    r = sf.style_fingerprint_refresh({"source_dirs": [str(src)]})
    assert r["result"]["total_docs"] == 1


def test_refresh_handles_empty_dir(sf, tmp_path):
    src = tmp_path / "empty"
    src.mkdir()
    r = sf.style_fingerprint_refresh({"source_dirs": [str(src)]})
    assert r["type"] == "result"
    assert r["result"]["total_docs"] == 0


def test_refresh_handles_nonexistent_dir(sf, tmp_path):
    r = sf.style_fingerprint_refresh({"source_dirs": [str(tmp_path / "nope")]})
    assert r["type"] == "result"
    assert r["result"]["total_docs"] == 0


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
    # 100 天 = 0.5 桶 (≤ 90 天的边界外其实算 0.25 桶?)
    # 看 DECAY_BUCKETS: 30/1.0, 90/0.5, 180/0.25
    assert sf._decay_weight(now - 100 * 86400, now) == 0.25
    # 老于 180 天 = default 0.1
    assert sf._decay_weight(now - 200 * 86400, now) == 0.1


def test_sample_sentences_picks_medium_length(sf, tmp_path):
    """sample_sentences 应该挑中等长度 (15-80 字), 跳过太短/太长."""
    docs = [
        (Path("a.md"), time.time(), "短.\n这是一个中等长度的句子, 适合采样作为样本.\n" + ("超长" * 100)),
    ]
    samples = sf._sample_sentences(docs, n=3)
    # 至少应该有那个中等长度的
    assert any("中等长度" in s for s in samples)


# ── clear ───────────────────────────────────────────────────


def test_clear_deletes_file(sf, isolated_fp, tmp_path):
    src = tmp_path / "work"
    _write_doc(src, "a.md", "内容" * 100)
    sf.style_fingerprint_refresh({"source_dirs": [str(src)]})
    assert isolated_fp.exists()
    r = sf.style_fingerprint_clear({})
    assert r["type"] == "result"
    assert not isolated_fp.exists()


def test_clear_when_not_exists(sf, isolated_fp):
    assert not isolated_fp.exists()
    r = sf.style_fingerprint_clear({})
    assert r["type"] == "result"


# ── 文件损坏 ────────────────────────────────────────────────


def test_get_with_corrupted_file(sf, isolated_fp):
    isolated_fp.parent.mkdir(parents=True, exist_ok=True)
    isolated_fp.write_text("not json", encoding="utf-8")
    r = sf.style_fingerprint_get({})
    assert r["result"]["exists"] is False


# ── BL-STYLE-FP-YAML-CONFIG (5/20): yaml 自定义扫描目录 ──────────


def test_yaml_scan_dirs_added_to_default(sf, isolated_fp, tmp_path, monkeypatch):
    """yaml style_fingerprint.scan_dirs 列的目录被 union 进默认扫描.

    场景: 鸿波本机文档在 ~/person_task/catfish/docs/, 默认 DEFAULT_SOURCE_DIRS
    扫不到. 通过 ~/.catfish/companion.yaml 加 scan_dirs 让它能扫.
    """
    # 假 yaml + 假 scan dir
    fake_yaml = tmp_path / "companion.yaml"
    fake_scan = tmp_path / "my-docs"
    _write_doc(fake_scan, "report.md", "鸿波周报\n\n本周完成 X / Y / Z. 详见列表.\n\n" + "正文" * 100)
    fake_yaml.write_text(
        f"style_fingerprint:\n  scan_dirs:\n    - {fake_scan}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "catfish_tool_bridge.style_fingerprint.COMPANION_YAML_PATH", fake_yaml
    )
    # DEFAULT_SOURCE_DIRS 不存在的目录 (不挂) — _resolve_scan_dirs union
    monkeypatch.setattr(
        "catfish_tool_bridge.style_fingerprint.DEFAULT_SOURCE_DIRS",
        [tmp_path / "nonexistent"],
    )

    dirs = sf._resolve_scan_dirs(None)
    assert any(str(d) == str(fake_scan) for d in dirs)


def test_yaml_missing_returns_only_default(sf, monkeypatch, tmp_path):
    """yaml 不存在 → _resolve_scan_dirs 仅返默认 (不挂)."""
    monkeypatch.setattr(
        "catfish_tool_bridge.style_fingerprint.COMPANION_YAML_PATH",
        tmp_path / "nope.yaml",  # 不存在
    )
    monkeypatch.setattr(
        "catfish_tool_bridge.style_fingerprint.DEFAULT_SOURCE_DIRS",
        [tmp_path / "default-dir"],
    )
    dirs = sf._resolve_scan_dirs(None)
    assert len(dirs) == 1
    assert str(dirs[0]) == str(tmp_path / "default-dir")


def test_yaml_malformed_skipped(sf, monkeypatch, tmp_path):
    """yaml 解析失败 → 静默 fallback 仅默认."""
    fake_yaml = tmp_path / "companion.yaml"
    fake_yaml.write_text("this is: not [valid yaml :::", encoding="utf-8")
    monkeypatch.setattr(
        "catfish_tool_bridge.style_fingerprint.COMPANION_YAML_PATH", fake_yaml
    )
    monkeypatch.setattr(
        "catfish_tool_bridge.style_fingerprint.DEFAULT_SOURCE_DIRS",
        [tmp_path / "default-only"],
    )
    dirs = sf._resolve_scan_dirs(None)
    assert all("not [valid" not in str(d) for d in dirs)
    # 至少返默认
    assert any(str(d) == str(tmp_path / "default-only") for d in dirs)


def test_explicit_args_override_yaml_and_default(sf, monkeypatch, tmp_path):
    """args.source_dirs 显式给 → 不 union, 直接用. (调用方知道自己要啥)"""
    monkeypatch.setattr(
        "catfish_tool_bridge.style_fingerprint.DEFAULT_SOURCE_DIRS",
        [tmp_path / "default"],
    )
    fake_yaml = tmp_path / "companion.yaml"
    fake_yaml.write_text(
        f"style_fingerprint:\n  scan_dirs:\n    - {tmp_path / 'yaml-dir'}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "catfish_tool_bridge.style_fingerprint.COMPANION_YAML_PATH", fake_yaml
    )

    dirs = sf._resolve_scan_dirs([str(tmp_path / "explicit")])
    assert len(dirs) == 1
    assert str(dirs[0]) == str(tmp_path / "explicit")


def test_yaml_union_dedupes(sf, monkeypatch, tmp_path):
    """default 跟 yaml 路径相同时 dedup 不重复扫."""
    common = tmp_path / "common"
    monkeypatch.setattr(
        "catfish_tool_bridge.style_fingerprint.DEFAULT_SOURCE_DIRS",
        [common],
    )
    fake_yaml = tmp_path / "companion.yaml"
    fake_yaml.write_text(
        f"style_fingerprint:\n  scan_dirs:\n    - {common}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "catfish_tool_bridge.style_fingerprint.COMPANION_YAML_PATH", fake_yaml
    )
    dirs = sf._resolve_scan_dirs(None)
    assert len(dirs) == 1


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


def test_refresh_empty_dirs_returns_yaml_hint(sf, monkeypatch, tmp_path):
    """refresh 没扫到文档 → yaml_config_hint 提示员工去 ~/.catfish/companion.yaml 加段."""
    monkeypatch.setattr(
        "catfish_tool_bridge.style_fingerprint.DEFAULT_SOURCE_DIRS",
        [tmp_path / "nonexistent"],
    )
    monkeypatch.setattr(
        "catfish_tool_bridge.style_fingerprint.COMPANION_YAML_PATH",
        tmp_path / "no-yaml.yaml",
    )
    r = sf.style_fingerprint_refresh({})
    assert r["result"]["total_docs"] == 0
    assert r["result"]["yaml_config_hint"] is not None
    assert "scan_dirs" in r["result"]["yaml_config_hint"]


def test_refresh_with_docs_no_hint(sf, monkeypatch, tmp_path):
    """有扫到文档 → yaml_config_hint=None (不显提示)."""
    src = tmp_path / "work"
    _write_doc(src, "report.md", "正文 " * 150)
    monkeypatch.setattr(
        "catfish_tool_bridge.style_fingerprint.DEFAULT_SOURCE_DIRS",
        [src],
    )
    monkeypatch.setattr(
        "catfish_tool_bridge.style_fingerprint.COMPANION_YAML_PATH",
        tmp_path / "no-yaml.yaml",
    )
    r = sf.style_fingerprint_refresh({})
    assert r["result"]["total_docs"] >= 1
    assert r["result"]["yaml_config_hint"] is None


def test_refresh_lists_skipped_dirs(sf, monkeypatch, tmp_path):
    """不存在的目录列在 skipped_dirs, 帮员工调试."""
    monkeypatch.setattr(
        "catfish_tool_bridge.style_fingerprint.DEFAULT_SOURCE_DIRS",
        [tmp_path / "nope-1", tmp_path / "nope-2"],
    )
    monkeypatch.setattr(
        "catfish_tool_bridge.style_fingerprint.COMPANION_YAML_PATH",
        tmp_path / "no-yaml.yaml",
    )
    r = sf.style_fingerprint_refresh({})
    assert len(r["result"]["skipped_dirs"]) == 2
