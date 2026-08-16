"""名字 → 条目 的解析 —— 跟前端 lib/wikiResolve.ts 同一套规则。

两侧读同一个契约 edge/contracts/wiki_resolve_cases.json, 任何一侧改了行为都会红。

# 为什么要有这个 (8/4)

在此之前"一个 related 名字指向哪个条目"有四套实现:

    WikiGraph.tsx findTarget ×2      title → slug → **title 子串, 取数组第一个**
    wikiRelevance.ts                 只按 title/slug 精确比
    _check_dangling_related (本文件)  只按 stem/title 精确比
    wiki_health.py                   又一套

同一条边, 图上连着、体检说它断了、蒸馏侧第三种看法。**口径分叉本身就是 bug** ——
没有哪一处是"对"的, 它们只是不同。

前端那个子串兜底最要命: `files.find(f => f.title.includes(name))` 取数组第一个,
而 wiki_list_files 是**按 mtime 倒序**排的。实测「中电福富」子串同时命中
「中电福富信息科技有限公司」(org) 和「销售许可证-中电福富API与应用系统安全审计
V2.0」(cert) —— 97 条边指向谁, 取决于哪个文件最近被改过。8/4 当天 org 较新所以
碰巧连对, 谁去动一下那张证书, 97 条边当场全翻过去, 无声无息。

断链至少看得出是"少了"; 连错是**理直气壮地错**。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["WikiNode", "resolve_wiki_ref", "load_nodes", "ResolveResult"]

_SLUG_NOISE = re.compile(r"[\s\-_.　]")
# 标点/分隔符 —— 中英文都要。实测断链里有一半是纯标点差异:
#   related 写「CS4 信息系统建设及服务能力等级证书」(空格)
#   title 是「CS4-信息系统建设及服务能力等级证书」(连字符)
#   related 写「通信工程施工总承包二级」, title 是「通信工程施工总承包(二级)」
# 这不是两个东西, 是同一个名字被打了两遍。
_PUNCT = re.compile(r"[\s\-_.·、,，:：;；(){}（）\[\]【】\"'“”‘’/\\　]")


def _norm(s: str) -> str:
    return s.strip().lower()


def _norm_title(s: str) -> str:
    """去掉所有标点和空白后比 —— 只吸收"同一个名字写法不同", 不做语义猜测。

    注意它**不会**把「中电福富」和「中电福富信息科技有限公司」合并 (字符本身
    就不同), 那种要靠 aliases 明写。这一档只处理标点噪声。
    """
    return _PUNCT.sub("", s.lower())


def _norm_slug(s: str) -> str:
    """zhongdianfufu / zhongdian-fufu / zhong_dian_fu_fu 视为同一个。

    跟 TS normSlug 同语义 (契约用例钉住)。
    """
    return _SLUG_NOISE.sub("", s.lower())


@dataclass
class WikiNode:
    rel_path: str
    slug: str
    title: str
    aliases: list[str] = field(default_factory=list)
    #: frontmatter 的 `deprecated: true`。8/15 加。
    #:
    #: **解析时不看这个字段** —— 废弃条目仍然要能被 `[[老名字]]` 解析到,
    #: 否则历史链接会大面积断。它只用来决定"要不要主动推荐给 LLM":
    #: prefetch 的 wiki 清单会跳过废弃条目 (员工机器上 261 个里有 30 个是
    #: 废弃的, 之前连同正主一起注进去, 既费 token 又让 LLM 在两份之间犹豫)。
    deprecated: bool = False


@dataclass
class ResolveResult:
    kind: str                       # "hit" | "miss" | "ambiguous"
    node: WikiNode | None = None
    how: str = ""                   # title | alias | slug | substring
    candidates: list[WikiNode] = field(default_factory=list)


def resolve_wiki_ref(name: str, nodes: list[WikiNode]) -> ResolveResult:
    """精确优先, 别名是写下来的事实, 子串只在唯一命中时才认。

    **结果不依赖 nodes 的顺序** —— 这是跟老实现最大的区别, 也是那个
    "图长什么样取决于哪个文件最近被改过" 的根源。
    """
    n = _norm(name)
    if not n:
        return ResolveResult("miss")

    hit = [x for x in nodes if _norm(x.title) == n]
    if hit:
        return ResolveResult("hit", hit[0], "title")

    hit = [x for x in nodes if any(_norm(a) == n for a in x.aliases)]
    if len(hit) == 1:
        return ResolveResult("hit", hit[0], "alias")
    if len(hit) > 1:
        return ResolveResult("ambiguous", candidates=hit)

    # 标点无关的 title —— 「CS4 信息系统…」vs「CS4-信息系统…」
    hit = [x for x in nodes if _norm_title(x.title) == _norm_title(name)]
    if len(hit) == 1:
        return ResolveResult("hit", hit[0], "title")
    if len(hit) > 1:
        return ResolveResult("ambiguous", candidates=hit)

    hit = [x for x in nodes if _norm(x.slug) == n]
    if hit:
        return ResolveResult("hit", hit[0], "slug")

    hit = [x for x in nodes if _norm_slug(x.slug) == _norm_slug(name)]
    if len(hit) == 1:
        return ResolveResult("hit", hit[0], "slug")

    # 子串: 保留是因为它救回不少存量的全称/简称边, 砍掉会大面积断链。
    # 但多命中时**绝不挑一个** —— 那正是 mtime 决定图长什么样的根源。
    hit = [x for x in nodes if n in _norm(x.title)]
    if len(hit) == 1:
        return ResolveResult("hit", hit[0], "substring")
    if len(hit) > 1:
        return ResolveResult("ambiguous", candidates=hit)

    return ResolveResult("miss")


def _fm_of(text: str) -> str:
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    return "" if end == -1 else text[3:end]


def _field(fm: str, key: str) -> str:
    for line in fm.splitlines():
        s = line.strip()
        if s.startswith(f"{key}:"):
            return s[len(key) + 1 :].strip()
    return ""


def _list_field(fm: str, key: str) -> list[str]:
    raw = _field(fm, key)
    if not raw:
        return []
    inner = raw.strip().lstrip("[").rstrip("]")
    return [p.strip().strip('"').strip("'") for p in inner.split(",") if p.strip()]


def load_nodes(catfish_home: Path, *, head_bytes: int | None = None) -> list[WikiNode]:
    """扫 wiki/entities + wiki/concepts, 建可解析的节点表。

    head_bytes (8/15 加): 只读每个文件前 N 字节。这里要的东西全在 frontmatter
    里 (title / aliases / deprecated), 正文一个字都用不上 —— 而 prefetch 每轮
    都调这个函数, 员工机器上 261 个文件读全文是 1.1 MB / 62 ms, 每轮都付。

    ⚠ 截断有个静默失效的风险: frontmatter 要是比 head_bytes 长, `_fm_of` 找不到
      收尾的 `---` 就返空串, title 悄悄退化成 slug —— 没有任何报错, 只是
      wiki 清单里突然冒出一堆拼音名。所以**没找到收尾就整份重读**。
      (实测员工机器上 frontmatter 最长 395 字节, 默认 4096 有十倍余量;
       但"现在够用"不是不做兜底的理由。)
    """
    out: list[WikiNode] = []
    for sub in ("wiki/entities", "wiki/concepts"):
        d = catfish_home / sub
        if not d.is_dir():
            continue
        try:
            entries = sorted(d.iterdir())
        except OSError:
            continue
        for p in entries:
            if p.suffix != ".md":
                continue
            try:
                if head_bytes is None:
                    text = p.read_text(encoding="utf-8", errors="replace")
                else:
                    with p.open("r", encoding="utf-8", errors="replace") as fh:
                        text = fh.read(head_bytes)
                    # 截断处正好切断了 frontmatter → 退回读全文, 不接受静默降级
                    if text.startswith("---") and text.find("\n---", 3) == -1:
                        text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            fm = _fm_of(text)
            out.append(
                WikiNode(
                    rel_path=f"{sub}/{p.name}",
                    slug=p.stem,
                    title=_field(fm, "title").strip("\"'") or p.stem,
                    aliases=_list_field(fm, "aliases"),
                    deprecated=_field(fm, "deprecated").strip().lower() == "true",
                )
            )
    return out
