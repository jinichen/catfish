"""wiki 写盘 / 去重 / 本体体检 / 员工正文保护 —— 从 catfish_memory_helpers.py 拆出 (8/15)。

依赖 fm (frontmatter 解析) 和 helpers 的 logger。

`_write_wiki_files` 是三条写盘路径的唯一收口, 动它之前先读它自己的 docstring。
`_is_employee_authored` 那条线是 P19: 员工亲手改过的条目, 后台蒸馏连送都不送
给 LLM —— 边界划在"送不送", 不是划在"验收不验收"。
"""
from __future__ import annotations

from pathlib import Path
import re
from typing import Dict, Optional, Tuple

# 本模块有两种加载方式, import 形式必须两种都活:
#   · hermes 进程内 —— plugins/memory/__init__.py 用 spec_from_file_location
#     + submodule_search_locations 加载, 是真包, **相对 import 才 work**
#     (它全程不碰 sys.path, 裸绝对 import 找不到兄弟模块)
#   · wiki_health.py 独立脚本 —— 自己 sys.path.insert, 此时没有父包,
#     相对 import 反过来会炸
# 见 tests/test_loader_fidelity.py, 那里每种方式各起一个干净子进程验。
try:
    from .catfish_memory_helpers import logger  # noqa: F401
except ImportError:  # 独立脚本模式 (无父包)
    from catfish_memory_helpers import logger  # noqa: F401
try:
    from .catfish_memory_wiki_provenance import (  # noqa: F401
        Provenance, _RELATION_FALLBACK, _canon_relation, _canon_source,
        _is_untyped_relation, _normalize_relations, _normalize_sources,
        detect_conflicts, parse_conflicts, record_conflicts,
    )
except ImportError:  # 独立脚本模式 (无父包)
    from catfish_memory_wiki_provenance import (  # noqa: F401
        Provenance, _RELATION_FALLBACK, _canon_relation, _canon_source,
        _is_untyped_relation, _normalize_relations, _normalize_sources,
        detect_conflicts, parse_conflicts, record_conflicts,
    )
try:
    from .catfish_memory_fm import _FM_LIST_FIELDS_UNION, _ensure_frontmatter_fence, _merge_wiki_file, _parse_frontmatter_lists, _rel_item_name, _split_frontmatter_body  # noqa: F401
except ImportError:  # 独立脚本模式 (无父包)
    from catfish_memory_fm import _FM_LIST_FIELDS_UNION, _ensure_frontmatter_fence, _merge_wiki_file, _parse_frontmatter_lists, _rel_item_name, _split_frontmatter_body  # noqa: F401


#: P3.5.205/206 在 prompt 里禁的"结论词" —— 现在真的去查一遍。
#: 只查, 不改内容: 这些词出现不代表一定错, 但它是"LLM 在做因果推断/价值判断"
#: 的信号, 而知识库要的是日志字面事实。
_CONCLUSION_WORDS = (
    "决定", "因此", "意味着", "视为红线", "直接影响",
)


def _scan_conclusion_words(rel_path: str, content: str) -> list[str]:
    r"""扫正文里的禁用结论词, 命中就打 WARNING。返回命中的词。

    # 为什么加这个 (8/4 鸿波 "要怎么提高准确性")

    prompt 里写了一整套准确性约束 (P3.5.205 / P3.5.206):

        - 只用日志字面出现的事实 rephrase, 不做因果推断 / 价值判断 / 生动化修饰
        - 允许衔接词: 同时 / 然后 / 目前 / 另外
        - 禁结论词: 决定 / 因此 / 意味着 / 影响 / 视为红线 / 直接影响

    但**没有一条是可执行的检查** —— 全靠 LLM 自觉。写进 prompt ≠ 生效, 这跟
    8/3~8/4 查了一整天的那些静默失败是同一个形状: 规则写了, 没人验, 于是跑偏
    了也没有任何东西会喊一声。

    # 为什么只警告不拦

    这些词出现不等于内容错 —— 员工原话里就可能有"决定"。硬拦会丢数据, 而且
    这是文风判断, 不该由一个词表说了算。

    它的价值是**让漂移可见**: 日志里出现这类 WARNING 变多, 说明蒸馏 prompt
    的约束正在失效 (换了模型 / prompt 被改 / 上游 hermes 变了), 该去查了。
    在此之前, 这种漂移是完全无声的。

    注意 "影响" 没进词表: 它在中文里太常见 ("影响范围" / "受影响的系统"),
    误报会淹掉真信号。词表宁可漏, 不可吵 —— 一个天天响的告警等于没有告警。
    """
    _, body = _split_frontmatter_body(content)
    hits = [w for w in _CONCLUSION_WORDS if w in body]
    if hits:
        logger.warning(
            "catfish-memory wiki: %s 正文出现禁用结论词 %s —— prompt 要求只 rephrase "
            "日志字面事实, 不做因果推断/价值判断. 内容照写不拦, 但这是蒸馏跑偏的信号.",
            rel_path, "/".join(hits),
        )
    return hits


