"""wiki 文件读写 —— 补齐鲶鱼对个人知识库的闭环能力。

# 为什么加这个模块 (8/3 鲶鱼在"加入知识库"上绕了一整轮)

员工让鲶鱼把一份对标矩阵存进知识库, 存完知识体系 TAB 一直看不到。鲶鱼查了
sqlite、embeddings 库、sync_turn 日志、ingested_state, 得出一串结论 ——
**几乎全是错的**:

  · "TAB 数据源是 wiki_embeddings.db"        → 错, 那是语义搜索用的
  · "TAB 靠 frontmatter 的 type 字段识别实体"  → 错, kind 从目录路径推
  · "手工放 entities 不被识别, 要走 source 蒸馏" → 完全反了
  · "必须用拼音 slug"                        → 错, slugify 保留中文

真相在 companion-app/src-tauri/src/commands/wiki_read.rs:409, 一共 20 行:
wiki_list_files 就是对 wiki/{entities,concepts,queries} 做 read_dir, 每个 .md
过一遍 build_file_info。没有数据库, 没有 embeddings, 没有 sync_turn。

# 但这不是鲶鱼"能力差"

查它当时手里有什么工具, 就明白了。Companion UI 侧有 7 个 wiki 命令
(list/create/read/update/delete/search/ingest), 鲶鱼只有 2 个:
search 和 ingest。

缺的三个正好卡死闭环:
  · 没有 list   → 它无法回答"TAB 现在到底有哪些条目"。员工说看不到, 它没有
                  任何工具能直接查证, 只能从磁盘产物反推 —— 那一整轮绕圈的
                  根源就在这。
  · 没有 create → 员工要"进知识库且马上能看到", 它做不到。唯一的写入口
                  catfish_wiki_ingest 只能丢进 raw/sources/, 而 TAB 从不读
                  那个目录。
  · 没有 read   → 写完的东西读不回来, 无法自检。

而且"等 24h 蒸馏"那句不是它编的, 是 catfish_wiki_ingest 的 description 原文。
描述如实讲了 ingest 这条路, 唯独没讲"蒸馏完成前 TAB 看不到它" —— 员工的期待
是"存了就能看见", 工具的行为是"存了要等一天", 这个落差没写在任何地方。

给一个 agent 一半的工具, 然后指望它推理出另一半的行为, 它只能猜。

# 本模块的硬约束: 跟 Rust 逐条对齐

list 的取舍必须跟 wiki_read.rs 的 build_file_info **完全一致** —— 包括
tombstone 跳过规则。否则就是造了第二个真相源: 工具说有、TAB 说没有, 比没有
这个工具更坏。tests/test_wiki_files.py 用同一批构造文件对拍两边。

create 的 frontmatter / slugify / 重名检测跟 wiki_write.rs 对齐, 保证鲶鱼建的
条目跟员工在 UI 里建的长得一样。
"""

from __future__ import annotations

import logging
import os
from datetime import date
from pathlib import Path
from typing import Any

logger = logging.getLogger("catfish.tool_bridge.wiki_files")

# 自家 wiki 的三个子目录 —— 跟 wiki_read.rs:412 同一份清单。
# TAB 只看这三个; raw/sources 不在其中 (这正是 8/3 那次看不到的直接原因)。
_OWN_SUBDIRS = ("wiki/entities", "wiki/concepts", "wiki/queries")

_KIND_BY_SUBDIR = {
    "wiki/entities": "entity",
    "wiki/concepts": "concept",
    "wiki/queries": "query",
}


def _catfish_home() -> Path:
    """~/.catfish/。env CATFISH_HOME 覆盖给测试用 (跟 wiki_search.py 同模式)。"""
    override = os.environ.get("CATFISH_HOME")
    if override:
        return Path(override)
    return Path.home() / ".catfish"


# ─────────────────────────────────────────────────────────────
# frontmatter 解析 —— 逐条对齐 wiki_read.rs:80~117
# ─────────────────────────────────────────────────────────────


