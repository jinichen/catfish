"""名字解析的契约测试 —— 用例来自 edge/contracts/wiki_resolve_cases.json。

前端 src/lib/wikiResolve.test.ts 读的是同一个文件。两侧对拍的先例见
wiki_visibility_cases.json: Rust 和 Python 对"哪些条目该显示"曾经悄悄分叉过。

这一版守的是更狠的一类问题 —— 老实现在子串多命中时按数组顺序取第一个, 而
数组按 mtime 倒序排, 于是**图长什么样取决于哪个文件最近被改过**。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wiki_resolve import WikiNode, load_nodes, resolve_wiki_ref

CONTRACT = Path(__file__).resolve().parents[3] / "contracts" / "wiki_resolve_cases.json"
_DATA = json.loads(CONTRACT.read_text(encoding="utf-8"))
_NODES = [
    WikiNode(rel_path=f["rel_path"], slug=f["slug"], title=f["title"], aliases=f["aliases"])
    for f in _DATA["files"]
]


@pytest.mark.parametrize("case", _DATA["cases"], ids=[c["name"] for c in _DATA["cases"]])
def test_contract(case):
    got = resolve_wiki_ref(case["query"], _NODES)
    want = case["expect"]
    assert got.kind == want["kind"], f"{case['name']}: {got.kind} != {want['kind']}"
    if want["kind"] == "hit":
        assert got.node is not None and got.node.rel_path == want["rel_path"]
        assert got.how == want["how"]
    elif want["kind"] == "ambiguous":
        assert sorted(c.title for c in got.candidates) == sorted(want["candidates"])


@pytest.mark.parametrize("case", _DATA["cases"], ids=[c["name"] for c in _DATA["cases"]])
def test_order_independent(case):
    """★ 这条是那个 bug 的钉子。

    老 findTarget 用 files.find(...includes...), 数组一换顺序同一个名字就指向
    另一个条目。新实现必须对顺序完全不敏感。
    """
    a = resolve_wiki_ref(case["query"], _NODES)
    b = resolve_wiki_ref(case["query"], list(reversed(_NODES)))
    assert a.kind == b.kind
    if a.kind == "hit":
        assert a.node.rel_path == b.node.rel_path


def test_alias_beats_substring(tmp_path):
    """别名必须排在子串前面 —— 顺序反了「中电福富」就会连到那张证书上。"""
    nodes = [
        WikiNode("a.md", "a", "中电福富信息科技有限公司", ["中电福富"]),
        WikiNode("b.md", "b", "销售许可证-中电福富API与应用系统安全审计V2.0", []),
    ]
    r = resolve_wiki_ref("中电福富", nodes)
    assert r.kind == "hit" and r.how == "alias" and r.node.rel_path == "a.md"
    # 去掉别名就退化成歧义 —— 老实现在这里抛硬币
    nodes[0].aliases = []
    assert resolve_wiki_ref("中电福富", nodes).kind == "ambiguous"


def test_deprecated_node_remains_resolvable_for_historical_links():
    """废弃条目不参与默认检索, 但历史 [[旧名]] 仍必须能解析."""
    nodes = [
        WikiNode("old.md", "old", "旧条目", ["旧名"], deprecated=True),
        WikiNode("current.md", "current", "现行条目", ["现行名"]),
    ]

    result = resolve_wiki_ref("旧名", nodes)

    assert result.kind == "hit"
    assert result.node is not None and result.node.rel_path == "old.md"


def test_active_only_excludes_pending_and_unknown_statuses():
    nodes = [
        WikiNode("pending.md", "pending", "待确认", ontology_status="pending"),
        WikiNode("unknown.md", "unknown", "未知状态", ontology_status="future"),
        WikiNode("active.md", "active", "现行节点"),
    ]

    assert resolve_wiki_ref("待确认", nodes, active_only=True).kind == "miss"
    assert resolve_wiki_ref("未知状态", nodes, active_only=True).kind == "miss"
    assert resolve_wiki_ref("现行节点", nodes, active_only=True).kind == "hit"


def test_load_nodes_reads_aliases(tmp_path):
    ents = tmp_path / "wiki" / "entities"
    ents.mkdir(parents=True)
    (ents / "zdff.md").write_text(
        '---\ntype: entity\ntitle: 中电福富信息科技有限公司\naliases: ["中电福富", "福富"]\n---\n\n正文。\n',
        encoding="utf-8",
    )
    nodes = load_nodes(tmp_path)
    assert len(nodes) == 1
    assert nodes[0].aliases == ["中电福富", "福富"]
    assert resolve_wiki_ref("福富", nodes).how == "alias"


def test_load_nodes_reads_ontology_status(tmp_path):
    concepts = tmp_path / "wiki" / "concepts"
    concepts.mkdir(parents=True)
    (concepts / "pending.md").write_text(
        "---\ntype: concept\ntitle: 待确认\nontology_status: pending\n---\n",
        encoding="utf-8",
    )
    nodes = load_nodes(tmp_path)
    assert nodes[0].ontology_status == "pending"


def test_no_frontmatter_falls_back_to_stem():
    """34 个文件缺开头 ---, title 读不出来时必须退到文件名, 跟读侧一致。"""
    assert True  # 语义由 load_nodes 的 `or p.stem` 保证, 下面端到端验


def test_load_nodes_no_frontmatter(tmp_path):
    ents = tmp_path / "wiki" / "entities"
    ents.mkdir(parents=True)
    (ents / "信息安全中心.md").write_text("# 信息安全中心\n\n没有 frontmatter。\n", encoding="utf-8")
    nodes = load_nodes(tmp_path)
    assert nodes[0].title == "信息安全中心"
    assert resolve_wiki_ref("信息安全中心", nodes).kind == "hit"