try:
    from .catfish_memory_wiki_dedup import (  # noqa: F401
        _drop_claimed_aliases, _normalize_slug_for_dedup, _read_title_of,
        _redirect_to_existing_equivalent, _title_of_content,
    )
except ImportError:  # 独立脚本模式 (无父包)
    from catfish_memory_wiki_dedup import (  # noqa: F401
        _drop_claimed_aliases, _normalize_slug_for_dedup, _read_title_of,
        _redirect_to_existing_equivalent, _title_of_content,
    )


# ─────────────────────────────────────────────────────────────
# 受控词表 —— 把"事实上已经存在的本体"显式化 (8/4 鸿波 "是不是应该用 ontology")
# ─────────────────────────────────────────────────────────────
#
# 8/4 实测鸿波机器的类型分布:
#
#     entity_type   cert 81 · person 13 · project 8 · org 6 · department 3
#                   + notification/standard/data 各 1 · 空 33 (22%)
#     concept_type  principle 18 · standard 18 · process 17 · rule 15
#                   + 规则 1 · 标准 1 · 流程 1 · system 1 · 空 1
#
# 两个事实同时成立:
#   · 类型**自然收敛**了 —— entity 前 5 类覆盖 111/147, concept 前 4 类覆盖 68/73。
#     不是无限发散, 定词表的成本很低。
#   · 但没有约束, 边缘就漂 —— rule/规则、standard/标准、process/流程 中英文并存,
#     指的是同一个东西。
#
# P3.5.176 删掉 enum 的理由是"enum 是硬编码"。但结果不是"更灵活", 是**没有词汇表**:
# 同义词各写各的, 筛选和分组就散了。
#
# 所以这里不是引入一个新本体, 是把已经长出来的那个写下来 + 加校验。
# 设计上守两条:
#   1. **归一化而不是拒绝** —— 规则→rule 这种直接改, 不丢数据
#   2. **不认识的新类型放行 + WARNING** —— 词表要能长, 但长了要有人知道
#
# 受控词表从 edge/contracts/wiki_type_vocab.json 读 —— Rust 侧 (wiki_write.rs)
# 读的是同一份。8/4: 之前只有蒸馏侧归一化, UI 手工建的不归一, 同一个词表两条
# 产线两个结果。抽成共享文件, 不给它分叉的机会 (跟名字解析那套 contract 同因)。
def _load_type_vocab() -> tuple[dict, frozenset, frozenset]:
    import json as _json
    p = Path(__file__).resolve().parents[2] / "contracts" / "wiki_type_vocab.json"
    try:
        d = _json.loads(p.read_text(encoding="utf-8"))
        return d["aliases"], frozenset(d["entity_types"]), frozenset(d["concept_types"])
    except (OSError, ValueError, KeyError) as e:
        # 读不到不能让蒸馏整个挂掉 —— 退化成"不归一化", 但要出声
        logger.warning("读不到受控词表 %s (%s), 类型归一化本次跳过", p, e)
        return {}, frozenset(), frozenset()


