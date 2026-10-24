"""10/23: 知识卡事实核对 v2 —— 检索原文 + 模型判断 (catfish_memory_wiki_ground)。"""
from __future__ import annotations

import asyncio
import json
import sys

import pytest

from catfish_memory_fm import _parse_frontmatter_lists, _split_frontmatter_body
import catfish_memory_wiki_ground as g

SOURCE = (
    "福建省人民政府关于印发福建省“十五五”新兴产业和未来产业发展规划的通知\n"
    "闽政〔2026〕12号\n"
    "到2030年，工业战略性新兴产业占规上工业比重达到35%左右，研发经费投入年均增长10%。"
    "新材料产业集群规模突破9000亿元。省发改委牵头，省工信厅配合，各设区市政府抓好落实。"
    "福建省人民政府办公厅 2026年7月30日印发。"
)


def _copies():
    """本模块可能加载了两份 (裸名 + conftest 的 _catfish_memory_pkg 子模块, 见 conftest), 桩两份都打。"""
    return {id(m): m for m in (g, sys.modules.get("_catfish_memory_pkg.catfish_memory_wiki_ground")) if m}.values()


@pytest.fixture(autouse=True)
def _no_vectors(monkeypatch, tmp_path):
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    for m in _copies():
        monkeypatch.setattr(m, "_embed_all", lambda texts: None)


def _card(body: str) -> str:
    return f"---\ntype: concept\ntitle: 规划\n---\n\n# 规划\n\n{body}\n"


def _fake_post(verdicts):
    calls = []

    async def post(prompt, model):
        calls.append(prompt)
        return json.dumps(verdicts(prompt), ensure_ascii=False)

    post.calls = calls
    return post


def test_切段_长句按逗号切_短标题并进下一句():
    ps = g.passages("一、总体要求\n" + "很长的一句话，" * 60 + "。\n二、")
    assert all(len(p) <= g._MAX_PASSAGE + g._MIN_PASSAGE for p in ps)
    assert ps[0].startswith("一、总体要求 ")


def test_检索_换个说法也能找到那段():
    r = g.Retriever(SOURCE, use_vectors=False)
    (cand,) = r.candidates(["新材料产业集群要超过九千亿"], [[]])
    assert "9000亿元" in r.passages[cand[0]]


def test_原文没有的数字不问模型直接判编造():
    post = _fake_post(lambda p: [])
    got = asyncio.run(g.judge_facts(["研发经费年均增长 15%"], [[]], SOURCE, "m", post=post))
    assert got[0][0] is False and "15" in got[0][1]
    assert post.calls == []


def test_摘录对不上也不要紧_模型说支持就收_依据存原文那段():
    post = _fake_post(lambda p: [{"i": 1, "v": "支持", "e": []}])
    out, st = asyncio.run(g.ground_files(
        {"concepts/x.md": _card("- 战新产业占比 35% 左右 〔原文:「战略性新兴产业…35%」〕")}, SOURCE, "m", post=post))
    fm, body = _split_frontmatter_body(out["concepts/x.md"])
    lists = _parse_frontmatter_lists(fm)
    assert (st["kept"], st["dropped"]) == (1, 0)
    assert "〔原文" not in body and "35%" in body
    assert any("规上工业比重达到35%左右" in e for e in lists["evidence"])


def test_张冠李戴_模型判不支持_进unverified():
    post = _fake_post(lambda p: [{"i": 1, "v": "不支持", "e": [], "why": "牵头的是省发改委"}])
    out, st = asyncio.run(g.ground_files({"c.md": _card("- 省工信厅牵头落实规划")}, SOURCE, "m", post=post))
    fm, body = _split_frontmatter_body(out["c.md"])
    assert (st["kept"], st["dropped"]) == (0, 1)
    assert "牵头" not in body
    assert "牵头的是省发改委" in _parse_frontmatter_lists(fm)["unverified"][0]


