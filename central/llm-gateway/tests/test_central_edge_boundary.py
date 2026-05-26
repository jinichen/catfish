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
    # A 类 — Memory inject 链 · ✅ 5/26 晚 全清 (0 剩, was 5)
    # BL-GATEWAY-MEMORY-REGISTRY-DELETE (5/20): memory/providers 4 个删除. 5/19
    # BL-MEMORY-OWNERSHIP-FIX 已 disable, catfish-memory hermes plugin 接管.
    # 5/26 早 真 grep 清 3 个 (session_facts/feedback_inject/metrics 真代码 0 Path.home).
    # 5/26 下午 真砍 1 个: inject_session_history.py 真死代码 (app.py 只 import 不调).
    # 5/26 晚 真改 identity_inject.py: 加 bundle 参数, Companion prefetch 6 字段
    #   通过 /v1/chat/completions body._catfish_identity_bundle 透传. gateway pop 后
    #   不 forward upstream. fs 读 (Path.home + 4 处 docstring ~/.hermes) 全加
    #   noqa: BOUNDARY (SaaS 化后 fs 兜底 unreachable, bundle 是唯一数据源).
    # "inject_session_history.py" — 5/26 砍 (改 stub)
    # "employee_journal.py" — 5/26 跟 A2A 同批砍 (唯一 caller 是 a2a_journal_hook)
    # "identity_inject.py" — 5/26 晚移除 (Companion prefetch bundle 化)
    # B 类 — 后台任务 · ✅ 5/26 晚 全清 (0 剩, was 3)
    # 5/23 BL-GATEWAY-DROP-LEGACY-SUMMARIZE: session_summarizer + memory_distill 真 rm.
    # 5/26 P1 BL-PROACTIVE-DECOUPLE: proactive.py header 化 (journal_tail + last_model
    #   通过 Companion header 透传, gateway 不读员工 fs).
    # 5/26 晚 BL-SESSION-META-PLUGIN-TAKEOVER: catfish-memory hermes plugin 接管
    #   session_meta.json 写 (sync_turn hook 里 _tick_session_meta()). gateway 端
    #   session_meta.py 改 fail-loud stub, app.py 删 tick caller + 删 import.
    # "proactive.py" — 5/26 移除
    # "session_meta.py" — 5/26 晚移除 (plugin _tick_session_meta() 接管)
    # 5/26 晚 真清: tool_archive/db.py 砍 PG 路径死代码 (_use_pg/_pg_conn/_pg_*
    # stub 4 个 + 双轨 if/else, ~36 行). docstring 里 ~/.catfish/ 引用 3 处全加
    # noqa: BOUNDARY. ARCHIVE_DIR 默认 Path.home 路径加 noqa (Q3 SaaS 搬 tool-bridge
    # 时整套移走). 真代码 0 行不合规, boundary scan 已合规.
    # "tool_archive/db.py" — 5/26 晚移除 (ALLOWLIST 6 → 5)
    # C 类 — UI 直调端点 · ✅ 5/26 全清 (0 剩, was 2)
    # 5/26 砍 recent_outputs.py — gateway 不再扫员工 ~/.catfish/output/.
    # 老 BL-FIX-TIMEOUT-OUTPUTS 功能 deprecated, Companion timeout toast 自己列.
    # recent_outputs.py → fail-loud stub.
    # "recent_outputs.py" — 5/26 移除
    # 5/26 砍 skills_loader.py — **死代码**, 不是隐私违规. gateway 跑中央服务器,
    # 调 Path.home() 扫的是中央自己 home, 永远空 (中央没人 git clone catfish skills).
    # 真正的 skill catalog inject 是 catfish-memory plugin _render_skills_catalog
    # 在做 (plugin 跑员工 mac, 读员工 ~/.catfish/skills/, 真扫得到). gateway 这套
    # 从一开始就 inject 空块无效果. 同批砍 skills_inject + skills_vector (BM25 RAG
    # 5/25 ship 也基于错假设) + skill_guard.has_skill_intent / inject_skill_guard 改 no-op.
    # 修正: 5/26 第一版 stub 注释写"P0 隐私违规" 是错的 (SKILL.md 进 system prompt 上 LLM
    # 是产品本质, 不是 leak. 鸿波纠正"数据不进 prompt LLM 怎么工作"). 真理由是死代码.
    # "skills_loader.py" — 5/26 砍
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
    # F 类 — Facts 上传 · ✅ 5/26 晚 全清 (0 剩, was 1)
    # 5/26 晚: facts_router.py 真违规就 1 行 (FACTS_DIR Path.home fallback) + 6 行
    # docstring. 加 noqa: BOUNDARY 标. 真代码改成 env-required pattern (CATFISH_FACTS_DIR
    # 未配 fallback 留 dev/单测兜底, 启动日志 warning 提示生产必配 env).
    # 完整 PG + 对象存储改造留 Q3 BL-Q3-FACT-PG-MIGRATE ticket. 当前 Companion UI
    # 未接 facts endpoint, 0 真客户用, 不阻塞 ship.
    # "facts_router.py" — 5/26 晚移除 (env-required + noqa)
    # G 类 — Resolver / metadata · ✅ 5/26 全清 (0 剩, was 2)
    # session_goals.py 砍 (hermes 0.14 原生 /goal 替代).
    # user_model_resolver.py: get_session_model + get_user_last_session_model 改 stub
    #   fail-loud (读 hermes state.db). caller (proactive / facts_pipeline) 改成接
    #   model_name 参数, Companion 通过 X-Catfish-Last-Model header 传.
    # resolve_model_obj 纯查表保留, 不读 fs/db.
    # "session_goals.py" — 5/26 移除
    # "user_model_resolver.py" — 5/26 移除 (read fs/db 部分改 stub)
    # H 类 — 半合规 · ✅ 5/26 晚 全清 (0 剩, was 2)
    # 5/26 早 真清 metrics.py: 真代码 0 Path.home, 3 处 docstring ~/.catfish/ 加 noqa.
    # 5/26 晚 真清 quota.py:
    #   1) _quota_db_path Path.home() fallback 删 (改 RuntimeError raise)
    #   2) _use_pg 神逻辑修 (neither env set → True 让 _pg_conn 报错, 不再静默 sqlite)
    #   3) 6 处 docstring ~/.catfish/quota.db 引用全加 noqa: BOUNDARY
    #   生产 PG-only, 单测 sqlite 走 CATFISH_QUOTA_DB env 显式 override, 不 fallback 员工本机.
    # "quota.py" — 5/26 晚移除
    # "metrics.py" — 5/26 移除 (0 真代码 Path.home)
    # 基础设施 · ✅ 5/26 晚移除 (in-memory dict 替换 fs)
    # BL-INFLIGHT-MEM (5/26): 老 fs 文件 ~/.catfish/inflight_streams/<req>.json   # noqa: BOUNDARY
    #   存 request_id 状态. SaaS 化后 gateway 跑客户机房写不到员工 mac. 改进程
    #   内 dict (跨线程加 lock). reap_interrupted 永远返 0 (重启 dict 自然清空,
    #   失去"interrupted_resumed" audit nice-to-have feature, ops 翻 timeout
    #   error 日志兜底).
    # Q4 K8s 多 pod 时上 Redis pub/sub (BL-INFLIGHT-REDIS, 单独 ticket).
    # "inflight_streams.py" — 5/26 晚移除 (in-memory 化, ALLOWLIST 2 → 1)
    # 5/26 移除 app.py — 3 处 Path.home (record_transcript + skill_content + save_skill)
    # 5/26 batch 2 (E.1 收尾) 搬 tool-bridge 后 grep 0 命中. RecMode 全链路真 100% 在 edge.
    # "app.py" — 5/26 移除
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