_TYPE_ALIASES, _ENTITY_TYPES, _CONCEPT_TYPES = _load_type_vocab()


def _canon_subtype(rel_path: str, raw: str) -> tuple[str, bool]:
    """把 entity_type / concept_type 归一化到受控词表。

    返 (归一化后的值, 是否表外)。表外不拒绝, 只报告 —— 词表要能长。
    """
    v = (raw or "").strip()
    if not v:
        return "", False
    low = v.lower()
    canon = _TYPE_ALIASES.get(v) or _TYPE_ALIASES.get(low) or low
    known = _ENTITY_TYPES if "entities/" in rel_path else _CONCEPT_TYPES
    if canon != low:
        logger.info(
            "catfish-memory wiki: %s 的类型 %r 归一化成 %r (受控词表)",
            rel_path, v, canon,
        )
    elif canon not in known:
        logger.warning(
            "catfish-memory wiki: %s 用了词表外的类型 %r —— 不拦, 但词表该不该加它, "
            "值得看一眼 (现有: %s)",
            rel_path, canon, "/".join(sorted(known)),
        )
        return canon, True
    return canon, False


_SUBTYPE_LINE = re.compile(r"^(entity_type|concept_type):\s*(.*)$", re.MULTILINE)


def _normalize_types(rel_path: str, content: str) -> str:
    """写盘前把 frontmatter 里的类型字段归一化。"""
    fm, body = _split_frontmatter_body(content)
    if not fm:
        return content
    changed = False

    def _sub(m: "re.Match[str]") -> str:
        nonlocal changed
        key, val = m.group(1), m.group(2).strip()
        canon, _ = _canon_subtype(rel_path, val)
        if canon and canon != val:
            changed = True
            return f"{key}: {canon}"
        return m.group(0)

    new_fm = _SUBTYPE_LINE.sub(_sub, fm)
    if not changed:
        return content
    return f"---\n{new_fm}\n---\n{body}"


_RESERVED_TITLE_PREFIXES = ("raw/", "raw\\", "wiki/", "file:")

_ONTOLOGY_ACTIVE = "active"
_ONTOLOGY_PENDING = "pending"
_ONTOLOGY_TERMINAL = frozenset(("rejected", "deprecated"))


def _frontmatter_value(fm: str, key: str) -> str:
    match = re.search(rf"^{re.escape(key)}:\s*(.*?)\s*$", fm, re.MULTILINE)
    return match.group(1).strip() if match else ""


def _ensure_entity_aliases(rel_path: str, content: str) -> str:
    """Keep generated entities structurally complete without inventing aliases."""
    if "entities/" not in rel_path:
        return content
    fm, body = _split_frontmatter_body(content)
    if not fm or re.search(r"^aliases:\s*", fm, re.MULTILINE):
        return content
    lines = fm.splitlines()
    title_index = next(
        (i for i, line in enumerate(lines) if line.startswith("title:")), None
    )
    insert_at = (title_index + 1) if title_index is not None else len(lines)
    lines.insert(insert_at, "aliases: []")
    new_fm = "\n".join(lines)
    return f"---\n{new_fm}\n---\n{body}"


def _validate_new_ontology(rel_path: str, content: str) -> str | None:
    """Return a hard write error for records that cannot be ontology nodes."""
    fm, body = _split_frontmatter_body(content)
    if not fm:
        return "缺少 frontmatter"
    expected_kind = "entity" if "entities/" in rel_path else "concept"
    if _frontmatter_value(fm, "type") != expected_kind:
        return f"type 必须为 {expected_kind}"
    title = _frontmatter_value(fm, "title")
    if not title:
        return "title 不能空"
    if title.lower().startswith(_RESERVED_TITLE_PREFIXES):
        return "title 不能是 raw/sources 等原始资料路径"
    if re.search(r"\[\[\s*(?:raw/|raw\\|wiki/|file:)", body, re.IGNORECASE):
        return "正文不能把 raw/sources 等原始资料路径写成 wikilink"
    # 蒸馏结果允许先落盘再补齐词表，避免因为 LLM 漏一个字段而丢失整份内容。
    # 缺 subtype 会在下方 ontology_status 中标成 pending，不能进入关系图。
    return None


