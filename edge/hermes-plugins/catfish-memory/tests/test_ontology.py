"""受控词表 + typed relation + 引用完整性。

# 背景 (8/4 鸿波 "是不是应该用 ontology")

先量了真实数据, 结论是"本体已经存在, 只是没被约束":

    entity_type   cert 81 · person 13 · project 8 · org 6 · department 3
                  + notification/standard/data 各 1 · 空 33 (22%)
    concept_type  principle 18 · standard 18 · process 17 · rule 15
                  + 规则 1 · 标准 1 · 流程 1 · system 1

类型**自然收敛**了 (entity 前 5 类覆盖 111/147), 但没有约束就漂 ——
rule/规则、standard/标准、process/流程 中英文并存, 指的是同一个东西。
P3.5.176 删 enum 的理由是"enum 是硬编码", 结果不是更灵活, 是没有词汇表。

关系那一半更极端: 408 条 related 边, **0 条带类型**。查下来不是 LLM 不配合 ——
读侧 6/29 (P3.5.132 #5) 就支持 `{name, rel}` 了, 而**写侧 prompt 从头到尾没提过
rel**。功能建在读侧、写侧不知道, 等于没建。

# 差点上线的坑

准备让 prompt 开始产出 typed relation 时才发现: Python 侧 _parse_frontmatter_lists
是"regex 抓所有 quoted 字符串", 而 typed 形式 `{name: "X", rel: "隶属"}` 里有逗号
和两个 quoted 值 —— 切出来变成 ['X', '隶属'], **关系标签变成假节点名**, 合并时
会被写回 frontmatter, 图谱上凭空多出 dangling 边。

读侧 (wiki_read.rs::split_top_level) 一直是 brace-aware 的, 写侧不是。又是两侧
不同口径, 而且是在开关打开的前一刻才查出来的。
"""

from __future__ import annotations

import pytest

from catfish_memory_helpers import (
    _canon_subtype,
    _check_dangling_related,
    _merge_wiki_file,
    _normalize_types,
    _parse_frontmatter_lists,
    _rel_item_name,
    _split_top_level,
    _write_wiki_files,
)


# ─────────────────────────────────────────────────────────────
# ① 受控词表
# ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,want",
    [("规则", "rule"), ("标准", "standard"), ("流程", "process"),
     ("原则", "principle"), ("资质", "cert"), ("公司", "org"), ("员工", "person")],
)
def test_canon_maps_chinese_synonyms(raw, want):
    """实测数据里 rule/规则 并存 —— 归一化而不是拒绝, 不丢数据。"""
    canon, _ = _canon_subtype("wiki/concepts/x.md", raw)
    assert canon == want


def test_canon_keeps_known_english():
    assert _canon_subtype("wiki/entities/x.md", "cert")[0] == "cert"


def test_canon_allows_unknown_but_flags(caplog):
    """词表要能长 —— 不认识的放行, 但要有人知道。"""
    with caplog.at_level("WARNING"):
        canon, unknown = _canon_subtype("wiki/entities/x.md", "spaceship")
    assert canon == "spaceship" and unknown
    assert "词表外" in caplog.text


def test_canon_empty_is_not_an_error():
    assert _canon_subtype("wiki/entities/x.md", "") == ("", False)


def test_normalize_types_rewrites_frontmatter():
    src = "---\ntype: concept\ntitle: X\nconcept_type: 规则\n---\n\n正文。\n"
    out = _normalize_types("wiki/concepts/x.md", src)
    assert "concept_type: rule" in out
    assert "title: X" in out and "正文。" in out


def test_normalize_types_leaves_good_alone():
    src = "---\ntype: entity\ntitle: X\nentity_type: cert\n---\n\n正文。\n"
    assert _normalize_types("wiki/entities/x.md", src) == src


# ─────────────────────────────────────────────────────────────
# ② typed relation —— 差点上线的那个坑
# ─────────────────────────────────────────────────────────────


def test_split_top_level_does_not_cut_inside_braces():
    """回归钉子: typed 形式里的逗号不能当分隔符。"""
    inner = '{name: "中电福富", rel: "隶属"}, "[[北京福富]]", {name: "A", rel: "认证"}'
    assert _split_top_level(inner) == [
        '{name: "中电福富", rel: "隶属"}',
        '"[[北京福富]]"',
        '{name: "A", rel: "认证"}',
    ]


