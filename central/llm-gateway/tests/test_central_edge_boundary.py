"""BL-CENTRAL-EDGE-BOUNDARY (5/17): 防中央端读员工本机数据.

# 规则 (docs/CENTRAL-EDGE-DATA-BOUNDARY.md)

中央端 (catfish-gateway) 只能碰审计数据, 不许读员工本机文件 (~/.hermes / ~/.catfish).
存量 30 个违规走 ALLOWLIST 排期, 一个一个 ticket 砍, **不许新增**.

# 这个测试干啥

扫 central/llm-gateway/src/catfish_gateway/ 下每个 .py, 找 `Path.home()` / `~/.hermes` /
`~/.catfish` 引用. 如果文件不在 ALLOWLIST 又有引用 → fail. 加新违规 PR 直接 CI 红.

# 怎么消条目

完成一个迁移 BL ticket (例: A 类 inject_session_history 改成读 request body 不读
state.db) → 从 ALLOWLIST 里删掉对应文件. 一旦全删完, 中央端就 100% 合规.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


# central/llm-gateway/src/catfish_gateway/
SRC_ROOT = Path(__file__).parent.parent / "src" / "catfish_gateway"


# ── 违规模式 ────────────────────────────────────────────


# 中央端代码不允许的 pattern
FORBIDDEN_PATTERNS = [
    # Path.home() 直接拿员工 home — 中央端没员工 home 概念
    (r"\bPath\.home\(\)", "Path.home() — 中央端没员工 home 概念"),
    # 任何对 ~/.hermes 的字面引用 (字符串)
    (r"~/\.hermes/", "~/.hermes/ — hermes 边缘端数据, 该 Companion 拼好放 request body"),
    # 任何对 ~/.catfish 的字面引用
    (r"~/\.catfish/", "~/.catfish/ — catfish 边缘端数据, 该 Companion 拼好放 request body"),
]


# ── 已知违规 ALLOWLIST (排期清理中, 不许加新条目) ──────


# 30 个存量违规, 列在 docs/CENTRAL-EDGE-DATA-BOUNDARY.md backlog.
# 每个完成迁移 → 从这清单删 → 不再豁免.
# **新加文件不许进这个 list**, 走 PR review 时 reviewer 拒绝.
KNOWN_VIOLATIONS_ALLOWLIST: set[str] = {
    # A 类 — Memory inject 链 (2 剩, was 5; 5/26 减 3)
    # BL-GATEWAY-MEMORY-REGISTRY-DELETE (5/20): memory/providers/{employee_journal,
    # feedback, hermes_memory, skills_catalog}.py 4 个删除. 5/19 BL-MEMORY-OWNERSHIP-FIX
    # 已 disable, 5/20 audit verdict 确认 no-op 死代码, catfish-memory plugin (hermes
    # 侧 prefetch) 接管.
    # 5/26 真清: 亲眼 grep 后发现 session_facts.py / feedback_inject.py / metrics.py 真代码 0
    # Path.home() (只 docstring 里有 ~/.catfish/ 字面引用), 加 # noqa: BOUNDARY 标即可,
    # 已从 ALLOWLIST 移除. employee_journal + inject_session_history 真有 Path.home()
    # 调用 (employee_journal:62 / inject_session_history:81 读 state.db), 保留待迁.
    # 鸿波 5/26 "为什么这么乱" reflection: 不再信注释推断, 只信 grep 命中.
    "inject_session_history.py",
    "employee_journal.py",
    # "session_facts.py" — 5/26 移除 (0 真代码 Path.home)
    # "feedback_inject.py" — 5/26 移除 (0 真代码 Path.home)
    "identity_inject.py",
    # B 类 — 后台任务 (3) — 5/23 BL-GATEWAY-DROP-LEGACY-SUMMARIZE (task #5 Stage 1):
    # session_summarizer.py + memory_distill.py 整文件已 rm, catfish-memory plugin
    # (hermes 侧 prefetch + sync_turn) 接管 employee_journal / distilled_facts 全套写读.
    # allowlist 删 2 行.
    "proactive.py",
    "session_meta.py",
    "tool_archive/db.py",
    # C 类 — UI 直调端点
    # BL-CENTRAL-WEB-PURGE-USERDATA (5/17): sessions_browse + tasks_browse 已不再
    # 被 gateway endpoint 调用 (端点全删). 5/22 鸿波: 真 git rm 这俩文件 + 对应
    # tests/test_sessions_browse.py + tests/test_tasks_browse.py. allowlist 也跟着删.
    "recent_outputs.py",
    "skills_loader.py",
    # D 类 — A2A federation (5)
    "a2a_audit.py",
    "a2a_journal_hook.py",
    "a2a_allow.py",
    "a2a_jwt.py",
    "a2a_self_register.py",
    # E 类 — RecMode (2 remaining, was 3) **极高敏感**
    # 5/25 BL-RECMODE-MIGRATE-TO-EDGE: aggregator.py + selector_repair.py 整体搬
    # edge/tool-bridge/, central 端只剩 stub (RuntimeError on import). 老 ALLOWLIST
    # 里的 "recmode/aggregator.py" 已移除 — stub 不再含 Path.home() 落盘代码,
    # 测试自然 pass. 还在 ALLOWLIST 的 cdp_listener / cleanup 写 ~/.catfish/recordings/
    # 是 CDP listener + cleanup utility (不调 vision LLM, 不存 LLM 分析结果),
    # 跟搬走的"截图字节 + LLM 分析结果落盘"不是同一类敏感. 仍标待迁 (下一 sprint).
    # "recmode/aggregator.py" — 5/25 移除 (搬到 edge/tool-bridge)
    # "recmode/selector_repair.py" — 5/25 同批搬走, 从未在 ALLOWLIST (无 Path.home)
    "recmode/cdp_listener.py",
    "recmode/cleanup.py",
    # F 类 — Facts 上传 (1, facts_db.py 已合规走 PG-only 不留 jsonl 字面引用)
    "facts_router.py",
    # G 类 — Resolver / metadata (2)
    "user_model_resolver.py",
    "session_goals.py",
    # H 类 — 半合规 (1 剩, was 2): quota.py 暂留 (sqlite 兜底,
    # BL-QUOTA-SQLITE-DEPRECATE 改造中)
    # 5/26 真清: metrics.py 真代码 0 Path.home (3 处 docstring ~/.catfish/ 加 noqa).
    # 已从 ALLOWLIST 移除. 真活的 jsonl fallback 走 CATFISH_AUDIT_PATH env, 不 hardcode.
    "quota.py",
    # "metrics.py" — 5/26 移除 (0 真代码 Path.home)
    # 基础设施 (中性, request_id 状态)
    "inflight_streams.py",
    # app.py 还有少量字符串引用 (文档注释里), 单独审
    "app.py",
}


# 例外: 注释里出现 ~/.hermes 是合法的 (文档参考), 用 # noqa: BOUNDARY 标
COMMENT_OK_MARKER = "noqa: BOUNDARY"


def _scan_file(py_path: Path) -> list[tuple[int, str, str]]:
    """扫一个 py 文件, 返 (lineno, matched_text, reason) 列表 (空 = 干净)."""
    violations: list[tuple[int, str, str]] = []
    try:
        text = py_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return violations  # 跳过读不开的, 不算违规

    for lineno, line in enumerate(text.splitlines(), start=1):
        if COMMENT_OK_MARKER in line:
            continue
        for pattern, reason in FORBIDDEN_PATTERNS:
            m = re.search(pattern, line)
            if m:
                violations.append((lineno, m.group(0), reason))
                break  # 一行报 1 个就够
    return violations


def _rel_path(p: Path) -> str:
    """文件路径 (相对 catfish_gateway/) 用于跟 ALLOWLIST 比对."""
    return str(p.relative_to(SRC_ROOT))


def test_no_new_central_edge_boundary_violations():
    """BL-CENTRAL-EDGE-BOUNDARY: 中央端不许新增读员工本机数据的代码.

    新文件违规 → 直接 fail PR. 存量 30 个文件在 KNOWN_VIOLATIONS_ALLOWLIST,
    完成一个迁移 ticket 就从 allowlist 删一个.
    """
    if not SRC_ROOT.exists():
        pytest.skip(f"src 路径不存在 {SRC_ROOT}")

    new_violations: dict[str, list[tuple[int, str, str]]] = {}
    for py in sorted(SRC_ROOT.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        # 单测文件本身不算 (allowlist 测试 fixture / 文档可能引用)
        if py.name.startswith("test_"):
            continue
        rel = _rel_path(py)
        violations = _scan_file(py)
        if not violations:
            continue
        if rel in KNOWN_VIOLATIONS_ALLOWLIST:
            continue  # 存量豁免
        new_violations[rel] = violations

    if new_violations:
        lines = ["❌ 中央端新增了员工本机数据访问 — 违反 BL-CENTRAL-EDGE-BOUNDARY 规则"]
        lines.append("规则文档: docs/CENTRAL-EDGE-DATA-BOUNDARY.md")
        lines.append("")
        for rel, vs in new_violations.items():
            lines.append(f"📁 {rel}")
            for lineno, matched, reason in vs[:5]:
                lines.append(f"   line {lineno}: {matched}  ← {reason}")
            if len(vs) > 5:
                lines.append(f"   ... 还有 {len(vs) - 5} 处")
            lines.append("")
        lines.append("修法 (任选其一):")
        lines.append("  1. 把读员工数据这件事搬 Companion (推荐, 跟规则一致)")
        lines.append("  2. 如果是文档注释引用 ~/.hermes / ~/.catfish, 加 `# noqa: BOUNDARY`")
        lines.append("  3. 真没法避免 + 走过 PR review → 加进 KNOWN_VIOLATIONS_ALLOWLIST")
        pytest.fail("\n".join(lines))


def test_allowlist_no_dead_entries():
    """如果某文件已经清干净了 (没 violation), 该从 ALLOWLIST 移除.

    防 ALLOWLIST 越来越长 — 完成迁移一个 ticket 就该收割掉 allowlist 一项.
    """
    if not SRC_ROOT.exists():
        pytest.skip(f"src 路径不存在 {SRC_ROOT}")

    dead_entries = []
    for rel in sorted(KNOWN_VIOLATIONS_ALLOWLIST):
        py = SRC_ROOT / rel
        if not py.exists():
            # 文件被砍了 — allowlist 该跟着删
            dead_entries.append((rel, "文件不存在 (被删 / 移走)"))
            continue
        violations = _scan_file(py)
        if not violations:
            dead_entries.append((rel, "已清干净 — 该从 allowlist 移除收割"))

    if dead_entries:
        lines = ["✨ 以下文件已合规, 该从 KNOWN_VIOLATIONS_ALLOWLIST 删除:"]
        for rel, reason in dead_entries:
            lines.append(f"  - {rel}  ({reason})")
        pytest.fail("\n".join(lines))


def test_allowlist_size_only_shrinks_over_time():
    """记录 baseline (5/17 rule ship 时 30 + 2 + 1 = 33 个), 不许超过.

    这是慢动作收紧 — 长期看 ALLOWLIST 大小单调下降.
    """
    BASELINE_SIZE = 33  # 5/17 ship 日基线: 30 违规 + quota/metrics 半合规 + app/inflight 基础设施
    assert len(KNOWN_VIOLATIONS_ALLOWLIST) <= BASELINE_SIZE, (
        f"KNOWN_VIOLATIONS_ALLOWLIST 当前 {len(KNOWN_VIOLATIONS_ALLOWLIST)} > 基线 {BASELINE_SIZE}. "
        "新增了违规? 走 PR review 时 reviewer 该拒. "
        "如果是合并多个文件后总数变了, 改 baseline."
    )