def _upsert_ontology_status(content: str, status: str) -> str:
    """写入本体状态；状态是机器可读的，不混进正文。"""
    fm, body = _split_frontmatter_body(content)
    if not fm:
        return content
    line = f"ontology_status: {status}"
    if re.search(r"^ontology_status:\s*.*$", fm, re.MULTILINE):
        new_fm = re.sub(r"^ontology_status:\s*.*$", line, fm, count=1, flags=re.MULTILINE)
    else:
        lines = fm.splitlines()
        type_index = next((i for i, value in enumerate(lines) if value.startswith("type:")), None)
        insert_at = type_index + 1 if type_index is not None else 0
        lines.insert(insert_at, line)
        new_fm = "\n".join(lines)
    return f"---\n{new_fm}\n---\n{body}"


def _ontology_relation_names(rel_path: str, content: str) -> tuple[list[str], bool]:
    """读取 frontmatter 与正文中的关系，并指出是否存在旧式无类型关系。"""
    fm, body = _split_frontmatter_body(content)
    raw_items = _parse_frontmatter_lists(fm).get("related", [])
    names: list[str] = []
    untyped = False
    for item in raw_items:
        name = _rel_item_name(item).strip().strip('"').strip("'")
        if not name:
            continue
        names.append(name.replace("[[", "").replace("]]", "").split("|", 1)[0].strip())
        # 9/17: 兜底「关联」也算没类型 —— 它只说明有关系, 不说明是什么关系
        if _is_untyped_relation(item):
            untyped = True
    # 图谱读侧也承认正文 wikilink；没有写进 typed related 时，只能待确认。
    body_names = [m.strip() for m in re.findall(r"\[\[([^\]]+)\]\]", body) if m.strip()]
    for name in body_names:
        if name not in names:
            names.append(name)
            untyped = True
    return names, untyped


def _classify_ontology_status(catfish_home: Path, rel_path: str, content: str) -> tuple[str, list[str]]:
    """在写盘前把条目分成 active/pending。

    这是保护网，不删除内容：pending 条目仍在树中可见，只有关系图不消费它。
    已明确标为 rejected/deprecated 的条目不被自动复活。
    """
    if "entities/" not in rel_path and "concepts/" not in rel_path:
        return _ONTOLOGY_ACTIVE, []
    fm, _ = _split_frontmatter_body(content)
    explicit = _frontmatter_value(fm, "ontology_status").lower()
    if explicit in _ONTOLOGY_TERMINAL:
        return explicit, []
    subtype_key = "entity_type" if "entities/" in rel_path else "concept_type"
    subtype = _frontmatter_value(fm, subtype_key).strip().lower()
    names, untyped = _ontology_relation_names(rel_path, content)
    reasons: list[str] = []
    if not subtype:
        reasons.append("missing_type")
    # 9/24 鸿波「没有关系的还要强制确定关系, 不是很乱吗」: 关系是可选的。没写关系
    # 的条目直接 active —— 以前判 pending (missing_relation), 员工只能硬配一条。
    if not names:
        return (_ONTOLOGY_PENDING, reasons) if reasons else (_ONTOLOGY_ACTIVE, [])
    if untyped:
        reasons.append("untyped_relation")
    try:
        from .wiki_resolve import load_nodes, resolve_wiki_ref  # noqa: PLC0415
    except ImportError:
        from wiki_resolve import load_nodes, resolve_wiki_ref  # noqa: PLC0415
    # 9/24: 目标是「待确认」也算找得到。以前只认 active 目标 —— 同一轮蒸馏建的
    # 几条互相引用, 一条 pending 就全体 pending, 关系明明都写对了还要员工逐条点
    # (9/20 日志那批 6 条就是这样)。只排除明确废弃/驳回的条目。
    nodes = [
        node for node in load_nodes(catfish_home)
        if not node.deprecated and node.ontology_status.lower() not in _ONTOLOGY_TERMINAL
    ]
    for name in names:
        result = resolve_wiki_ref(name, nodes)
        if result.kind == "ambiguous":
            reasons.append("ambiguous_relation")
        elif result.kind != "hit":
            reasons.append("unresolved_relation")
    return (_ONTOLOGY_PENDING, sorted(set(reasons))) if reasons else (_ONTOLOGY_ACTIVE, [])


