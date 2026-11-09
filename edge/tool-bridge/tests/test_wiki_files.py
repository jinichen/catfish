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


def test_create_without_relation_is_active(tmp_path, monkeypatch):
    """9/24: 关系是可选的 —— 没写关系不再进待确认 (以前员工只能硬配一条)。"""
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    result = wiki_files.create_wiki_entry("entity", "独立实体", "正文", subtype="org")
    assert result["ok"] and "warning" not in result, result
    content = (tmp_path / result["rel_path"]).read_text(encoding="utf-8")
    assert "ontology_status: active" in content


def test_relation_to_pending_target_is_not_pending(tmp_path, monkeypatch):
    """9/24: 同一批互相引用 —— 目标还是待确认也算找得到, 不连锁 pending。"""
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    first = wiki_files.create_wiki_entry(
        "concept", "归档规则", "正文", subtype="rule",
        related=[{"name": "还没建的流程", "rel": "配套"}],
    )
    assert "unresolved_relation" in first["warning"], first
    second = wiki_files.create_wiki_entry(
        "concept", "版本控制流程", "正文", subtype="process",
        related=[{"name": "归档规则", "rel": "配套"}],
    )
    assert second["ok"] and "warning" not in second, second


def test_create_canonicalizes_relation_and_treats_fallback_as_untyped(tmp_path, monkeypatch):
    """9/17: rel 归一到 contracts/wiki_relation_vocab.json; 「关联」= 没类型 → pending。"""
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    parent = wiki_files.create_wiki_entry("concept", "顶级体系", "正文", subtype="system")
    assert parent["ok"]
    ok = wiki_files.create_wiki_entry(
        "entity", "市场部", "正文", subtype="department",
        related=[{"name": "顶级体系", "rel": "所属部门"}],
    )
    assert ok["ok"] and "warning" not in ok, ok
    text = (tmp_path / ok["rel_path"]).read_text(encoding="utf-8")
    assert '{name: "顶级体系", rel: "隶属"}' in text and "ontology_status: active" in text

    vague = wiki_files.create_wiki_entry(
        "entity", "销售部", "正文", subtype="department",
        related=[{"name": "顶级体系", "rel": "瞎编的"}],
    )
    assert vague["ok"] and "untyped_relation" in vague["warning"], vague
    text = (tmp_path / vague["rel_path"]).read_text(encoding="utf-8")
    assert 'rel: "关联"' in text and "ontology_status: pending" in text


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
    # 11/09 起不带 frontmatter 的覆写会保留原 frontmatter, 所以这里用标了 tombstone 的空壳
    r = wiki_files.update_wiki_file("wiki/entities/待清空.md", "---\ntombstone: true\n---\n")
    assert r["ok"]
    assert r["visible_in_tab"] is False
    assert "warning" in r and "资料库会把它隐藏" in r["warning"], r


def test_update_without_frontmatter_keeps_the_old_one(tmp_path, monkeypatch):
    """11/09「AAA信用等级证书」: 小鲶换证时整篇覆写只给了正文, 标题 / 类型 / 标签全丢, 掉进「未分类」。"""
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    wiki_files.create_wiki_entry("entity", "AAA信用等级证书", "旧证 2025。", subtype="cert", tags=["专项体系"])
    rel = "wiki/entities/AAA信用等级证书.md"
    r = wiki_files.update_wiki_file(rel, "# AAA信用等级证书\n\n新证 2026-10-22 换发。\n")
    assert r["ok"] and "保留原来的" in r["warning"], r
    got = wiki_files.read_wiki_file(rel)
    fm, body = wiki_files._split_frontmatter(got["content"])
    assert wiki_files._parse_field(fm, "entity_type") == "cert" and "专项体系" in fm
    assert "新证 2026-10-22 换发" in body and "旧证" not in body
    # 带了 frontmatter 的照旧整篇覆写 (改类型就是这么改的)
    r2 = wiki_files.update_wiki_file(rel, "---\ntype: entity\ntitle: AAA信用等级证书\nentity_type: doc\n---\n\n正文\n")
    assert r2["ok"] and "保留原来的" not in (r2.get("warning") or "")
    fm2, _ = wiki_files._split_frontmatter(wiki_files.read_wiki_file(rel)["content"])
    assert wiki_files._parse_field(fm2, "entity_type") == "doc"


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


