"""11/08 蒸馏写知识库也过 tool-bridge 的把关: 名字「像」的并进已有页; 关系类型不对的降成「关联」等人定。
借不到 tool-bridge 时整体跳过 (生产里共 venv)。全部虚构。"""
from __future__ import annotations

import pytest

import catfish_memory_wiki_guard as g
from catfish_memory_helpers import _write_wiki_files

pytestmark = pytest.mark.skipif(not g.available(), reason="tool-bridge 不在这个 venv 里")


def _fm(title, body="正文。", subtype="department", related="[]", kind="entity"):
    tf = "entity_type" if kind == "entity" else "concept_type"
    return f"---\ntype: {kind}\ntitle: {title}\n{tf}: {subtype}\naliases: []\ntags: []\nrelated: {related}\nsources: [manual]\n---\n\n# {title}\n\n{body}\n"


@pytest.fixture()
def home(tmp_path, monkeypatch):
    (tmp_path / "wiki" / "entities").mkdir(parents=True)
    (tmp_path / "wiki" / "concepts").mkdir(parents=True)
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    return tmp_path


def test_名字像的_并进已有页_新名字成别名(home):
    (home / "wiki/entities/renliziyuanbu.md").write_text(_fm("人力资源部", "管招聘。"), encoding="utf-8")
    _write_wiki_files(home, {"wiki/entities/renlibu.md": _fm("人力部", "负责人张三。")})
    assert not (home / "wiki/entities/renlibu.md").exists()
    merged = (home / "wiki/entities/renliziyuanbu.md").read_text(encoding="utf-8")
    assert "title: 人力资源部" in merged and '"人力部"' in merged and "负责人张三" in merged


def test_类型不同的不并_完全同名的照旧走原来的规则(home):
    (home / "wiki/entities/hr.md").write_text(_fm("人力资源部"), encoding="utf-8")
    _write_wiki_files(home, {"wiki/entities/hrsys.md": _fm("人力资源系统", subtype="software")})
    assert (home / "wiki/entities/hrsys.md").exists()


def test_关系类型不对的_降成关联_进待确认(home):
    (home / "wiki/entities/chen.md").write_text(_fm("陈功", subtype="person"), encoding="utf-8")
    _write_wiki_files(home, {"wiki/entities/list.md": _fm("持证人员名单", subtype="roster",
                                                           related='[{name: "陈功", rel: "包含"}]')})
    txt = (home / "wiki/entities/list.md").read_text(encoding="utf-8")
    assert 'rel: "关联"' in txt and 'rel: "包含"' not in txt and "ontology_status: pending" in txt
    _write_wiki_files(home, {"wiki/entities/list2.md": _fm("另一份名单", subtype="roster",
                                                            related='[{name: "陈功", rel: "记录"}]')})
    assert 'rel: "记录"' in (home / "wiki/entities/list2.md").read_text(encoding="utf-8")