# ─────────────────────────────────────────────────────────────
# 引用完整性 —— related 指向不存在的节点 (8/4 实测 65/408 = 16%)
# ─────────────────────────────────────────────────────────────


def _check_dangling_related(catfish_home: Path, rel_path: str, content: str) -> list[str]:
    r"""报告 related 里指向不存在节点的名字。只报告, 不改内容。

    8/4 实测: 408 条 related 边里 65 条 (16%) 指向的节点根本不存在。

    为什么不自动删: dangling 有两种, 语义完全相反 ——
      · LLM 编了个不存在的东西        → 该删
      · 这个节点还没被蒸馏出来, 之后会有 → 删了反而破坏未来的连接
    分不清就不该动。当前 UI 对缺失节点只显示虚拟态，不会自动建真文件；因此这里
    也只报告，不会因为待补节点而生成伪概念。

    但 16% 无声无息不行 —— 图谱上那些边直接消失, 没人知道。报出来。
    """
    fm, _ = _split_frontmatter_body(content)
    if not fm:
        return []
    names = [
        n.strip().strip('"').strip("'").strip("[]").strip()
        for n in _parse_frontmatter_lists(fm).get("related", [])
    ]
    names = [n for n in names if n]
    if not names:
        return []
    # 8/4: 改走 wiki_resolve —— 前端 lib/wikiResolve.ts 同一套规则, 由
    # edge/contracts/wiki_resolve_cases.json 对拍钉住。
    #
    # 原来这里是"stem/title 精确相等", 比前端严格得多 (前端还有别名、标点归一、
    # 唯一子串三档)。于是同一条边蒸馏侧报断链、图上其实连着 —— 报出来的 16%
    # 里大部分是假警报, 真问题反而被淹掉。
    # 这里必须相对优先、绝对兜底 —— 本模块有两种加载方式, 语义相反:
    #
    #   · hermes 进程内 (memory.provider: catfish-memory)
    #     ~/.hermes/hermes-agent/plugins/memory/__init__.py 用
    #     spec_from_file_location(..., submodule_search_locations=[provider_dir])
    #     加载, 并把同目录每个 *.py 预注册成 `plugins.memory.catfish-memory.<stem>`。
    #     它**全程没动过 sys.path**, 所以裸 `import wiki_resolve` 找不到;
    #     `.wiki_resolve` 才找得到 (就是预注册的那个)。
    #
    #   · dream_cli.py 子进程 (员工点"蒸馏"按钮)
    #     python3 跑脚本时脚本目录自动进 sys.path[0], 此时 helpers 没有父包,
    #     相对 import 反过来会炸, 只能走绝对。
    #
    # 原来只有绝对那一行。它没出事只是因为 ~/.catfish/memory_plugin.yaml 里
    # `wiki.auto_ingest: false` —— 进程内这条路根本没被走到。把那一位翻成 true,
    # _write_wiki_files 会在 2460 行抛 ModuleNotFoundError, 而它外面那层只
    # `except OSError`, 接不住; 再外面是 _spawn_summarize_thread 起的
    # fire-and-forget daemon 线程 —— 于是 wiki 一个字都写不进去, 且没人看得见。
    try:
        from .wiki_resolve import load_nodes, resolve_wiki_ref  # noqa: PLC0415
    except ImportError:
        from wiki_resolve import load_nodes, resolve_wiki_ref  # noqa: PLC0415

    nodes = load_nodes(catfish_home)
    missing = [n for n in names if resolve_wiki_ref(n, nodes).kind != "hit"]
    if missing:
        logger.info(
            "catfish-memory wiki: %s 的 related 有 %d 个指向不存在的节点 (%s) —— "
            "可能是 LLM 编的, 也可能是还没蒸馏出来的, 不自动删",
            rel_path, len(missing), ", ".join(missing[:4]),
        )
    return missing


