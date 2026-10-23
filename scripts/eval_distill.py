#!/usr/bin/env python3
"""知识卡蒸馏评测: 拿真实资料跑一遍「分析 → 写卡 → 核对原文 → 合并」, 量两件事。

  · 召回: 每份资料人工标几条「应该抽出来的事实」(关键字, 任一命中即算), 看卡上有没有;
  · 编造: 卡片正文里的数字, 原文里一处都找不到的有几个 —— 这一项不依赖核对逻辑本身,
          是独立的旁证 (核对开着时应该是 0)。

另列: 留在正文的事实条数、对不上原文被拿出正文的条数 (unverified)。

题目和资料放在员工本机 ~/.catfish/eval/ —— **含工作内容, 不进仓库**:

  distill_cases.json   [{"source": "十五五规划.md", "expect": [["426"], ["九千亿", "9000亿"], ...]}, ...]
  distill_sources/     资料正文 (.md, 已经是文字)

每份资料在一个临时目录里从零跑, 不碰真知识库。调真模型 (走网关), 一份 1-2 分钟。

  ~/.hermes/hermes-agent/venv/bin/python scripts/eval_distill.py [--model M] [--compare]

--compare: 关掉核对再跑一遍做对照 (CATFISH_WIKI_VERIFY=0)。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / "edge" / "hermes-plugins" / "catfish-memory"
sys.path.insert(0, str(PLUGIN))

import catfish_memory_helpers as h  # noqa: E402
import catfish_memory_wiki_sources as ws  # noqa: E402
import catfish_memory_wiki_verify as verify  # noqa: E402
from catfish_memory_fm import _parse_frontmatter_lists, _split_frontmatter_body  # noqa: E402


def _cards(home: Path):
    for f in sorted((home / "wiki").glob("*/*.md")):
        if "/raw/" in str(f):
            continue
        fm, body = _split_frontmatter_body(f.read_text(encoding="utf-8"))
        yield _parse_frontmatter_lists(fm), body


def run_case(case: dict, src_dir: Path, model: str) -> dict:
    source = (src_dir / case["source"]).read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        (home / "wiki" / "raw" / "sources").mkdir(parents=True)
        (home / "wiki" / "entities").mkdir()
        (home / "wiki" / "concepts").mkdir()
        f = home / "wiki" / "raw" / "sources" / f"1-{case['source']}"
        shutil.copy(src_dir / case["source"], f)

        async def one(text, path):
            return await ws.wiki_two_step("eval", home, text, h.Provenance.from_run("", [path]),
                                          lambda stage: model, max_chars=len(text))

        stats = asyncio.run(ws.drain_sources(home, [f], one, h._mark_wiki_sources_ingested, time_budget=900))
        bodies, evidence, unverified, cards = [], [], [], 0
        for lists, body in _cards(home):
            cards += 1
            bodies.append(body)
            evidence += lists.get("evidence", [])
            unverified += lists.get("unverified", [])
    text = verify.norm("\n".join(bodies + evidence))
    hits = sum(1 for alts in case["expect"] if any(verify.norm(a) in text for a in alts))
    src_ints = verify.ints(source)
    fact_lines = [l for b in bodies for l in b.splitlines() if l.strip() and not l.strip().startswith("#")]
    fabricated = sum(len(verify.ints(verify._strip_today(l)) - src_ints) for l in fact_lines)
    return {"source": case["source"], "chunks": stats["chunks"], "cards": cards, "facts": len(fact_lines),
            "unverified": len(unverified), "hits": hits, "expect": len(case["expect"]), "fabricated_numbers": fabricated}


def report(title: str, rows: list) -> None:
    print(f"\n{title}")
    print(f"{'资料':<14}{'段':>3}{'卡':>4}{'正文事实':>8}{'拿出正文':>8}{'召回':>8}{'编造数字':>8}")
    for r in rows:
        print(f"{r['source'][:12]:<14}{r['chunks']:>3}{r['cards']:>4}{r['facts']:>8}{r['unverified']:>8}"
              f"{r['hits']:>5}/{r['expect']:<2}{r['fabricated_numbers']:>8}")
    hit, exp = sum(r["hits"] for r in rows), sum(r["expect"] for r in rows)
    print(f"{'合计':<14}{'':>3}{sum(r['cards'] for r in rows):>4}{sum(r['facts'] for r in rows):>8}"
          f"{sum(r['unverified'] for r in rows):>8}{hit:>5}/{exp:<2}{sum(r['fabricated_numbers'] for r in rows):>8}"
          f"   召回 {hit / max(exp, 1):.0%}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", default=str(Path.home() / ".catfish"))
    ap.add_argument("--model", default="")
    ap.add_argument("--compare", action="store_true", help="关掉核对再跑一遍做对照")
    args = ap.parse_args()
    home = Path(args.home)
    cases_file = home / "eval" / "distill_cases.json"
    if not cases_file.exists():
        print(f"没有题目文件: {cases_file}")
        return 1
    cases = json.loads(cases_file.read_text(encoding="utf-8"))
    model = args.model or h._read_picker_state_model(home) or "catfish-private-vision"
    src_dir = home / "eval" / "distill_sources"
    print(f"模型: {model}")
    os.environ["CATFISH_WIKI_VERIFY"] = "1"
    report("核对开", [run_case(c, src_dir, model) for c in cases])
    if args.compare:
        os.environ["CATFISH_WIKI_VERIFY"] = "0"
        report("核对关 (对照)", [run_case(c, src_dir, model) for c in cases])
    return 0


if __name__ == "__main__":
    sys.exit(main())
