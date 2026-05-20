"""Catfish skill install / a2a / memory ops tools — 抽自 catfish_tools.py (5/20 拆分).

工具:
  catfish_a2a_ask: 跨 agent 咨询 (BL-FED2)
  catfish_skill_install: 装 skill (从 hub URL 或本地路径, 含 dry_run + dedup 检查)
  catfish_skill_delete: 删 skill
  catfish_memory_dedupe: hermes memory 去重 (jieba jaccard)
  catfish_memory_compress: hermes memory 压缩

混杂命名 — a2a 跟 skill 不同 namespace, 但都是中等大小的 ops, 抽到一起保持
主文件 <800 行规则. 后续 sprint 可再细拆.

5/20: 944 行从 catfish_tools.py 抽出.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from .catfish_tools_today import _hermes_dir, _home, _is_today, _unix_to_iso

logger = logging.getLogger("catfish.tool_bridge")

# ============================================================
# Skill ops (propose / propose_revision / run)
# (BL-TOOL-SPLIT 5/20: 805 行抽到 catfish_tools_skill_ops.py)
# ============================================================

from .catfish_tools_skill_ops import (  # noqa: E402, F401
    SKILL_PROPOSALS_PATH,
    SKILL_REVISIONS_PATH,
    _append_proposal_event,
    _append_revision_event,
    _catfish_skills_root,
    _extract_file_paths,
    _find_render_function,
    _function_signature_help,
    _is_redline_skill_name,
    _load_skill_module,
    _read_proposals_history,
    _read_revisions_history,
    _read_skill_metadata,
    _semver_tuple,
    _skill_audit_path,
    _write_skill_audit,
    propose_skill,
    propose_skill_revision,
    run_skill,
)

# ============================================================
# catfish_a2a_ask — Plan D Catfish Federation (五一 sprint Day 4-5)
# ============================================================


def a2a_ask(args: Dict[str, Any]) -> Dict[str, Any]:
    """tool: 问另一个员工的鲶鱼一个问题.

    HTTP POST 到 gateway 的 /a2a/internal/ask, gateway 内部做 lookup + sign + 调 B.
    """
    to_sub = (args.get("to_sub") or "").strip()
    question = (args.get("question") or "").strip()
    purpose = (args.get("purpose") or "").strip()
    context_hint = (args.get("context_hint") or "").strip()

    if not to_sub or not question:
        return {"ok": False, "error": "to_sub / question 必填"}

    # 当前员工 sub. 单机 mock 通过 env CATFISH_USER_SUB.
    from_sub = os.environ.get("CATFISH_USER_SUB", "").strip()
    if not from_sub:
        return {
            "ok": False,
            "error": "CATFISH_USER_SUB env 未设, 单机 mock 必须设 (生产从 SSO 拿)",
        }

    gateway_url = os.environ.get(
        "CATFISH_GATEWAY_URL",
        "http://127.0.0.1:8999",
    ).rstrip("/")

    body = {
        "from_sub": from_sub,
        "to_sub": to_sub,
        "question": question,
        "purpose": purpose,
        "context_hint": context_hint,
    }

    try:
        import urllib.request  # noqa: PLC0415

        req = urllib.request.Request(
            f"{gateway_url}/a2a/internal/ask",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {
            "ok": False,
            "error": f"调 gateway /a2a/internal/ask 失败: {e}",
        }

    if not data.get("ok"):
        err_type = data.get("error_type", "")
        err_msg = data.get("error", "")
        if err_type == "denied":
            return {
                "ok": False,
                "error": f"{to_sub} 的鲶鱼按 ALLOW.md 拒绝了这个问题: {err_msg}. "
                         "请换个角度问, 或问员工有没有授权这类信息.",
            }
        return {
            "ok": False,
            "error": f"A2A 调用 ({err_type}): {err_msg}",
        }

    return {
        "ok": True,
        "answer": data.get("answer", ""),
        "chunks_count": data.get("chunks_count", 0),
        "summary": (
            f"已通过 Plan D Federation 拿到 {to_sub} 的回答 "
            f"({data.get('chunks_count', 0)} 个 chunk). 详见 answer 字段."
        ),
    }



# ============================================================
# skill_install (5/20 拆: 575 行抽到 catfish_tools_install.py)
# ============================================================

from .catfish_tools_install import (  # noqa: E402, F401
    _check_skill_dedup,
    _dry_run_skill,
    _install_from_hub,
    _list_existing_skills,
    skill_install,
)

# ============================================================
# BL-MEMORY-DEDUPE-COMPRESS (5/17 凌晨, P2 #1+#2 lite 版)
# catfish_memory_dedupe / catfish_memory_compress — 给 LLM 主动调的整理工具
# 走 jieba (catfish 已装) + heuristic, 不调 hermes LCM / 不用 embedding.
# 真版本留白天清醒做.
# ============================================================


_HERMES_ENTRY_DELIM = "\n§\n"


def _read_hermes_memory_entries(target: str) -> List[str]:
    """读 hermes 0.13 ~/.hermes/memories/<USER|MEMORY>.md, 按 § 分隔解析."""
    if target not in ("user", "memory"):
        return []
    filename = "USER.md" if target == "user" else "MEMORY.md"
    path = Path.home() / ".hermes" / "memories" / filename
    if not path.exists():
        return []
    try:
        content = path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return []
    if not content.strip():
        return []
    return [s.strip() for s in content.split(_HERMES_ENTRY_DELIM) if s.strip()]


def _jieba_tokens(text: str) -> set:
    """jieba 分词 → 去停用词 → set. 用于 Jaccard."""
    try:
        import jieba  # noqa: PLC0415
    except ImportError:
        # jieba 没装 → fallback: 字符级 unigram
        return set(text)
    # 简易停用词 (中文常见 + 标点)
    stopwords = {
        "的", "了", "是", "在", "我", "你", "他", "她", "我们", "你们",
        "和", "跟", "也", "都", "就", "这", "那", "有", "没", "不",
        ",", "。", "?", "!", "、", " ", "\n", "(", ")", "—",
    }
    tokens = set(jieba.lcut(text))
    return {t for t in tokens if t.strip() and t not in stopwords}


def _jaccard_similarity(a: set, b: set) -> float:
    """Jaccard 相似度 |a ∩ b| / |a ∪ b|."""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union > 0 else 0.0


def memory_dedupe(args: Dict[str, Any]) -> Dict[str, Any]:
    """扫 hermes USER.md / MEMORY.md, 找 Jaccard 相似度 ≥ threshold 的 entry 对.

    返建议给 LLM, 不真改盘 (员工 explicit consent 才动, 通过 memory tool).
    """
    target = args.get("target", "both")
    threshold = float(args.get("threshold", 0.6))

    targets_to_scan = ["user", "memory"] if target == "both" else [target]
    all_suggestions: List[Dict[str, Any]] = []

    for t in targets_to_scan:
        entries = _read_hermes_memory_entries(t)
        if len(entries) < 2:
            continue
        # 每对 entry 比 Jaccard
        token_cache = {e: _jieba_tokens(e) for e in entries}
        seen_pairs = set()
        for i, e1 in enumerate(entries):
            for j, e2 in enumerate(entries):
                if i >= j:  # 不重复对
                    continue
                pair_key = (e1[:30], e2[:30])
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)
                sim = _jaccard_similarity(token_cache[e1], token_cache[e2])
                if sim >= threshold:
                    # 选长的 entry 作为合并基础 (信息更全)
                    longer, shorter = (e1, e2) if len(e1) >= len(e2) else (e2, e1)
                    all_suggestions.append({
                        "target": t,
                        "entries": [e1, e2],
                        "similarity": round(sim, 2),
                        "suggested_keep": longer,
                        "suggested_remove": shorter,
                        "suggested_merge": (
                            f"{longer} (合并: '{shorter}')" if longer != shorter else longer
                        ),
                    })

    return {
        "ok": True,
        "scanned_targets": targets_to_scan,
        "threshold": threshold,
        "suggestions_count": len(all_suggestions),
        "suggestions": all_suggestions,
        "next_step_for_llm": (
            "把 suggestions 列给员工 review, 员工 yes 才调 "
            "memory(action='remove', old_text=suggested_remove) + "
            "memory(action='replace', old_text=suggested_keep, content=suggested_merge). "
            "员工 no → 不动."
        ),
    }


def memory_compress(args: Dict[str, Any]) -> Dict[str, Any]:
    """看 USER.md / MEMORY.md 使用率, 提议把最老 N 条合并成摘要.

    不真改盘, 只返建议. 员工 yes 才动.
    """
    target = args.get("target")
    oldest_n = int(args.get("oldest_n", 5))
    if target not in ("user", "memory"):
        return {
            "ok": False,
            "error": f"target 必须是 'user' 或 'memory', 收到: {target!r}",
        }

    char_limit = 1375 if target == "user" else 2200  # hermes 默认
    entries = _read_hermes_memory_entries(target)
    total_chars = sum(len(e) for e in entries)
    usage_pct = (total_chars / char_limit * 100) if char_limit > 0 else 0

    if usage_pct < 80:
        return {
            "ok": True,
            "target": target,
            "usage_pct": round(usage_pct, 1),
            "char_limit": char_limit,
            "total_chars": total_chars,
            "entries_count": len(entries),
            "action_needed": False,
            "message": (
                f"{target} 当前用 {usage_pct:.1f}% ({total_chars}/{char_limit} chars), "
                f"< 80%, 不需要压缩. 等 entries 多了再叫我."
            ),
        }

    # > 80% — 选最老 N 条 (entries 头部是老的, hermes 按 add 顺序写)
    oldest = entries[: min(oldest_n, len(entries))]
    oldest_chars = sum(len(e) for e in oldest)

    return {
        "ok": True,
        "target": target,
        "usage_pct": round(usage_pct, 1),
        "char_limit": char_limit,
        "total_chars": total_chars,
        "entries_count": len(entries),
        "action_needed": True,
        "oldest_n": len(oldest),
        "oldest_entries": oldest,
        "oldest_chars": oldest_chars,
        "would_save_chars_if_summary_under": int(oldest_chars * 0.4),
        "next_step_for_llm": (
            f"建议把 {target} 这 {len(oldest)} 条最老 entry (共 {oldest_chars} chars) "
            f"合并成一条摘要 entry (目标 < {int(oldest_chars * 0.4)} chars). "
            "你写好摘要后给员工 review: '我想把这 N 条压成 1 条摘要 X, 老的就删了'. "
            "员工 yes → 调 memory(action='remove') 删 N 条, 再 "
            "memory(action='add', content=摘要) 写新. 员工 no → 不动."
        ),
    }


# ============================================================
# catfish_skill_delete — 安全删除 skill (五一 sprint Day 2)
# ============================================================


def skill_delete(args: Dict[str, Any]) -> Dict[str, Any]:
    """tool: 删除 catfish 工程审定 skill 整个目录.

    设计:
    - 必须 confirm=True (防误删)
    - 删除前先复制到 ~/.catfish/skill-trash/<unix-ts>-<basename>/ (30 天内可恢复)
    - audit jsonl 记 event_type=delete

    args:
        skill_path: 'department/leadership-briefing'
        reason: 删除原因 (写 audit)
        confirm: True (硬要求, 防误删)
    """
    skill_path = (args.get("skill_path") or "").strip().strip("/")
    reason = (args.get("reason") or "").strip()
    confirm = bool(args.get("confirm", False))

    if not skill_path:
        return {"ok": False, "error": "skill_path 必填"}
    if ".." in skill_path.split("/"):
        return {"ok": False, "error": "skill_path 不允许 '..' 越界"}
    if not reason:
        return {"ok": False, "error": "reason 必填 (写 audit log)"}
    if not confirm:
        return {
            "ok": False,
            "error": (
                "confirm 必须 true. 这是不可逆操作 (虽然 30 天内可从 trash 恢复). "
                "员工没明确说删, 不要自己判断 confirm=true."
            ),
        }

    root = _catfish_skills_root()
    if root is None:
        return {"ok": False, "error": "找不到 catfish skills 目录"}

    skill_dir = root / skill_path
    if not skill_dir.is_dir():
        return {"ok": False, "error": f"skill 不存在: {skill_path}"}

    # 备份到 ~/.catfish/skill-trash/<unix-ts>-<basename>/
    ts = int(time.time())
    basename = skill_dir.name
    trash_root = Path.home() / ".catfish" / "skill-trash"
    trash_root.mkdir(parents=True, exist_ok=True)
    backup_dir = trash_root / f"{ts}-{basename}"

    audit_event: Dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "event_type": "delete",
        "skill_path": skill_path,
        "reason": reason[:500],
    }

    try:
        shutil.move(str(skill_dir), str(backup_dir))
        # 读 metadata 记到 audit (虽然 skill 已经移走, 但 backup_dir 里 SKILL.md 还在)
        metadata = _read_skill_metadata(backup_dir / "SKILL.md")
        audit_event.update({
            "ok": True,
            "skill_version": metadata["version"],
            "deprecated": metadata["deprecated"],
            "backup_path": str(backup_dir),
        })
        _write_skill_audit(audit_event)

        return {
            "ok": True,
            "deleted_path": skill_path,
            "backup_path": str(backup_dir),
            "summary": (
                f"已删除 skill {skill_path} (备份在 {backup_dir}, 30 天内可恢复). "
                f"重启 Companion 后仪表盘也会移除. 原因: {reason}"
            ),
        }
    except Exception as e:
        audit_event.update({
            "ok": False,
            "error_type": type(e).__name__,
            "error_msg": str(e)[:500],
        })
        _write_skill_audit(audit_event)
        return {
            "ok": False,
            "error": f"删除 {skill_path} 失败: {e!r}",
        }