# ─────────────────────────────────────────────────────────────
# 人工修正保护 (8/4 鸿波 "有的数据还是需要人修正的")
# ─────────────────────────────────────────────────────────────

_AUTHORED_BY_EMPLOYEE = re.compile(r"^authored_by:\s*employee\s*$", re.MULTILINE)
_LLM_APPENDIX_HEAD = "## 蒸馏补充 (待你确认)"


def _is_employee_authored(text: str) -> bool:
    """这条是不是员工亲手写/改过的。"""
    fm, _ = _split_frontmatter_body(text)
    return bool(fm) and bool(_AUTHORED_BY_EMPLOYEE.search(fm))


def _append_as_appendix(old_text: str, new_text: str) -> str:
    r"""员工改过的条目 —— 新蒸馏内容只能进附录, 不许覆盖正文。

    # 为什么 (8/4)

    实测: 220 条 wiki 里 **219 条是 LLM 生成的, 只有 1 条 sources=manual**。
    而 frontmatter 里**没有任何字段记录"这条是谁写的"** —— 没有 author, 没有
    reviewed, 没有 verified。

    后果不是"信息缺失"。merge_files_with_llm 读文件时根本不看来源:

        old_text = target.read_text(...)          # 不管这是谁写的
        merged = await _call_merge_llm(old_text, new_content, model)

    **员工在知识体系 TAB 里手工改的内容, 下一次蒸馏会被原样喂给 LLM 重写。**
    你改掉一条错的断言, 几天后它可能又变回去了, 而且不会收到任何提示。

    8/4 加的那些验收只检查"字段有没有丢、正文有没有暴缩", **不检查"这段是不是
    人写的"** —— 一次完全合规的 LLM 重写照样能把人的修正抹掉。

    # 做法

    员工改过的条目 (authored_by: employee):
      · **正文原样保留**, 一个字不动
      · frontmatter 的 list 字段仍取并集 (tags/related 是累加语义, 不冲突)
      · 新蒸馏内容进「蒸馏补充 (待你确认)」附录, 员工自己决定要不要并进去
      · 多次蒸馏只保留最近一份附录, 不层叠

    不是直接丢掉新内容 —— 那会让知识库停止更新。是把**裁决权交回给人**:
    机器可以提出, 但改不了人已经定下的东西。
    """
    old_fm, old_body = _split_frontmatter_body(old_text)
    new_fm, new_body = _split_frontmatter_body(new_text)

    merged_fm = old_fm
    for field in _FM_LIST_FIELDS_UNION:
        old_lists = _parse_frontmatter_lists(old_fm)
        new_lists = _parse_frontmatter_lists(new_fm)
        union: list[str] = []
        seen: dict[str, int] = {}
        for v in old_lists.get(field, []) + new_lists.get(field, []):
            n = _rel_item_name(v) if field == "related" else v
            if n in seen:
                if v.startswith("{"):
                    union[seen[n]] = v
                continue
            seen[n] = len(union)
            union.append(v)
        if not union:
            continue
        quoted = ", ".join(
            v if (v.startswith("{") or v.startswith('"')) else f'"{v}"' for v in union
        )
        line = f"{field}: [{quoted}]"
        if re.search(rf"^{field}:\s*\[.*?\]\s*$", merged_fm, re.MULTILINE):
            merged_fm = re.sub(
                rf"^{field}:\s*\[.*?\]\s*$", line, merged_fm, count=1, flags=re.MULTILINE
            )
        else:
            merged_fm = merged_fm.rstrip() + "\n" + line

    # 正文: 员工那份原样保留, 砍掉上一次的附录再贴新的 (不层叠)
    body = re.split(rf"^{re.escape(_LLM_APPENDIX_HEAD)}\s*$", old_body, maxsplit=1,
                    flags=re.MULTILINE)[0].rstrip()
    add = new_body.strip()
    if add:
        body += (
            f"\n\n{_LLM_APPENDIX_HEAD}\n\n"
            "<!-- 这段是后台蒸馏新抽出来的, 没有覆盖你写的正文。确认后可以自己并进去, "
            "或者直接删掉这一节。 -->\n\n" + add + "\n"
        )
    return f"---\n{merged_fm}\n---\n\n{body}\n"