def _split_frontmatter(text: str) -> tuple[str, str]:
    """对齐 wiki_read.rs:80 split_frontmatter。

    注意两个容易写歪的地方 (Rust 原实现就是这样, 不要"顺手改好"):
      1. 不以 --- 开头 → 返 ("", 原文)。整篇当 body, **不报错**。
      2. 找不到收尾的 \\n--- → 也返 ("", 原文)。半截 frontmatter 当正文,
         文件照样会出现在 TAB 里 (只是 title fallback 成文件名)。
    """
    trimmed = text.lstrip()
    if not trimmed.startswith("---"):
        return "", text
    after_first = trimmed[3:]
    end_idx = after_first.find("\n---")
    if end_idx < 0:
        return "", text
    fm = after_first[:end_idx].strip()
    body = after_first[end_idx + 4 :].lstrip("\n")
    return fm, body


def _parse_field(fm: str, key: str) -> str | None:
    """对齐 wiki_read.rs:96 parse_frontmatter_field。只抽顶层 `key: value`。"""
    prefix = f"{key}:"
    for line in fm.splitlines():
        line = line.strip()
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    return None


def _parse_list(fm: str, key: str) -> list[str]:
    """对齐 wiki_read.rs:107 parse_list_field。"""
    raw = _parse_field(fm, key)
    if raw is None:
        return []
    inner = raw.strip().lstrip("[").rstrip("]")
    out = []
    for piece in inner.split(","):
        v = piece.strip().strip('"').strip("'")
        if v:
            out.append(v)
    return out


def _is_tombstone(size_bytes: int, fm: str, body: str) -> bool:
    """对齐 wiki_read.rs:258 is_tombstone —— list 里唯一的静默跳过条件。

    三个条件同时成立才算墓碑:
      1. 文件 <= 256 字节
      2. 且 (没 frontmatter, 或 frontmatter 标了 deprecated/tombstone: true)
      3. 且 body 为空, 或 body 是含废弃关键词的 HTML 注释

    8/3 那份对标矩阵 21KB, 第 1 条就不成立 —— 它本来就该显示。把这条规则
    照搬过来, 是为了让 list 工具的"看不见"跟 TAB 的"看不见"永远是同一件事。
    """
    if size_bytes > 256:
        return False
    has_fm = bool(fm)
    fm_marks_deprecated = any(
        line.strip().lower() in ("deprecated: true", "tombstone: true")
        for line in fm.splitlines()
    )
    if has_fm and not fm_marks_deprecated:
        return False
    body_trim = body.strip()
    if not body_trim:
        return True
    lower = body_trim.lower()
    in_comment = lower.startswith("<!--") and lower.rstrip().endswith("-->")
    has_marker = any(
        m in lower
        for m in ("废弃", "deprecated", "tombstone", "请参见", "已迁移", "see also")
    )
    return in_comment and has_marker


def _build_file_info(home: Path, path: Path, subdir: str) -> dict[str, Any] | None:
    """对齐 wiki_read.rs:293 build_file_info_inner (allow_tombstone=false)。

    kind 从**目录**推, 不看 frontmatter —— 8/3 猜错的就是这一点。
    title 缺了 fallback 成 slug, 所以无 frontmatter 的文件照样能显示。
    """
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        stat = path.stat()
    except OSError:
        return None
    size_bytes = stat.st_size
    fm, body = _split_frontmatter(content)
    if _is_tombstone(size_bytes, fm, body):
        return None
    slug = path.stem
    return {
        "rel_path": f"{subdir}/{path.name}",
        "kind": _KIND_BY_SUBDIR[subdir],
        "slug": slug,
        "title": _parse_field(fm, "title") or slug,
        "subtype": _parse_field(fm, "entity_type") or _parse_field(fm, "concept_type"),
        "tags": _parse_list(fm, "tags"),
        "sources": _parse_list(fm, "sources"),
        "size_bytes": size_bytes,
        "mtime": stat.st_mtime,
    }


# ─────────────────────────────────────────────────────────────
# slug —— 对齐 wiki_write.rs:99~113
# ─────────────────────────────────────────────────────────────

_SLUG_BAD = set('/\\:*?"<>|\n\r\t 　.')


