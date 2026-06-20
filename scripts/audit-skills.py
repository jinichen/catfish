#!/usr/bin/env python3
"""audit-skills — 扫 catfish + hermes 已装 skill, 报 budget / duplicate / unused.

# 背景

P3.5.41 (6/18 鸿波 audit steipete/agent-scripts skill-cleaner 后催):
catfish 仪表盘'我装的 · 98 个' skill, 没人知道:
  - 总 description 占多少 prompt budget (hermes-private-main 40K context 怎么算)
  - 跨 ~/.hermes/skills + catfish/edge/*/hermes-skill + catfish/skills 可能 duplicate
  - 近 N 月没用过的 skill (跟 P3.5.32 Insight Reports '📦 久未使用' 同思路, 应用对象是 skill)
  - 哪些 description 太长可压缩省 token

steipete/skill-cleaner 是 Codex/OpenClaw 专用, hardcode ~/.codex 路径,
不能直接拿. 借鉴它的 6 维报告思路, 抄给 catfish (hermes 路径).

# 用法

  python3 scripts/audit-skills.py                          # 输出 markdown 报告到 stdout
  python3 scripts/audit-skills.py --months 3              # 近 3 月没用算 unused (默认 1 月)
  python3 scripts/audit-skills.py --save                  # 存 ~/.catfish/outputs/<date>/skill-audit.md
  python3 scripts/audit-skills.py --json                  # 机器可读 JSON
  python3 scripts/audit-skills.py --root /path/to/extra   # 加额外 skill 目录 (可重复)

# 不引依赖

纯 stdlib (pathlib / re / json / math / datetime / argparse). YAML frontmatter 手写
简单 parser (跟 catfish-memory_helpers.py 的 _parse_frontmatter_field 一致).

# token cost 算法

跟 Codex core-skills/src/render.rs / OpenAI 一致: ceil(utf8_bytes / 4).
catfish 走 hermes 注入 skill description 到 system prompt, 用 catfish-private-main
内网 model (Qwen) — 40K context (P3.5.32.6 audit 实证). 没 Codex 2% 那种硬约束,
但 description 总和过大会撞 SYSTEM_PROMPT 超限 (P3.5.32.5 reverted 时撞过).
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ── 配置 ──────────────────────────────────────────────────────────

# catfish 主要 model context window (P3.5.32.6 实证 catfish-private-main 慢 = 40K context)
CATFISH_PRIVATE_CONTEXT = 40_000
# Codex 2% budget 公式 (借鉴 skill-cleaner) — catfish 没硬约束, 但参考用
CODEX_BUDGET_PERCENT = 0.02


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="audit catfish + hermes 已装 skill (budget/duplicate/unused)",
    )
    p.add_argument(
        "--months", type=int, default=1,
        help="近 N 月没在 hermes session 出现过 = unused (默认 1)",
    )
    p.add_argument(
        "--root", action="append", default=[],
        help="加额外 skill 目录路径 (可重复, eg --root ~/Dropbox/skills)",
    )
    p.add_argument(
        "--save", action="store_true",
        help="存到 ~/.catfish/outputs/<date>/skill-audit.md",
    )
    p.add_argument(
        "--json", dest="as_json", action="store_true",
        help="JSON 输出 (机器可读), 不输出 markdown",
    )
    p.add_argument(
        "--top", type=int, default=10,
        help="Top N 长 description / 大 skill 列表条数 (默认 10)",
    )
    return p.parse_args()


# ── skill 路径发现 ────────────────────────────────────────────────


def discover_roots(extra: list[str]) -> list[tuple[str, Path]]:
    """返 (root_label, path) 列表. 跟 catfish skill 装机现状对齐."""
    home = Path.home()
    candidates: list[tuple[str, Path]] = [
        ("hermes", home / ".hermes" / "skills"),
        ("hermes-personal", home / ".hermes" / "personal-skills"),
        ("hermes-plugins", home / ".hermes" / "plugins"),
        # catfish 仓库内部 (装机前的源)
        ("catfish-managed", home / "person_task" / "catfish" / "skills"),
        ("catfish-edge", home / "person_task" / "catfish" / "edge"),
        # P3.5.43 (鸿波 6/20): RecMode + propose_skill 落到这里. 之前 audit 不扫
        # 这条路径, 录的 skill 不算 token 预算 — audit 报告漏掉这部分.
        # skill_sync 会同步到 ~/.hermes/skills (上面 'hermes' root), 但**未同步前**
        # 跟未保存的 draft 都还在这, audit 也该看到.
        ("catfish-runtime", home / ".catfish" / "skills"),
    ]
    out: list[tuple[str, Path]] = []
    seen_real: set[Path] = set()
    for label, p in candidates:
        if not p.is_dir():
            continue
        real = p.resolve()
        if real in seen_real:
            continue
        seen_real.add(real)
        out.append((label, p))
    for extra_p in extra:
        p = Path(extra_p).expanduser()
        if not p.is_dir():
            print(f"⚠ --root {extra_p} 不是目录, skip", file=sys.stderr)
            continue
        real = p.resolve()
        if real in seen_real:
            continue
        seen_real.add(real)
        out.append(("extra", p))
    return out


def find_skill_md(roots: list[tuple[str, Path]]) -> list[dict[str, Any]]:
    """扫每个 root, 找所有 SKILL.md. 返 {root_label, path, rel_path, abs_path} 列表."""
    out: list[dict[str, Any]] = []
    for label, root in roots:
        # 跳 venv / node_modules / .pycache 防误判
        skip_parts = {"venv", "node_modules", "__pycache__", ".git", ".pytest_cache"}
        for skill_md in root.rglob("SKILL.md"):
            if any(part in skip_parts for part in skill_md.parts):
                continue
            try:
                rel = skill_md.relative_to(root)
            except ValueError:
                rel = skill_md
            out.append({
                "root": label,
                "path": str(skill_md),
                "rel_path": str(rel),
                "abs_path": skill_md,
            })
    return out


# ── YAML frontmatter 简单 parser (不依赖 pyyaml) ──────────────────

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_skill_md(abs_path: Path) -> dict[str, Any]:
    """读 SKILL.md, parse frontmatter name / description + 算 body size."""
    try:
        text = abs_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return {"error": f"read 失败: {e}", "name": abs_path.parent.name}

    name = abs_path.parent.name  # default fallback
    description = ""
    body = text

    m = _FRONTMATTER_RE.match(text)
    if m:
        fm = m.group(1)
        body = text[m.end():]
        # 简单逐行扫 key: value (跨行不支持, skill description 一般单行)
        for line in fm.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line:
                key, _, val = line.partition(":")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key == "name" and val:
                    name = val
                elif key == "description" and val:
                    description = val

    total_bytes = len(text.encode("utf-8"))
    desc_bytes = len(description.encode("utf-8"))
    body_bytes = len(body.encode("utf-8"))

    return {
        "name": name,
        "description": description,
        "total_bytes": total_bytes,
        "desc_bytes": desc_bytes,
        "body_bytes": body_bytes,
        "total_tokens": math.ceil(total_bytes / 4),
        "desc_tokens": math.ceil(desc_bytes / 4),
        "body_tokens": math.ceil(body_bytes / 4),
    }


# ── unused detection (扫 hermes sessions) ────────────────────────


def find_unused(
    skills: list[dict[str, Any]],
    months: int,
) -> set[str]:
    """近 N 月 hermes session jsonl 没 mention 过 skill name → unused.

    扫 ~/.hermes/sessions/*.jsonl + history.jsonl.
    mention 规则: skill name 单词 / SKILL.md 路径 / $skill 触发词.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=30 * months)
    sessions_dir = Path.home() / ".hermes" / "sessions"
    history_file = Path.home() / ".hermes" / "history.jsonl"

    skill_names = {s["name"] for s in skills if s.get("name")}
    mentioned: set[str] = set()

    # scan files mtime
    log_files: list[Path] = []
    if sessions_dir.is_dir():
        log_files.extend(sessions_dir.rglob("*.jsonl"))
    if history_file.is_file():
        log_files.append(history_file)

    for log in log_files:
        try:
            mtime = datetime.fromtimestamp(log.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if mtime < cutoff:
            continue
        try:
            text = log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # 简单匹配 — skill name 作为 word boundary 出现, 或 SKILL.md 路径
        for name in skill_names:
            if name in mentioned:
                continue
            # 边界匹配 — 防 substring 误中 (eg "code" 在 "decode" 内)
            if re.search(rf"\b{re.escape(name)}\b", text):
                mentioned.add(name)

    unused = skill_names - mentioned
    return unused


# ── duplicate detection ──────────────────────────────────────────


def find_duplicates(
    skills_parsed: list[dict[str, Any]],
) -> list[tuple[str, list[dict[str, Any]]]]:
    """同 name 跨 root 装 = duplicate. 返 [(name, [skill_info, ...]), ...]"""
    by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in skills_parsed:
        name = s.get("name") or ""
        if name:
            by_name[name].append(s)
    return [(n, infos) for n, infos in by_name.items() if len(infos) > 1]


# ── 报告生成 ──────────────────────────────────────────────────────


def build_report(
    roots: list[tuple[str, Path]],
    skills_parsed: list[dict[str, Any]],
    duplicates: list[tuple[str, list[dict[str, Any]]]],
    unused: set[str],
    months: int,
    top: int,
) -> dict[str, Any]:
    """整理 6 维报告 dict (供 markdown / json 两种输出)."""
    # 1. budget
    total_tokens = sum(s["total_tokens"] for s in skills_parsed)
    total_desc_tokens = sum(s["desc_tokens"] for s in skills_parsed)
    budget_codex_2pct = math.floor(CATFISH_PRIVATE_CONTEXT * CODEX_BUDGET_PERCENT)

    # 2. top desc
    by_desc = sorted(skills_parsed, key=lambda s: s["desc_tokens"], reverse=True)
    top_desc = by_desc[:top]
    # 3. top body
    by_body = sorted(skills_parsed, key=lambda s: s["body_tokens"], reverse=True)
    top_body = by_body[:top]

    # 4. root summary
    root_count: dict[str, int] = defaultdict(int)
    root_tokens: dict[str, int] = defaultdict(int)
    for s in skills_parsed:
        root_count[s["root"]] += 1
        root_tokens[s["root"]] += s["total_tokens"]

    # 5. unused detail
    unused_details = [s for s in skills_parsed if s["name"] in unused]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "months_threshold": months,
        "roots": [{"label": l, "path": str(p)} for l, p in roots],
        "skill_count": len(skills_parsed),
        "total_tokens": total_tokens,
        "total_desc_tokens": total_desc_tokens,
        "catfish_private_context": CATFISH_PRIVATE_CONTEXT,
        "codex_2pct_budget": budget_codex_2pct,
        "budget_used_pct_of_codex": (
            round(total_desc_tokens / budget_codex_2pct * 100, 1)
            if budget_codex_2pct
            else 0.0
        ),
        "top_desc": top_desc,
        "top_body": top_body,
        "duplicates": [
            {"name": n, "instances": [
                {"root": s["root"], "rel_path": s["rel_path"],
                 "desc_tokens": s["desc_tokens"]} for s in infos
            ]}
            for n, infos in duplicates
        ],
        "unused": [
            {"name": s["name"], "root": s["root"], "rel_path": s["rel_path"],
             "desc_tokens": s["desc_tokens"]}
            for s in unused_details
        ],
        "root_summary": [
            {"root": r, "count": root_count[r], "total_tokens": root_tokens[r]}
            for r in sorted(root_count, key=lambda r: -root_tokens[r])
        ],
    }


def render_markdown(rep: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Catfish + Hermes Skill Audit")
    lines.append("")
    lines.append(f"- 生成时间: `{rep['generated_at']}`")
    lines.append(f"- unused 阈值: 近 {rep['months_threshold']} 月没在 hermes session 出现")
    lines.append(f"- skill 总数: **{rep['skill_count']}**")
    lines.append("")

    # 1. budget
    lines.append("## 1. Budget (token cost)")
    lines.append("")
    lines.append(f"- 全 skill 总 tokens (frontmatter + body): **{rep['total_tokens']:,}**")
    lines.append(f"- 全 skill description tokens (system prompt 注入用): **{rep['total_desc_tokens']:,}**")
    lines.append(f"- catfish-private-main context: {rep['catfish_private_context']:,} tokens")
    lines.append(
        f"- 参考 Codex 2% budget (借鉴 skill-cleaner): {rep['codex_2pct_budget']:,} tokens"
    )
    lines.append(
        f"- description 占 Codex 参考预算 %: **{rep['budget_used_pct_of_codex']}%** "
        + ("⚠️ 超 100% 该清" if rep["budget_used_pct_of_codex"] > 100 else "")
    )
    lines.append("")
    lines.append("> 算法: `ceil(utf8_bytes / 4)`. 跟 OpenAI / Codex 一致.")
    lines.append("")

    # 2. duplicates
    lines.append(f"## 2. Duplicates ({len(rep['duplicates'])})")
    lines.append("")
    if not rep["duplicates"]:
        lines.append("_无重复_")
    else:
        lines.append("跨 root 装的同名 skill — 考虑保留一份, 卸其他:")
        lines.append("")
        for d in rep["duplicates"]:
            lines.append(f"### {d['name']}")
            for inst in d["instances"]:
                lines.append(f"- `{inst['root']}` / {inst['rel_path']} ({inst['desc_tokens']} desc tokens)")
            lines.append("")
    lines.append("")

    # 3. unused
    lines.append(f"## 3. Unused (近 {rep['months_threshold']} 月没出现, {len(rep['unused'])})")
    lines.append("")
    if not rep["unused"]:
        lines.append("_全部 skill 近期都用过_")
    else:
        lines.append("近期 hermes session jsonl + history 没 mention 过的 skill — 考虑卸:")
        lines.append("")
        # 排序: desc_tokens 大的优先 (卸了省 budget)
        sorted_u = sorted(rep["unused"], key=lambda x: -x["desc_tokens"])
        for s in sorted_u:
            lines.append(f"- `{s['name']}` ({s['root']}) — {s['desc_tokens']} desc tokens")
        lines.append("")
    lines.append("")
    lines.append("> 匹配规则: skill name 单词边界 / SKILL.md 路径. 启发式, 可能漏 (LLM 没用 skill name 直说就漏判).")
    lines.append("> 删前 verify — 推荐 disable 一段时间观察.")
    lines.append("")

    # 4. top desc (压缩 candidates)
    lines.append(f"## 4. 压缩 candidates — 最长 description Top {len(rep['top_desc'])}")
    lines.append("")
    lines.append("description 太长直接占 system prompt budget, 压短能省:")
    lines.append("")
    lines.append("| Rank | Name | Root | Desc tokens | Total tokens |")
    lines.append("|---|---|---|---|---|")
    for i, s in enumerate(rep["top_desc"], 1):
        lines.append(
            f"| {i} | `{s['name']}` | {s['root']} | {s['desc_tokens']} | {s['total_tokens']:,} |"
        )
    lines.append("")

    # 5. top body
    lines.append(f"## 5. 大体积 skill — body Top {len(rep['top_body'])}")
    lines.append("")
    lines.append("body 大不直接占 system prompt (除非 LLM 主动 read), 但仍占磁盘 / git diff:")
    lines.append("")
    lines.append("| Rank | Name | Root | Body tokens |")
    lines.append("|---|---|---|---|")
    for i, s in enumerate(rep["top_body"], 1):
        lines.append(f"| {i} | `{s['name']}` | {s['root']} | {s['body_tokens']:,} |")
    lines.append("")

    # 6. root summary
    lines.append("## 6. Root summary")
    lines.append("")
    lines.append("| Root | Skill 数 | 总 tokens |")
    lines.append("|---|---|---|")
    for r in rep["root_summary"]:
        lines.append(f"| `{r['root']}` | {r['count']} | {r['total_tokens']:,} |")
    lines.append("")
    for r in rep["roots"]:
        lines.append(f"- `{r['label']}` → `{r['path']}`")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("**建议优先级**:")
    lines.append("")
    lines.append("1. 删 duplicate (卸保留 root 之外的副本)")
    lines.append("2. 卸 unused 里 desc_tokens 高的 — 直接省 system prompt budget")
    lines.append("3. 压缩 description Top 10 — 留 trigger 关键词, 砍冗长说明")
    lines.append("")
    lines.append("**不建议盲删**: SKILL.md 是 hermes auto-load, 卸前先 `git status` 看是否 catfish-managed.")

    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    roots = discover_roots(args.root)
    if not roots:
        print("⚠ 没找到任何 skill root, 退出", file=sys.stderr)
        return 1

    files = find_skill_md(roots)
    if not files:
        print("⚠ root 内没 SKILL.md, 退出", file=sys.stderr)
        return 1

    skills_parsed: list[dict[str, Any]] = []
    for f in files:
        info = parse_skill_md(f["abs_path"])
        if "error" in info:
            print(f"⚠ parse 失败 {f['path']}: {info['error']}", file=sys.stderr)
            continue
        info["root"] = f["root"]
        info["rel_path"] = f["rel_path"]
        info["path"] = f["path"]
        skills_parsed.append(info)

    duplicates = find_duplicates(skills_parsed)
    unused = find_unused(skills_parsed, args.months)

    rep = build_report(roots, skills_parsed, duplicates, unused, args.months, args.top)

    if args.as_json:
        out = json.dumps(rep, ensure_ascii=False, indent=2, default=str)
    else:
        out = render_markdown(rep)

    if args.save:
        date_str = datetime.now().strftime("%Y-%m-%d")
        save_dir = Path.home() / ".catfish" / "outputs" / date_str
        save_dir.mkdir(parents=True, exist_ok=True)
        save_path = save_dir / ("skill-audit.json" if args.as_json else "skill-audit.md")
        save_path.write_text(out, encoding="utf-8")
        print(f"✓ 报告已存 {save_path}", file=sys.stderr)
    else:
        print(out)

    return 0


if __name__ == "__main__":
    sys.exit(main())