def _write_wiki_files(
    catfish_home: Path,
    files: Dict[str, str],
    skip_merge_paths: Optional[set] = None,
    provenance: "Provenance | None" = None,
) -> Tuple[int, int]:
    """写 wiki files 真 ~/.catfish/wiki/entities/ + wiki/concepts/. 返 (n_entities, n_concepts).

    P18 (6/5 鸿波): 同 slug 触发 _merge_wiki_file (frontmatter list 并集 +
    保留 created + body 新+ 旧 legacy 注释留底), 不再无脑 overwrite.
    P19 (6/5 鸿波): skip_merge_paths 真 path set 已被 _call_merge_llm 处理过
    (LLM merge), 直接 overwrite. 没 merge `走 P18 regex merge 安全网`.
    新建 file (不重名) 沿用 overwrite.
    9/17: provenance = 本轮实际读过的日志日期 + 上传资料, 用来校验/回填 sources
    (见 _normalize_sources)。merge 前对新内容做一次带 provenance 的校验, merge 后
    再做一次不带 provenance 的格式收敛 —— 老条目的历史来源不能被这一轮否定。
    """
    if not files:
        return (0, 0)
    skip = skip_merge_paths or set()
    n_entities = 0
    n_concepts = 0
    for rel_path, content in files.items():
        # 8/4: LLM 每次造的拼音 slug 不一样 → 先看有没有规范化等价的已有文件,
        # 有就指回去走 merge, 不新建。
        rel_path = _redirect_to_existing_equivalent(catfish_home, rel_path, content)
        target = catfish_home / rel_path
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            # 9/17: 新内容先过 sources 校验 (丢编造的日期 / 回填本轮真来源), 再进 merge
            content = _normalize_sources(rel_path, _ensure_frontmatter_fence(rel_path, content), provenance)
            final_content = content
            # 9/17 冲突检测要对照的是**写盘前**的旧文件 (merge 之后就看不出谁覆盖了谁)
            old_on_disk = target.read_text(encoding="utf-8", errors="replace") if target.exists() else None
            # 8/4: 本体规则体检 —— 只报不拦, 见 report_ontology_gaps 文档
            for _gap in report_ontology_gaps(rel_path, final_content):
                logger.info("catfish-memory wiki 体检: %s —— %s", rel_path, _gap)
            if target.exists() and _is_employee_authored(
                target.read_text(encoding="utf-8", errors="replace")
            ):
                # 8/4: 员工改过的条目 —— LLM 不许覆盖正文, 新内容只进附录。
                try:
                    old_text = target.read_text(encoding="utf-8")
                    final_content = _append_as_appendix(old_text, content)
                    logger.info(
                        "catfish-memory wiki: %s 是员工改过的 (authored_by: employee), "
                        "正文保持原样, 新蒸馏内容放进「蒸馏补充」附录等他确认",
                        rel_path,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "catfish-memory 附录合并 %s 失败, **保留员工原文不动**: %s",
                        rel_path, e,
                    )
                    final_content = target.read_text(encoding="utf-8", errors="replace")
            elif target.exists() and rel_path not in skip:
                # 重名 + LLM 没处理 → P18 regex merge 安全网
                try:
                    old_text = target.read_text(encoding="utf-8")
                    final_content = _ensure_frontmatter_fence(
                        rel_path, _merge_wiki_file(old_text, content)
                    )
                    logger.info(
                        "catfish-memory wiki regex merge (P18 fallback): %s",
                        rel_path,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "catfish-memory wiki merge %s 失败 (fallback overwrite): %s",
                        rel_path, e,
                    )
                    final_content = _ensure_frontmatter_fence(rel_path, content)
            # 8/4: 本体规则体检 —— 只报不拦, 见 report_ontology_gaps 文档
            for _gap in report_ontology_gaps(rel_path, final_content):
                logger.info("catfish-memory wiki 体检: %s —— %s", rel_path, _gap)
            final_content = _normalize_types(rel_path, final_content)
            # 9/17: rel 归一到词表 (表外 → 「关联」等人定); sources 格式收敛
            # (merge 并进来的 employee_journal 这类老值在这里被清掉)
            final_content = _normalize_relations(rel_path, final_content)
            final_content = _normalize_sources(rel_path, final_content, None)
            final_content = _ensure_entity_aliases(rel_path, final_content)
            final_content = _drop_claimed_aliases(catfish_home, rel_path, final_content)
            if old_on_disk is not None:
                # 9/17 (semantica 第 2 条): 类型 / typed relation 跟旧文件不一致 →
                # 保留旧值, 新值记进 conflicts 等员工在关系工作台二选一。不静默覆盖。
                seen = (provenance.fill() or [""])[0] if provenance is not None else ""
                # 旧文件也先归一化类型词 (老条目可能还是「规则」而不是 rule), 免得假冲突
                final_content = record_conflicts(
                    rel_path, final_content, _normalize_types(rel_path, old_on_disk), seen,
                )
            ontology_error = _validate_new_ontology(rel_path, final_content)
            if ontology_error:
                logger.warning(
                    "catfish-memory 拒绝写入不完整本体条目 %s: %s",
                    rel_path,
                    ontology_error,
                )
                continue
            status, status_reasons = _classify_ontology_status(
                catfish_home, rel_path, final_content
            )
            final_content = _upsert_ontology_status(final_content, status)
            if status == _ONTOLOGY_PENDING:
                logger.warning(
                    "catfish-memory wiki: %s 写入 pending，关系需人工确认 (%s)",
                    rel_path, ",".join(status_reasons),
                )
            _scan_conclusion_words(rel_path, final_content)
            _check_dangling_related(catfish_home, rel_path, final_content)
            target.write_text(final_content + ("\n" if not final_content.endswith("\n") else ""), encoding="utf-8")
            if "entities/" in rel_path:
                n_entities += 1
            elif "concepts/" in rel_path:
                n_concepts += 1
        except OSError as e:
            logger.warning("catfish-memory write wiki file %s 失败: %s", rel_path, e)
    return (n_entities, n_concepts)