def _slugify(title: str, max_chars: int = 50) -> str:
    """对齐 wiki_write.rs:99 slugify。

    ⚠ 它**保留中文** —— 只把文件系统敏感字符换成 '-'。"必须用拼音 slug"是 8/3
    猜错的另一条。「中电系资质对标对齐矩阵」出来就是同名文件, 不需要转拼音。
    """
    cleaned = "".join("-" if c in _SLUG_BAD else c for c in title[:max_chars])
    trimmed = cleaned.strip("-").strip(".")
    return trimmed or "untitled"


def _validate_slug(slug: str) -> str | None:
    """对齐 wiki_write.rs:31 validate_slug。返错误信息, None 表示通过。

    ⚠ 已知不一致 (照搬 Rust 行为, 未擅自"修好"):
      Rust 那边 `slug.len() > 100` 是**字节数**, 而 slugify 的 max_chars=50 是
      **字符数**。中文 3 字节/字, 所以 34~50 个汉字的标题会被这里拒掉, 而
      slugify 本来允许 50 个。这是 Rust 侧就存在的口径打架, 不是本模块引入的。
      这里保持一致是为了不让鲶鱼建出 UI 自己的校验器会拒绝的文件; 要修得两边
      一起修, 单独改 Python 会造出新的不一致。
    """
    if not slug:
        return "slug 不能空"
    if len(slug.encode("utf-8")) > 100:
        return f"slug 太长 (>100 字节): {slug}"
    for c in slug:
        if not (c.isalnum() or c in ("-", "_")):
            return f"slug 含非法字符 '{c}': {slug}"
    if slug.startswith("-") or slug.startswith("."):
        return f"slug 不能 - / . 开头: {slug}"
    return None


def _normalize_slug(s: str) -> str:
    """对齐 wiki_write.rs:54 normalize_slug —— 用于等价重名检测。"""
    return "".join(
        c
        for c in s
        if not c.isspace() and c not in ("-", "_", ".", "　")
    ).lower()


def _find_normalized_collision(home: Path, subdir: str, new_slug: str) -> str | None:
    """对齐 wiki_write.rs:68。命中返已有 rel_path。

    防的是 8/3 实际发生过的事: 同一份内容先手工放一个中文名文件, 又建一个
    拼音名文件, 最后两个都在目录里躺着。
    """
    norm_new = _normalize_slug(new_slug)
    if not norm_new:
        return None
    d = home / subdir
    if not d.is_dir():
        return None
    try:
        for p in d.iterdir():
            if p.suffix != ".md":
                continue
            if _normalize_slug(p.stem) == norm_new:
                return f"{subdir}/{p.name}"
    except OSError:
        return None
    return None


def _safe_rel_path(home: Path, rel_path: str) -> tuple[Path | None, str | None]:
    """rel_path 白名单 —— 对齐 wiki_write.rs:161 的检查, 另加 realpath 兜底。

    Rust 那边只查 `contains("..") || !starts_with("wiki/")`。字符串检查挡得住
    直白的 `../`, 挡不住符号链接指出去。这里多做一步 resolve 后确认仍在
    home 底下 —— 鲶鱼是自动调用的, 比人手点 UI 更值得多一道。
    """
    if ".." in rel_path or not rel_path.startswith("wiki/"):
        return None, f"rel_path 白名单不通过: {rel_path}"
    abs_path = (home / rel_path)
    try:
        resolved = abs_path.resolve()
        home_resolved = home.resolve()
    except OSError as e:
        return None, f"路径解析失败: {e}"
    if not str(resolved).startswith(str(home_resolved) + os.sep):
        return None, f"rel_path 解析后跑出 ~/.catfish: {rel_path}"
    return abs_path, None


# ─────────────────────────────────────────────────────────────
# 四个对外能力
# ─────────────────────────────────────────────────────────────


