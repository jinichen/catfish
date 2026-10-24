#!/usr/bin/env python3
"""事实核对评测: 量核对这一步本身 —— 真事实被错拿出正文 (错杀) 和假事实被放进正文 (漏放) 各多少。

eval_distill.py 量的是整条蒸馏流水线 (召回 / 编造数字), 核对逻辑换了说不清是更准了还是更松了。
这里把「卡上写的一句话 + 原文」固定下来, 每句标好真假, 不同核对办法跑同一批题对比。

题目放在员工本机 ~/.catfish/eval/ —— **含工作内容, 不进仓库**:

  verify_cases.json   [{"id", "source", "line" (带〔原文:「…」〕标记), "fact", "label": true/false/null,
                        "kind": "raw" | "perturb-number" | "perturb-name" | "perturb-negate", "of": 原题 id}]

三步:

  collect   拿 distill_cases.json 里的资料跑一遍分析 + 写卡 (核对关), 把卡上每一行原样收下来,
            label 先空着 —— 人 (或更强的模型对着全文) 逐条标真假;
  perturb   从标为真的题里造假题: 改数字 / 换成同一份资料里的另一个名字 / 改成否定。
            摘录照旧留着 —— 模拟「摘录是对的, 话写错了」, 这正是只查摘录的办法查不出来的;
  run       跑核对办法, 出错杀率 / 漏放率。--method rules = v1 摘录比对 (catfish_memory_wiki_verify);
            --method ground = v2 检索原文 + 模型判断 (catfish_memory_wiki_ground)。

  ~/.hermes/hermes-agent/venv/bin/python scripts/eval_verify.py collect [--model M]
  ~/.hermes/hermes-agent/venv/bin/python scripts/eval_verify.py perturb
  ~/.hermes/hermes-agent/venv/bin/python scripts/eval_verify.py run [--method rules|ground] [--model M] [--show]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import shutil
import sys
import tempfile
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / "edge" / "hermes-plugins" / "catfish-memory"
sys.path.insert(0, str(PLUGIN))

import catfish_memory_helpers as h  # noqa: E402
import catfish_memory_wiki_sources as ws  # noqa: E402
import catfish_memory_wiki_verify as verify  # noqa: E402
from catfish_memory_fm import _split_frontmatter_body  # noqa: E402

EVAL = Path.home() / ".catfish" / "eval"
CASES = EVAL / "verify_cases.json"
SOURCES = EVAL / "distill_sources"


def _load() -> list:
    return json.loads(CASES.read_text(encoding="utf-8")) if CASES.exists() else []


def _save(cases: list) -> None:
    CASES.write_text(json.dumps(cases, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------- collect

def _raw_lines(source_name: str, model: str) -> list:
    """跑分析 + 写卡, 在核对之前截下每张卡的原样内容。"""
    captured = []
    real = verify.verify_files

    def spy(files, source):
        for path, content in files.items():
            captured.append((path, content))
        return real(files, source)

    verify.verify_files = spy
    try:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "wiki" / "raw" / "sources").mkdir(parents=True)
            (home / "wiki" / "entities").mkdir()
            (home / "wiki" / "concepts").mkdir()
            f = home / "wiki" / "raw" / "sources" / f"1-{source_name}"
            shutil.copy(SOURCES / source_name, f)

            async def one(text, path):
                return await ws.wiki_two_step("eval", home, text, h.Provenance.from_run("", [path]),
                                              lambda stage: model, max_chars=len(text),
                                              source_title=ws.source_title_of(path), source_ref=f"raw/sources/{path.stem}")

            asyncio.run(ws.drain_sources(home, [f], one, h._mark_wiki_sources_ingested, time_budget=900))
    finally:
        verify.verify_files = real
    out = []
    for path, content in captured:
        fm, body = _split_frontmatter_body(content)
        for line in body.splitlines():
            s = line.strip()
            if not verify._is_fact_line(s) or verify._COMMENTARY_RE.match(s):
                continue
            fact = verify.MARK_RE.sub("", s).strip().lstrip("-*• ").strip()
            if len(verify.norm(fact)) < 4:
                continue
            out.append({"card": Path(path).stem, "line": s, "fact": fact})
    return out


def collect(model: str) -> None:
    sources = [c["source"] for c in json.loads((EVAL / "distill_cases.json").read_text(encoding="utf-8"))]
    cases = [c for c in _load() if c["kind"] != "raw"]
    n = 0
    for name in sources:
        rows = _raw_lines(name, model)
        for r in rows:
            n += 1
            cases.append({"id": f"r{n}", "source": name, "kind": "raw", "label": None, **r})
        print(f"{name}: {len(rows)} 行")
    _save(cases)
    print(f"共 {n} 行 → {CASES} (label 待标)")


# ---------------------------------------------------------------- perturb

_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
_NEGATE = [("是", "不是"), ("已", "未"), ("同意", "不同意"), ("完成", "未完成"),
           ("增长", "下降"), ("提高", "降低"), ("加快", "放缓"), ("支持", "限制"), ("牵头", "不参与")]
#: 换名字只在「同一份资料里抽出来的条目名」之间换: 单位 (按后缀) 或两三个字的人名
_ORG_RE = re.compile(r"^[一-鿿（）()]{2,30}(?:有限公司|公司|集团|研究院|政府|办公厅|委员会|部|局|厅|中心|小组|党委|支部)$")
_PERSON_RE = re.compile(r"^[一-鿿]{2,3}$")


def _is_name(s: str) -> bool:
    return bool(_ORG_RE.match(s) or _PERSON_RE.match(s))


def _bump(m: re.Match) -> str:
    s = m.group(0)
    if "." in s:
        return f"{float(s) * 1.3:.{len(s.split('.')[1])}f}"
    v = int(s)
    if 1900 <= v <= 2100:
        return str(v + random.choice([-2, -1, 1, 2]))
    return str(v + max(1, round(v * random.choice([0.2, 0.5, -0.3]))) if v > 3 else v + 2)


def perturb(seed: int) -> None:
    random.seed(seed)
    cases = [c for c in _load() if c["kind"] == "raw"]
    true = [c for c in cases if c["label"] is True]
    if not true:
        print("还没有标为真的题 —— 先标 label")
        return
    names_by_src: dict = {}
    for c in cases:
        if _is_name(c["card"]):
            names_by_src.setdefault(c["source"], set()).add(c["card"])
    out = []

    def add(c, kind, fact):
        if fact == c["fact"]:
            return
        line = c["line"].replace(c["fact"], fact, 1)
        if line == c["line"]:
            return
        out.append({"id": f"{c['id']}-{kind.split('-')[1][0]}", "source": c["source"], "kind": kind,
                    "label": False, "of": c["id"], "card": c["card"], "line": line, "fact": fact})

    for c in true:
        nums = list(_NUM_RE.finditer(c["fact"]))
        big = [m for m in nums if len(m.group(0).replace(".", "")) >= 1]
        if big:
            m = random.choice(big)
            add(c, "perturb-number", c["fact"][:m.start()] + _bump(m) + c["fact"][m.end():])
        names = [x for x in names_by_src.get(c["source"], ()) if x in c["fact"]]
        if names:
            n = random.choice(sorted(names))
            person = bool(_PERSON_RE.match(n))
            others = sorted(x for x in names_by_src[c["source"]]
                            if x != n and n not in x and x not in n and bool(_PERSON_RE.match(x)) == person)
            if others:
                add(c, "perturb-name", c["fact"].replace(n, random.choice(others), 1))
        for a, b in _NEGATE:
            if a in c["fact"] and b not in c["fact"]:
                add(c, "perturb-negate", c["fact"].replace(a, b, 1))
                break
    _save(cases + out)
    by = {}
    for o in out:
        by[o["kind"]] = by.get(o["kind"], 0) + 1
    print(f"造了 {len(out)} 道假题: {by}")


# ---------------------------------------------------------------- run

def accept_rules(cases: list, source: str, model: str) -> list:
    out = []
    for c in cases:
        _, st = verify.verify_page(f"---\ntitle: x\n---\n{c['line']}\n", source)
        out.append(st["kept"] == 1)
    return out


def name_of(source: str) -> str:
    return source.strip().splitlines()[0][:20] if source.strip() else "?"


NO_VECTORS = False


def accept_ground(cases: list, source: str, model: str) -> list:
    import catfish_memory_wiki_ground as ground  # noqa: PLC0415

    if NO_VECTORS:
        ground._embed_all = lambda texts: None  # 只用字面检索

    facts = [verify.MARK_RE.sub("", c["line"]).strip().lstrip("-*• ").strip() for c in cases]
    quotes = [verify.split_quotes(verify.MARK_RE.findall(c["line"])) for c in cases]
    got = asyncio.run(ground.judge_facts(facts, quotes, source, model))
    unjudged = sum(1 for v in got if v is None)
    if unjudged:
        print(f"  {name_of(source)}: {unjudged} 条模型判不了, 按 v1 算")
    got = [v or ground._fallback(c["line"], source) for c, v in zip(cases, got)]
    for c, (ok, why, ev) in zip(cases, got):
        c["_why"] = why
    return [ok for ok, _, _ in got]


METHODS = {"rules": accept_rules, "ground": accept_ground}


def run(method: str, show: bool, model: str, only: str = "") -> None:
    cases = [c for c in _load() if c["label"] is not None and (not only or only in c["source"])]
    fn = METHODS[method]
    rows = []
    for name in dict.fromkeys(c["source"] for c in cases):
        mine = [c for c in cases if c["source"] == name]
        source = (SOURCES / name).read_text(encoding="utf-8")
        rows += list(zip(mine, fn(mine, source, model)))
    true = [(c, a) for c, a in rows if c["label"] is True]
    false = [(c, a) for c, a in rows if c["label"] is False]
    fr = [c for c, a in true if not a]
    fa = [c for c, a in false if a]
    print(f"办法: {method}" + (f"  模型: {model}" if method != "rules" else ""))
    print(f"真事实 {len(true)} 条, 错杀 {len(fr)} ({len(fr) / max(len(true), 1):.0%})")
    print(f"假事实 {len(false)} 条, 漏放 {len(fa)} ({len(fa) / max(len(false), 1):.0%})")
    for kind in ("raw", "perturb-number", "perturb-name", "perturb-negate"):
        k = [(c, a) for c, a in false if c["kind"] == kind]
        if k:
            print(f"  {kind:<16}{sum(1 for _, a in k if a):>4}/{len(k)} 漏放")
    partial = lambda rows: sum(1 for c, a in rows if not a and "(部分" in c.get("_why", ""))  # noqa: E731
    if method != "rules":
        print(f"  其中判「部分」的: 真事实 {partial(true)} 条, 假事实 {partial(false)} 条 (改成「部分」也收 → 错杀少这么多、漏放多这么多)")
    if show:
        print("\n错杀:")
        for c in fr:
            print(f"  [{c['id']}] {c['fact'][:120]}  {c.get('_why', '')[:80]}")
        print("\n漏放:")
        for c in fa:
            print(f"  [{c['id']} {c['kind']}] {c['fact'][:150]}")


def main() -> int:
    import logging  # noqa: PLC0415

    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("collect")
    p.add_argument("--model", default="")
    p = sub.add_parser("perturb")
    p.add_argument("--seed", type=int, default=7)
    p = sub.add_parser("run")
    p.add_argument("--method", default="rules", choices=sorted(METHODS))
    p.add_argument("--show", action="store_true")
    p.add_argument("--model", default="")
    p.add_argument("--source", default="", help="只跑资料名含这几个字的")
    p.add_argument("--no-vectors", action="store_true", help="ground 只用字面检索找候选段, 不算向量")
    args = ap.parse_args()
    if args.cmd == "collect":
        collect(args.model or h._read_picker_state_model(EVAL.parent) or "catfish-public-qwen-flash")
    elif args.cmd == "perturb":
        perturb(args.seed)
    else:
        global NO_VECTORS
        NO_VECTORS = args.no_vectors
        run(args.method, args.show, args.model or h._read_picker_state_model(EVAL.parent) or "catfish-public-qwen-flash", args.source)
    return 0


if __name__ == "__main__":
    sys.exit(main())
