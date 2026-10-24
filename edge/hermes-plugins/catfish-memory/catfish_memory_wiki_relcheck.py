"""知识卡关系的方向纠正 + 翻成一句话给核对 (10/23)。

# 为什么

scripts/eval_relations.py (5 份资料蒸馏出的 108 条关系, 对着原文逐条标): 只有 58% 是对的。
错法很集中:
  · 方向反了 (15): 「审计机制 参与 审计部」「中电福富 隶属 陈耿生」「省政府 印发 闽政〔2026〕12号」
    「福富××系统 记录 证书清单」(知识库里这种 10 条全反);
  · 原文没这回事 (19): 讲话里提到集团成果就写「柯瑞文 负责 星辰AI大模型」; 同页并列就写「配套」;
    警示案例里「某省地市公司」的朱某某写成「隶属 中电福富」;
  · 类型不对 (11)。
正文事实有核对 (catfish_memory_wiki_ground), 关系一条都没核对过。

# 做法

  1. 方向 (确定性, 不问模型): 每种关系两头该是什么 (隶属: 人/部门 → 部门/公司; 记录: 台账清单 → 被记的;
     印发: 文件 → 发文机关 …)。两头的类型从条目的 entity_type / concept_type 来, 没有就按名字猜
     (…公司 / …部 / …清单)。两头正好反过来 → 隶属↔包含 这种有反义词的就地换词; 没有反义词、
     对方在同一批卡里 → 挪到对方卡上; 都不行就留给第 2 步判。
  2. 每条关系按词表意思翻成一句话 (「审计部 参与 审计整改机制」→「审计部参与了审计整改机制」),
     跟正文事实一起交给 catfish_memory_wiki_ground 对原文判; 不支持的从 related 拿掉、进 unverified
     (「关系：「A」隶属「B」 —— 原因」), 员工在「没对上原文」里加回 / 丢掉。
     候选段按两头的名字找 (catfish_memory_wiki_ground.Retriever.anchored), 不按整句;
     两个名字原文里都没有的不问模型直接拿出。只收「支持」, 「部分」也拿出。

# 评测 (10/23, scripts/eval_relations.py check, qwen-flash, 两遍)

  流水线新写的 103 条 (原 61% 对): 核对后留下 45-50 条, 其中对 94-96%; 对的关系留下 65-71%
                                  (其余进「没对上原文」等员工加回); 方向自动改 4 条, 改对 3、改错 0
  知识库抽的 148 条 (原 61% 对):   留下 67 条, 其中对 91%; 对的留下 68%
  「部分」也收: 留下的对 82%, 对的留下 74% —— 不收
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Tuple

# ---------------------------------------------------------------- 类型粗分

_ENTITY_CLASS = {
    "person": "person", "department": "dept", "org": "org", "organization": "org", "company": "org",
    "subsidiary": "org", "customer": "org", "project": "project", "program": "project",
    "cert": "cert", "doc": "doc", "document": "doc", "notification": "doc", "data": "doc",
    "system": "system", "product": "system", "standard": "rule", "strategy": "rule",
}
_CONCEPT_RULE = {"rule", "standard", "principle", "process", "scope", "framework", "method", "category", "system"}
_KEYWORDS: List[Tuple[str, str]] = [
    (r"(材料|文件|台账|数据|清单|报告|解读|通知|课件|纪要|规划|方案|汇报|资料要点)", "doc"),
    (r"项目", "project"),
    (r"(平台|产品|系统|软件)", "system"),
    (r"(机构|公司|集团|政府)", "org"),
    (r"(机制|制度|规则|流程|口径|原则|标准|规范|策略|战略)", "rule"),
]
_NAME_HINTS: List[Tuple[str, str]] = [
    (r"(清单|台账|通知|规划|报告|课件|纪要|讲话|对照表|方案|〔\d{4}〕\d+号)", "doc"),
    (r"(有限公司|公司|集团|政府|委员会|研究院)$", "org"),
    (r"(部|中心|办公室|办公厅|小组|党委|支部|BG|事业部|处|厅|局)$", "dept"),
    (r"(机制|制度|规则|流程|原则|标准|规范)$", "rule"),
    (r"(平台|系统|软件|大模型|数据库)", "system"),
    (r"项目$", "project"),
    (r"某某$", "person"),
]


def classify(entity_type: str = "", concept_type: str = "", name: str = "") -> str:
    """粗分成 person / dept / org / project / system / cert / doc / rule; 认不出返回空。"""
    t = (entity_type or "").strip().lower()
    if t in _ENTITY_CLASS:
        return _ENTITY_CLASS[t]
    c = (concept_type or "").strip().lower()
    if c in _CONCEPT_RULE:
        return "rule"
    if c == "资料要点":
        return "doc"
    for raw in (entity_type or "", concept_type or ""):
        for pat, cls in _KEYWORDS:
            if raw and re.search(pat, raw):
                return cls
    for pat, cls in _NAME_HINTS:
        if name and re.search(pat, name):
            return cls
    return ""


# ---------------------------------------------------------------- 方向规则

#: 关系 → (本条目该是, 对方该是)。不在表里的关系不管方向。
_EXPECT: Dict[str, Tuple[frozenset, frozenset]] = {
    "记录": (frozenset({"doc"}), frozenset({"person", "dept", "org", "system", "project", "cert"})),
    "印发": (frozenset({"doc"}), frozenset({"org", "dept"})),
    "参与": (frozenset({"person", "dept", "org"}), frozenset({"project", "rule", "doc"})),
    "负责": (frozenset({"person", "dept", "org"}), frozenset({"project", "system", "cert", "rule", "doc"})),
    "遵循": (frozenset({"person", "dept", "org", "project", "system", "doc"}), frozenset({"rule", "doc"})),
    "依据": (frozenset({"person", "dept", "org", "project", "system", "doc", "rule"}), frozenset({"rule", "doc"})),
    "持有": (frozenset({"person", "dept", "org"}), frozenset({"cert", "system"})),
    "维护": (frozenset({"person", "dept"}), frozenset({"cert", "system"})),
    "在办": (frozenset({"person", "dept", "org"}), frozenset({"cert"})),
    "分析": (frozenset({"doc"}), frozenset({"person", "dept", "org", "project", "system", "cert", "rule"})),
}
#: 上下级: 级别 (人 < 部门 < 公司); 隶属 = 低 → 高, 包含 = 高 → 低
_RANK = {"person": 0, "dept": 1, "org": 2}
_INVERSE = {"隶属": "包含", "包含": "隶属"}


def direction(rel: str, src: str, tgt: str) -> str:
    """'ok' / 'reversed' / 'unknown' (类型认不出、或这种关系不管方向)。"""
    if not src or not tgt:
        return "unknown"
    if rel in _INVERSE:
        if src in _RANK and tgt in _RANK and src != tgt:
            low_to_high = _RANK[src] < _RANK[tgt]
            return "ok" if low_to_high == (rel == "隶属") else "reversed"
        return "unknown"
    rule = _EXPECT.get(rel)
    if not rule:
        return "unknown"
    s_ok, t_ok = rule
    if src in s_ok and tgt in t_ok:
        return "ok"
    if src in t_ok and tgt in s_ok:
        return "reversed"
    return "unknown"


# ---------------------------------------------------------------- 翻成一句话

_SENTENCE = {
    # 10/23: 原来写「上级或所属单位」, 模型按行政隶属判 —— 「智算中心 隶属 AI+引领工程」
    # 「考核机制 隶属 福富公司」都判不支持; 词表里隶属本来就包括 子项目→项目、制度→单位
    "隶属": "{a}属于{b}（{a}是{b}的下级、组成部分，或是{b}名下的项目、制度、产品）",
    "包含": "{a}包含{b}（{b}是{a}的下级、组成部分，或是{a}名下的项目、制度、产品）",
    "负责": "{a}负责{b}（由{a}主管、牵头或承担）",
    "参与": "{a}参与了{b}",
    "协作": "{a}与{b}有协作、合作关系",
    "依据": "{a}以{b}为依据",
    "遵循": "{a}遵循{b}",
    "使用": "{a}使用{b}",
    "持有": "{a}持有{b}",
    "认证": "{a}通过了{b}的认证或评估",
    "对标": "{a}与{b}做过对比、对标",
    "配套": "{a}与{b}配套使用（原文说了两者要一起用）",
    "替代": "{a}替代了{b}",
    "前置": "{a}是{b}的前置条件",
    "同类": "{a}与{b}是同一类东西",
    "在办": "{a}正在申请或办理{b}",
    "维护": "{a}维护{b}",
    "申报": "{a}申报{b}",
    "分析": "{a}分析了{b}",
    "记录": "{a}（台账、清单或数据）里记录了{b}",
    "印发": "{a}由{b}印发或发布（{b}是发文单位）",
    "关联": "{a}与{b}有直接关系",
}


def sentence(a: str, rel: str, b: str, meanings: Optional[Dict[str, str]] = None) -> str:
    tpl = _SENTENCE.get(rel)
    if tpl:
        return tpl.format(a=f"「{a}」", b=f"「{b}」")
    meaning = (meanings or {}).get(rel, "")
    return f"「{a}」{rel}「{b}」" + (f"（{meaning}）" if meaning else "")


def unverified_item(a: str, rel: str, b: str, reason: str) -> str:
    """进 unverified 的写法; Companion「加回」认这个前缀, 加回到关系里而不是正文。"""
    return f"关系：「{a}」{rel}「{b}」 —— {reason}"


# ---------------------------------------------------------------- 一批卡的方向纠正

def fix_directions(cards: Dict[str, dict], known: Dict[str, str]) -> List[str]:
    """cards: path → {"title", "class", "related": [(name, rel)]} (原地改)。
    known: 名字 → 类型 (本批卡 + 知识库已有条目)。返回改了什么 (日志用)。"""
    by_title = {c["title"]: p for p, c in cards.items()}
    log: List[str] = []
    for path, card in cards.items():
        keep = []
        for name, rel in card["related"]:
            d = direction(rel, card["class"], known.get(name, "") or classify(name=name))
            if d != "reversed":
                keep.append((name, rel))
                continue
            if rel in _INVERSE:
                keep.append((name, _INVERSE[rel]))
                log.append(f"{card['title']} {rel} {name} → {_INVERSE[rel]}")
            elif name in by_title:
                other = cards[by_title[name]]
                if all(n != card["title"] for n, _ in other["related"]):
                    other["related"].append((card["title"], rel))
                log.append(f"{card['title']} {rel} {name} → 挪到「{name}」: {name} {rel} {card['title']}")
            else:
                keep.append((name, rel))  # 改不了, 留给模型判
        card["related"] = keep
    return log


def iter_relations(cards: Dict[str, dict]) -> Iterable[Tuple[str, str, str]]:
    for path, card in cards.items():
        for name, rel in card["related"]:
            yield path, name, rel