def test_一批卡一次调用():
    post = _fake_post(lambda p: [{"i": i, "v": "支持", "e": []} for i in range(1, p.count("### 说法") + 1)])
    files = {f"c{i}.md": _card(f"- 省发改委牵头 ({i})\n- 新材料产业集群规模突破 9000 亿元") for i in range(3)}
    _, st = asyncio.run(g.ground_files(files, SOURCE, "m", post=post))
    assert (st["kept"], st["dropped"]) == (6, 0)
    assert len(post.calls) == 1


def test_模型调不通_重试后那批退回摘录比对():
    calls = []

    async def post(prompt, model):
        calls.append(1)
        return None

    out, st = asyncio.run(g.ground_files(
        {"c.md": _card("- 占比 35% 〔原文:「工业战略性新兴产业占规上工业比重达到35%左右」〕\n- 省发改委牵头")},
        SOURCE, "m", post=post))
    assert (st["kept"], st["dropped"]) == (1, 1)  # v1: 没摘录的那条拿出正文
    assert len(calls) == 1 + g.RETRIES
    fm, _ = _split_frontmatter_body(out["c.md"])
    assert any("没给原文摘录" in u for u in _parse_frontmatter_lists(fm)["unverified"])


def test_偶尔一次失败_重试成功就不退回():
    answers = [None, json.dumps([{"i": 1, "v": "支持", "e": []}])]

    async def post(prompt, model):
        return answers.pop(0)

    _, st = asyncio.run(g.ground_files({"c.md": _card("- 省发改委牵头")}, SOURCE, "m", post=post))
    assert (st["kept"], st["dropped"]) == (1, 0)


def test_模型漏判的那几条是None_只重试那几条():
    assert g.parse_verdicts('[{"i": 1, "v": "支持"}]', 2) == [("支持", [], ""), None]
    assert g.parse_verdicts("不是 JSON", 1) == [None]
    broken = '[{"i": 1, "v": "部分", "e": ["段3"], "why": "原文是"十五五"规划"}, {"i": 2, "v": "支持", "e": []}]'
    assert g.parse_verdicts(broken, 2) == [("部分", [3], ""), ("支持", [], "")]
    prompts = []

    async def post(prompt, model):
        prompts.append(prompt)
        n = prompt.count("### 说法")
        return json.dumps([{"i": 1, "v": "支持", "e": []}] if n == 2 else [{"i": 1, "v": "不支持", "e": [], "why": "x"}])

    got = asyncio.run(g.judge_facts(["省发改委牵头", "省工信厅配合"], [[], []], SOURCE, "m", post=post))
    assert [v[0] for v in got] == [True, False]
    assert len(prompts) == 2 and prompts[1].count("### 说法") == 1 and "省工信厅配合" in prompts[1]
    assert g.parse_verdicts('好的\n[{"i": 2, "v": "部分", "e": [3]}, {"i": 1, "v": "支持", "e": ["段1"]}]', 2) == [
        ("支持", [1], ""), ("部分", [3], "")]


def test_旁白和生成残渣丢掉_标题行保留():
    post = _fake_post(lambda p: [{"i": 1, "v": "支持", "e": []}])
    body_in = "## 要点\n- 省发改委牵头\n(注: 以上为摘要)\n```\n(Self-Correction during generation: fix)\n- 。"
    out, st = asyncio.run(g.ground_files({"c.md": _card(body_in)}, SOURCE, "m", post=post))
    _, body = _split_frontmatter_body(out["c.md"])
    assert "## 要点" in body and "注:" not in body and "```" not in body and "Self-Correction" not in body
    assert (st["kept"], st["dropped"]) == (1, 0) and post.calls[0].count("### 说法") == 1


