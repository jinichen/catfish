#!/usr/bin/env python3
"""知识库体检 —— 把"本体做得怎么样"变成可复算的数。

    python3 wiki_health.py            # 量 ~/.catfish
    python3 wiki_health.py <dir>      # 量指定目录
    python3 wiki_health.py --json     # 机器可读, 方便存基线做趋势

# 为什么要有这个 (8/4 鸿波 "怎么判定 ontology 的效果")

8/3~8/4 修的这一批毛病, 全部符合同一个描述: **写的时候没人拦, 坏了没人吭声**。
19 个文件缺开头的 `---` → title 静默退化成 slug; 21 组同名条目拆成 50 个文件;
408 条关系边 0 条带类型; 65 条边指向不存在的节点。这些都不会报错, 只会让员工
"觉得鲶鱼记性不太好"。

所以效果不能靠翻页看感觉, 得有个每周能重跑的数。

# 但要说清楚这个脚本量不到什么

下面全是**结构指标**, 它们是必要条件不是充分条件。类型 100% 覆盖、零重复、
零 dangling 的知识库, 照样可能答不对问题 —— 因为内容本身可能是错的, 或者
关系连对了但连的是没用的关系。

**本体真正的价值在"跨实体的问题能不能顺着关系答出来"**, 那个只能靠金标问题集
人工判分 (见文件末尾 GOLD_QUESTIONS 的说明)。结构指标的作用是: 结构烂的时候,
答不对是必然的, 先把地基量出来。

解析一律复用 catfish_memory_helpers 里的函数, 不另写一套 —— 两侧口径不一致
是今天反复踩的坑, 量出来的数必须跟系统实际看到的一致。
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from catfish_memory_helpers import (  # noqa: E402
    _canon_subtype,
    _normalize_slug_for_dedup,
    _parse_frontmatter_lists,
    _read_title_of,
    _rel_item_name,
)

KINDS = {"entities": "entity_type", "concepts": "concept_type"}


def _split_fm(text: str) -> tuple[str, str]:
    """跟 Rust split_frontmatter 同语义: 不以 --- 开头 → 整篇算正文。

    这一条正是 19 个文件"看起来好好的却什么都读不出来"的根源, 所以体检必须
    用同一个判据, 否则会把坏文件算成好的。
    """
    if not text.startswith("---"):
        return "", text
    end = text.find("\n---", 3)
    if end == -1:
        return "", text
    return text[3:end], text[end + 4 :]


def _fm_field(fm: str, key: str) -> str:
    for line in fm.splitlines():
        s = line.strip()
        if s.startswith(f"{key}:"):
            return s[len(key) + 1 :].strip().strip("\"'")
    return ""


def scan(root: Path) -> dict:
    wiki = root / "wiki"
    files: list[tuple[str, Path, str]] = []
    for kind in KINDS:
        for p in sorted((wiki / kind).glob("*.md")):
            files.append((kind, p, p.read_text(encoding="utf-8", errors="replace")))

    total = len(files)
    r: dict = {"总条目": total, "entity": 0, "concept": 0}
    if total == 0:
        return r
    r["entity"] = sum(1 for k, _, _ in files if k == "entities")
    r["concept"] = total - r["entity"]

    no_fm, typed_ok, type_empty, off_vocab = [], 0, [], Counter()
    edges_total = edges_typed = 0
    dangling: list[str] = []
    employee = 0
    by_display: dict[str, list[str]] = defaultdict(list)
    by_slug: dict[str, list[str]] = defaultdict(list)

    # 先建"存在哪些节点"的索引, 用显示名 —— 跟读侧一致
    known: set[str] = set()
    for kind, p, _ in files:
        known.add(_read_title_of(p))
        known.add(p.stem)

    for kind, p, text in files:
        fm, _body = _split_fm(text)
        if not fm:
            no_fm.append(f"{kind}/{p.name}")

        subtype = _fm_field(fm, KINDS[kind])
        if subtype:
            canon, unknown = _canon_subtype(f"wiki/{kind}/{p.name}", subtype)
            typed_ok += 1
            if unknown:
                off_vocab[canon] += 1
        else:
            type_empty.append(f"{kind}/{p.name}")

        for item in _parse_frontmatter_lists(fm).get("related", []):
            edges_total += 1
            if item.lstrip().startswith("{"):
                edges_typed += 1
            name = _rel_item_name(item)
            if name and name not in known:
                dangling.append(f"{kind}/{p.name} → {name}")

        if _fm_field(fm, "authored_by") == "employee":
            employee += 1

        by_display[_read_title_of(p)].append(f"{kind}/{p.name}")
        by_slug[f"{kind}/{_normalize_slug_for_dedup(p.stem)}"].append(p.name)

    dup_display = {k: v for k, v in by_display.items() if len(v) > 1}
    dup_slug = {k: v for k, v in by_slug.items() if len(v) > 1}
    dup_files = sum(len(v) for v in dup_display.values())

    r.update(
        {
            "缺frontmatter": len(no_fm),
            "缺frontmatter明细": no_fm[:10],
            "有类型": typed_ok,
            "类型为空": len(type_empty),
            "词表外类型": dict(off_vocab),
            "重复组": len(dup_display),
            "重复涉及文件": dup_files,
            "重复明细": {k: v for k, v in list(dup_display.items())[:10]},
            "slug变体组": len(dup_slug),
            "关系边": edges_total,
            "带类型的边": edges_typed,
            "dangling边": len(dangling),
            "dangling明细": dangling[:10],
            "人工确认": employee,
        }
    )
    return r


def _pct(n: int, d: int) -> str:
    return f"{n / d * 100:5.1f}%" if d else "  n/a"


def report(r: dict) -> None:
    t, e = r["总条目"], r["关系边"]
    if t == 0:
        print("wiki 目录是空的")
        return
    print(f"\n条目 {t}  (entity {r['entity']} · concept {r['concept']})")
    print(f"关系边 {e}\n")
    rows = [
        ("frontmatter 完整", t - r["缺frontmatter"], t,
         "缺开头 --- 的文件, title/tags/related 全部读不出来"),
        ("类型已标注", r["有类型"], t, "entity_type / concept_type 非空"),
        ("无重复", t - r["重复涉及文件"], t, "按员工看得见的名字分组"),
        ("关系带类型", r["带类型的边"], e, "{name, rel} 形式, 光有边不知道是什么关系"),
        ("引用有效", e - r["dangling边"], e, "指向真实存在的节点"),
        ("人工确认过", r["人工确认"], t, "authored_by: employee — 蒸馏不会覆盖正文"),
    ]
    w = max(len(n) for n, *_ in rows)
    for name, ok, tot, note in rows:
        print(f"  {name:<{w}}  {_pct(ok, tot)}  {ok:>4}/{tot:<4}  {note}")
    if r["词表外类型"]:
        print(f"\n  词表外类型: {r['词表外类型']}")
    for k, label in (("缺frontmatter明细", "缺 frontmatter"),
                     ("dangling明细", "指向不存在的节点")):
        if r.get(k):
            print(f"\n  {label} (前 10):")
            for x in r[k]:
                print(f"    · {x}")
    if r["重复明细"]:
        print("\n  重名 (前 10):")
        for name, fs in r["重复明细"].items():
            print(f"    · {name}: {', '.join(fs)}")
    print()


# ── 结构指标之外 ──────────────────────────────────────────
#
# 上面全部满分也不代表本体有用。真正的判据是**跨实体的问题能不能答出来** ——
# 那正是"有本体"相对"一堆散条目"的唯一实质优势。
#
# 建议维护一个 20 题左右的金标集 (wiki/queries/gold.md), 每题写死:
#   问题 / 正确答案 / 答对必须用到哪几条条目
# 挑那些**单条目答不了、必须顺着关系走两跳**的题, 例如:
#   「中电福富有哪些人拿了高新认定相关的资质?」  org → person → cert
#   「ITSS 运维资质是谁在负责, 走的哪个流程?」    cert → person → process
# 单条目就能答的题没有区分度, 测不出本体的价值。
#
# 每次蒸馏逻辑有改动就重跑一遍, 记答对数。结构指标看地基, 金标集看效果,
# 两个都要 —— 结构烂的时候答不对是必然, 结构好了答不对才说明问题在内容。

if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--json"]
    root = Path(args[0]) if args else Path.home() / ".catfish"
    res = scan(root)
    if "--json" in sys.argv:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        report(res)
