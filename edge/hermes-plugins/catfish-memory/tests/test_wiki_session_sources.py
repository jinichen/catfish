"""11/02 (借 DeepTutor 记忆脚注): 知识卡来源精确到「哪次对话」, 不只到「哪天的日志」。

核对事实时模型指认的原文段落 → 落在哪段对话的日志里 → sources 补 `session:<id>`。
"""
from __future__ import annotations

import asyncio
import json
import sys

import pytest

from catfish_memory_fm import _parse_frontmatter_lists, _split_frontmatter_body
import catfish_memory_wiki_ground as g
import catfish_memory_wiki_provenance as prov

JOURNAL = (
    "## [2026-11-01 09:10] session | 20261101_091002_aaa111\n\n"
    "员工说: 季度预算由财务部王芳审批, 超过五十万要报分管副总。\n\n"
    "## [2026-11-01 15:20] session | 20261101_152005_bbb222\n\n"
    "员工确认: 新项目立项会每周三下午开, 地点在三楼会议室。\n\n"
    "## [2026-11-01 18:00] distill | 3 entities, 1 concepts\n\n"
    "wiki/entities/ 已更新。\n"
)


def test_段落定位到对话():
    got = prov.sessions_for_passages(JOURNAL, ["季度预算由财务部王芳审批", "新项目立项会每周三下午开"])
    assert got == ["session:20261101_091002_aaa111", "session:20261101_152005_bbb222"]


def test_落在非对话段或找不到的不算():
    assert prov.sessions_for_passages(JOURNAL, ["wiki/entities/ 已更新"]) == []
    assert prov.sessions_for_passages(JOURNAL, ["原文里根本没有这句话的内容"]) == []
    assert prov.sessions_for_passages("没有任何抬头的资料正文", ["资料正文"]) == []


def test_老格式只留后6位的对话不算():
    old = "## [2026-07-17 17:53] session | …8d4bfe\n\n季度预算由财务部审批\n"
    assert prov.sessions_for_passages(old, ["季度预算由财务部审批"]) == []


def test_来源校验_只收本轮真读过的对话():
    p = prov.Provenance.from_run(JOURNAL, [])
    assert p.session_ids == {"20261101_091002_aaa111", "20261101_152005_bbb222"}
    card = ('---\ntype: concept\ntitle: 预算\nsources: ["journal:2026-11-01", '
            '"session:20261101_091002_aaa111", "session:20990101_000000_fake99"]\n---\n\n# 预算\n')
    out = prov._normalize_sources("concepts/x.md", card, p)
    srcs = _parse_frontmatter_lists(_split_frontmatter_body(out)[0])["sources"]
    assert srcs == ["journal:2026-11-01", "session:20261101_091002_aaa111"]


def _copies():
    return {id(m): m for m in (g, sys.modules.get("_catfish_memory_pkg.catfish_memory_wiki_ground")) if m}.values()


@pytest.fixture()
def _no_vectors(monkeypatch, tmp_path):
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    for m in _copies():
        monkeypatch.setattr(m, "_embed_all", lambda texts: None)


def test_核对通过的事实_来源补上那次对话(_no_vectors):
    async def post(prompt, model):
        return json.dumps([{"i": 1, "v": "支持", "e": []}], ensure_ascii=False)

    card = "---\ntype: concept\ntitle: 预算\nsources: [\"journal:2026-11-01\"]\n---\n\n# 预算\n\n- 季度预算由财务部王芳审批\n"
    out, st = asyncio.run(g.ground_files({"concepts/x.md": card}, JOURNAL, "m", post=post))
    assert st["kept"] == 1
    srcs = _parse_frontmatter_lists(_split_frontmatter_body(out["concepts/x.md"])[0])["sources"]
    assert "session:20261101_091002_aaa111" in srcs
    assert "session:20261101_152005_bbb222" not in srcs
