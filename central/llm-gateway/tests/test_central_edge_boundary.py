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
    # A 类 — Memory inject 链 (1 剩, was 5; 5/26 减 4 累计)
    # BL-GATEWAY-MEMORY-REGISTRY-DELETE (5/20): memory/providers 4 个删除. 5/19
    # BL-MEMORY-OWNERSHIP-FIX 已 disable, catfish-memory hermes plugin 接管.
    # 5/26 早 真 grep 清 3 个 (session_facts/feedback_inject/metrics 真代码 0 Path.home).
    # 5/26 下午 真砍 1 个: inject_session_history.py 真死代码 (app.py 只 import 不调),
    #   改 fail-loud stub + 删 app.py import. (employee_journal 跟 A2A 整批同砍, 因
    #   唯一 caller a2a_journal_hook 在 A2A 系列.)
    # "inject_session_history.py" — 5/26 砍 (改 stub)
    # "employee_journal.py" — 5/26 跟 A2A 同批砍 (唯一 caller 是 a2a_journal_hook)
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
    # D 类 — A2A federation · ✅ 5/26 整套全清 (5 剩 0)
    # Plan D Federation (5/12 BL-FED2) 整批砍: 0 真客户 + 1695 LOC + 私钥违规.
    # gateway 7 个 a2a_*.py 全 stub, app.py 3 处 caller 删, tool-bridge tool dispatch
    # 改 deprecated error, identity-server /registry/by-expertise 返 410 GONE,
    # employee_journal (唯一 caller a2a_journal_hook) 同批砍.
    # "a2a_audit.py" — 5/26 砍
    # "a2a_journal_hook.py" — 5/26 砍
    # "a2a_allow.py" — 5/26 砍
    # "a2a_jwt.py" — 5/26 砍 (私钥从中央代码清掉, 真要 federation 重做要在 Companion 持私钥)
    # "a2a_self_register.py" — 5/26 砍
    # E 类 — RecMode · ✅ 5/26 全清
    # 5/25 batch 0: aggregator.py + selector_repair.py 搬 edge (E.1)
    # 5/26 batch 1: cdp_listener.py + cleanup.py 搬 edge (E.2)
    # central 端 4 个 module 全是 fail-loud stub (RuntimeError on import).
    # gateway /api/learn/* 5 个 endpoint (start/stop/active/status/cleanup) 都是
    # thin proxy 走 tool_bridge_rpc → tool-bridge Unix socket.
    # "recmode/aggregator.py" — 5/25 移除
    # "recmode/selector_repair.py" — 5/25 同批搬走 (从未在 ALLOWLIST)
    # "recmode/cdp_listener.py" — 5/26 移除
    # "recmode/cleanup.py" — 5/26 移除
    # docs/CENTRAL-EDGE-DATA-BOUNDARY.md E 类全清 ✅
    # F 类 — Facts 上传 (1, facts_db.py 已合规走 PG-only 不留 jsonl 字面引用)
    "facts_router.py",
    # G 类 — Resolver / metadata (1 剩, was 2)
    # 5/26 砍 session_goals.py — hermes 0.14 原生 /goal + /subgoal (#25449) 替代,
    # catfish 自己 BL-HERMES013-3 (5/11) 实现的 250 行变死代码 + 状态分裂源
    # (catfish 注入 ~/.catfish/session_goal.txt vs hermes 注入 hermes 内部 goal,
    # 双 inject 互不知道). gateway session_goals.py 改 fail-loud stub, app.py
    # 2 处 caller 删, Companion session_goal.rs 改 stub, AdvisorView sessionGoal
    # 字段删. 详见 docs/HERMES-013-ALIGN.md L137 BL-HERMES013-3 deprecation 标注.
    # "session_goals.py" — 5/26 移除
    "user_model_resolver.py",
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