def list_wiki_files(kind: str | None = None, limit: int = 200) -> dict[str, Any]:
    """列自家 wiki —— 结果**就是**知识体系 TAB 显示的内容。

    这是这次补的工具里最要紧的一个: 员工说"知识库里看不到 X"时, 鲶鱼终于能
    直接查证, 而不是去翻 sqlite 反推。
    """
    home = _catfish_home()
    subdirs = _OWN_SUBDIRS
    if kind:
        want = {v: k for k, v in _KIND_BY_SUBDIR.items()}.get(kind)
        if not want:
            return {"ok": False, "error": f"kind 只能 entity/concept/query, 收到: {kind}"}
        subdirs = (want,)

    items: list[dict[str, Any]] = []
    for subdir in subdirs:
        d = home / subdir
        if not d.is_dir():
            continue
        try:
            entries = list(d.iterdir())
        except OSError as e:
            logger.warning("读 %s 失败: %s", d, e)
            continue
        for p in entries:
            if p.suffix != ".md":
                continue
            info = _build_file_info(home, p, subdir)
            if info:
                items.append(info)

    items.sort(key=lambda x: x["mtime"], reverse=True)
    total = len(items)
    return {
        "ok": True,
        "total": total,
        "items": items[:limit],
        "truncated": total > limit,
        "note": (
            "这就是知识体系 TAB 的全部内容 (wiki_list_files 同源)。"
            "不在这个列表里 = TAB 里也看不到。"
        ),
    }


def create_wiki_entry(
    kind: str,
    title: str,
    body: str,
    subtype: str = "",
    tags: list[str] | None = None,
    related: list[str] | None = None,
) -> dict[str, Any]:
    """建一个 entity/concept —— 写完**立刻**在 TAB 可见 (员工切走再切回即可)。

    跟 catfish_wiki_ingest 的分工: ingest 是"存档一份原始材料, 等后台蒸馏";
    这个是"现在就要一个知识库条目"。8/3 那次员工要的是后者, 而鲶鱼手里只有
    前者。
    """
    if kind not in ("entity", "concept"):
        return {"ok": False, "error": f"kind 必须 entity 或 concept, 收到: {kind}"}
    title = (title or "").strip()
    if not title:
        return {"ok": False, "error": "title 不能空"}

    slug = _slugify(title)
    if err := _validate_slug(slug):
        return {"ok": False, "error": err}

    home = _catfish_home()
    subdir = "wiki/entities" if kind == "entity" else "wiki/concepts"
    d = home / subdir
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return {"ok": False, "error": f"建目录 {d} 失败: {e}"}

    path = d / f"{slug}.md"
    if path.exists():
        return {
            "ok": False,
            "error": f"已存在 {subdir}/{slug}.md — 用 catfish_wiki_update 改, 不要重建",
            "rel_path": f"{subdir}/{slug}.md",
        }
    if existing := _find_normalized_collision(home, subdir, slug):
        return {
            "ok": False,
            "error": (
                f"等价条目已存在: {existing} — '{slug}' 规范化后跟它相同。"
                f"用 catfish_wiki_update 改那一个, 不要建重复的。"
            ),
            "rel_path": existing,
        }

    today = date.today().isoformat()
    type_field = "entity_type" if kind == "entity" else "concept_type"
    tags_yaml = ", ".join((t or "").replace('"', "") for t in (tags or []) if t)
    related_yaml = ", ".join(
        f'"[[{(r or "").strip().strip(chr(34)).replace(chr(34), "")}]]"'
        for r in (related or [])
        if r
    )
    content = (
        f"---\n"
        f"type: {kind}\n"
        f"title: {title}\n"
        f"{type_field}: {subtype}\n"
        f"created: {today}\n"
        f"updated: {today}\n"
        f"tags: [{tags_yaml}]\n"
        f"related: [{related_yaml}]\n"
        f"sources: [manual]\n"
        f"---\n"
        f"\n"
        f"# {title}\n"
        f"\n"
        f"{body}\n"
    )
    try:
        path.write_text(content, encoding="utf-8")
    except OSError as e:
        return {"ok": False, "error": f"写 {path} 失败: {e}"}

    return {
        "ok": True,
        "rel_path": f"{subdir}/{slug}.md",
        "bytes": len(content.encode("utf-8")),
        "created": True,
        "note": "已写入。员工在知识体系 TAB 切走再切回就能看到 (TAB 挂载时重新扫目录)。",
    }


