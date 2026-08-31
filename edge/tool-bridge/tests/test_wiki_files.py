"""catfish_wiki_list 的可见性必须跟知识体系 TAB 完全一致。

用例表不写在这里 —— 它在 edge/contracts/wiki_visibility_cases.json, 跟
Rust 侧 (companion-app/src-tauri/src/commands/wiki_read.rs 的
visibility_contract 测试) 共用同一份。

为什么这么安排: 两份实现讲同一件事就必然会漂, 而漂的后果比没有这个工具更坏 ——
工具说有、TAB 说没有, 鲶鱼会拿着一个不可信的真相源继续推理。判断表只留一份,
两边各自读它断言, 谁漂谁红。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from catfish_tool_bridge import wiki_files

# tests/ → tool-bridge/ → edge/  (parents[2])
CONTRACT = (
    Path(__file__).resolve().parents[2] / "contracts" / "wiki_visibility_cases.json"
)


def _load_cases():
    data = json.loads(CONTRACT.read_text(encoding="utf-8"))
    return data["cases"]


def _materialize(tmp_path: Path, cases) -> Path:
    """把用例表铺成一棵真实的 ~/.catfish 目录树。"""
    for c in cases:
        p = tmp_path / c["path"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(c["content"], encoding="utf-8")
    return tmp_path


def test_contract_file_exists():
    assert CONTRACT.is_file(), (
        f"契约表不见了: {CONTRACT}\n"
        "它是 Rust / Python 两边共用的唯一判断依据, 不能只删一边。"
    )


@pytest.mark.parametrize("case", _load_cases(), ids=lambda c: c["name"])
def test_visibility_matches_contract(case, tmp_path, monkeypatch):
    cases = _load_cases()
    home = _materialize(tmp_path, cases)
    monkeypatch.setenv("CATFISH_HOME", str(home))

    result = wiki_files.list_wiki_files(limit=1000)
    assert result["ok"], result
    by_rel = {item["rel_path"]: item for item in result["items"]}

    rel = case["path"]
    expect = case["expect"]

    if not expect["visible"]:
        assert rel not in by_rel, (
            f"{case['name']}\n"
            f"  这个文件不该出现在 TAB / catfish_wiki_list 里, 但它出现了。\n"
            f"  路径: {rel}"
        )
        return

    assert rel in by_rel, (
        f"{case['name']}\n"
        f"  这个文件该出现在 TAB / catfish_wiki_list 里, 但没有。\n"
        f"  路径: {rel}\n"
        f"  实际列出的: {sorted(by_rel)}"
    )
    got = by_rel[rel]
    assert got["title"] == expect["title"], (
        f"{case['name']}\n  title 不对: 期望 {expect['title']!r}, 实际 {got['title']!r}"
    )
    assert got["kind"] == expect["kind"], (
        f"{case['name']}\n  kind 不对: 期望 {expect['kind']!r}, 实际 {got['kind']!r}"
    )


# ─────────────────────────────────────────────────────────────
# create / read / update
# ─────────────────────────────────────────────────────────────


def test_create_then_visible_immediately(tmp_path, monkeypatch):
    """create 的全部意义: 写完立刻可见。

    8/3 那次员工要的就是这个, 而当时唯一的写入口 ingest 只能丢进 raw/sources/,
    TAB 从不读那里。
    """
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    r = wiki_files.create_wiki_entry(
        kind="entity",
        title="中电系资质对标对齐矩阵",
        body="153 项资质, 7 家公司。",
        subtype="资质",
        tags=["资质", "对标"],
    )
    assert r["ok"], r
    # 中文标题直接当文件名, 不转拼音 (对齐 wiki_write.rs 的 slugify)
    assert r["rel_path"] == "wiki/entities/中电系资质对标对齐矩阵.md", r

    listed = wiki_files.list_wiki_files()
    titles = [i["title"] for i in listed["items"]]
    assert "中电系资质对标对齐矩阵" in titles, listed


def test_create_without_relation_is_pending(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    result = wiki_files.create_wiki_entry("entity", "待确认实体", "正文", subtype="org")
    assert result["ok"] and result["warning"]
    content = (tmp_path / result["rel_path"]).read_text(encoding="utf-8")
    assert "ontology_status: pending" in content


def test_create_system_without_parent_is_active(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    result = wiki_files.create_wiki_entry("concept", "顶级体系", "正文", subtype="system")
    assert result["ok"] and "warning" not in result
    content = (tmp_path / result["rel_path"]).read_text(encoding="utf-8")
    assert "ontology_status: active" in content


def test_related_parser_keeps_typed_relation_shape():
    frontmatter = "related: [{name: 中电福富, rel: 同一实体}, \"[[资质体系]]\"]"
    assert wiki_files._parse_related(frontmatter) == [
        {"name": "中电福富", "rel": "同一实体"},
        {"name": "资质体系", "rel": None},
    ]


def test_list_merges_body_links_like_companion(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    path = tmp_path / "wiki/entities/正文关系.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "---\ntype: entity\ntitle: 正文关系\nrelated: [{name: 已有节点, rel: 依据}]\n---\n\n正文 [[正文节点]]",
        encoding="utf-8",
    )
    item = wiki_files.list_wiki_files(limit=10)["items"][0]
    assert item["related"] == [
        {"name": "已有节点", "rel": "依据"},
        {"name": "正文节点", "rel": None},
    ]


def test_create_typed_relation_is_active_only_for_active_target(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    root = wiki_files.create_wiki_entry("concept", "根体系", "正文", subtype="system")
    assert root["ok"], root
    created = wiki_files.create_wiki_entry(
        "entity",
        "受管对象",
        "正文",
        subtype="org",
        related=[{"name": "根体系", "rel": "隶属"}],
    )
    assert created["ok"] and "warning" not in created, created
    pending = wiki_files.create_wiki_entry(
        "entity",
        "悬空对象",
        "正文",
        subtype="org",
        related=[{"name": "不存在的节点", "rel": "隶属"}],
    )
    assert pending["ok"] and "unresolved_relation" in pending["warning"], pending


def test_create_rejects_equivalent_duplicate(tmp_path, monkeypatch):
    """防 8/3 实际发生过的事: 同一份内容躺成两个文件。"""
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    assert wiki_files.create_wiki_entry("entity", "中电系 资质", "a", subtype="org")["ok"]
    dup = wiki_files.create_wiki_entry("entity", "中电系资质", "b", subtype="org")
    assert not dup["ok"]
    assert "等价条目已存在" in dup["error"], dup


def test_create_rejects_source_path_pseudo_concept(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    result = wiki_files.create_wiki_entry(
        "concept", "raw/sources/组织架构.md", "原始资料", subtype="system"
    )
    assert not result["ok"]
    assert "原始资料/文件路径" in result["error"]


def test_create_normalizes_type_and_adds_entity_aliases(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    result = wiki_files.create_wiki_entry(
        "entity", "一个部门", "部门正文", subtype="部门"
    )
    assert result["ok"], result
    content = (tmp_path / result["rel_path"]).read_text(encoding="utf-8")
    assert "entity_type: department" in content
    assert "aliases: []" in content


def test_create_rejects_source_path_relation(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    result = wiki_files.create_wiki_entry(
        "entity",
        "一个实体",
        "正文",
        subtype="org",
        related=["raw/sources/原始材料"],
    )
    assert not result["ok"]
    assert "sources" in result["error"]


def test_read_reports_tab_visibility(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    wiki_files.create_wiki_entry(
        "entity", "可见的", "正文足够长, 不会被当墓碑。", subtype="doc"
    )
    got = wiki_files.read_wiki_file("wiki/entities/可见的.md")
    assert got["ok"] and got["visible_in_tab"], got


def test_update_warns_when_it_becomes_invisible(tmp_path, monkeypatch):
    """覆写成空壳 → 命中墓碑规则被 TAB 隐藏。

    这种「写成功了但看不见」正是 8/3 那一轮的形状, 必须当场说出来, 不能等员工
    自己发现再来问一遍。
    """
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    wiki_files.create_wiki_entry("entity", "待清空", "原本有内容。", subtype="doc")
    r = wiki_files.update_wiki_file("wiki/entities/待清空.md", "\n")
    assert r["ok"]
    assert r["visible_in_tab"] is False
    assert "warning" in r and "TAB" in r["warning"], r


def test_update_refuses_missing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    r = wiki_files.update_wiki_file("wiki/entities/不存在.md", "x")
    assert not r["ok"] and "catfish_wiki_create" in r["error"], r


@pytest.mark.parametrize(
    "bad",
    [
        "../../.ssh/id_rsa",
        "wiki/../../../etc/passwd",
        "uploads/x.md",
        "/etc/passwd",
    ],
)
def test_path_traversal_blocked(bad, tmp_path, monkeypatch):
    """鲶鱼是自动调用的, 路径白名单要比人手点 UI 更严。"""
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    assert not wiki_files.read_wiki_file(bad)["ok"]
    assert not wiki_files.update_wiki_file(bad, "x")["ok"]