def report_ontology_gaps(rel_path: str, content: str) -> list[str]:
    """写入时体检 —— 缺 aliases / 关系没类型, 报出来。

    # 为什么只报不拦 (8/4)

    aliases 和 typed relation 这两条, 到今天为止**只在 prompt 里要求过, 没有
    任何代码检查**。408 条关系边 0 条带类型, 成因就是"读侧支持了、写侧 prompt
    没提"; 现在 prompt 提了, 但 LLM 哪天不配合照样没人知道 —— 从"没人要求"
    变成"要求了但不验证", 还是会静默退化。

    不拦是因为拦了就丢数据: 一条内容正确、只是没写别名的条目, 价值远大于零。
    但"不拦"不等于"不出声" —— 这两天所有最贵的 bug 都是不出声造成的。
    """
    gaps: list[str] = []
    fm, _ = _split_frontmatter_body(content)
    if not fm:
        return ["没有 frontmatter"]

    if "entities/" in rel_path and "aliases:" not in fm:
        gaps.append("没有 aliases 字段 (简称/全称对不上时会连不上或连错)")

    items = _parse_frontmatter_lists(fm).get("related", [])
    untyped = [i for i in items if _is_untyped_relation(i)]
    if items and untyped:
        gaps.append(
            f"{len(untyped)}/{len(items)} 条关系没有 rel 类型 "
            f"(只知道有关系, 不知道是什么关系)"
        )
    return gaps
