"""frontmatter / markdown 解析 —— 从 catfish_memory_helpers.py 拆出 (8/15)。

只依赖 helpers 的 logger。被 wiki 和 merge 用。

这一层的判据必须跟前端 `lib/wikiResolve.ts` 和 `wiki_health.py` 保持一致 ——
8/4 就是因为蒸馏侧用"stem/title 精确相等"、前端用三档匹配, 同一条边一侧报
断链另一侧连着, 报出来的 16% 里大部分是假警报。
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

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


# 路径白名单 — 防 LLM 输出真 ---FILE: 逃逸 wiki/ 根.
# P1.1.1 fix (6/4): \w + re.UNICODE 让 slug 接受中文 (LLM 不遵守拼音, 直接用中文 name —
# Obsidian 也支持 unicode slug, 没必要强制 ASCII).
_WIKI_PATH_PATTERN = __import__("re").compile(
    r"^wiki/(entities|concepts)/[\w][\w_-]*\.md$",
    __import__("re").UNICODE,
)

# sentinel pattern 真 ---FILE: <path>--- 行.
_FILE_SENTINEL = __import__("re").compile(r"^---FILE:\s*(.+?)\s*---\s*$", __import__("re").MULTILINE)


def _parse_generation_output(text: str) -> Dict[str, str]:
    """切 LLM 输出按 ---FILE: <path>--- sentinel, 返 {rel_path: content} dict.

    路径白名单 _WIKI_PATH_PATTERN — 只允 wiki/entities/<slug>.md /
    wiki/concepts/<slug>.md. 其它 path silent skip (LLM 真乱写 / 真逃逸防护).
    """
    if not text:
        return {}
    # 找所有 sentinel 真 (path, start_offset) 真
    matches = list(_FILE_SENTINEL.finditer(text))
    if not matches:
        return {}

    files: Dict[str, str] = {}
    for i, m in enumerate(matches):
        rel_path = m.group(1).strip()
        # 白名单验
        if not _WIKI_PATH_PATTERN.match(rel_path):
            logger.debug("catfish-memory wiki parse: skip 非白名单 path %r", rel_path)
            continue
        content_start = m.end()
        content_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = text[content_start:content_end].strip()
        # 真 strip trailing fence 真 ``` (LLM 真有时 wrap markdown)
        if content.endswith("```"):
            content = content[:-3].rstrip()
        if content.startswith("```"):
            # 真 strip first line 真 ``` / ```markdown 之类
            content = content.split("\n", 1)[-1].lstrip()
        if not content:
            continue
        files[rel_path] = content
    return files


_FM_LIST_FIELDS_UNION = ("tags", "related", "sources", "aliases")
_FM_LIST_RE = re.compile(r"^([a-z_]+):\s*\[(.*?)\]\s*$", re.MULTILINE)
_FM_SCALAR_RE = re.compile(r"^([a-z_]+):\s*(.+?)\s*$", re.MULTILINE)


def _split_frontmatter_body(text: str) -> Tuple[str, str]:
    """切 markdown 真 (frontmatter_yaml, body). 没 frontmatter 返 ('', text)."""
    if not text.startswith("---\n"):
        return "", text
    end = text.find("\n---\n", 4)
    if end < 0:
        return "", text
    return text[4:end], text[end + 5 :]


def _split_top_level(inner: str) -> list[str]:
    r"""按顶层逗号切分, 不切进 {} 和 [[]] 里面 —— Rust split_top_level 的 Python 版。

    8/4: typed relation `{name: "中电福富", rel: "隶属"}` 里**有逗号**。老实现是
    "regex 抓所有 quoted 字符串", 于是切出 ['中电福富', '隶属', ...] —— 关系标签
    「隶属」变成一个假节点名, 合并时会被当成关联写回 frontmatter, 图谱上凭空多出
    dangling 边。

    读侧 (wiki_read.rs::split_top_level) 一直是 brace-aware 的; 写侧不是。
    **又是两侧不同口径** —— 而且这次是在我准备让 prompt 开始产出 typed relation
    的前一刻才查出来的, 差一点就上线了。
    """
    out: list[str] = []
    depth = 0
    buf: list[str] = []
    in_str = False
    quote = ""
    for c in inner:
        if in_str:
            buf.append(c)
            if c == quote:
                in_str = False
            continue
        if c in ('"', "'"):
            in_str = True
            quote = c
            buf.append(c)
        elif c in "{[":
            depth += 1
            buf.append(c)
        elif c in "}]":
            depth -= 1
            buf.append(c)
        elif c == "," and depth == 0:
            out.append("".join(buf).strip())
            buf = []
        else:
            buf.append(c)
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return [x for x in out if x]


_REL_NAME_RE = re.compile(r'name\s*:\s*["\']?([^"\',}]+)')


def _rel_item_name(item: str) -> str:
    """拿一条 related 的**节点名** —— 两种形态都认。

        {name: "中电福富", rel: "隶属"}  → 中电福富
        "[[北京福富]]"                   → 北京福富
        中电福富                          → 中电福富

    并集去重要按名字, 不能按整个字符串: 同一个节点带不带 rel 是同一条边,
    按字符串去重会留下两份。
    """
    v = item.strip()
    if v.startswith("{"):
        m = _REL_NAME_RE.search(v)
        return m.group(1).strip() if m else ""
    return v.strip('"').strip("'").strip().strip("[]").strip()


def _parse_frontmatter_lists(fm: str) -> Dict[str, List[str]]:
    """从 YAML frontmatter 抠 list 字段 ([\"[[a]]\", \"b\"]). 简单 regex,
    不全 YAML, 但对 prompt `生成` 真 format 够用.

    fix (6/5 测): 之前 regex `([^,]+)` 把 `, ` `分隔符` 也 match` 当 item
    → tags 重复. 改用先 split 再清, 简单稳.
    """
    out: Dict[str, List[str]] = {}
    for m in _FM_LIST_RE.finditer(fm):
        key = m.group(1)
        if key not in _FM_LIST_FIELDS_UNION:
            continue
        inner = m.group(2).strip()
        if not inner:
            out[key] = []
            continue
        # 先 split by `,` (不在 `[[..]]` 内), 再 strip quotes
        # 简化: regex 抓所有 quoted (带 [[..]] 或纯字符串) 优先, 否则裸 token
        # 8/4: 改用 brace-aware 切分 (见 _split_top_level 注释)。
        # 老实现是"regex 抓所有 quoted", typed relation 里的 rel 值会被当成
        # 独立条目 —— 关系标签变假节点。
        raw_items = _split_top_level(inner)
        items = []
        for it in raw_items:
            if it.startswith("{"):
                items.append(it)                    # typed, 整块保留
            else:
                items.append(it.strip('"').strip("'").strip())
        items = [s for s in items if s and s not in ("[", "]")]
        out[key] = items
    return out


def _parse_frontmatter_scalar(fm: str, key: str) -> Optional[str]:
    """抠 scalar 字段 (created / type / title 这种). 跳过 list ([..])."""
    for m in _FM_SCALAR_RE.finditer(fm):
        if m.group(1) == key:
            val = m.group(2).strip()
            if val.startswith("["):
                continue
            return val.strip('"').strip("'")
    return None


def _merge_wiki_file(old_text: str, new_text: str) -> str:
    """P18 (6/5 鸿波) — 重名 entity/concept merge 法 (纯 Python, 不烧 LLM).

    策略:
      frontmatter list 字段 (tags/related/sources/aliases): 旧 ∪ 新 (去重保序)
      frontmatter scalar:
        created: 保留旧 (entity `真`身份历史不丢`**)
        updated: 用新 (今天日期)
        title / *_type: 用新 (允许 reclassify)
      body: 用新, 旧 body 转 HTML 注释 `<!-- legacy body (created=<旧updated>) -->`
            放文末, 便于人工对照. 多次 update `只保留最近一份 legacy`.
    """
    old_fm, old_body = _split_frontmatter_body(old_text)
    new_fm, new_body = _split_frontmatter_body(new_text)
    if not new_fm:  # 新 file 没 frontmatter → 异常, 直接返新 (上层 fallback)
        return new_text

    old_lists = _parse_frontmatter_lists(old_fm)
    new_lists = _parse_frontmatter_lists(new_fm)

    # 1. list 字段并集替换到 new_fm
    merged_fm = new_fm
    for field in _FM_LIST_FIELDS_UNION:
        # 8/4: 按**节点名**去重, 不按整串 —— 同一个节点带不带 rel 是同一条边。
        # 两边都有时保留带 rel 的那个 (信息更多)。
        union: list[str] = []
        seen_names: dict[str, int] = {}
        for v in list(old_lists.get(field, [])):
            n = _rel_item_name(v) if field == "related" else v
            if n in seen_names:
                if v.startswith("{"):
                    union[seen_names[n]] = v
                continue
            seen_names[n] = len(union)
            union.append(v)
        for v in new_lists.get(field, []):
            n = _rel_item_name(v) if field == "related" else v
            if n in seen_names:
                if v.startswith("{"):
                    union[seen_names[n]] = v        # 新的带 rel → 升级
                continue
            seen_names[n] = len(union)
            union.append(v)
        if not union:
            continue
        # 双引号包每个 item (跟 Generation prompt 规范一致)
        quoted = ", ".join(
            v if (v.startswith("{") or v.startswith('"')) else f'"{v}"'
            for v in union
        )
        new_line = f"{field}: [{quoted}]"
        # 替已存的 list 字段; 没的话不动 (let new_fm 自然的没)
        merged_fm = re.sub(
            rf"^{field}:\s*\[.*?\]\s*$",
            new_line,
            merged_fm,
            count=1,
            flags=re.MULTILINE,
        )

    # 2. created 保留旧 (新生成的 created=今天, 改回旧)
    old_created = _parse_frontmatter_scalar(old_fm, "created")
    if old_created:
        merged_fm = re.sub(
            r"^created:\s*.+$",
            f"created: {old_created}",
            merged_fm,
            count=1,
            flags=re.MULTILINE,
        )

    # 3. body: 新 + 旧 legacy 注释
    old_updated = _parse_frontmatter_scalar(old_fm, "updated") or "unknown"
    stripped_old = old_body.strip()
    if stripped_old:
        # 防嵌套: 如果旧 body 已含 legacy 注释, 抽 inner 替, 不层叠
        inner_old = re.sub(
            r"<!--\s*legacy body \(.*?\)\s*-->\n?(.*?)\n?<!--\s*/legacy\s*-->",
            "",
            stripped_old,
            flags=re.DOTALL,
        ).strip()
        if inner_old:
            legacy_block = (
                f"\n\n<!-- legacy body (last updated={old_updated}) -->\n"
                f"{inner_old}\n"
                f"<!-- /legacy -->\n"
            )
            new_body = new_body.rstrip() + legacy_block

    return f"---\n{merged_fm}\n---\n{new_body}"


_FM_KEY_LINE = re.compile(r"^[a-z_]+:\s")
_FM_FENCE_LINE = re.compile(r"^---\s*$", re.MULTILINE)


def _ensure_frontmatter_fence(rel_path: str, content: str) -> str:
    r"""写盘前兜底: frontmatter 少了开头那行 `---` 就补上。

    # 为什么需要 (8/4 鸿波 "知识库里很多条目是拼音")

    读侧 (companion wiki_read.rs:81 split_frontmatter) 的规则很硬:

        let trimmed = text.trim_start();
        if !trimmed.starts_with("---") { return (String::new(), text); }

    不以 `---` 开头 → 整个 frontmatter 当正文, **一个字段都读不到**。后果不是
    "格式略丑":
      · title 读不到 → fallback 成文件名 → UI 上显示成拼音 slug
      · tags 读不到  → 标签筛选里消失
      · related 读不到 → 知识图谱里没有任何连线, 实体树掉进"未分类"

    8/4 在鸿波机器上实测: 255 条里有 19 条是这样, 文件里明明写着
    `title: 高新资质申报`, UI 上一直显示 `gaoxin-zizhi-shenbao`。而且全程零报错
    —— 读侧遇到这种文件是"正常返回一个 title=slug 的条目", 不是失败。

    # 为什么修在这里

    _write_wiki_files 是三条写盘路径的唯一收口, 而三条里只有一条保证了 `---`:

      新建            → content 是 _parse_generation_output 的 LLM 原始输出, 无校验
      已存在 + P19    → content 是 _call_merge_llm 的**原样回复**, 完全无校验
      已存在 + P18    → _merge_wiki_file 用 f"---\n{fm}\n---\n{body}" 重建 ✓

    P19 那条最容易犯: prompt 要求 LLM 输出完整 markdown, 拿到就
    `out[rel_path] = merged` 写盘。LLM 少打一行 `---` 就毁一个条目, 而它只在
    **更新已有条目**时触发 —— 坏掉的正好都是老条目。

    与其在三处各补一遍, 不如钉在收口。

    # 判据

    只在"看起来是丢了开头 fence"时才补, 不瞎改:
      1. 开头不是 `---`
      2. 且第一行长得像 YAML 键 (`^[a-z_]+:\s`)
      3. 且后面存在一行独立的 `---` (本该是收尾 fence)

    三条都满足才动手。没有 frontmatter 的纯正文 markdown 是合法的, 不碰。

    补的同时打 WARNING —— 这是上游 LLM 输出跑偏的信号, 静默修好等于把问题
    藏起来, 下次换个形式再犯。
    """
    if content.lstrip().startswith("---"):
        return content
    stripped = content.lstrip("\n")
    first_line = stripped.split("\n", 1)[0]
    if not _FM_KEY_LINE.match(first_line):
        return content          # 纯正文, 本来就没 frontmatter
    if not _FM_FENCE_LINE.search(stripped):
        return content          # 连收尾 fence 都没有, 不是这一类, 别猜
    logger.warning(
        "catfish-memory wiki: %s 的 frontmatter 缺开头的 --- (LLM 输出跑偏), "
        "已补上。不补的话读侧一个字段都读不到, title 会退化成文件名。",
        rel_path,
    )
    return "---\n" + stripped
