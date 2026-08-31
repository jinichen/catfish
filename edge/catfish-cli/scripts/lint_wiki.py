#!/usr/bin/env python3
"""BL-CATFISH-WIKI-MODE P2.2 — Structural lint for ~/.catfish/wiki/.

Scan all *.md, 报 3 类问题:
- broken-link: frontmatter related 或 body [[wikilink]] target file 不存在
- orphan: concept 无 inbound link (没 entity/concept reference 它)
- no-outlinks: file 无 outbound link (dead-end, entity OK 但`WARN`)

0 LLM 调用. 纯 Python regex + file scan. 每周跑 (catfish-cli `catfish lint`):

  catfish lint              # 真`扫 + 报`** (read-only)
  catfish lint --json       # JSON output `pipe 到 dashboard / ci`

exit code:
  0 = `真`broken + orphan = 0`** (干净)
  1 = `真`broken 或 orphan > 0`** (需修)
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

WIKI_ROOT = Path.home() / ".catfish" / "wiki"
WIKILINK_RE = re.compile(r"\[\[([^\]|]+?)(?:\|[^\]]+?)?\]\]")
FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---", re.DOTALL)


def parse_related(fm: str) -> list[str]:
    """Parse all supported ``related`` shapes into target names.

    The writer accepts both the historical string list and the structured
    ``{name, rel}`` list.  The linter must use the same input vocabulary;
    otherwise a valid structured edge is incorrectly reported as a dead end.
    """
    # 不要在第一个 `]` 处截断：`[[A]]` 本身就含有 `]`，而且 related
    # 可能跨行。取 related 字段到下一个顶层 frontmatter 字段为止，再抽 wikilink。
    m = re.search(
        r"^related:\s*(.*?)(?=^\w[\w_-]*:\s|\Z)",
        fm,
        re.MULTILINE | re.DOTALL,
    )
    if not m:
        return []
    raw = m.group(1).strip()
    inner = raw[1:-1].strip() if raw.startswith("[") and raw.endswith("]") else raw

    # Split only on commas outside an inline map and quoted value.  This is
    # deliberately small and dependency-free because lint_wiki.py is also
    # shipped as a standalone CLI script.
    entries: list[str] = []
    buf: list[str] = []
    depth = 0
    quote: str | None = None
    escaped = False
    for char in inner:
        if escaped:
            buf.append(char)
            escaped = False
            continue
        if quote and char == "\\":
            buf.append(char)
            escaped = True
            continue
        if char in ('"', "'"):
            if quote == char:
                quote = None
            elif quote is None:
                quote = char
            buf.append(char)
            continue
        if quote is None and char == "{":
            depth += 1
        elif quote is None and char == "}":
            depth = max(0, depth - 1)
        if char == "," and quote is None and depth == 0:
            entries.append("".join(buf).strip())
            buf = []
        else:
            buf.append(char)
    if buf:
        entries.append("".join(buf).strip())

    names: list[str] = []
    for entry in entries:
        if entry.startswith("{") and entry.endswith("}"):
            name_match = re.search(
                r"\bname\s*:\s*(?:\"([^\"]+)\"|'([^']+)'|([^,}]+))",
                entry[1:-1],
            )
            if name_match:
                name = next(group for group in name_match.groups() if group is not None).strip()
            else:
                continue
        else:
            name = entry.strip().strip('"').strip("'")
            link_match = WIKILINK_RE.fullmatch(name)
            if link_match:
                name = link_match.group(1).strip()
        if name and name not in names:
            names.append(name)
    return names


_KIND_MAP = {"entities": "entity", "concepts": "concept", "queries": "query"}


def scan_files() -> dict[str, dict]:
    """Return rel_path -> info dict."""
    files = {}
    for sub in ("entities", "concepts", "queries"):
        d = WIKI_ROOT / sub
        if not d.is_dir():
            continue
        for f in d.glob("*.md"):
            try:
                content = f.read_text(encoding="utf-8")
            except OSError:
                continue
            fm_match = FRONTMATTER_RE.match(content)
            fm = fm_match.group(1) if fm_match else ""
            title_m = re.search(r"^title:\s*(.+)$", fm, re.MULTILINE)
            title = title_m.group(1).strip() if title_m else f.stem
            deprecated = bool(
                re.search(r"^deprecated:\s*(?:true|yes|1)\s*$", fm, re.MULTILINE | re.IGNORECASE)
            )
            related = parse_related(fm)
            body = content[fm_match.end():] if fm_match else content
            body_links = [m.group(1).strip() for m in WIKILINK_RE.finditer(body)]
            rel_path = f"wiki/{sub}/{f.name}"
            files[rel_path] = {
                "title": title,
                "slug": f.stem,
                "kind": _KIND_MAP[sub],  # entities→entity (rstrip('s') 真 char-strip 真 bug)
                "related": related,
                "body_links": body_links,
                "all_outbound": list(set(related + body_links)),
                "deprecated": deprecated,
            }
    return files


def find_target(name: str, files: dict[str, dict]) -> str | None:
    """name → rel_path (title/slug case-insensitive + 模糊 includes)."""
    lower = name.lower().strip()
    for k, v in files.items():
        if v["title"].lower() == lower or v["slug"].lower() == lower:
            return k
    for k, v in files.items():
        if lower in v["title"].lower():
            return k
    return None


def lint(files: dict[str, dict]) -> dict:
    """Build inbound map + check 3 类 issue. Return report dict."""
    inbound = defaultdict(list)
    broken = []  # list of (src_path, link_name)
    live = {p: info for p, info in files.items() if not info["deprecated"]}
    for src_path, info in live.items():
        for link in info["all_outbound"]:
            target = find_target(link, files)
            if target:
                inbound[target].append(src_path)
            else:
                broken.append((src_path, link))

    orphans = [
        p for p, info in live.items()
        if info["kind"] == "concept" and not inbound.get(p)
    ]
    dead_ends = [p for p, info in live.items() if not info["all_outbound"]]

    return {
        "total": len(live),
        "deprecated": len(files) - len(live),
        "by_kind": {
            "entity": sum(1 for v in live.values() if v["kind"] == "entity"),
            "concept": sum(1 for v in live.values() if v["kind"] == "concept"),
            "query": sum(1 for v in live.values() if v["kind"] == "query"),
        },
        "broken_links": [{"src": s, "link": l} for s, l in broken],
        "orphans": [{"path": p, "title": files[p]["title"]} for p in orphans],
        "dead_ends": [{"path": p, "title": files[p]["title"]} for p in dead_ends],
    }


def print_report(report: dict, files: dict) -> int:
    print(f"=== wiki lint: {WIKI_ROOT} ===")
    bk = report["by_kind"]
    print(
        f"扫 {report['total']} files "
        f"({bk['entity']} entities, {bk['concept']} concepts, {bk['query']} queries)"
    )
    print()

    fail = 0

    # 1. broken links
    print("[broken-link] frontmatter / body [[wikilink]] target 不存在")
    if not report["broken_links"]:
        print("  ✓ 全 hit (0 dangling)")
    else:
        by_src = defaultdict(list)
        for item in report["broken_links"]:
            by_src[item["src"]].append(item["link"])
        for src, links in sorted(by_src.items()):
            uniq = sorted(set(links))
            preview = ", ".join(uniq[:5])
            tail = " ..." if len(uniq) > 5 else ""
            print(f"  ⚠ {src}: {len(uniq)} dangling — {preview}{tail}")
        fail += len(report["broken_links"])
    print()

    # 2. orphan concepts
    print("[orphan] concept 无 inbound link")
    if not report["orphans"]:
        print("  ✓ 全 referenced")
    else:
        for o in sorted(report["orphans"], key=lambda x: x["path"]):
            print(f"  🏝 {o['path']} — {o['title']}")
        fail += len(report["orphans"])
    print()

    # 3. dead-end (no outlinks) — WARN 不 fail
    print("[no-outlinks] file 无 outbound link (dead-end, WARN)")
    if not report["dead_ends"]:
        print("  ✓ 全 connected")
    else:
        for d in sorted(report["dead_ends"], key=lambda x: x["path"]):
            print(f"  🚫 {d['path']} — {d['title']}")
    print()

    if fail == 0:
        print("✓ wiki structure 干净")
        return 0
    print(
        f"⚠ {fail} issue 修法:\n"
        "  - broken-link: 编辑 file 真 frontmatter 真 `related:` 删 或 创建 target file\n"
        "  - orphan concept: 真`真`没人 reference 它` → 加 reference `其它 file` `真`或 `删 真` concept (低价值)\n"
        "  - 全自动 fix `留 future P2.2.1` (`catfish lint --fix`)"
    )
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description="catfish wiki structural lint")
    ap.add_argument("--json", action="store_true", help="JSON output (machine-readable)")
    args = ap.parse_args()

    if not WIKI_ROOT.is_dir():
        print(f"✗ {WIKI_ROOT} 不存在 (chat 触发 distill 自动生)", file=sys.stderr)
        return 1

    files = scan_files()
    if not files:
        print(f"✓ wiki 空 ({WIKI_ROOT})")
        return 0

    report = lint(files)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        # exit code `broken / orphan > 0` 非 0
        return 0 if not report["broken_links"] and not report["orphans"] else 1

    return print_report(report, files)


if __name__ == "__main__":
    sys.exit(main())
