#!/usr/bin/env python3
"""知识库金标问题集 —— 「知识库有没有变好」用数说 (11/08)。

wiki_health.py (hermes-plugins/catfish-memory) 量的是结构 (类型覆盖、断链、重名), 它自己也说: 结构好不等于答得对。
这里量「平时会问的问题, 搜不搜得到该搜到的那一页」:

    ~/.catfish/wiki_gold.json      员工自己维护的问题集 (每条: 问法 + 期望命中的页标题; 或按属性查的条件 + 期望)
    python3 scripts/wiki_gold_eval.py            跑一遍, 打印命中率和没命中的
    python3 scripts/wiki_gold_eval.py --init     还没有问题集时, 从现有知识库里各类型抽几条生成一份起手的
    python3 scripts/wiki_gold_eval.py --json     机器可读, 存进 ~/.catfish/wiki_health/<日期>-gold.json 做趋势

问题集格式 (一条一个对象):
    {"q": "CMMI 几级",            "expect": ["CMMI-5 软件成熟度评估（五级）"]}                 搜索, 前 5 条里命中算过
    {"query": {"type": "cert", "status": "即将到期"}, "expect_min": 1}                      按属性查, 至少几条
    {"q": "...", "expect": [...], "k": 3}                                                   前 3 条里命中才算

跑的是跟小鲶一样的那条路 (catfish_tool_bridge.wiki_search / wiki_types.query), 在员工本机跑, 不上传。
发版前跑一遍, 命中率掉了就别发。
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from catfish_tool_bridge import wiki_files, wiki_types  # noqa: E402
from catfish_tool_bridge import wiki_search as ws  # noqa: E402

_Q_BY_TYPE = {
    "cert": ["{t} 的证书编号是多少", "{t} 什么时候到期", "{t} 是谁发的"],
    "software": ["{t} 的软著登记号", "{t} 是哪个部门维护的"],
    "project": ["{t} 项目的客户是谁", "{t} 现在到什么阶段了"],
    "person": ["{t} 是哪个部门的", "{t} 的联系方式"],
    "department": ["{t} 归哪个体系", "{t} 负责什么"],
    "org": ["{t} 跟我们是什么关系", "{t} 的全称"],
    "doc": ["{t} 的文号", "{t} 要求我们做什么"],
}


def gold_path() -> Path:
    return wiki_files._catfish_home() / "wiki_gold.json"


def init_gold(per_type: int = 4, seed: int = 7) -> list[dict]:
    """从现有知识库里各类型随机抽几条, 按类型套问法生成起手的问题集 (之后员工按自己真会问的改)。"""
    rng = random.Random(seed)
    schema = wiki_types.load_schema()
    items: list[dict] = []
    for tk in schema:
        pages = list(wiki_types._iter_typed(tk, schema))
        rng.shuffle(pages)
        for rel, fm, _body in pages[:per_type]:
            title = wiki_types._unquote(wiki_files._parse_field(fm, "title") or "") or Path(rel).stem
            for tpl in _Q_BY_TYPE.get(tk, ["{t} 是什么"])[:2]:
                items.append({"q": tpl.format(t=title), "expect": [title], "type": tk})
    status_from = {tk: (v.get("status") or {}).get("from") for tk, v in schema.items()}
    for tk, f in status_from.items():
        if f:
            items.append({"query": {"type": tk, "where": [{"field": f, "empty": False}]}, "expect_min": 1, "type": tk,
                          "note": f"{schema[tk].get('label')} 至少有一条填了{f}"})
    return items


def run(items: list[dict]) -> dict:
    results = []
    for it in items:
        start = time.monotonic()
        if "query" in it:
            r = wiki_types.query(it["query"])
            got = r.get("total", 0) if r.get("ok") else -1
            ok = got >= int(it.get("expect_min", 1))
            results.append({**it, "ok": ok, "got": got, "ms": int((time.monotonic() - start) * 1000)})
            continue
        k = int(it.get("k") or 5)
        r = ws.search_wiki(str(it["q"]), top_k=k)
        titles = [m["title"] for m in r["matches"]]
        expect = [str(x) for x in it.get("expect") or []]
        rank = next((i + 1 for i, t in enumerate(titles) if t in expect), None)
        results.append({**it, "ok": rank is not None, "rank": rank, "got": titles[:k], "semantic": r.get("semantic"),
                        "ms": int((time.monotonic() - start) * 1000)})
    n = len(results)
    hit = sum(1 for r in results if r["ok"])
    return {"date": time.strftime("%Y-%m-%d %H:%M"), "total": n, "hit": hit, "rate": round(hit / n, 3) if n else None,
            "semantic": any(r.get("semantic") for r in results), "results": results}


def report(r: dict) -> None:
    print(f"金标问题 {r['total']} 条, 命中 {r['hit']} 条 ({(r['rate'] or 0) * 100:.0f}%), 向量{'用上了' if r['semantic'] else '没用上 (Companion 没开?)'}")
    for x in r["results"]:
        if x["ok"]:
            continue
        if "query" in x:
            print(f"  ✗ 按属性查 {json.dumps(x['query'], ensure_ascii=False)} → {x['got']} 条 (期望 ≥{x.get('expect_min', 1)})")
        else:
            print(f"  ✗ {x['q']} → 期望 {x['expect']}, 前 {x.get('k', 5)} 条是 {x['got']}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", action="store_true", help="从现有知识库生成起手的问题集 (已有就不覆盖)")
    ap.add_argument("--json", action="store_true", help="结果存成 json (~/.catfish/wiki_health/<日期>-gold.json) 并打印路径")
    ap.add_argument("--min-rate", type=float, default=None, help="命中率低于这个就退出码 1 (发版前用)")
    a = ap.parse_args()
    p = gold_path()
    if a.init:
        if p.exists():
            print(f"已有 {p}, 不覆盖")
        else:
            items = init_gold()
            p.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"生成 {len(items)} 条起手问题 → {p} (按自己平时真会问的改一改)")
    if not p.exists():
        print(f"没有问题集 {p}: 先 --init 生成, 或自己写一份")
        return 2
    items = json.loads(p.read_text(encoding="utf-8"))
    r = run(items)
    report(r)
    if a.json:
        d = wiki_files._catfish_home() / "wiki_health"
        d.mkdir(parents=True, exist_ok=True)
        out = d / f"{time.strftime('%Y%m%d-%H%M')}-gold.json"
        out.write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"→ {out}")
    if a.min_rate is not None and (r["rate"] or 0) < a.min_rate:
        print(f"命中率 {r['rate']} 低于 {a.min_rate}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
