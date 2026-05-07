"""BL-L26 attachment_bm25 单测.

覆盖:
  - split_into_passages: 双换行 / 超长段按句号切
  - tokenize_query: 中文 2-char window / 英文 / 混合
  - score_passage: 命中 / 不命中 / 多 token / 长度归一化
  - query_top_k: 标准用例 / 空 query 兜底 / 全不命中兜底 / 中文 2-char 词
  - CLI: 不存在 path / 真 60KB 假合同 / 空 query
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import attachment_bm25 as bm25  # noqa: E402


# ============================================================
# 段落切分
# ============================================================


def test_split_returns_empty_for_empty_text() -> None:
    assert bm25.split_into_passages("") == []
    assert bm25.split_into_passages("   \n  \n  ") == []


def test_split_basic_double_newlines() -> None:
    text = "段一第一句话.\n\n段二是另一个段落.\n\n段三结束."
    paras = bm25.split_into_passages(text)
    assert len(paras) == 3
    assert "段一" in paras[0]
    assert "段二" in paras[1]
    assert "段三" in paras[2]


def test_split_breaks_oversized_paragraph_by_sentence() -> None:
    """单段 > PARAGRAPH_MAX_CHARS 应按句号切分"""
    huge = ("一二三四五" * 200) + "句号。" + ("六七八九十" * 200) + "再来。" + ("拾壹拾贰" * 200) + "结束。"
    paras = bm25.split_into_passages(huge)
    assert len(paras) >= 2
    for p in paras:
        # 留一定余量给句号尾
        assert len(p) <= bm25.PARAGRAPH_MAX_CHARS + 50


def test_split_keeps_short_paragraphs_independent() -> None:
    """短段 (像页码 / 标题) 也独立成行, 不强制并合"""
    text = "标题\n\n详细内容长内容..." * 3 + "\n\n页 1"
    paras = bm25.split_into_passages(text)
    # 应该至少 2 段 (标题 / 内容 / 页 1 各成行 — 至少不全合并)
    assert len(paras) >= 2


# ============================================================
# Query 切词
# ============================================================


def test_tokenize_empty() -> None:
    assert bm25.tokenize_query("") == []
    assert bm25.tokenize_query("   ") == []


def test_tokenize_english_lowercase() -> None:
    tokens = bm25.tokenize_query("Termination Clause AND Renewal")
    assert "termination" in tokens
    assert "clause" in tokens
    assert "and" in tokens
    assert "renewal" in tokens


def test_tokenize_chinese_bigram_window() -> None:
    """中文按 2-char window 切, 含单字兜底"""
    tokens = bm25.tokenize_query("终止条件")
    # 应有 2-char window
    assert "终止" in tokens
    assert "止条" in tokens
    assert "条件" in tokens
    # 单字兜底也在
    assert "终" in tokens


def test_tokenize_mixed_chinese_english() -> None:
    tokens = bm25.tokenize_query("KPI 考核制度")
    assert "kpi" in tokens
    assert "考核" in tokens
    assert "核制" in tokens
    assert "制度" in tokens


def test_tokenize_dedups_keeping_order() -> None:
    tokens = bm25.tokenize_query("kpi KPI 考核 考核")
    assert tokens.count("kpi") == 1
    assert tokens.count("考核") == 1


def test_tokenize_caps_at_max() -> None:
    long = " ".join(f"word{i}" for i in range(100))
    tokens = bm25.tokenize_query(long)
    assert len(tokens) <= bm25.QUERY_MAX_TOKENS


# ============================================================
# 段落打分
# ============================================================


def test_score_no_match_returns_zero() -> None:
    assert bm25.score_passage("天气好出去玩", ["合同", "终止"]) == 0.0


def test_score_single_match_positive() -> None:
    s = bm25.score_passage("本合同的终止应书面通知.", ["终止"])
    assert s > 0


def test_score_more_tokens_matched_higher() -> None:
    """命中更多 query tokens 的段应分更高 (coverage_bonus)"""
    p1 = "本合同终止条件要协商."
    p2 = "本合同的甲乙双方."
    tokens = bm25.tokenize_query("终止 条件")
    s1 = bm25.score_passage(p1, tokens)
    s2 = bm25.score_passage(p2, tokens)
    assert s1 > s2


def test_score_length_normalization_favors_concise() -> None:
    """同样 1 次命中, 短段应分更高 (长度归一化)"""
    short = "终止合同需要书面通知."
    long = "终止合同需要书面通知. " + ("aaaaaa " * 200)
    tokens = bm25.tokenize_query("终止")
    assert bm25.score_passage(short, tokens) > bm25.score_passage(long, tokens)


# ============================================================
# query_top_k 整流程
# ============================================================


def test_empty_passages_returns_empty() -> None:
    hits, strategy = bm25.query_top_k([], "anything", top_k=5)
    assert hits == []
    assert strategy == "empty"


def test_empty_query_returns_first_top_k() -> None:
    paras = [f"段落 {i} 内容." for i in range(10)]
    hits, strategy = bm25.query_top_k(paras, "", top_k=3)
    assert strategy == "empty"
    assert len(hits) == 3
    assert hits[0]["ord"] == 0


def test_returns_relevant_passages_chinese_2char() -> None:
    """关键测试: 央企公文 2-char 中文词必须能命中 (trigram FTS5 不行, TF-score 行)"""
    paras = [
        "本合同自双方签字盖章之日起生效, 至 2027 年 12 月 31 日终止.",
        "甲方应在每月 5 日前向乙方支付服务费.",
        "如出现违约, 守约方有权解除合同并要求赔偿.",
        "本合同的解除应当书面通知, 对方在 30 日内回复.",
        "其他不相关的段落: 天气好, 出去玩.",
    ]
    hits, strategy = bm25.query_top_k(paras, "合同 解除", top_k=3)
    assert strategy == "tf_score"
    assert len(hits) >= 1
    # 至少前 3 命中应包含 "合同" 或 "解除"
    top_texts = " ".join(h["text"] for h in hits[:3])
    assert "合同" in top_texts and "解除" in top_texts
    # 不相关段不应在 top-1
    assert "天气好" not in hits[0]["text"]


def test_returns_relevant_for_kpi_chinese_mix() -> None:
    paras = [
        "本规章规定 KPI 考核制度, 每季度评一次.",
        "员工请假管理: 病假 / 事假 / 婚假 流程.",
        "财务报销: 滴滴出行电子发票上传.",
    ]
    hits, _ = bm25.query_top_k(paras, "KPI 考核", top_k=2)
    assert len(hits) >= 1
    assert "KPI" in hits[0]["text"] and "考核" in hits[0]["text"]


def test_no_match_fallback_returns_first_passages() -> None:
    """query 完全不命中任何段, 返开头几段兜底 (PDF 封面常含目的)"""
    paras = [f"段 {i} aaa." for i in range(5)]
    hits, strategy = bm25.query_top_k(paras, "完全不可能命中的查询词xyz", top_k=2)
    # 中文 query 切成 ['完全', '全不', ...] 单字, "不" "能" 等可能在段中没有, 走兜底
    # 这里测试: 即使 0 命中, 也应有兜底返开头
    assert strategy == "empty"
    assert len(hits) == 2
    assert hits[0]["ord"] == 0


def test_top_k_caps_results() -> None:
    paras = [f"section {i}: contains keyword bingo." for i in range(20)]
    hits, _ = bm25.query_top_k(paras, "bingo", top_k=5)
    assert len(hits) <= 5


def test_score_descending_order() -> None:
    paras = [
        "无关内容",
        "关键词 1 次出现",
        "关键词 关键词 关键词 (3 次出现)",
        "关键词 关键词 (2 次出现)",
    ]
    hits, _ = bm25.query_top_k(paras, "关键词", top_k=3)
    # 3次 > 2次 > 1次 > 0次
    scores = [h["score"] for h in hits]
    assert scores == sorted(scores, reverse=True)


# ============================================================
# CLI 端到端
# ============================================================


SCRIPT = Path(__file__).parent / "attachment_bm25.py"


def test_cli_missing_text_path() -> None:
    res = subprocess.run(
        [sys.executable, str(SCRIPT), "--text-path", "/nonexistent", "--query", "x"],
        capture_output=True, text=True,
    )
    out = json.loads(res.stdout)
    assert "error" in out
    assert "不存在" in out["error"]


def test_cli_basic_query(tmp_path: Path) -> None:
    """端到端: 写一个 60KB 假合同 (双字节 char), 跑 helper 查关键词"""
    sidecar = tmp_path / "fake.parsed.txt"
    paragraphs = []
    for i in range(120):
        # 每段 ~600 byte
        paragraphs.append(f"第{i}条 关于本合同的细则规定如下: " + ("条款描述内容 " * 40))
    paragraphs[15] = "第15条 合同终止条件: 双方协商一致即可解除."
    paragraphs[42] = "第42条 终止 违约责任: 守约方有权要求赔偿合同终止."
    sidecar.write_text("\n\n".join(paragraphs), encoding="utf-8")
    assert sidecar.stat().st_size >= 50_000, f"fixture 才 {sidecar.stat().st_size} 字节, 需 ≥50KB"

    res = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--text-path", str(sidecar),
         "--query", "合同 终止",
         "--top-k", "3"],
        capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert out["query_strategy"] == "tf_score"
    assert out["total_passages"] == 120
    assert len(out["passages"]) == 3
    # 第 15 / 42 应在 top-3
    top_text = " ".join(p["text"] for p in out["passages"])
    assert "第15条" in top_text or "第42条" in top_text


def test_cli_empty_query_returns_first_passages(tmp_path: Path) -> None:
    sidecar = tmp_path / "f.parsed.txt"
    sidecar.write_text(
        "\n\n".join(f"段 {i} 内容. {'啊' * 60}" for i in range(10)),
        encoding="utf-8",
    )
    res = subprocess.run(
        [sys.executable, str(SCRIPT), "--text-path", str(sidecar), "--query", "", "--top-k", "2"],
        capture_output=True, text=True,
    )
    assert res.returncode == 0
    out = json.loads(res.stdout)
    assert out["query_strategy"] == "empty"
    assert len(out["passages"]) == 2
