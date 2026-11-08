"""蒸馏写知识库前过一道 tool-bridge 的把关 (11/08 鸿波「以后新加入的还会出现这个问题吗」)。

11/08 给小鲶在对话里写知识库加了相似名查重和关系按类型校验 (tool-bridge wiki_guard.py), 但知识库 2/3 的内容
是这里的后台蒸馏写的 —— 「一级建造师持证人员名单 包含 陈功」就是蒸馏写出来的。三条写入产线要一个规则, 所以这里
直接借 tool-bridge 那份 (生产里跟 hermes 共一个 venv); 借不到 (独立跑测试、老安装) 就只记日志、不拦。

  redirect_to_alike   新建的条目跟已有的名字「像」(人力部 / 人力资源部) 且类型相容 → 写入路径指回那一条走 merge,
                      新标题记成它的别名 (标题照旧用已有的那条的)
  demote_bad_relations 关系按类型定义不允许的 (名单「包含」人) → 降成「关联」, 后面照旧算 untyped → 待确认, 交给人定
"""
from __future__ import annotations

from pathlib import Path
import re

try:
    from .catfish_memory_helpers import logger
    from .catfish_memory_fm import _split_frontmatter_body
    from .wiki_resolve import load_nodes
except ImportError:  # 独立脚本模式 (无父包)
    from catfish_memory_helpers import logger
    from catfish_memory_fm import _split_frontmatter_body
    from wiki_resolve import load_nodes

try:
    from catfish_tool_bridge import wiki_files as _tb_files
    from catfish_tool_bridge import wiki_guard as _tb_guard
except Exception:  # noqa: BLE001  (tool-bridge 不在这个 venv 里)
    _tb_files = None
    _tb_guard = None


def available() -> bool:
    return _tb_guard is not None


def _field(fm: str, key: str) -> str:
    for line in fm.splitlines():
        line = line.strip()
        if line.startswith(key + ":"):
            return line.split(":", 1)[1].strip().strip("\"'")
    return ""


def _set_field(content: str, key: str, value: str) -> str:
    fm, _body = _split_frontmatter_body(content)
    if not fm:
        return content
    rows = fm.splitlines()
    for i, row in enumerate(rows):
        if row.startswith(key + ":"):
            rows[i] = f"{key}: {value}"
            return content.replace(fm, "\n".join(rows), 1)
    return content


def _add_alias(content: str, alias: str) -> str:
    fm, _body = _split_frontmatter_body(content)
    if not fm or not alias:
        return content
    m = re.search(r"^aliases:[ \t]*\[(.*)\][ \t]*$", fm, re.MULTILINE)
    if not m:
        return content.replace(fm, fm + f'\naliases: ["{alias}"]', 1)
    inner = m.group(1).strip()
    if alias in inner:
        return content
    line = "aliases: [" + (inner + ", " if inner else "") + f'"{alias}"' + "]"
    return content.replace(fm, fm[: m.start()] + line + fm[m.end():], 1)


def redirect_to_alike(catfish_home: Path, rel_path: str, content: str) -> tuple[str, str]:
    """新建的页跟已有的「像」→ (已有那页的 rel_path, 改好标题 / 别名的内容); 不像或不可用 → 原样返回。"""
    if _tb_guard is None or (catfish_home / rel_path).exists():
        return rel_path, content
    fm, _body = _split_frontmatter_body(content)
    title = _field(fm, "title")
    if not title:
        return rel_path, content
    kind = "entity" if "entities/" in rel_path else "concept"
    subtype = _field(fm, "entity_type") or _field(fm, "concept_type")
    try:
        nodes = load_nodes(catfish_home, head_bytes=4096)
    except Exception:  # noqa: BLE001
        return rel_path, content
    for n in nodes:
        if getattr(n, "deprecated", False) or f"wiki/{kind if kind == 'entity' else 'concept'}" not in n.rel_path.replace("entities", "entity").replace("concepts", "concept"):
            continue
        n_sub = getattr(n, "subtype", "") or ""
        if subtype and n_sub and subtype != n_sub:
            continue
        names = [n.title, *getattr(n, "aliases", [])]
        if any(x.strip().lower() == title.strip().lower() for x in names):
            continue   # 完全同名另有规则 (_redirect_to_existing_equivalent / name_owners)
        if any(_tb_guard.names_alike(x, title) for x in names if x):
            logger.info("catfish-memory wiki: 新条目「%s」跟已有的「%s」很像, 并进那一条 (新名字记成别名)", title, n.title)
            return n.rel_path, _add_alias(_set_field(content, "title", n.title), title)
    return rel_path, content


def demote_bad_relations(catfish_home: Path, rel_path: str, content: str) -> tuple[str, list[str]]:
    """关系按类型定义不允许的降成「关联」。返回 (内容, 说明列表)。"""
    if _tb_guard is None or _tb_files is None:
        return content, []
    try:
        if Path(_tb_files._catfish_home()).resolve() != Path(catfish_home).resolve():
            return content, []   # 两边根目录都不一样, 查出来的也不是这个库的, 不如不查
    except OSError:
        return content, []
    fm, _body = _split_frontmatter_body(content)
    if not fm:
        return content, []
    subtype = _field(fm, "entity_type") or _field(fm, "concept_type")
    related = _tb_files._parse_related(fm)
    try:
        bad, _missing = _tb_guard.bad_relations(subtype, related, self_title=_field(fm, "title"))
    except Exception as e:  # noqa: BLE001
        logger.info("catfish-memory wiki: 关系校验没跑成 (%s), 照旧写入", e)
        return content, []
    if not bad:
        return content, []
    new = content
    for b in bad:
        new = new.replace(f'{{name: "{b["name"]}", rel: "{b["rel"]}"}}', f'{{name: "{b["name"]}", rel: "关联"}}')
        logger.warning("catfish-memory wiki: %s 的关系写得不对 —— %s; 先降成「关联」, 等人在关系整理里定", rel_path, b["why"])
    return new, [b["why"] for b in bad]
