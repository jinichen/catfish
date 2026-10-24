#!/usr/bin/env python3
"""知识卡关系对错评测: 「A 关系 B」对照原文, 量对的比例和错在哪。

事实核对 (eval_verify.py) 只管卡片正文; 关系 (frontmatter related) 没人量过 —— 10/23 鸿波在界面上
连着撞见「中电福富 隶属 业软BG」(方向反了)、「自研平台」(不是个东西)、一堆对方没条目的关系。

两批题, 放员工本机 ~/.catfish/eval/relation_cases.json —— **含工作内容, 不进仓库**:

  collect     拿 distill_cases.json 的资料从零跑一遍蒸馏, 收下写出来的每条关系
              (量「流水线新写的关系」; 改提示词 / 归一化以后重跑这一步对比)
  sample-kb   从现在的知识库里, 来源是上传资料的条目, 按关系类型分层抽 N 条
              (量「库里现在的关系」, 区分员工确认过 / 待确认)
  report      按批次 / 关系类型 / 条目状态出对的比例和错误分布

每条题 label (人或更强的模型对着原文 + 词表释义标):
  correct          对
  wrong_direction  关系有, 方向反了 (A 隶属 B 写成 B 隶属 A / 写在了上级卡上)
  wrong_type       两者确有关系, 类型不对 (fix 填该用的词)
  unsupported      原文没有这层关系
  bad_target       对象不是一个具体的东西 (泛称、口号、半句话)

  ~/.hermes/hermes-agent/venv/bin/python scripts/eval_relations.py collect [--model M]
  ~/.hermes/hermes-agent/venv/bin/python scripts/eval_relations.py sample-kb [--n 150]
  ~/.hermes/hermes-agent/venv/bin/python scripts/eval_relations.py report
  ~/.hermes/hermes-agent/venv/bin/python scripts/eval_relations.py check [--set pipeline|kb]
      (拿标好的题跑关系核对: 方向纠正 + 对原文判, 看留下来的对多少)

10/24 试过「关系用词自动纠正」(核对时让模型按原文给最贴切的关系词, 跟现在的不一样就改), 没上线:
两批标好的题 (pipeline / pipeline2) 里类型不对的 25 条只改对 1 条, 本来对的反被改词 5-7 条。
模型给的词常把方向弄反 (「天卫 关联 卫星及应用」该是隶属, 它给「包含」), 又会把讲话里提到改成「负责」;
而类型不对的里 8 条该用的就是「关联」(词表里没有贴切的词), 本来就改不了。要再试得先解决方向。

10/24 接着试了「选句子」: 每条关系把两个方向都写成带真名字的句子 (「法律合规与审计部属于中电信数智科技有限公司」
/ 反过来那句 / 负责 / 参与 … 共 38 句 + 「关联」「无」), 模型只挑原文说的那句, 词和方向由程序读出 ——
方向换反基本没了 (用「甲 / 乙」代号时还会反, 写真名字才好)。但用词纠正整体仍不划算 (两批 × 两遍):
  只选句子            第一批 留下的对 93% 改对 10 改坏 1; 第二批 61-65% 改坏 8-11
  核对定留不留+改词   93-95% / 改对 2;               74-78% / 改对 3-4 改坏 5
  核对拿出的再救回    86-87% / 改对 4-5;             78% / 改对 2
  (现在线上: 只核对)  94-96%;                        80-83%
  deepseek-flash 只选句子 70% / 74%, gemini-flash 79% / 71% (改对 5-9 改坏 8-10)
卡在模型对「暗含的归属」「成员单位算包含还是参与」这类判断不稳, 不是方向。代码存在
~/catfish-backups/experiments/relation-choose-20261024.patch (Retriever 名字锚定之外的选句子实现 + 评测 choose 模式)。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import shutil
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / "edge" / "hermes-plugins" / "catfish-memory"
sys.path.insert(0, str(PLUGIN))

import catfish_memory_helpers as h  # noqa: E402
import catfish_memory_wiki_sources as ws  # noqa: E402
from catfish_memory_fm import _parse_frontmatter_lists, _rel_item_name, _split_frontmatter_body  # noqa: E402

HOME = Path.home() / ".catfish"
EVAL = HOME / "eval"
CASES = EVAL / "relation_cases.json"
SOURCES = EVAL / "distill_sources"
LABELS = ("correct", "wrong_direction", "wrong_type", "unsupported", "bad_target")


def _load() -> list:
    return json.loads(CASES.read_text(encoding="utf-8")) if CASES.exists() else []


def _save(cases: list) -> None:
    CASES.write_text(json.dumps(cases, ensure_ascii=False, indent=1), encoding="utf-8")


def _rel_of(item: str) -> str:
    import re  # noqa: PLC0415

    m = re.search(r'\brel\s*:\s*"?([^",}]+)"?', item)
    return m.group(1).strip() if m else ""


def _scalar(fm: str, key: str) -> str:
    import re  # noqa: PLC0415

    m = re.search(rf"^{key}:\s*(.*?)\s*$", fm, re.MULTILINE)
    return m.group(1).strip().strip('"') if m else ""


def _cards(wiki: Path):
    for f in sorted(list(wiki.glob("entities/*.md")) + list(wiki.glob("concepts/*.md"))):
        fm, body = _split_frontmatter_body(f.read_text(encoding="utf-8", errors="ignore"))
        if fm:
            yield f, fm, body


def _relations(fm: str):
    for item in _parse_frontmatter_lists(fm).get("related", []):
        if not item.startswith("{"):
            continue
        name, rel = _rel_item_name(item).strip(), _rel_of(item)
        if name and rel:
            yield name, rel


# ---------------------------------------------------------------- collect

def collect(model: str, set_name: str = "pipeline") -> None:
    sources = [c["source"] for c in json.loads((EVAL / "distill_cases.json").read_text(encoding="utf-8"))]
    found = []
    n = 0
    for name in sources:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "wiki" / "raw" / "sources").mkdir(parents=True)
            (home / "wiki" / "entities").mkdir()
            (home / "wiki" / "concepts").mkdir()
            f = home / "wiki" / "raw" / "sources" / f"1-{name}"
            shutil.copy(SOURCES / name, f)

            async def one(text, path):
                return await ws.wiki_two_step("eval", home, text, h.Provenance.from_run("", [path]),
                                              lambda stage: model, max_chars=len(text),
                                              source_title=ws.source_title_of(path), source_ref=f"raw/sources/{path.stem}")

            asyncio.run(ws.drain_sources(home, [f], one, h._mark_wiki_sources_ingested, time_budget=900))
            titles = {_scalar(fm, "title") for _, fm, _ in _cards(home / "wiki")}
            got = 0
            for _, fm, _ in _cards(home / "wiki"):
                for target, rel in _relations(fm):
                    n += 1
                    got += 1
                    found.append({
                        "id": f"{set_name[0]}{set_name[-1] if set_name[-1].isdigit() else ''}-{n}", "set": set_name, "source_files": [str(SOURCES / name)],
                        "card": _scalar(fm, "title"), "card_type": _scalar(fm, "entity_type") or _scalar(fm, "concept_type"),
                        "rel": rel, "target": target, "target_exists": target in titles,
                        "status": _scalar(fm, "ontology_status") or "active", "label": None,
                    })
        print(f"{name}: {got} 条关系")
    # 跑完再读一遍再写 (跑的这十几分钟里别处可能改过这个文件 —— 10/23 标好的 150 条被冲掉过一次)
    _save([c for c in _load() if c["set"] != set_name] + found)
    print(f"共 {n} 条 → {CASES} (label 待标)")


# ---------------------------------------------------------------- sample-kb

def sample_kb(n: int, seed: int) -> None:
    random.seed(seed)
    wiki = HOME / "wiki"
    titles = {_scalar(fm, "title") for _, fm, _ in _cards(wiki)}
    pool = []
    for f, fm, _ in _cards(wiki):
        srcs = [s for s in _parse_frontmatter_lists(fm).get("sources", []) if s.startswith("raw/sources/")]
        files = [str(wiki / f"{s}.md") for s in srcs if (wiki / f"{s}.md").exists()]
        if not files:
            continue
        status = _scalar(fm, "ontology_status") or "active"
        authored = _scalar(fm, "authored_by") == "employee"
        for target, rel in _relations(fm):
            if rel == "关联":
                continue  # 兜底词, 本来就没说是什么关系
            pool.append({
                "set": "kb", "source_files": files, "card": _scalar(fm, "title"), "card_path": str(f.relative_to(HOME)),
                "card_type": _scalar(fm, "entity_type") or _scalar(fm, "concept_type"),
                "rel": rel, "target": target, "target_exists": target in titles,
                "status": "employee" if authored and status == "active" else status, "label": None,
            })
    by_rel = defaultdict(list)
    for p in pool:
        by_rel[p["rel"]].append(p)
    # 分层: 每种关系至少 min(5, 全部), 剩下按比例
    picked = []
    for rel, items in by_rel.items():
        random.shuffle(items)
        picked += items[:min(5, len(items))]
    rest = [p for p in pool if p not in picked]
    random.shuffle(rest)
    picked += rest[:max(0, n - len(picked))]
    cases = [c for c in _load() if c["set"] != "kb"]
    for i, p in enumerate(picked, 1):
        cases.append({"id": f"k{i}", **p})
    _save(cases)
    print(f"库里来自资料的有类型关系 {len(pool)} 条, 抽 {len(picked)} 条: {dict(Counter(p['rel'] for p in picked))}")


# ---------------------------------------------------------------- check

ACCEPT_PARTIAL = False


def check(model: str, set_name: str) -> None:
    """拿标好的关系跑一遍关系核对 (方向纠正 + 对原文判), 看留下来的有多少是对的。

    跟线上一样的两步: catfish_memory_wiki_relcheck.fix_directions → 每条翻成一句话交给
    catfish_memory_wiki_ground.judge_facts。方向改过的题: 原标 wrong_direction 的算改对了,
    原标 correct 的算改错了。
    """
    import catfish_memory_wiki_ground as ground  # noqa: PLC0415
    import catfish_memory_wiki_relcheck as rc  # noqa: PLC0415

    cases = [c for c in _load() if c["set"] == set_name and c.get("label") in LABELS]
    rows = []  # (case, 改后的 label, kept)
    for src in sorted({c["source_files"][0] for c in cases}):
        mine = [c for c in cases if c["source_files"][0] == src]
        source = "\n".join(Path(f).read_text(encoding="utf-8", errors="ignore") for f in mine[0]["source_files"])
        cards: dict = {}
        for c in mine:
            card = cards.setdefault(c["card"], {"title": c["card"], "class": rc.classify(c["card_type"], c["card_type"], c["card"]),
                                                "related": []})
            card["related"].append((c["target"], c["rel"], c["id"]))
        known = {t: k["class"] for t, k in cards.items() if k["class"]}
        # fix_directions 只认 (name, rel): 用 id 记住每条题落到哪
        plain = {t: {"title": t, "class": k["class"], "related": [(n, r) for n, r, _ in k["related"]]} for t, k in cards.items()}
        ids = {(t, n, r): i for t, k in cards.items() for n, r, i in k["related"]}
        rc.fix_directions(plain, known)
        after = []
        seen = set()
        for t, k in plain.items():
            for n, r in k["related"]:
                cid = ids.get((t, n, r))
                flipped = False
                if cid is None:  # 换了词 (隶属↔包含) 或挪到了对方卡上
                    cid = ids.get((t, n, rc._INVERSE.get(r, ""))) or ids.get((n, t, r))
                    flipped = True
                if cid is None or cid in seen:
                    continue
                seen.add(cid)
                after.append((cid, t, r, n, flipped))
        sentences = [rc.sentence(t, r, n) for _, t, r, n, _ in after]
        got = asyncio.run(ground.judge_facts(sentences, [[] for _ in sentences], source, model,
                                             anchors=[[t, n] for _, t, _, n, _ in after]))
        by_id = {c["id"]: c for c in mine}
        for (cid, t, r, n, flipped), v in zip(after, got):
            c = by_id[cid]
            label = c["label"]
            if flipped:
                label = "correct" if label == "wrong_direction" else ("wrong_direction" if label == "correct" else label)
            keep = v is None or v[0] or (ACCEPT_PARTIAL and "(部分" in v[1])
            rows.append((c, label, keep, flipped))
    n = len(rows)
    before = sum(c["label"] == "correct" for c, _, _, _ in rows)
    kept = [(c, lab) for c, lab, k, _ in rows if k]
    ok_kept = sum(lab == "correct" for _, lab in kept)
    correct_all = sum(lab == "correct" for _, lab, _, _ in rows)
    flips = [(c, lab) for c, lab, _, f in rows if f]
    print(f"批次 {set_name}: {n} 条; 改之前对 {before} ({before / n:.0%})")
    print(f"方向自动改 {len(flips)} 条: 改对 {sum(c['label'] == 'wrong_direction' for c, _ in flips)}, "
          f"改错 {sum(c['label'] == 'correct' for c, _ in flips)}")
    print(f"核对后留下 {len(kept)} 条, 其中对 {ok_kept} ({ok_kept / max(len(kept), 1):.0%}); "
          f"拿出去 {n - len(kept)} 条")
    print(f"对的关系 {correct_all} 条里留下 {ok_kept} ({ok_kept / max(correct_all, 1):.0%}) —— 其余被误拿出, 员工可在「没对上原文」加回")
    wrong_kept = Counter(lab for _, lab in kept if lab != "correct")
    print("留下来的错的: " + ("、".join(f"{k} {v}" for k, v in wrong_kept.most_common()) or "无"))


# ---------------------------------------------------------------- report

def report() -> None:
    cases = [c for c in _load() if c.get("label") in LABELS]
    if not cases:
        print("还没有标好的题")
        return
    for name in sorted({c["set"] for c in cases}):
        mine = [c for c in cases if c["set"] == name]
        if not mine:
            continue
        ok = sum(c["label"] == "correct" for c in mine)
        print(f"\n== {'知识库现有的' if name == 'kb' else '流水线新写的 (' + name + ')'}: {len(mine)} 条, 对 {ok} ({ok / len(mine):.0%})")
        errs = Counter(c["label"] for c in mine if c["label"] != "correct")
        print("   错在: " + "、".join(f"{k} {v}" for k, v in errs.most_common()))
        dangling = [c for c in mine if not c["target_exists"]]
        if dangling:
            dok = sum(c["label"] == "correct" for c in dangling)
            print(f"   对方没有条目的 {len(dangling)} 条, 其中对 {dok} ({dok / len(dangling):.0%})")
        if name == "kb":
            for st in ("employee", "active", "pending"):
                s = [c for c in mine if c["status"] == st]
                if s:
                    sok = sum(c["label"] == "correct" for c in s)
                    print(f"   {'员工确认/改过' if st == 'employee' else '已生效 (小鲶写的)' if st == 'active' else '待确认'}: {len(s)} 条, 对 {sok / len(s):.0%}")
        print(f"   {'关系':<6}{'条数':>5}{'对':>6}   主要错法")
        by = defaultdict(list)
        for c in mine:
            by[c["rel"]].append(c)
        for rel, items in sorted(by.items(), key=lambda x: -len(x[1])):
            rok = sum(c["label"] == "correct" for c in items)
            e = Counter(c["label"] for c in items if c["label"] != "correct").most_common(2)
            print(f"   {rel:<6}{len(items):>5}{rok / len(items):>6.0%}   {'、'.join(f'{k} {v}' for k, v in e)}")


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("collect")
    p.add_argument("--model", default="")
    p.add_argument("--set", default="pipeline", help="写到哪一批 (改了提示词另起一批对比, 不覆盖标好的)")
    p = sub.add_parser("sample-kb")
    p.add_argument("--n", type=int, default=150)
    p.add_argument("--seed", type=int, default=7)
    sub.add_parser("report")
    p = sub.add_parser("check")
    p.add_argument("--model", default="")
    p.add_argument("--set", default="pipeline")
    p.add_argument("--accept-partial", action="store_true", help="模型判「部分」的关系也留下")
    args = ap.parse_args()
    if args.cmd == "collect":
        collect(args.model or h._read_picker_state_model(HOME) or "catfish-public-qwen-flash", args.set)
    elif args.cmd == "check":
        global ACCEPT_PARTIAL
        ACCEPT_PARTIAL = args.accept_partial
        check(args.model or h._read_picker_state_model(HOME) or "catfish-public-qwen-flash", args.set)
    elif args.cmd == "sample-kb":
        sample_kb(args.n, args.seed)
    else:
        report()
    return 0


if __name__ == "__main__":
    sys.exit(main())
