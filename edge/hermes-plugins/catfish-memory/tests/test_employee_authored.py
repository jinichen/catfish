"""员工修正过的条目, 后台蒸馏不许覆盖。"""
from __future__ import annotations
import pytest
from catfish_memory_helpers import (
    _append_as_appendix, _is_employee_authored, _write_wiki_files,
)

HUMAN = ("---\ntype: entity\ntitle: 中电福富\nauthored_by: employee\n"
         'tags: [公司]\nrelated: ["[[北京福富]]"]\n---\n\n'
         "# 中电福富\n\n员工亲手改过的正文 —— 这段一个字都不能被机器动。\n")
LLM = ("---\ntype: entity\ntitle: 中电福富\ntags: [中电系]\n"
       'related: ["[[资质对标分析流程]]"]\n---\n\n'
       "# 中电福富\n\n蒸馏新抽出来的说法, 跟员工写的不一样。\n")


def test_detects_marker():
    assert _is_employee_authored(HUMAN)
    assert not _is_employee_authored(LLM)


def test_body_preserved_verbatim(tmp_path):
    ents = tmp_path / "wiki" / "entities"; ents.mkdir(parents=True)
    f = ents / "x.md"; f.write_text(HUMAN, encoding="utf-8")
    _write_wiki_files(tmp_path, {"wiki/entities/x.md": LLM})
    out = f.read_text(encoding="utf-8")
    assert "员工亲手改过的正文" in out, "员工正文被覆盖了"
    assert "蒸馏补充" in out and "蒸馏新抽出来的说法" in out, "新内容该进附录, 不是丢掉"
    assert out.index("员工亲手改过的正文") < out.index("蒸馏补充"), "附录该在正文之后"


def test_frontmatter_lists_still_union(tmp_path):
    """tags/related 是累加语义, 不冲突 —— 仍然取并集。"""
    ents = tmp_path / "wiki" / "entities"; ents.mkdir(parents=True)
    f = ents / "x.md"; f.write_text(HUMAN, encoding="utf-8")
    _write_wiki_files(tmp_path, {"wiki/entities/x.md": LLM})
    out = f.read_text(encoding="utf-8")
    assert "公司" in out and "中电系" in out
    assert "北京福富" in out and "资质对标分析流程" in out


def test_appendix_does_not_stack(tmp_path):
    """跑两次蒸馏只留最近一份附录, 不层叠。"""
    ents = tmp_path / "wiki" / "entities"; ents.mkdir(parents=True)
    f = ents / "x.md"; f.write_text(HUMAN, encoding="utf-8")
    _write_wiki_files(tmp_path, {"wiki/entities/x.md": LLM})
    _write_wiki_files(tmp_path, {"wiki/entities/x.md": LLM.replace("蒸馏新抽出来的说法", "第二次的说法")})
    out = f.read_text(encoding="utf-8")
    assert out.count("蒸馏补充") == 1, "附录层叠了"
    assert "第二次的说法" in out and "蒸馏新抽出来的说法" not in out
    assert "员工亲手改过的正文" in out


def test_normal_entry_unaffected(tmp_path):
    """没标记的条目行为不变 —— 别把正常路径弄坏。"""
    ents = tmp_path / "wiki" / "entities"; ents.mkdir(parents=True)
    f = ents / "y.md"; f.write_text(LLM, encoding="utf-8")
    _write_wiki_files(tmp_path, {"wiki/entities/y.md": LLM.replace("蒸馏新抽出来的说法", "更新后的说法")})
    out = f.read_text(encoding="utf-8")
    assert "更新后的说法" in out
    assert "蒸馏补充" not in out, "普通条目不该走附录路径"


# 这条测试原来只有 `assert not called` 一个断言, 那是**只有阴性、没有阳性对照**:
# monkeypatch 万一没打中真正被调用的那个模块对象 (conftest 把 helpers 按
# `catfish_memory_helpers` 和 `_catfish_memory_pkg.catfish_memory_helpers`
# 两个名字各 exec 了一遍, 是两个不同的 module 对象), called 一样是空的, 测试
# 一样绿 —— 它绿得跟代码对不对无关。
#
# 实测过: 把 setattr 目标换成一个临时造的空模块, 6 passed, 一条都不红。
#
# ── 8/15 拆分后更新 ──
#
# _call_merge_llm 和 merge_files_with_llm 一起搬进了 catfish_memory_merge.py。
# 于是 `monkeypatch.setattr(H, "_call_merge_llm", ...)` 打的是 helpers 里那个
# **re-export 出来的绑定**, 而 merge_files_with_llm 从自己模块的 globals 取,
# 两者不是同一个 —— `from X import name` 建的是新绑定, 不是别名。
#
# 拆分当天这条阳性对照立刻红了, 阴性那条照绿。这就是它存在的意义:
# 没有它, 这次拆分会把 P19 那道"员工改过的条目连送都不送给 LLM"的边界
# 悄悄拆坏, 而测试全绿。
#
# 改成 patch 真正的宿主模块。
# 补 test_p19_merges_non_employee_files 作阳性对照: 同一个 fake、同一次
# monkeypatch, 非员工条目必须**真的**调到。它绿, 才证明上面那条的"没调到"
# 是代码的选择, 不是 patch 落空。(2026-08-15 补)


@pytest.mark.asyncio
async def test_p19_skips_employee_files(tmp_path, monkeypatch):
    """员工改过的连送都不送给 LLM —— 边界划在这里, 不是划在验收上。"""
    import catfish_memory_merge as M
    import catfish_memory_helpers as H
    called = []
    async def fake_merge(old, new, model):
        called.append(1); return None
    monkeypatch.setattr(M, "_call_merge_llm", fake_merge)
    ents = tmp_path / "wiki" / "entities"; ents.mkdir(parents=True)
    (ents / "x.md").write_text(HUMAN, encoding="utf-8")
    await M.merge_files_with_llm(tmp_path, {"wiki/entities/x.md": LLM}, "m")
    assert not called, "员工改过的条目被送去 LLM 了"


@pytest.mark.asyncio
async def test_p19_merges_non_employee_files(tmp_path, monkeypatch):
    """**阳性对照**: 没有 authored_by 标记的条目, 同一个 fake 必须真被调到。

    这条红 = monkeypatch 没打中被调用的那个模块对象, 于是上面那条的"没调到"
    不能说明任何事。拆分 helpers 时如果把 _call_merge_llm 和
    merge_files_with_llm 分到两个文件, 这条会立刻红。
    """
    import catfish_memory_merge as M
    import catfish_memory_helpers as H
    called = []
    async def fake_merge(old, new, model):
        called.append((old, new, model)); return None
    monkeypatch.setattr(M, "_call_merge_llm", fake_merge)
    ents = tmp_path / "wiki" / "entities"; ents.mkdir(parents=True)
    (ents / "y.md").write_text(LLM, encoding="utf-8")   # 无 authored_by
    await M.merge_files_with_llm(tmp_path, {"wiki/entities/y.md": LLM}, "m")
    assert called, "fake 一次都没被调到 —— monkeypatch 落空了, 不是代码跳过了"