def test_关掉开关只用摘录比对(monkeypatch):
    monkeypatch.setenv("CATFISH_WIKI_GROUND", "0")
    post = _fake_post(lambda p: [])
    _, st = asyncio.run(g.ground_files({"c.md": _card("- 省发改委牵头")}, SOURCE, "m", post=post))
    assert post.calls == [] and (st["kept"], st["dropped"]) == (0, 1)


def test_蒸馏一遍_核对走v2_合并后的卡也查数字(tmp_path, monkeypatch):
    """wiki_two_step 端到端 (模型都打桩): 两张卡 + 资料要点卡一次判完; 合并过的卡照旧过数字检查。"""
    from types import SimpleNamespace

    import catfish_memory_wiki_sources as ws

    ws.digest_card  # 先加载, 让它带进来的那份核对模块也在 _copies() 里
    import importlib
    if ws.__package__:
        importlib.import_module(ws.__package__ + ".catfish_memory_wiki_ground")

    cards = {
        "wiki/concepts/规划.md": _card("- 新材料产业集群规模突破 9000 亿元\n- 省工信厅牵头"),
        "wiki/entities/省发改委.md": "---\ntype: entity\ntitle: 省发改委\n---\n\n- 牵头落实规划\n",
    }
    written = {}

    async def analysis(text, model, max_chars=0):
        return "## Facts\n- 规划 | 研发经费投入年均增长10% | 「研发经费投入年均增长10%」\n"

    async def generation(analysis, model, home):
        return "gen"

    async def merge(home, files, model):
        files = dict(files)
        files["wiki/concepts/规划.md"] = files["wiki/concepts/规划.md"].replace("9000 亿元", "9000 亿元, 带动就业 88 万人")
        return files, {"wiki/concepts/规划.md"}, 0

    def write(home, files, skip_merge_paths=(), provenance=None):
        written.update(files)
        return (1, len(files) - 1)

    deps = SimpleNamespace(
        _append_journal=lambda *a: None, _call_analysis_llm=analysis, _call_generation_llm=generation,
        _parse_generation_output=lambda g_: dict(cards), _write_wiki_files=write, merge_files_with_llm=merge,
    )
    post = _fake_post(lambda p: [{"i": i, "v": "不支持" if "省工信厅牵头" in p.split(f"### 说法 {i}\n")[1].split("\n")[0] else "支持", "e": []}
                                 for i in range(1, p.count("### 说法") + 1)])
    for m in _copies():
        monkeypatch.setattr(m, "_post_judge", post)
    got = asyncio.run(ws.wiki_two_step("t", tmp_path, SOURCE, None, lambda stage: "m", deps=deps,
                                       source_title="十五五规划", source_ref="raw/sources/1-x"))
    assert got is not None
    assert len(post.calls) == 1 and post.calls[0].count("### 说法") == 4
    plan = written["wiki/concepts/规划.md"]
    fm, body = _split_frontmatter_body(plan)
    unverified = _parse_frontmatter_lists(fm)["unverified"]
    assert "省工信厅牵头" not in body and any("省工信厅牵头" in u for u in unverified)
    assert "88" not in body and any("合并时多出的数字" in u for u in unverified)
    assert any("研发经费" in _split_frontmatter_body(c)[1] for p, c in written.items() if "资料要点" in p)



# ---------------------------------------------------------------- 10/23 关系核对

def test_关系方向反了_有反义词就地换():
    import catfish_memory_wiki_relcheck as rc
    cards = {"a.md": {"title": "中电福富", "class": "org", "related": [("陈耿生", "隶属"), ("业软BG", "包含")]}}
    log = rc.fix_directions(cards, {"陈耿生": "person", "业软BG": "dept"})
    assert cards["a.md"]["related"] == [("陈耿生", "包含"), ("业软BG", "包含")] and len(log) == 1


