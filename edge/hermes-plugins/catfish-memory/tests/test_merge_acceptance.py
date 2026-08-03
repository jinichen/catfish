"""P19 LLM merge 的输出必须过验收, 不合格落回 P18。

# 这组测试守的是什么 (8/4)

P19 把整个文件交给 LLM 重写, 拿到就写盘, 一行校验都没有。而它只在**更新已有
条目**时触发 —— 一个条目被更新 N 次就被 LLM 重写 N 次。

8/4 实测后果: 255 条里 19 条 frontmatter 丢了开头的 `---`, 读侧一个字段都读不
到, title 退化成文件名, tags/related 失效 —— 那些条目在图谱里是没有连线的孤岛。

P18 regex merge 反而更安全: frontmatter list 取并集、created 保留旧、旧 body 转
`<!-- legacy body -->` 注释留在文末。确定性, 信息不丢。

所以不砍 P19, 给它加验收。回退机制本来就有 (merged 为假 → 不进 ok_paths →
写盘走 P18), 这里把"LLM 调用失败"扩展成"LLM 输出不合格"。

三条检查都来自实际事故形态, 不是想象出来的。
"""

from __future__ import annotations

import pytest

from catfish_memory_helpers import _accept_llm_merge


OLD = (
    "---\n"
    "type: entity\n"
    "title: 中电福富\n"
    "created: 2026-06-01\n"
    "tags: [公司, 中电系]\n"
    'related: ["[[资质对标分析流程]]", "[[北京福富]]"]\n'
    "---\n\n"
    "# 中电福富\n\n" + "旧正文。" * 40 + "\n"
)
NEW = (
    "---\n"
    "type: entity\n"
    "title: 中电福富\n"
    "created: 2026-08-04\n"
    "tags: [公司]\n"
    'related: ["[[资质对标分析流程]]"]\n'
    "---\n\n"
    "# 中电福富\n\n" + "新正文。" * 40 + "\n"
)


def _ok_merged(**over) -> str:
    """一份合格的合并结果: frontmatter 完整 + 旧 list 字段都在 + 正文没缩水。"""
    tags = over.get("tags", "[公司, 中电系]")
    related = over.get("related", '["[[资质对标分析流程]]", "[[北京福富]]"]')
    body = over.get("body", "合并后的正文。" * 40)
    return (
        "---\n"
        "type: entity\n"
        "title: 中电福富\n"
        "created: 2026-06-01\n"
        f"tags: {tags}\n"
        f"related: {related}\n"
        "---\n\n"
        f"# 中电福富\n\n{body}\n"
    )


def test_accepts_good_merge():
    assert _accept_llm_merge("wiki/entities/x.md", OLD, NEW, _ok_merged()) is not None


def test_rejects_empty():
    for bad in ("", "   \n"):
        assert _accept_llm_merge("wiki/entities/x.md", OLD, NEW, bad) is None


def test_rejects_missing_frontmatter_fence(caplog):
    """8/4 那 19 个坏文件的形态: 少了开头的 ---。"""
    bad = _ok_merged().lstrip("-").lstrip("\n")   # 砍掉开头的 ---
    with caplog.at_level("WARNING"):
        assert _accept_llm_merge("wiki/entities/x.md", OLD, NEW, bad) is None
    assert "frontmatter" in caplog.text


def test_rejects_when_old_related_lost(caplog):
    """related 是并集语义, 丢了 → 图谱里连线消失, 实体掉进未分类。"""
    bad = _ok_merged(related='["[[资质对标分析流程]]"]')   # 丢了 [[北京福富]]
    with caplog.at_level("WARNING"):
        assert _accept_llm_merge("wiki/entities/x.md", OLD, NEW, bad) is None
    assert "related" in caplog.text and "北京福富" in caplog.text


def test_rejects_when_old_tag_lost(caplog):
    bad = _ok_merged(tags="[公司]")   # 丢了 中电系
    with caplog.at_level("WARNING"):
        assert _accept_llm_merge("wiki/entities/x.md", OLD, NEW, bad) is None
    assert "tags" in caplog.text


def test_rejects_body_collapse(caplog):
    """LLM 偷懒把长文压成一句 —— P18 至少有 legacy 留底, P19 是直接覆盖。"""
    bad = _ok_merged(body="就一句话。")
    with caplog.at_level("WARNING"):
        assert _accept_llm_merge("wiki/entities/x.md", OLD, NEW, bad) is None
    assert "缩到" in caplog.text


def test_allows_moderate_shrink():
    """阈值保守是故意的 —— 宁可放过不完美的合并, 也别频繁退回 P18 让叙述碎掉。"""
    baseline = max(len(OLD), len(NEW))
    body = "还行的正文。" * 30          # 明显超过 40% 基线
    assert _accept_llm_merge("wiki/entities/x.md", OLD, NEW, _ok_merged(body=body)) is not None


def test_new_file_without_old_frontmatter_still_accepted():
    """旧文件没有 frontmatter (比如 8/4 之前那批坏文件) → 第 ② 条不适用, 别误杀。"""
    old_broken = "type: entity\ntitle: 中电福富\n---\n\n旧正文。\n"
    assert _accept_llm_merge("wiki/entities/x.md", old_broken, NEW, _ok_merged()) is not None


# ─────────────────────────────────────────────────────────────
# 禁用结论词扫描 —— 把 prompt 里的约束变成真会出声的检查
# ─────────────────────────────────────────────────────────────


def test_scan_flags_conclusion_words(caplog):
    from catfish_memory_helpers import _scan_conclusion_words
    bad = "---\ntype: entity\ntitle: X\n---\n\n因此我们决定采用方案 A。\n"
    with caplog.at_level("WARNING"):
        hits = _scan_conclusion_words("wiki/entities/x.md", bad)
    assert set(hits) == {"因此", "决定"}, hits
    assert "禁用结论词" in caplog.text


def test_scan_ignores_frontmatter():
    """只扫正文 —— frontmatter 里出现这些字不算 (比如 title 就叫「决定记录」)。"""
    from catfish_memory_helpers import _scan_conclusion_words
    ok = "---\ntype: entity\ntitle: 决定记录\n---\n\n这是一段平铺直叙的正文。\n"
    assert _scan_conclusion_words("wiki/entities/x.md", ok) == []


def test_scan_does_not_flag_common_words():
    """「影响」故意不在词表里 —— 中文里太常见, 误报会淹掉真信号。"""
    from catfish_memory_helpers import _scan_conclusion_words
    txt = "---\ntype: entity\ntitle: X\n---\n\n受影响的系统包括 A 和 B, 同时 C 也在范围内。\n"
    assert _scan_conclusion_words("wiki/entities/x.md", txt) == []


def test_scan_never_blocks_write(tmp_path):
    """只警告不拦 —— 内容必须照样写进去。"""
    from catfish_memory_helpers import _write_wiki_files
    bad = "---\ntype: entity\ntitle: X\n---\n\n因此这条被写进去了。\n"
    n_e, _ = _write_wiki_files(tmp_path, {"wiki/entities/x.md": bad})
    assert n_e == 1
    assert "因此这条被写进去了" in (tmp_path / "wiki/entities/x.md").read_text(encoding="utf-8")