def test_create_refuses_title_that_is_existing_alias(tmp_path, monkeypatch):
    """9/24「福富」: 标题已是别的条目的别名 → 同一个东西, 让它去更新那一条。"""
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    ents = tmp_path / "wiki" / "entities"
    ents.mkdir(parents=True)
    (ents / "company.md").write_text(
        '---\ntype: entity\ntitle: 中电福富信息科技有限公司\nentity_type: org\naliases: ["中电福富", "福富"]\n---\n\n公司。\n',
        encoding="utf-8",
    )
    r = wiki_files.create_wiki_entry("entity", "福富", "中标单位", subtype="org")
    assert not r["ok"] and r["rel_path"] == "wiki/entities/company.md", r
    assert not (ents / "福富.md").exists()


def test_update_drops_alias_claimed_by_another_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    ents = tmp_path / "wiki" / "entities"
    ents.mkdir(parents=True)
    (ents / "company.md").write_text(
        '---\ntype: entity\ntitle: 中电福富信息科技有限公司\nentity_type: org\naliases: ["中电福富"]\n---\n\n公司。\n',
        encoding="utf-8",
    )
    (ents / "group.md").write_text("---\ntype: entity\ntitle: 某集团\nentity_type: org\n---\n\n旧。\n", encoding="utf-8")
    r = wiki_files.update_wiki_file(
        "wiki/entities/group.md",
        '---\ntype: entity\ntitle: 某集团\nentity_type: org\naliases: ["中电福富", "某集团公司"]\n---\n\n新。\n',
    )
    assert r["ok"] and "中电福富" in r["warning"], r
    text = (ents / "group.md").read_text(encoding="utf-8")
    assert 'aliases: ["某集团公司"]' in text, text


def test_员工登记的自定义关系不被归成关联(tmp_path, monkeypatch):
    """10/23: 知识体系「关系类型」里登记过的词, 写入时照原样保留; 没登记的照旧归「关联」。"""
    import json as _json
    from catfish_tool_bridge import wiki_files as wf
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    assert wf._canonical_relation("挂靠") == "关联"
    (tmp_path / "wiki_relation_vocab.custom.json").write_text(
        _json.dumps({"relations": {"挂靠": "本条目挂靠在对方名下"}}, ensure_ascii=False), encoding="utf-8")
    assert wf._canonical_relation("挂靠") == "挂靠"
    assert wf._canonical_relation("办理中") == "在办"


def test_update_normalizes_relation_words_like_create(tmp_path, monkeypatch):
    """11/09: 整篇覆写写进了词表外的「被依据」「涉及」; 现在跟新建一样归一 (别名 → 词表词, 表外 → 关联)。"""
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    wiki_files.create_wiki_entry("entity", "一级建造师持证人员名单", "名单。", subtype="doc")
    rel = "wiki/entities/一级建造师持证人员名单.md"
    fm = ('---\ntype: entity\ntitle: 一级建造师持证人员名单\nentity_type: doc\n'
          'related: [{name: "集成能力中心", rel: "涉及"}, {name: "中电福富", rel: "名单"}, {name: "张三", rel: "记录"}]\n---\n\n正文\n')
    assert wiki_files.update_wiki_file(rel, fm)["ok"]
    got = wiki_files._parse_related(wiki_files._split_frontmatter(wiki_files.read_wiki_file(rel)["content"])[0])
    assert got == [{"name": "集成能力中心", "rel": "关联"}, {"name": "中电福富", "rel": "记录"}, {"name": "张三", "rel": "记录"}]
    # 都在词表里: 原样不动
    same = fm.replace('rel: "涉及"', 'rel: "记录"').replace('rel: "名单"', 'rel: "记录"')
    from catfish_tool_bridge import wiki_guard
    assert wiki_guard.canon_related(same) == same


def test_create_title_with_fullwidth_parens_and_plus(tmp_path, monkeypatch):
    """11/09: 「福富增值业务订购关系管理平台（VSOP）应用软件」建不出来 (slug 含非法字符 '（'); 跟 Rust entry_slug 对齐。"""
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    for title, slug in [("福富增值业务订购关系管理平台（VSOP）应用软件", "福富增值业务订购关系管理平台-VSOP-应用软件"),
                        ("AI+政务产品包", "AI-政务产品包"), ("3+5+1专项团队", "3-5-1专项团队")]:
        r = wiki_files.create_wiki_entry("entity", title, "正文足够长, 不会被当墓碑。", subtype="software")
        assert r["ok"], r
        assert r["rel_path"] == f"wiki/entities/{slug}.md"
        assert f"title: {title}" in wiki_files.read_wiki_file(r["rel_path"])["content"]
    assert wiki_files._slugify("+++") == "untitled"
