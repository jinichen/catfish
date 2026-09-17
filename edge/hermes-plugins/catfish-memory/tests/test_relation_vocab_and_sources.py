"""9/17: related[].rel 闭合词表 + sources 粒度 —— 写入侧真验, 不只在 prompt 里要求。

# 背景 (看 semantica 项目后对照本机 wiki 量出来的)

    500 多条 related 里 418 条 rel=「关联」(84%)  —— 只知道有关系, 不知道是什么关系
    51 篇 sources 还是 `employee_journal`           —— 等于没写来源

prompt 从 7/9、8/4 起就要求 `journal:YYYY-MM-DD` 和带类型的 rel, 但写入侧从没验过。
"要求了但不验证"跟"没要求"的结果一样, 8/4 test_ontology.py 已经说过一次了。

# 这个文件钉什么

  ① rel 归一到 contracts/wiki_relation_vocab.json; 别名归正; 表外 → 兜底「关联」
  ② 「关联」不算有类型 → ontology_status pending
  ③ sources 只收 journal:日期 / raw/sources/<stem> / manual; employee_journal 丢
  ④ 日期要真出现在本轮日志里, 编的丢; 空了按本轮实际读过的资料回填
  ⑤ merge 并进来的老 employee_journal 也被清掉
  ⑥ 三条产线读的是同一份词表 (Python / Rust include_str / TS 测试钉)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from catfish_memory_helpers import (
    Provenance,
    _canon_relation,
    _canon_source,
    _classify_ontology_status,
    _is_untyped_relation,
    _normalize_relations,
    _normalize_sources,
    _write_wiki_files,
    report_ontology_gaps,
)

_CONTRACT = Path(__file__).resolve().parents[3] / "contracts" / "wiki_relation_vocab.json"


def _fm(related: str, sources: str = '["journal:2026-07-14"]') -> str:
    return (
        "---\ntype: entity\ntitle: X\nentity_type: doc\naliases: []\n"
        f"related: [{related}]\nsources: {sources}\n---\n\n正文。\n"
    )


# ─────────────────────────────────────────────────────────────
# ① 词表
# ─────────────────────────────────────────────────────────────


def test_vocab_file_is_well_formed():
    d = json.loads(_CONTRACT.read_text(encoding="utf-8"))
    assert d["fallback"] == "关联"
    assert "关联" not in d["relations"], "兜底词不能同时是正式关系词"
    for alias, target in d["aliases"].items():
        assert target in d["relations"] or target == d["fallback"], f"别名 {alias} 指向表外 {target}"
        assert alias not in d["relations"], f"{alias} 既是正式词又是别名"


@pytest.mark.parametrize(
    "raw,want",
    [("隶属", "隶属"), ("所属部门", "隶属"), ("负责人", "负责"), ("协作部门", "协作"),
     ("持有主体", "持有"), ("采用口径", "依据"), ("同期项目", "同类"), ("对比", "对标")],
)
def test_canon_relation_maps_aliases(raw, want):
    assert _canon_relation(raw) == (want, True)


@pytest.mark.parametrize("raw", ["不同条目", "瞎编的", "related", "", '"隶属于"'])
def test_out_of_vocab_falls_back(raw):
    canon, known = _canon_relation(raw)
    if raw.strip('"') in ("隶属于",):
        assert (canon, known) == ("隶属", True)
    else:
        assert canon == "关联"


def test_normalize_relations_rewrites_only_rel(caplog):
    c = _fm('{name: "中电福富", rel: "所属部门"}, {name: "A", rel: "瞎编"}, "[[B]]"')
    with caplog.at_level("WARNING"):
        out = _normalize_relations("wiki/entities/x.md", c)
    assert '{name: "中电福富", rel: "隶属"}' in out
    assert '{name: "A", rel: "关联"}' in out
    assert '"[[B]]"' in out, "裸 wikilink 不动"
    assert "瞎编" in caplog.text and "wiki_relation_vocab.json" in caplog.text


def test_normalize_relations_leaves_good_alone():
    c = _fm('{name: "A", rel: "隶属"}, {name: "B", rel: "持有"}')
    assert _normalize_relations("wiki/entities/x.md", c) == c


# ─────────────────────────────────────────────────────────────
# ② 「关联」= 没类型
# ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "item,untyped",
    [('{name: "A", rel: "隶属"}', False), ('{name: "A", rel: "关联"}', True),
     ('{name: "A"}', True), ('[[A]]', True), ('{name: "A", rel: ""}', True)],
)
def test_is_untyped_relation(item, untyped):
    assert _is_untyped_relation(item) is untyped


def test_fallback_relation_makes_entry_pending(tmp_path):
    (tmp_path / "wiki" / "entities").mkdir(parents=True)
    (tmp_path / "wiki/entities/a.md").write_text(
        "---\ntype: entity\nontology_status: active\ntitle: A\nentity_type: org\n---\n\n正文。\n",
        encoding="utf-8",
    )
    status, reasons = _classify_ontology_status(
        tmp_path, "wiki/entities/x.md", _fm('{name: "A", rel: "关联"}')
    )
    assert status == "pending" and "untyped_relation" in reasons


def test_fallback_relation_is_reported_as_gap():
    gaps = report_ontology_gaps("wiki/entities/x.md", _fm('{name: "A", rel: "关联"}, {name: "B", rel: "隶属"}'))
    assert any("1/2" in g for g in gaps), gaps


# ─────────────────────────────────────────────────────────────
# ③ ④ sources
# ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,want",
    [("journal:2026-07-14", "journal:2026-07-14"), ("journal: 2026-07-14", "journal:2026-07-14"),
     ("2026-07-14", "journal:2026-07-14"), ("manual", "manual"),
     ("raw/sources/1783325914-企业资质列表(4)", "raw/sources/1783325914-企业资质列表(4)"),
     ("wiki/raw/sources/abc.md", "raw/sources/abc"), ("raw\\sources\\abc.md", "raw/sources/abc"),
     ("employee_journal", None), ("", None), ("随便写的", None), ("journal:2026-7-1", None)],
)
def test_canon_source(raw, want):
    assert _canon_source(raw) == want


def test_sources_drop_generic_and_fabricated_dates(caplog):
    prov = Provenance(journal_dates={"2026-07-14", "2026-07-16"}, raw_sources={"周报-0529"})
    c = _fm('{name: "A", rel: "隶属"}',
            '[employee_journal, "journal:2026-07-14", "journal:2031-01-01", "raw/sources/周报-0529.md"]')
    with caplog.at_level("WARNING"):
        out = _normalize_sources("wiki/entities/x.md", c, prov)
    assert 'sources: ["journal:2026-07-14", "raw/sources/周报-0529"]' in out
    assert "employee_journal" in caplog.text and "2031-01-01" in caplog.text


def test_sources_backfilled_from_run_when_empty():
    prov = Provenance.from_run(
        "## [2026-07-14 09:00] journal | a\n\nx\n## [2026-07-16 10:00] journal | b\n\ny\n",
        [Path("/x/wiki/raw/sources/1783-资质.md")],
    )
    c = _fm('{name: "A", rel: "隶属"}', "[employee_journal]")
    out = _normalize_sources("wiki/entities/x.md", c, prov)
    assert 'sources: ["journal:2026-07-14", "journal:2026-07-16", "raw/sources/1783-资质"]' in out


def test_sources_without_provenance_only_normalizes_format():
    """merge 之后那次调用: 不校验日期、不回填 —— 老条目的历史来源不能被这一轮否定。"""
    c = _fm('{name: "A", rel: "隶属"}', '[employee_journal, "journal:2025-01-01"]')
    out = _normalize_sources("wiki/entities/x.md", c, None)
    assert 'sources: ["journal:2025-01-01"]' in out


def test_missing_sources_line_gets_added_when_provenance_known():
    c = "---\ntype: entity\ntitle: X\nentity_type: doc\n---\n\n正文。\n"
    out = _normalize_sources("wiki/entities/x.md", c, Provenance(journal_dates={"2026-08-15"}))
    assert 'sources: ["journal:2026-08-15"]' in out


# ─────────────────────────────────────────────────────────────
# ⑤ 端到端: 写盘 + merge
# ─────────────────────────────────────────────────────────────


def test_write_path_enforces_both(tmp_path):
    (tmp_path / "wiki" / "entities").mkdir(parents=True)
    (tmp_path / "wiki/entities/a.md").write_text(
        "---\ntype: entity\nontology_status: active\ntitle: A\nentity_type: org\n---\n\n正文。\n",
        encoding="utf-8",
    )
    prov = Provenance(journal_dates={"2026-07-14"})
    _write_wiki_files(
        tmp_path,
        {"wiki/entities/x.md": _fm('{name: "A", rel: "所属部门"}', "[employee_journal]")},
        provenance=prov,
    )
    text = (tmp_path / "wiki/entities/x.md").read_text(encoding="utf-8")
    assert '{name: "A", rel: "隶属"}' in text
    assert 'sources: ["journal:2026-07-14"]' in text
    assert "ontology_status: active" in text


def test_merge_clears_legacy_employee_journal(tmp_path):
    """老条目带 employee_journal, 新内容带真日期 → 并集后只剩真日期。"""
    (tmp_path / "wiki" / "entities").mkdir(parents=True)
    (tmp_path / "wiki/entities/x.md").write_text(
        _fm('{name: "A", rel: "隶属"}', "[employee_journal]"), encoding="utf-8",
    )
    _write_wiki_files(
        tmp_path,
        {"wiki/entities/x.md": _fm('{name: "A", rel: "隶属"}', '["journal:2026-07-14"]')},
        provenance=Provenance(journal_dates={"2026-07-14"}),
    )
    text = (tmp_path / "wiki/entities/x.md").read_text(encoding="utf-8")
    assert "employee_journal" not in text
    assert '"journal:2026-07-14"' in text


# ─────────────────────────────────────────────────────────────
# ⑥ 三条产线同一份词表
# ─────────────────────────────────────────────────────────────


def test_rust_and_ts_read_the_same_contract():
    edge = _CONTRACT.parents[1]
    rust = (edge / "companion-app/src-tauri/src/commands/wiki_frontmatter.rs").read_text(encoding="utf-8")
    assert "wiki_relation_vocab.json" in rust, "Rust 侧没 include_str 这份词表"
    ts = (edge / "companion-app/src/tabs/Wiki/wikiRelationshipTasks.ts").read_text(encoding="utf-8")
    d = json.loads(_CONTRACT.read_text(encoding="utf-8"))
    for rel in d["relations"]:
        assert f'"{rel}"' in ts, f"TS DEFAULT_RELATION_TYPES 缺 {rel}"


# ─────────────────────────────────────────────────────────────
# ⑦ 冲突检测 (semantica 第 2 条): 不静默覆盖
# ─────────────────────────────────────────────────────────────

from catfish_memory_helpers import detect_conflicts, parse_conflicts, record_conflicts  # noqa: E402


def _note(subtype: str, rel_a: str, extra_fm: str = "") -> str:
    return (
        f"---\ntype: entity\ntitle: X\nentity_type: {subtype}\naliases: []\n"
        f'related: [{{name: "A", rel: "{rel_a}"}}, {{name: "B", rel: "持有"}}]\n'
        f'sources: ["journal:2026-07-14"]\n{extra_fm}---\n\n正文。\n'
    )


def test_detects_type_and_relation_conflicts_only():
    found = detect_conflicts(_note("org", "隶属"), _note("department", "协作"), "journal:2026-09-17")
    assert [(c["field"], c["current"], c["proposed"]) for c in found] == [
        ("entity_type", "org", "department"), ("rel:A", "隶属", "协作"),
    ]
    assert detect_conflicts(_note("org", "隶属"), _note("org", "隶属")) == []


def test_fallback_and_missing_relations_are_not_conflicts():
    """「关联」/裸 wikilink 是"还没定", 不是"定了不一样"; 新增关系也不是冲突。"""
    old = _note("org", "隶属")
    assert detect_conflicts(old, _note("org", "关联")) == []
    new = old.replace('{name: "B", rel: "持有"}', '{name: "C", rel: "使用"}')
    assert detect_conflicts(old, new) == []


def test_record_keeps_old_values_and_writes_conflicts_line(caplog):
    with caplog.at_level("WARNING"):
        out = record_conflicts("wiki/entities/x.md", _note("department", "协作"), _note("org", "隶属"), "journal:2026-09-17")
    assert "entity_type: org" in out
    assert '{name: "A", rel: "隶属"}' in out and '{name: "B", rel: "持有"}' in out
    conflicts = parse_conflicts(out.split("---")[1])
    assert conflicts == [
        {"field": "entity_type", "current": "org", "proposed": "department", "seen": "journal:2026-09-17"},
        {"field": "rel:A", "current": "隶属", "proposed": "协作", "seen": "journal:2026-09-17"},
    ]
    assert "保留旧值" in caplog.text


def test_record_carries_old_conflicts_and_clears_adopted_ones():
    old = _note("department", "隶属",
                'conflicts: [{field: "entity_type", current: "org", proposed: "department", seen: "j"}, '
                '{field: "rel:A", current: "隶属", proposed: "协作", seen: "j"}]\n')
    out = record_conflicts("x.md", _note("department", "隶属"), old)
    assert parse_conflicts(out.split("---")[1]) == [
        {"field": "rel:A", "current": "隶属", "proposed": "协作", "seen": "j"},
    ]


def test_no_conflicts_means_no_conflicts_line():
    out = record_conflicts("x.md", _note("org", "隶属"), _note("org", "隶属"))
    assert "conflicts:" not in out


def test_write_path_does_not_silently_overwrite(tmp_path):
    """端到端: 第二次蒸馏换了类型和关系 → 盘上还是旧值 + conflicts 字段。"""
    (tmp_path / "wiki" / "entities").mkdir(parents=True)
    for name in ("a", "b"):
        (tmp_path / f"wiki/entities/{name}.md").write_text(
            f"---\ntype: entity\nontology_status: active\ntitle: {name.upper()}\nentity_type: org\n---\n\n正文。\n",
            encoding="utf-8",
        )
    prov = Provenance(journal_dates={"2026-07-14"})
    _write_wiki_files(tmp_path, {"wiki/entities/x.md": _note("org", "隶属")}, provenance=prov)
    _write_wiki_files(tmp_path, {"wiki/entities/x.md": _note("department", "协作")}, provenance=prov)
    text = (tmp_path / "wiki/entities/x.md").read_text(encoding="utf-8")
    assert "entity_type: org" in text and '{name: "A", rel: "隶属"}' in text
    assert 'conflicts: [{field: "entity_type", current: "org", proposed: "department"' in text
    assert 'rel:A' in text


def test_write_path_old_chinese_type_is_not_a_false_conflict(tmp_path):
    (tmp_path / "wiki" / "concepts").mkdir(parents=True)
    (tmp_path / "wiki/concepts/x.md").write_text(
        "---\ntype: concept\ntitle: X\nconcept_type: 规则\n---\n\n正文。\n", encoding="utf-8",
    )
    _write_wiki_files(tmp_path, {"wiki/concepts/x.md": "---\ntype: concept\ntitle: X\nconcept_type: rule\n---\n\n新正文。\n"})
    assert "conflicts:" not in (tmp_path / "wiki/concepts/x.md").read_text(encoding="utf-8")