def read_wiki_file(rel_path: str) -> dict[str, Any]:
    """读一个 wiki 文件全文 —— 让鲶鱼能自检"我刚写的到底成了什么样"。"""
    home = _catfish_home()
    abs_path, err = _safe_rel_path(home, rel_path)
    if err:
        return {"ok": False, "error": err}
    assert abs_path is not None
    if not abs_path.is_file():
        return {"ok": False, "error": f"文件不存在: {rel_path}"}
    try:
        content = abs_path.read_text(encoding="utf-8", errors="replace")
        stat = abs_path.stat()
    except OSError as e:
        return {"ok": False, "error": f"读 {rel_path} 失败: {e}"}
    fm, body = _split_frontmatter(content)
    return {
        "ok": True,
        "rel_path": rel_path,
        "content": content,
        "frontmatter": fm,
        "body": body,
        "size_bytes": stat.st_size,
        "mtime": stat.st_mtime,
        "visible_in_tab": not _is_tombstone(stat.st_size, fm, body)
        and any(rel_path.startswith(s + "/") for s in _OWN_SUBDIRS),
    }


def update_wiki_file(rel_path: str, content: str) -> dict[str, Any]:
    """整文件覆写。

    只允许改已存在的文件 —— 建新的走 create, 那条路有重名检测。
    """
    home = _catfish_home()
    abs_path, err = _safe_rel_path(home, rel_path)
    if err:
        return {"ok": False, "error": err}
    assert abs_path is not None
    if not abs_path.is_file():
        return {
            "ok": False,
            "error": f"文件不存在: {rel_path} — 建新条目用 catfish_wiki_create",
        }
    try:
        abs_path.write_text(content, encoding="utf-8")
    except OSError as e:
        return {"ok": False, "error": f"写 {rel_path} 失败: {e}"}
    fm, body = _split_frontmatter(content)
    size = len(content.encode("utf-8"))
    result = {
        "ok": True,
        "rel_path": rel_path,
        "bytes": size,
        "created": False,
    }
    # 写完顺手告诉调用方它还在不在 TAB 里 —— 覆写成一个 256 字节以内的空壳
    # 会让它变成 tombstone 从列表消失, 这种"写成功了但看不见"必须当场说出来,
    # 那正是 8/3 那一轮的形状。
    if _is_tombstone(size, fm, body):
        result["warning"] = (
            "写进去了, 但这份内容命中了墓碑规则 (<=256 字节 + 无 frontmatter/标了 "
            "deprecated + body 空或废弃注释), 知识体系 TAB 会把它隐藏。"
            "要让它可见就把内容写实一些。"
        )
        result["visible_in_tab"] = False
    else:
        result["visible_in_tab"] = True
    return result


# ─────────────────────────────────────────────────────────────
# tool-bridge dispatch 入口
# ─────────────────────────────────────────────────────────────


def tool_wiki_list(args: dict) -> dict[str, Any]:
    kind = args.get("kind")
    limit = args.get("limit")
    return list_wiki_files(
        kind=str(kind) if kind else None,
        limit=int(limit) if limit else 200,
    )


def tool_wiki_create(args: dict) -> dict[str, Any]:
    return create_wiki_entry(
        kind=str(args.get("kind", "entity")),
        title=str(args.get("title", "")),
        body=str(args.get("body", "")),
        subtype=str(args.get("subtype", "") or ""),
        tags=list(args.get("tags") or []),
        related=list(args.get("related") or []),
    )


def tool_wiki_read(args: dict) -> dict[str, Any]:
    rel = str(args.get("rel_path", "")).strip()
    if not rel:
        return {"ok": False, "error": "rel_path 不能空 (先用 catfish_wiki_list 拿路径)"}
    return read_wiki_file(rel)


def tool_wiki_update(args: dict) -> dict[str, Any]:
    rel = str(args.get("rel_path", "")).strip()
    if not rel:
        return {"ok": False, "error": "rel_path 不能空 (先用 catfish_wiki_list 拿路径)"}
    if "content" not in args:
        return {"ok": False, "error": "content 不能缺 —— 这是整文件覆写, 要传完整内容"}
    return update_wiki_file(rel, str(args.get("content", "")))
