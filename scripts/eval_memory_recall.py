#!/usr/bin/env python3
"""长期记忆检索评测: 员工问一句话时, 答案需要的那条事实**有没有进到主聊天的上下文里**。

不调 LLM —— 只看 catfish-memory 给主聊天的长期记忆那一段里有没有期望的事实, 所以便宜、
可重复, 每次改检索都能跑一遍对比。新旧两种取法放在一起比:

  旧 (10/22 之前): distilled_facts.md 前 3KB (按文件位置截)
  新 (10/22 P1):   「当前状态」段 + 按问题检索 (catfish_memory_recall)
                   + 问题里点名的实体在知识库里的关系 (10/23 P2, catfish_memory_graph)
  10/23 起「新」的检索是词面 + 向量 (catfish_memory_vectors); 另列一栏「只词面」对比。
  第一次跑会先把记忆向量算完 (走网关, ~30 秒), 存在 ~/.catfish/memory_vectors.db。

题目放在员工本机 ~/.catfish/eval/memory_questions.json —— **含员工工作内容, 不进仓库**:

  [{"q": "启铵总在三期里管什么", "cat": "人物", "expect": ["林启铵：副总经理", "终审"]}, ...]

expect 里任意一个子串出现就算命中。题目要像员工平时那样问, 不要照抄记忆原文 (照抄等于
给词面检索送分)。

  ~/.hermes/hermes-agent/venv/bin/python scripts/eval_memory_recall.py [--show-miss]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / "edge" / "hermes-plugins" / "catfish-memory"
sys.path.insert(0, str(PLUGIN))

import os  # noqa: E402

import catfish_memory_graph as graph  # noqa: E402
import catfish_memory_recall as recall  # noqa: E402
import catfish_memory_vectors as vectors  # noqa: E402

#: 跟 catfish_memory_render 的预算一致
_OLD_BYTES = 3000
_RECALL_BUDGET = 1800


def old_context(distilled: str) -> str:
    return distilled.encode("utf-8")[:_OLD_BYTES].decode("utf-8", errors="ignore")


def new_context(path: Path, distilled: str, q: str) -> str:
    status = recall.current_status_block(distilled) or old_context(distilled)
    return "\n\n".join((
        status,
        recall.recall_block(path, distilled, q, _RECALL_BUDGET),
        graph.render_graph(path.parent, q),
    ))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", default=str(Path.home() / ".catfish"))
    ap.add_argument("--show-miss", action="store_true", help="列出新取法没命中的题")
    args = ap.parse_args()
    home = Path(args.home)
    qfile = home / "eval" / "memory_questions.json"
    if not qfile.exists():
        print(f"没有题目文件: {qfile}")
        return 1
    questions = json.loads(qfile.read_text(encoding="utf-8"))
    path = home / "distilled_facts.md"
    distilled = path.read_text(encoding="utf-8", errors="ignore")
    # 先把向量算齐 (同步), 不然后台还在算时评的是半套
    os.environ["CATFISH_MEMORY_VECTORS"] = "0"
    idx = recall._index_for(path, distilled)
    vectors.build(recall._vector_db(path), idx.texts)

    by_cat: dict = defaultdict(lambda: [0, 0, 0, 0])  # 题数, 旧命中, 只词面, 词面+向量
    old_ctx = old_context(distilled)
    sizes = []
    misses = []
    for item in questions:
        os.environ["CATFISH_MEMORY_VECTORS"] = "0"
        lex_ctx = new_context(path, distilled, item["q"])
        os.environ["CATFISH_MEMORY_VECTORS"] = "1"
        new_ctx = new_context(path, distilled, item["q"])
        sizes.append(len(new_ctx))
        o = any(e in old_ctx for e in item["expect"])
        lx = any(e in lex_ctx for e in item["expect"])
        n = any(e in new_ctx for e in item["expect"])
        c = by_cat[item.get("cat", "其它")]
        c[0] += 1
        c[1] += o
        c[2] += lx
        c[3] += n
        if not n:
            misses.append(item)

    total = [sum(v[i] for v in by_cat.values()) for i in range(4)]
    print(f"{'类别':<8}{'题数':>4}{'旧命中':>8}{'只词面':>8}{'词面+向量':>8}")
    for cat, (k, o, lx, n) in sorted(by_cat.items()):
        print(f"{cat:<8}{k:>4}{o:>8}{lx:>8}{n:>8}")
    print(f"{'合计':<8}{total[0]:>4}{total[1]:>8}{total[2]:>8}{total[3]:>8}"
          f"   ({total[1] / total[0]:.0%} → {total[2] / total[0]:.0%} → {total[3] / total[0]:.0%})")
    print(f"上下文大小: 旧 {len(old_ctx)} 字 / 新 平均 {sum(sizes) // len(sizes)} 字 (最大 {max(sizes)})")
    if args.show_miss and misses:
        print("\n新取法没命中:")
        for m in misses:
            print(f"  - [{m.get('cat')}] {m['q']}  期望 {m['expect']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