def test_parse_lists_keeps_typed_block_whole():
    """老实现在这里切出 ['中电福富', '隶属', ...] —— 「隶属」成了假节点。"""
    fm = 'related: [{name: "中电福富", rel: "隶属"}, "[[北京福富]]"]'
    got = _parse_frontmatter_lists(fm)["related"]
    assert got == ['{name: "中电福富", rel: "隶属"}', "[[北京福富]]"]
    assert "隶属" not in got, "关系标签被当成了独立条目"


@pytest.mark.parametrize(
    "item,want",
    [('{name: "中电福富", rel: "隶属"}', "中电福富"),
     ('"[[北京福富]]"', "北京福富"),
     ("[[北京福富]]", "北京福富"),
     ("中电福富", "中电福富")],
)
def test_rel_item_name(item, want):
    assert _rel_item_name(item) == want


def test_merge_dedupes_by_name_and_keeps_typed():
    """同一个节点带不带 rel 是同一条边 —— 按名字去重, 保留信息多的那个。"""
    old = '---\ntype: entity\ntitle: X\nrelated: ["[[中电福富]]"]\n---\n\n旧。\n'
    new = '---\ntype: entity\ntitle: X\nrelated: [{name: "中电福富", rel: "隶属"}]\n---\n\n新。\n'
    merged = _merge_wiki_file(old, new)
    assert merged.count("中电福富") == 1, "同一节点留了两份"
    assert 'rel: "隶属"' in merged, "带 rel 的那个该被保留 (信息更多)"


def test_merge_does_not_quote_typed_block():
    """渲染时 typed 块不能再包一层引号, 否则读侧解析不出来。"""
    old = '---\ntype: entity\ntitle: X\nrelated: [{name: "A", rel: "认证"}]\n---\n\n旧。\n'
    new = '---\ntype: entity\ntitle: X\nrelated: ["[[B]]"]\n---\n\n新。\n'
    merged = _merge_wiki_file(old, new)
    assert '"{name:' not in merged, "typed 块被多包了一层引号"


# ─────────────────────────────────────────────────────────────
# ③ 引用完整性
# ─────────────────────────────────────────────────────────────


def test_dangling_reported_not_deleted(tmp_path, caplog):
    """8/4 实测 65/408 (16%) 指向不存在的节点。

    只报告不删: dangling 有两种语义相反的成因 —— LLM 编的 (该删) 和还没蒸馏出来
    的 (删了破坏未来连接)。分不清就不该动。
    """
    ents = tmp_path / "wiki" / "entities"
    ents.mkdir(parents=True)
    (ents / "a.md").write_text("---\ntype: entity\ntitle: 已存在的\n---\n\n。\n", encoding="utf-8")
    content = (
        "---\ntype: entity\ntitle: X\n"
        'related: ["[[已存在的]]", "[[根本没有这个]]"]\n---\n\n正文。\n'
    )
    with caplog.at_level("INFO"):
        missing = _check_dangling_related(tmp_path, "wiki/entities/x.md", content)
    assert missing == ["根本没有这个"]
    assert "不自动删" in caplog.text


def test_dangling_check_never_blocks_write(tmp_path):
    (tmp_path / "wiki" / "entities").mkdir(parents=True)
    content = '---\ntype: entity\ntitle: X\nrelated: ["[[不存在]]"]\n---\n\n正文。\n'
    n_e, _ = _write_wiki_files(tmp_path, {"wiki/entities/x.md": content})
    assert n_e == 1
    assert (tmp_path / "wiki/entities/x.md").exists()


def test_write_path_normalizes_type(tmp_path):
    """端到端: 写盘时类型被归一化。"""
    (tmp_path / "wiki" / "concepts").mkdir(parents=True)
    _write_wiki_files(
        tmp_path,
        {"wiki/concepts/x.md": "---\ntype: concept\ntitle: X\nconcept_type: 流程\n---\n\n正文。\n"},
    )
    assert "concept_type: process" in (tmp_path / "wiki/concepts/x.md").read_text(encoding="utf-8")
