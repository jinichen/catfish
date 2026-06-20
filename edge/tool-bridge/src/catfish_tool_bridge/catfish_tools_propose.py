"""Catfish propose_skill / propose_skill_revision — 抽自 catfish_tools_skill_ops.py (5/21 拆分).

agent 自动 propose 新 skill / skill revision, 走员工 confirm 门槛后才写文件.
红线字段冻 (健康 / 财务 / 感情 / 政治 / 宗教 + skill_path 头核 etc), 限频
(24h ≤ 3 per skill, 1h ≤ 5 全局).
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .catfish_tools_today import _unix_to_iso  # noqa: F401  used by propose_skill_revision

logger = logging.getLogger("catfish.tool_bridge")

# ============================================================
# BL-MM9 (5/8) — catfish_propose_skill: agent 自动抽 skill (员工 confirm 门槛)
# ============================================================
#
# 跟 hermes "creates skills from experience" 对标但加员工 confirm 门槛 — 跟
# BL-MM7 user_profile 三 evidence + lock 同哲学.
#
# 流程:
#   1. LLM chat 中观察到员工反复做某事 (≥3 次同 pattern)
#   2. LLM 调 catfish_propose_skill(name, reason, action_steps, evidence_count)
#   3. 工具写一行 JSON 到 ~/.catfish/skill_proposals.jsonl, 状态 status=proposed
#   4. LLM 跟员工说 '我注意到你 N 次 X, 要不存成 skill?'
#   5a. 员工 yes → LLM 调 catfish_skill_install (带上 reason / action_steps)
#       同时再调 catfish_propose_skill 把 status 改 accepted (传同 name)
#   5b. 员工 no → LLM 调 catfish_propose_skill 把 status 改 rejected
#       (员工 reject 过同 name 后, LLM 别再 propose, 写 SOUL 纪律)
#
# 限流防骚扰:
#   - 同 name 同 status 已 ≤24h 内 propose 过 → 拒绝再 propose
#   - 同 session 累计 propose ≥ 3 → 提醒 LLM 节制
#
# 红线:
#   - 健康 / 财务 / 感情 / 政治 / 宗教 namespace skill 严禁 propose (跟 BL-MM7 红线一致)
#   - SOUL § 红线字段 章节会用 prompt 明确告诉 LLM

SKILL_PROPOSALS_PATH = Path.home() / ".catfish" / "skill_proposals.jsonl"
_PROPOSAL_REDLINE_KEYWORDS = (
    "health", "medical", "diagnos", "drug",  # 健康
    "salary", "loan", "debt", "finance",     # 财务
    "love", "dating", "marriage", "divorce", # 感情
    "politic", "election", "govern",         # 政治
    "religion", "buddh", "christ", "muslim", # 宗教
    "健康", "病", "诊", "药",
    "工资", "贷款", "债", "理财",
    "恋爱", "结婚", "离婚",
    "政治", "选举",
    "宗教", "基督", "佛", "伊斯兰",
)
_PROPOSAL_RECENT_HOURS = 24       # 同 name 24h 内不重复 propose
_PROPOSAL_PER_SESSION_LIMIT = 5   # 单 session 最多 propose 5 个 skill (防骚扰)


def _read_proposals_history() -> list[Dict[str, Any]]:
    """读 ~/.catfish/skill_proposals.jsonl, 返 list of dict (jsonl, 一行一条)."""
    if not SKILL_PROPOSALS_PATH.exists():
        return []
    out: list[Dict[str, Any]] = []
    try:
        with open(SKILL_PROPOSALS_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return out


def _append_proposal_event(event: Dict[str, Any]) -> None:
    """append 一条 event 到 ~/.catfish/skill_proposals.jsonl (atomic 不重要, 多 LLM 不并发写)."""
    SKILL_PROPOSALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SKILL_PROPOSALS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def _is_redline_skill_name(name: str, reason: str) -> bool:
    """检查 skill name + reason 是否触红线 (健康/财务/感情/政治/宗教)."""
    text = (name + " " + reason).lower()
    return any(kw in text for kw in _PROPOSAL_REDLINE_KEYWORDS)


def propose_skill(args: Dict[str, Any]) -> Dict[str, Any]:
    """tool: BL-MM9 (5/8) — LLM 提案一个 skill 给员工确认.

    校验:
      - name / reason / action_steps 都必填
      - triggered_by='auto' → evidence_count ≥3 (BL-MM9 防骚扰)
      - triggered_by='user_request' → evidence_count ≥1 (员工显式触发不卡)
      - name kebab-case (避免奇怪字符)
      - 红线字段拒绝
      - 同 name 24h 内已 propose 过 → 拒绝
      - 单 session 累计 ≥ 5 → 拒绝 (防骚扰)

    BL-MM9-fix (5/9): 鸿波 '下午我主动让鲶鱼生成 SKILL, 为什么不能生成, 很不合理' —
    加 triggered_by 区分两种场景, user_request 跳 3 次门槛.
    """
    name = (args.get("name") or "").strip()
    reason = (args.get("reason") or "").strip()
    action_steps = (args.get("action_steps") or "").strip()
    triggered_by = (args.get("triggered_by") or "auto").strip().lower()
    if triggered_by not in ("auto", "user_request"):
        triggered_by = "auto"  # 不识别的值兜底当 auto (严格模式)
    try:
        evidence_count = int(args.get("evidence_count") or 0)
    except (TypeError, ValueError):
        evidence_count = 0

    # P3.5.43 (鸿波 6/20 拍 'SKILL 三路径统一'): 字段对齐 RecMode SkillOutput,
    # accept 后 catfish_skill_install 拿到现成的 triggers/kind/namespace 不用 LLM
    # 重新猜. 跟 skill_format.SkillManifest 同 schema, 跟 hermes frontmatter 直接装.
    triggers_raw = args.get("triggers") or []
    if not isinstance(triggers_raw, list):
        triggers_raw = []
    triggers = [t.strip() for t in triggers_raw if isinstance(t, str) and t.strip()]
    kind = (args.get("kind") or "procedural").strip().lower()
    if kind not in ("procedural", "instructional"):
        kind = "procedural"
    skill_namespace = (args.get("skill_namespace") or "personal").strip().lower()
    if skill_namespace not in ("personal", "department", "public", "creative"):
        skill_namespace = "personal"  # 不识别兜底

    # validation
    if not name:
        return {"type": "error", "error": "name 必填"}
    if not re.match(r"^[a-z0-9][a-z0-9-]{1,49}$", name):
        return {
            "type": "error",
            "error": (
                "name 必须 kebab-case, 1-50 字符, 字母数字开头 (例: 'project-proposal'). "
                f"收到: {name!r}"
            ),
        }
    if not reason or len(reason) < 10:
        return {"type": "error", "error": "reason 必填且 ≥ 10 字 (含具体观察证据)"}
    if not action_steps or len(action_steps) < 20:
        return {"type": "error", "error": "action_steps 必填且 ≥ 20 字 (3-5 步说明 skill 干啥)"}
    # P3.5.43: triggers 校验 (跟 skill_format.TRIGGERS_MIN/MAX 对齐).
    # 老 caller 没传 triggers → warn 但不阻塞 (proposal 期可空, install 时由 LLM 补).
    # 上限严格 (防 prompt 预算超).
    if len(triggers) > 20:
        return {"type": "error", "error": f"triggers 至多 20 个 (现 {len(triggers)} 个), 占 system prompt 预算"}
    # 阈值校验 — 两套, 看 triggered_by
    min_evidence = 3 if triggered_by == "auto" else 1
    if evidence_count < min_evidence:
        if triggered_by == "auto":
            err = (
                f"evidence_count={evidence_count} < 3 (auto 模式). "
                "BL-MM9 哲学: 你自己观察员工 ≥3 次同 pattern 才该 propose, 1-2 次静默观察. "
                "如果是员工**明确说**'存成 skill', triggered_by 改 'user_request' 即可放行."
            )
        else:
            err = f"evidence_count={evidence_count} < 1 — 至少 1 次实际操作"
        return {"type": "error", "error": err}

    # 红线检查
    if _is_redline_skill_name(name, reason):
        return {
            "type": "error",
            "error": (
                "skill 名 / 理由触红线 (健康/财务/感情/政治/宗教). "
                "鲶鱼不 propose 这类 skill — SOUL § 红线字段 已禁."
            ),
        }

    # 限流: 同 name 24h 内已 propose
    history = _read_proposals_history()
    now_ts = time.time()
    cutoff = now_ts - _PROPOSAL_RECENT_HOURS * 3600
    recent_same_name = [
        e for e in history
        if e.get("name") == name
        and e.get("ts", 0) >= cutoff
        and e.get("event_type") == "proposed"
        and e.get("status") == "proposed"  # 还没被员工 accept/reject
    ]
    if recent_same_name:
        return {
            "type": "error",
            "error": (
                f"已经在 24h 内 propose 过 '{name}' (proposal_id={recent_same_name[-1].get('proposal_id')}), "
                "等员工 accept/reject 后再 propose, 别骚扰."
            ),
        }

    # 限流: 单 session 累计 (用最近 1 小时近似 session)
    one_hour_ago = now_ts - 3600
    recent_in_session = [
        e for e in history
        if e.get("ts", 0) >= one_hour_ago
        and e.get("event_type") == "proposed"
    ]
    if len(recent_in_session) >= _PROPOSAL_PER_SESSION_LIMIT:
        return {
            "type": "error",
            "error": (
                f"最近 1 小时已 propose {len(recent_in_session)} 个 skill (上限 {_PROPOSAL_PER_SESSION_LIMIT}). "
                "员工还没 confirm 之前别再 propose, 让员工先选."
            ),
        }

    # 写 jsonl
    proposal_id = f"prop_{int(now_ts)}_{name}"
    # BL-MM9-fix (5/9): 字段对齐 learning.rs 期待的 schema (namespace/name 拆开)
    # P3.5.43 (6/20): 加 triggers/kind 跟 RecMode SkillOutput / skill_format
    # SkillManifest 同 schema. accept 后 catfish_skill_install 直接拿这些字段
    # 生成 hermes 兼容 SKILL.md 不用 LLM 重新猜.
    event = {
        "event_type": "proposed",  # learning.rs 看 'propose' 也兼容下
        "proposal_id": proposal_id,
        "skill_namespace": skill_namespace,  # P3.5.43: 不再 hardcode 'personal'
        "skill_name": name,
        "name": name,                     # 旧字段兼容
        "kind": kind,                     # P3.5.43 新: procedural / instructional
        "triggers": triggers,             # P3.5.43 新: 触发关键词 list
        "description": reason,            # learning.rs Dashboard 显示用 + 同时是 skill description
        "reason": reason,
        "action_steps": action_steps,
        "evidence_count": evidence_count,
        "triggered_by": triggered_by,     # 'auto' | 'user_request' 留 audit
        "status": "proposed",
        "ts": now_ts,
        "ts_iso": _unix_to_iso(now_ts),
    }
    try:
        _append_proposal_event(event)
    except OSError as e:
        return {"type": "error", "error": f"写 skill_proposals.jsonl 失败: {e}"}

    total_proposed = sum(1 for e in history if e.get("event_type") == "proposed") + 1
    return {
        "type": "ok",
        "proposal_id": proposal_id,
        "name": name,
        "total_proposals": total_proposed,
        "summary": (
            f"已记下提案 '{name}' (基于 {evidence_count} 次员工行为). "
            f"现在跟员工说: '我注意到你最近 {evidence_count} 次 {reason[:50]}, "
            f"要不我把这个流程存成 skill, 下次你说一句就触发? 你说装我就装.' "
            f"等员工说 yes 再调 catfish_skill_install. 员工 reject 时再调本工具传 status='rejected' 关单."
        ),
    }


# ============================================================
# BL-MM13 (5/8) — propose_skill_revision: agent 自进化老 skill
# ============================================================
#
# 跟 BL-MM9 propose_skill 一脉相承, 但操作对象不同:
#   - MM9: 抽**新** skill (员工反复做 → 提议存)
#   - MM13: 改**老** skill 内容 (员工反馈 / audit 失败 → 提议改)
#
# 流程:
#   1. LLM 看 BL-MM11 feedback + audit + BL-MM12 quality_score 找问题 skill
#   2. LLM 调 catfish_propose_skill_revision(skill_path, current_v, proposed_v,
#      reason, diff_summary, evidence_summary)
#   3. 工具校验 + 写 ~/.catfish/skill_revisions.jsonl, status=proposed
#   4. Dashboard SkillRevisionCard (BL-MM14) 列出, 员工 click 采纳/拒绝
#   5. 采纳 → Tauri 调 BL-MM3 备份老版 + 写新版到 skill_path
#   6. 拒绝 → 标 dismissed, 24h 内不重 propose 同 skill
#
# 限流防骚扰 (跟 MM9 一致):
#   - 同 skill_path 24h 内 已 proposed 过 → 拒
#   - 单 session ≥ 3 个 revision propose → 拒 (一次别改太多)
#
# 红线 (跟 MM7/MM9 一致):
#   - 健康 / 财务 / 感情 / 政治 / 宗教 namespace skill 严禁 propose 改

SKILL_REVISIONS_PATH = Path.home() / ".catfish" / "skill_revisions.jsonl"
_REVISION_RECENT_HOURS = 24       # 同 skill_path 24h 内不重复 propose
_REVISION_PER_SESSION_LIMIT = 3   # 单 session 最多 propose 3 个 revision


def _read_revisions_history() -> list[Dict[str, Any]]:
    """读 ~/.catfish/skill_revisions.jsonl, 返 list of dict."""
    if not SKILL_REVISIONS_PATH.exists():
        return []
    out: list[Dict[str, Any]] = []
    try:
        with open(SKILL_REVISIONS_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return out


def _append_revision_event(event: Dict[str, Any]) -> None:
    """append 一条 event 到 ~/.catfish/skill_revisions.jsonl."""
    SKILL_REVISIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SKILL_REVISIONS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def _semver_tuple(v: str) -> Optional[tuple[int, int, int]]:
    m = _SEMVER_RE.match(v.strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def propose_skill_revision(args: Dict[str, Any]) -> Dict[str, Any]:
    """tool: BL-MM13 (5/8) — LLM 提议改进一个已存在的 skill.

    校验:
      - skill_path / current_version / proposed_version / reason / diff_summary /
        evidence_summary 都必填
      - skill_path kebab-case 含 '/' 命名空间 (`department/weekly-report` 形式)
      - SemVer 严格 (current 和 proposed 都 X.Y.Z)
      - proposed_version > current_version
      - reason ≥ 30 字, diff_summary ≥ 30 字, evidence_summary ≥ 20 字
      - 红线 namespace 拒
      - 同 skill_path 24h 内已 propose → 拒
      - 单 session ≥ 3 → 拒
    """
    skill_path = (args.get("skill_path") or "").strip()
    current_v = (args.get("current_version") or "").strip()
    proposed_v = (args.get("proposed_version") or "").strip()
    reason = (args.get("reason") or "").strip()
    diff_summary = (args.get("diff_summary") or "").strip()
    evidence_summary = (args.get("evidence_summary") or "").strip()

    # validation: 必填
    if not skill_path:
        return {"type": "error", "error": "skill_path 必填"}
    if not re.match(r"^[a-z0-9][a-z0-9-]*(/[a-z0-9][a-z0-9-]*)+$", skill_path):
        return {
            "type": "error",
            "error": (
                "skill_path 必须 kebab-case 含 '/' (例 'department/weekly-report'). "
                f"收到: {skill_path!r}"
            ),
        }

    # SemVer
    cur_t = _semver_tuple(current_v)
    prop_t = _semver_tuple(proposed_v)
    if cur_t is None:
        return {"type": "error", "error": f"current_version 不是 SemVer X.Y.Z: {current_v!r}"}
    if prop_t is None:
        return {"type": "error", "error": f"proposed_version 不是 SemVer X.Y.Z: {proposed_v!r}"}
    if prop_t <= cur_t:
        return {
            "type": "error",
            "error": (
                f"proposed_version {proposed_v} 必须 > current_version {current_v}. "
                "改动要 bump 版本号才能让员工区分新旧."
            ),
        }

    if len(reason) < 30:
        return {"type": "error", "error": "reason ≥ 30 字 (含具体观察 / feedback / audit 数据)"}
    if len(diff_summary) < 30:
        return {"type": "error", "error": "diff_summary ≥ 30 字 (3-8 句 markdown bullets)"}
    if len(evidence_summary) < 20:
        return {
            "type": "error",
            "error": "evidence_summary ≥ 20 字 (BL-MM11 / audit / BL-MM12 数据汇总)",
        }

    # 红线 — 复用 MM9 _is_redline_skill_name 检查 (路径 + reason)
    if _is_redline_skill_name(skill_path, reason):
        return {
            "type": "error",
            "error": (
                "skill_path / 理由触红线 (健康/财务/感情/政治/宗教). "
                "鲶鱼不 propose 改这类 skill — SOUL § 红线字段 已禁."
            ),
        }

    # 限流: 同 skill_path 24h 内已 propose
    history = _read_revisions_history()
    now_ts = time.time()
    cutoff = now_ts - _REVISION_RECENT_HOURS * 3600
    recent_same = [
        e for e in history
        if e.get("skill_path") == skill_path
        and e.get("ts", 0) >= cutoff
        and e.get("event_type") == "proposed"
        and e.get("status") == "proposed"
    ]
    if recent_same:
        return {
            "type": "error",
            "error": (
                f"已经在 24h 内 propose 过 '{skill_path}' 的改进 "
                f"(revision_id={recent_same[-1].get('revision_id')}), "
                "等员工 accept/reject 后再 propose 新一轮, 别骚扰."
            ),
        }

    # 限流: 单 session 累计 (近 1 小时)
    one_hour_ago = now_ts - 3600
    recent_in_session = [
        e for e in history
        if e.get("ts", 0) >= one_hour_ago
        and e.get("event_type") == "proposed"
    ]
    if len(recent_in_session) >= _REVISION_PER_SESSION_LIMIT:
        return {
            "type": "error",
            "error": (
                f"最近 1 小时已 propose {len(recent_in_session)} 个 revision "
                f"(上限 {_REVISION_PER_SESSION_LIMIT}). 一次别改太多, 让员工先消化."
            ),
        }

    # 写 jsonl
    revision_id = (
        f"rev_{int(now_ts)}_{skill_path.replace('/', '-')}_{proposed_v}"
    )
    event = {
        "event_type": "proposed",
        "revision_id": revision_id,
        "skill_path": skill_path,
        "current_version": current_v,
        "proposed_version": proposed_v,
        "reason": reason,
        "diff_summary": diff_summary,
        "evidence_summary": evidence_summary,
        "status": "proposed",
        "ts": now_ts,
        "ts_iso": _unix_to_iso(now_ts),
    }
    try:
        _append_revision_event(event)
    except OSError as e:
        return {"type": "error", "error": f"写 skill_revisions.jsonl 失败: {e}"}

    total = sum(1 for e in history if e.get("event_type") == "proposed") + 1
    return {
        "type": "ok",
        "revision_id": revision_id,
        "skill_path": skill_path,
        "current_version": current_v,
        "proposed_version": proposed_v,
        "total_revisions": total,
        "summary": (
            f"已记下改进提议: '{skill_path}' v{current_v} → v{proposed_v}. "
            f"现在跟员工说: '这个 skill 最近 {evidence_summary[:80]}. "
            f"我建议改 {diff_summary[:80]}. 你看 Dashboard 决定采不采纳.' "
            f"员工 accept 走 catfish_skill_install + BL-MM3 自动备份老版."
        ),
    }