def test_关系方向反了_没反义词_挪到同批的对方卡上():
    import catfish_memory_wiki_relcheck as rc
    cards = {
        "a.md": {"title": "审计整改机制", "class": "rule", "related": [("审计部", "参与")]},
        "b.md": {"title": "审计部", "class": "dept", "related": []},
    }
    rc.fix_directions(cards, {"审计整改机制": "rule", "审计部": "dept"})
    assert cards["a.md"]["related"] == [] and cards["b.md"]["related"] == [("审计整改机制", "参与")]


def test_类型按名字猜():
    import catfish_memory_wiki_relcheck as rc
    assert rc.classify(name="证书清单-著作权202609") == "doc"
    assert rc.classify(name="福建省人民政府") == "org"
    assert rc.classify(name="信息安全BG") == "dept"
    assert rc.classify(entity_type="汇报材料") == "doc"
    assert rc.direction("记录", "system", "doc") == "reversed"
    assert rc.direction("印发", "org", "doc") == "reversed"
    assert rc.direction("隶属", "person", "org") == "ok"
    assert rc.direction("协作", "org", "org") == "unknown"


def test_关系跟事实一起判_不支持的拿出related进unverified():
    def verdicts(p):
        out = []
        for i in range(1, p.count("### 说法") + 1):
            claim = p.split(f"### 说法 {i}\n")[1].split("\n")[0]
            out.append({"i": i, "v": "不支持" if "省工信厅" in claim and "负责" in claim else "支持", "e": [], "why": "原文是省发改委牵头"})
        return out

    post = _fake_post(verdicts)
    card = ('---\ntype: entity\ntitle: 省工信厅\nentity_type: department\n'
            'related: [{name: "福建省人民政府", rel: "隶属"}, {name: "新兴产业规划", rel: "负责"}, "[[老张]]"]\n---\n\n- 省工信厅配合落实规划\n')
    out, st = asyncio.run(g.ground_files({"e.md": card}, SOURCE, "m", post=post))
    fm, _ = _split_frontmatter_body(out["e.md"])
    lists = _parse_frontmatter_lists(fm)
    assert (st["rel_kept"], st["rel_dropped"]) == (1, 1)
    assert 'rel: "隶属"' in fm and "负责" not in fm.split("unverified")[0] and '"[[老张]]"' in fm
    assert any(u.startswith("关系：「省工信厅」负责「新兴产业规划」 —— ") for u in lists["unverified"])
    assert len(post.calls) == 1  # 关系和事实同一次调用


def test_关系判不了照旧留着():
    async def post(prompt, model):
        return None

    card = '---\ntype: entity\ntitle: 省发改委\nrelated: [{name: "省工信厅", rel: "协作"}]\n---\n\n'
    out, st = asyncio.run(g.ground_files({"e.md": card}, SOURCE, "m", post=post))
    assert 'rel: "协作"' in out["e.md"] and st["rel_kept"] == 1


def test_两头名字原文里都没有的关系_不问模型直接拿出():
    post = _fake_post(lambda p: [])
    card = '---\ntype: entity\ntitle: 甲乙丙\nrelated: [{name: "丁戊己", rel: "协作"}]\n---\n\n'
    out, st = asyncio.run(g.ground_files({"e.md": card}, SOURCE, "m", post=post))
    assert st["rel_dropped"] == 1 and post.calls == []


def test_关系核对开关关掉_关系原样(monkeypatch):
    monkeypatch.setenv("CATFISH_WIKI_RELCHECK", "0")
    post = _fake_post(lambda p: [{"i": i, "v": "支持", "e": []} for i in range(1, p.count("### 说法") + 1)])
    card = '---\ntype: entity\ntitle: 省发改委\nrelated: [{name: "省工信厅", rel: "协作"}]\n---\n\n- 省发改委牵头\n'
    out, st = asyncio.run(g.ground_files({"e.md": card}, SOURCE, "m", post=post))
    assert 'related: [{name: "省工信厅", rel: "协作"}]' in out["e.md"] and st["rel_kept"] == 0
    assert post.calls[0].count("### 说法") == 1
