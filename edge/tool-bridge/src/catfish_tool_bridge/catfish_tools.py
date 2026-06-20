"""Catfish 原生 tools —— 不来自 hermes registry, 是 catfish 自己加的。

为什么需要:
    Hermes 的 session_search / memory_recall 是面向 LLM 自己"翻历史"用的, 但
    员工聊天里问"今天小鲶学了啥"时, LLM 调 session_search 要么搜不到要么
    答非所问 (今晚截图里就是这样)。我们暴露一个明确的 catfish_today_summary
    工具, 让 LLM 一眼知道该调它。

数据源:
    1. ~/.hermes/USER.md + memories/*.md  → 今天有更新的 memory
    2. ~/.hermes/skills/<ns>/<name>/SKILL.md  → 今天 mtime 落在今天的
    3. ~/.hermes/state.db sessions  → 今天启动的会话 + token 总量
    4. ~/.hermes/state.db messages  → 今天的 tool 调用次数

跟 companion-app/src-tauri/src/commands/learning.rs 是同一份逻辑的 Python
镜像 —— 故意不走 IPC 调 Companion, 因为 tool-bridge 起来时 Companion 不一
定开着 (CLI 也在用 tool-bridge)。两边各自直读 ~/.hermes 是最 robust 的。
"""
from __future__ import annotations

import base64
import json
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple



# ============================================================
# 工具 schema —— 给 LLM 看的描述
# (BL-TOOL-SPLIT 5/20: 2088 行 schema 抽到 catfish_tool_schemas.py 防超 800 红线)
# ============================================================

from .catfish_tool_schemas import CATFISH_NATIVE_TOOLS  # noqa: E402




# ============================================================
# Today summary + Screenshot + utility helpers
# (BL-TOOL-SPLIT 5/20: 625 行抽到 catfish_tools_today.py)
# ============================================================

from .catfish_tools_today import (  # noqa: E402, F401
    KNOWN_METHODOLOGIES,
    _build_summary,
    _collect_audit_stats_today,
    _collect_db_stats,
    _collect_memories,
    _collect_new_skills,
    _collect_soft_skill_stats,
    _collect_unused_skills,
    _extract_description,
    _hermes_dir,
    _home,
    _is_today,
    _screencapture_macos,
    _screencapture_windows,
    _today_start_unix,
    _unix_to_iso,
    _week_start_unix,
    capture_screenshot,
    collect_today_summary,
)


# ============================================================
# 浏览器全栈 (catfish_browser_*)
# (BL-TOOL-SPLIT 5/20: 1370 行 browser 抽到 catfish_tools_browser.py)
# 走 Playwright connect_over_cdp 复用员工已登录 Chrome.
# 详见 catfish_tools_browser.py 顶部 docstring (设计 / 历史 / 红线).
# ============================================================

from .catfish_tools_browser import (  # noqa: E402
    browser_click,
    browser_evaluate,
    browser_fill,
    browser_find_by_text,
    browser_goto,
    browser_screenshot,
    browser_snapshot,
)

# ============================================================
# Skill backup (catfish_skill_backup)
# ============================================================
#
# 配套 catfish-policy R10 + SOUL "Skill 生成纪律"扩展 + docs/SKILL-LIFECYCLE.md.
# 防御 skill 退化: skill_manage(action=update/delete) 之前必须先调本 tool 备份,
# 老版会留在 ~/.hermes/skills/<ns>/<skill>/.versions/<unix-ts>.md, 员工说"回退"
# 时模型从这里拿最近一版替换.

import shutil as _shutil  # noqa: E402  (renamed alias to avoid shadowing)


def skill_backup(args: Dict[str, Any]) -> Dict[str, Any]:
    """把当前 skill 的 SKILL.md 复制到 .versions/<unix-ts>.md."""
    skill_name = (args.get("skill_name") or "").strip()
    reason = (args.get("reason") or "").strip()

    if not skill_name:
        return {
            "type": "error",
            "error": "skill_name 必填, 格式 'namespace/skill_name', 例如 'productivity/catfish-email'",
        }
    if not reason:
        return {
            "type": "error",
            "error": "reason 必填, 一句话说明为啥要改/删这个 skill",
        }
    if "/" not in skill_name:
        return {
            "type": "error",
            "error": (
                f"skill_name 格式错: '{skill_name}'. 必须是 'namespace/skill', "
                "例如 'productivity/expense-submit'"
            ),
        }

    skills_root = _hermes_dir() / "skills"
    skill_dir = skills_root / skill_name
    skill_md = skill_dir / "SKILL.md"

    # skill_dir 可能是软链 (catfish 自家 skill 走 install.sh 软链回源代码),
    # 这种 skill 不能让 LLM 改, R6 已防, 但这里也加一道
    if skill_dir.is_symlink():
        return {
            "type": "error",
            "error": (
                f"skill '{skill_name}' 是软链 (大概率是 catfish 自家 skill, "
                "由 install.sh 管理), LLM 不能改. 想改让员工跑 install.sh 重装"
            ),
        }

    if not skill_md.exists():
        return {
            "type": "error",
            "error": (
                f"找不到 {skill_md}. skill '{skill_name}' 可能不存在, "
                "或者 namespace/name 拼错了. 用 skill_view / skill_list 确认下"
            ),
        }

    # backup 到 .versions/<unix-ts>.md
    versions_dir = skill_dir / ".versions"
    versions_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    backup_path = versions_dir / f"{ts}.md"

    try:
        _shutil.copy2(skill_md, backup_path)
    except OSError as e:
        return {"type": "error", "error": f"backup 失败: {e}"}

    # 看下 .versions/ 现在有几版, 给个 UI hint
    try:
        version_count = sum(
            1 for p in versions_dir.iterdir() if p.is_file() and p.suffix == ".md"
        )
    except OSError:
        version_count = 1

    return {
        "type": "ok",
        "skill_name": skill_name,
        "backup_path": str(backup_path),
        "version_count": version_count,
        "reason": reason,
        "summary": (
            f"已 backup '{skill_name}' 到 {backup_path}. 现在 .versions/ 有 "
            f"{version_count} 个历史版本. 现在可以安全调 skill_manage update/delete."
        ),
    }


# ============================================================
# session_facts — 跟 LLM attention 失焦 hot-fix 配套, 工程级兜底
# ============================================================
#
# 设计 (2026-04-28 鸿波 demo 后加):
#   SOUL.md 复述模式靠模型自觉, 不一定每次都 quote 关键事实. session_facts
#   是工程级兜底: 模型听到员工硬事实时调 catfish_remember, 写到一个 JSON 文件;
#   gateway 每次 chat 请求, 自动把这个文件内容拼到 system prompt 末尾.
#   不依赖 attention, 永远在最近 token.
#
# 跟 memory_save 的区别:
#   memory_save → 跨 session 永久 (写 ~/.hermes/memories/*.md)
#   catfish_remember → 当前 session 内的硬事实 (写 ~/.catfish/session_facts.json)
#                      session 结束员工 rm 文件即可清空
#
# 边界:
#   - key 1-100 字符, value 1-1000 字符 (防滥用)
#   - 同 key **保留版本** (BL-MM2 五一 sprint 5/5 晚): 不再 silent overwrite,
#     新值 push 到 revision list, 保留 prev_value, 显示 "已更新 N 次".
#     SOUL BL-MM1 要求模型 quote 旧值, 这里给到工程支持: gateway inject 时
#     带上 "上次值: X", 模型就算自觉性差也能看到.
#   - 单文件全局 (Phase 1 单用户单进程; SSO 上来后加 user_id 区分)
#   - 文件不存在 = 没有 facts, gateway inject 跳过
#
# Schema (v2, 2026-05-05):
#   {
#     "key1": [
#       {"value": "v1", "ts": 1714867200.0, "prev_value": null},
#       {"value": "v2", "ts": 1714867260.0, "prev_value": "v1"},
#     ],
#     ...
#   }
#   list 顺序: [0] 最早, [-1] 最新 (current). value = revisions[-1]["value"].
#
# 向后兼容:
#   旧 schema {"key": "value"} (string) 会被 _read_session_facts 自动迁移到
#   单 revision list 形态, 写回时落新 schema. 员工不需要手动迁.

SESSION_FACTS_PATH = Path.home() / ".catfish" / "session_facts.json"
_FACTS_KEY_MAX_LEN = 100
_FACTS_VALUE_MAX_LEN = 1000
_FACTS_MAX_ENTRIES = 50  # 防内存爆: 超过 50 个 key 拒绝再加
_FACTS_MAX_REVISIONS_PER_KEY = 5  # BL-MM2: 同 key 最多保留 5 个历史版本, 老的截掉

# 类型 alias
Revision = Dict[str, Any]  # {"value": str, "ts": float, "prev_value": str | None}
FactsMap = Dict[str, List[Revision]]


def _normalize_revision(r: Any) -> Optional[Revision]:
    """把磁盘上一条 revision 规整成合法形态. 不合法返 None."""
    if not isinstance(r, dict):
        return None
    val = r.get("value")
    if not isinstance(val, str):
        return None
    val = val[:_FACTS_VALUE_MAX_LEN]
    ts = r.get("ts")
    if not isinstance(ts, (int, float)):
        ts = 0.0
    prev = r.get("prev_value")
    if prev is not None and not isinstance(prev, str):
        prev = None
    if isinstance(prev, str):
        prev = prev[:_FACTS_VALUE_MAX_LEN]
    return {"value": val, "ts": float(ts), "prev_value": prev}


def _read_session_facts() -> FactsMap:
    """读 session_facts.json, 规整成 v2 schema (revision list).

    兼容:
      - 文件不存在 / JSON 损坏 → 返空 dict
      - 旧 schema {"key": "value"} → 自动迁移到单 revision list
      - 新 schema {"key": [{...}, ...]} → 校验每条 revision

    不会写盘 (read-only). 真正落盘是下次 _write_session_facts 时.
    """
    if not SESSION_FACTS_PATH.exists():
        return {}
    try:
        with open(SESSION_FACTS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}

    out: FactsMap = {}
    for k, v in data.items():
        if not isinstance(k, str):
            continue
        k = k[:_FACTS_KEY_MAX_LEN]
        if isinstance(v, str):
            # 旧 schema: 单 string. 包成单 revision (ts=0 表示未知).
            out[k] = [{"value": v[:_FACTS_VALUE_MAX_LEN], "ts": 0.0, "prev_value": None}]
        elif isinstance(v, list):
            revs: List[Revision] = []
            for r in v:
                norm = _normalize_revision(r)
                if norm is not None:
                    revs.append(norm)
            if revs:
                # 截到最近 N 个 (防文件被乱塞)
                if len(revs) > _FACTS_MAX_REVISIONS_PER_KEY:
                    revs = revs[-_FACTS_MAX_REVISIONS_PER_KEY:]
                out[k] = revs
        # 其他类型 (int/dict/None) 跳过
    return out


def _write_session_facts(facts: FactsMap) -> None:
    """写回 session_facts.json. 失败抛, 让 caller 处理 (返回 error)."""
    SESSION_FACTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SESSION_FACTS_PATH, "w", encoding="utf-8") as f:
        json.dump(facts, f, ensure_ascii=False, indent=2)


def _current_value(revisions: List[Revision]) -> Optional[str]:
    """从 revision list 取当前值. 空 list → None."""
    if not revisions:
        return None
    return revisions[-1].get("value")


def remember_fact(args: Dict[str, Any]) -> Dict[str, Any]:
    """tool: 记一个 session 内的硬事实 (BL-MM2: 版本化, 不 silent overwrite).

    - 新 key → push 第一条 revision (prev_value=None)
    - 旧 key + 同值 → no-op, 不算更新, 不 push 新 revision (防重复 tool call 灌脏数据)
    - 旧 key + 新值 → push 新 revision (prev_value=旧 current_value),
                      revision list 超 _FACTS_MAX_REVISIONS_PER_KEY 时截掉最早的
    """
    key = (args.get("key") or "").strip()
    value = (args.get("value") or "").strip()
    if not key:
        return {"type": "error", "error": "key 必填"}
    if not value:
        return {"type": "error", "error": "value 必填"}
    if len(key) > _FACTS_KEY_MAX_LEN:
        return {"type": "error", "error": f"key 太长 (>{_FACTS_KEY_MAX_LEN} 字符)"}
    if len(value) > _FACTS_VALUE_MAX_LEN:
        return {"type": "error", "error": f"value 太长 (>{_FACTS_VALUE_MAX_LEN} 字符)"}

    facts = _read_session_facts()
    existing = facts.get(key)
    is_existing = existing is not None and len(existing) > 0

    if not is_existing and len(facts) >= _FACTS_MAX_ENTRIES:
        return {
            "type": "error",
            "error": (
                f"session_facts 已满 ({_FACTS_MAX_ENTRIES} 条上限). "
                "员工 rm ~/.catfish/session_facts.json 清空, 或员工 explicit "
                "告诉你哪些可以删."
            ),
        }

    prev_value: Optional[str] = _current_value(existing) if is_existing else None

    # 同值再调一次 = no-op, 不污染 revision history
    if is_existing and prev_value == value:
        revs_existing = existing or []
        return {
            "type": "ok",
            "key": key,
            "value_preview": value[:100] + ("…" if len(value) > 100 else ""),
            "total_facts": len(facts),
            "overwrite": False,
            "no_change": True,
            "previous_value": None,  # 同值, 没有"上次值"概念
            "revision_count": len(revs_existing),
            "summary": (
                f"'{key}' 已是这个值, 不重复记. "
                f"当前 {len(facts)} 条 session_facts."
            ),
        }

    new_rev: Revision = {
        "value": value,
        "ts": time.time(),
        "prev_value": prev_value,
    }
    if is_existing:
        revs = list(existing or [])
        revs.append(new_rev)
        # 截到最近 N 个
        if len(revs) > _FACTS_MAX_REVISIONS_PER_KEY:
            revs = revs[-_FACTS_MAX_REVISIONS_PER_KEY:]
        facts[key] = revs
    else:
        facts[key] = [new_rev]

    try:
        _write_session_facts(facts)
    except Exception as e:
        return {"type": "error", "error": f"写 session_facts 失败: {e}"}

    revision_count = len(facts[key])
    if is_existing:
        verb = "更新"
        # 给模型显式 prev_value, 配合 SOUL BL-MM1 quote 旧值纪律
        prev_preview = (prev_value[:80] + "…") if prev_value and len(prev_value) > 80 else (prev_value or "")
        summary = (
            f"更新了 '{key}' (第 {revision_count} 版). 上次值: {prev_preview!r}. "
            f"按 BL-MM1 纪律, 你回员工时**必须**主动 quote 旧值 (\"我之前记的是 X, 现在改成 Y\"), "
            f"不要装作从来没记过."
        )
    else:
        verb = "记住"
        summary = (
            f"记住了 '{key}' (首次). "
            f"当前 {len(facts)} 条 session_facts. "
            f"gateway 会在每次 chat 自动 inject 到 system prompt 末尾."
        )

    return {
        "type": "ok",
        "key": key,
        "value_preview": value[:100] + ("…" if len(value) > 100 else ""),
        "total_facts": len(facts),
        "overwrite": is_existing,
        "previous_value": prev_value,
        "revision_count": revision_count,
        "summary": summary,
    }



# ============================================================
# Skill ops (propose / propose_revision / run_skill + helpers)
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
# P3.5.43: install_proposal — 从 jsonl proposal 一键装 hermes-兼容 SKILL.
from .catfish_tools_propose import install_proposal  # noqa: E402

# ============================================================
# a2a_ask / skill_install / memory_dedupe / memory_compress / skill_delete
# (BL-TOOL-SPLIT 5/20: 944 行抽到 catfish_tools_install_and_ops.py)
# ============================================================

from .catfish_tools_install_and_ops import (  # noqa: E402, F401
    _check_skill_dedup,
    _dry_run_skill,
    _install_from_hub,
    _jaccard_similarity,
    _jieba_tokens,
    _list_existing_skills,
    _read_hermes_memory_entries,
    a2a_ask,
    memory_compress,
    memory_dedupe,
    skill_delete,
    skill_install,
)

# ============================================================
# dispatch 入口
# ============================================================

NATIVE_TOOL_NAMES = {t["name"] for t in CATFISH_NATIVE_TOOLS}


def is_native(name: str) -> bool:
    return name in NATIVE_TOOL_NAMES


def dispatch_native(name: str, args: Dict[str, Any]) -> Any:
    # BL-MM9-FREEZE (5/12): 业务流程 tool 自动 trace.
    # 拦截白名单内 tool 调用前后记录到 ~/.catfish/traces/active.jsonl,
    # 供 catfish_freeze_skill 凝固为 script.py + SKILL.md.
    from . import trace_recorder  # noqa: PLC0415

    if trace_recorder.is_recorded(name):
        # BL-MM9-FREEZE-bugfix (5/12): 嵌套深度判定. 教学路径走最外层 dispatch
        # (员工教 LLM, LLM 调 catfish_browser_* → wrapper depth=1 → 录).
        # 复用路径走 catfish_run_skill → script.py 内部用 dispatch_native 调
        # catfish_browser_* → wrapper depth>=2 → **不录** (script 行为不该污染
        # 教学 trace, 否则下次 freeze 撞混).
        with trace_recorder.record_depth_guard():
            should_record = trace_recorder.is_outermost()
            _t0 = time.time()
            _err: Exception | None = None
            try:
                result = _dispatch_native_inner(name, args)
                _ok = bool(result.get("ok", True)) if isinstance(result, dict) else True
                return result
            except Exception as e:
                _err = e
                _ok = False
                raise
            finally:
                if should_record:
                    _dur = int((time.time() - _t0) * 1000)
                    trace_recorder.record(
                        tool_name=name,
                        args=args or {},
                        result=(
                            locals().get("result")
                            if _err is None
                            else {"ok": False, "error": repr(_err)}
                        ),
                        ok=_ok,
                        duration_ms=_dur,
                    )
    else:
        return _dispatch_native_inner(name, args)


def _dispatch_native_inner(name: str, args: Dict[str, Any]) -> Any:
    """原 dispatch_native body — 包了 trace wrapper 之后从这里调."""
    if name == "catfish_remember":
        return remember_fact(args)
    if name == "catfish_propose_skill":
        return propose_skill(args)
    if name == "catfish_propose_skill_revision":
        return propose_skill_revision(args)
    if name == "catfish_install_proposal":
        # P3.5.43: 一键从 proposal 装 hermes-兼容 SKILL (跟 propose_skill 配套).
        return install_proposal(args)
    if name == "catfish_today_summary":
        return collect_today_summary()
    if name == "catfish_screenshot":
        return capture_screenshot(args)
    if name == "catfish_browser_goto":
        return browser_goto(args)
    if name == "catfish_browser_click":
        return browser_click(args)
    if name == "catfish_browser_fill":
        return browser_fill(args)
    if name == "catfish_browser_snapshot":
        return browser_snapshot(args)
    if name == "catfish_browser_screenshot":
        return browser_screenshot(args)
    if name == "catfish_browser_find_by_text":
        return browser_find_by_text(args)
    # BL-TOOLBRIDGE-CONSOLE-TOOL (5/27 鸿波): 两个名字, 同一个实现 — 防 LLM 在
    # console/evaluate 之间反复 self-correct 但抓不到. Anthropic Computer Use /
    # Playwright MCP 习惯叫 console, 自己定的规范叫 evaluate, 双口入都接.
    if name in ("catfish_browser_evaluate", "catfish_browser_console"):
        return browser_evaluate(args)
    if name == "catfish_skill_backup":
        return skill_backup(args)
    if name == "catfish_run_skill":
        return run_skill(args)
    if name == "catfish_skill_install":
        return skill_install(args)
    if name == "catfish_skill_delete":
        return skill_delete(args)
    if name == "catfish_a2a_ask":
        # 5/26 DEPRECATED: A2A federation 整套砍 (0 真客户 + 1695 LOC).
        return {"type": "error", "error": "catfish_a2a_ask 5/26 deprecated — Plan D Federation 整套停, 详见 docs/HERMES-013-ALIGN.md"}
    # BL-MEMORY-DEDUPE-COMPRESS (5/17 凌晨)
    if name == "catfish_memory_dedupe":
        return memory_dedupe(args)
    if name == "catfish_memory_compress":
        return memory_compress(args)
    # 5/6 BL-MM7 user profile (长期画像, 跨 session)
    if name == "catfish_user_profile_get":
        from . import user_profile  # noqa: PLC0415
        return user_profile.user_profile_get(args)
    if name == "catfish_user_profile_propose":
        from . import user_profile  # noqa: PLC0415
        return user_profile.user_profile_propose(args)
    if name == "catfish_user_profile_confirm":
        from . import user_profile  # noqa: PLC0415
        return user_profile.user_profile_confirm(args)
    if name == "catfish_user_profile_clear":
        from . import user_profile  # noqa: PLC0415
        return user_profile.user_profile_clear(args)
    # 5/6 BL-MM8 文书风格 fingerprint
    if name == "catfish_style_fingerprint_get":
        from . import style_fingerprint  # noqa: PLC0415
        return style_fingerprint.style_fingerprint_get(args)
    if name == "catfish_style_fingerprint_refresh":
        from . import style_fingerprint  # noqa: PLC0415
        return style_fingerprint.style_fingerprint_refresh(args)
    if name == "catfish_style_fingerprint_clear":
        from . import style_fingerprint  # noqa: PLC0415
        return style_fingerprint.style_fingerprint_clear(args)
    # 5/8 BL-A2.1: 后台任务 (chat 不阻塞)
    if name == "catfish_run_task":
        from . import task_manager  # noqa: PLC0415
        return task_manager.submit_typed_task(
            kind=args.get("kind") or "execute_code",
            payload=args.get("payload") or {},
            label=args.get("label") or "",
        )
    if name == "catfish_task_status":
        from . import task_manager  # noqa: PLC0415
        return task_manager.manager().status_dict(args.get("task_id") or "")
    if name == "catfish_task_list":
        from . import task_manager  # noqa: PLC0415
        return {"tasks": task_manager.manager().list_active()}
    if name == "catfish_task_result":
        from . import task_manager  # noqa: PLC0415
        return task_manager.manager().result_dict(args.get("task_id") or "")
    # BL-LONG-RUNNING-V1-PHASE-C (6/1): retry 中断/失败任务
    if name == "catfish_task_retry":
        from . import task_manager  # noqa: PLC0415
        return task_manager.retry_task(args)
    # BL-D2 (5/10) Skills Hub publish
    if name == "catfish_skill_publish":
        from . import skill_publish  # noqa: PLC0415
        return skill_publish.skill_publish(args)
    # P3.3.18 (6/10) Wiki Hub publish / install / unpublish (manifesto 公理 2 例外)
    if name == "catfish_wiki_publish":
        from . import wiki_publish  # noqa: PLC0415
        return wiki_publish.wiki_publish(args)
    if name == "catfish_wiki_install":
        from . import wiki_install  # noqa: PLC0415
        return wiki_install.wiki_install(args)
    if name == "catfish_wiki_unpublish":
        from . import wiki_install  # noqa: PLC0415  (同 module)
        return wiki_install.wiki_unpublish(args)
    # BL-Q3-ARCHIVE (5/11) tool message archive 读回
    if name == "catfish_read_tool_archive":
        from . import read_tool_archive  # noqa: PLC0415
        return read_tool_archive.read_tool_archive(args)
    # BL-Q3-WEBSKILL (5/11) 验证码 OCR
    if name == "catfish_recognize_captcha":
        from . import recognize_captcha  # noqa: PLC0415
        return recognize_captcha.recognize_captcha(args)
    # BL-Q3-WEBSKILL (5/11) 视觉定位元素
    if name == "catfish_browser_locate":
        from . import browser_locate  # noqa: PLC0415
        return browser_locate.locate(args)
    # BL-MM9-FREEZE-v2 (5/12 鸿波拍板) 教学→凝固→复用闭环 (显式 session 边界)
    if name == "catfish_teach_start":
        from . import skill_freeze  # noqa: PLC0415
        return skill_freeze.teach_start(args)
    if name == "catfish_teach_end":
        from . import skill_freeze  # noqa: PLC0415
        return skill_freeze.teach_end(args)
    if name == "catfish_freeze_inspect":
        from . import skill_freeze  # noqa: PLC0415
        return skill_freeze.freeze_inspect(args)
    if name == "catfish_freeze_skill":
        from . import skill_freeze  # noqa: PLC0415
        return skill_freeze.freeze_skill(args)
    if name == "catfish_freeze_rotate":
        from . import skill_freeze  # noqa: PLC0415
        return skill_freeze.freeze_rotate(args)
    # BL-FED2.1 (5/12 鸿波拍板) 专长从 employee_journal 自动抽
    if name == "catfish_extract_expertise":
        from . import expertise  # noqa: PLC0415
        return expertise.tool_extract_expertise(
            args,
            llm_call_fn=_expertise_llm_call,
            journal_text=_load_employee_journal(),
        )
    if name == "catfish_list_expertise":
        from . import expertise  # noqa: PLC0415
        return expertise.tool_list_expertise(args)
    if name == "catfish_confirm_expertise":
        from . import expertise  # noqa: PLC0415
        return expertise.tool_confirm_expertise(args)
    # BL-FED2.3 (5/12 鸿波拍板) 跨员工路由
    if name == "catfish_expert_consult":
        from . import expert_consult  # noqa: PLC0415
        return expert_consult.tool_expert_consult(args)
    # 5/26 DEPRECATED: A2A federation 整套砍.
    if name == "catfish_list_a2a_help":
        return {"type": "error", "error": "catfish_list_a2a_help 5/26 deprecated — A2A 整套停"}
    # BL-FIX-SESSION-SEARCH (5/13 鸿波"历史会话搜不到")
    if name == "catfish_search_sessions":
        from . import sessions_search  # noqa: PLC0415
        return sessions_search.tool_search_sessions(args)
    # BL-FILE-SESSION-INDEX-V1 Phase 2 (5/30): 跨会话搜员工上传过的附件
    # (PDF/Excel/Word/图片/音频), 走名字 LIKE + 内容 BM25 双层. 配合 Phase 1
    # ~/.catfish/attachments.db (Companion attachment_record 写入).
    if name == "catfish_search_attachments":
        from . import attachments_search  # noqa: PLC0415
        return attachments_search.tool_search_attachments(args)
    # BL-FILE-SESSION-INDEX-V1 Phase 3 (5/30): 反向索引 — 列员工所有上传过的
    # 附件 + 每个文件出现在哪些会话. 给 '我所有上传过的 Excel' / '这个 PDF
    # 在哪些会话被引用' 场景.
    if name == "catfish_list_my_attachments":
        from . import attachments_search  # noqa: PLC0415
        return attachments_search.tool_list_my_attachments(args)
    # BL-EMAIL-SEARCH-TOOL (5/18 鸿波"对话里检索没搜到邮件")
    if name == "catfish_email_search":
        from . import email_search  # noqa: PLC0415
        return email_search.tool_email_search(args)
    # BL-SKILLS-RAG-TOOL (5/25 鸿波 "现在做") — Progressive Disclosure 折叠区主动捞
    if name == "catfish_search_skills":
        from . import search_skills  # noqa: PLC0415
        return search_skills.tool_search_skills(args)
    # BL-STRATEGIC-DOC-SYNC Phase 3 (6/7 鸿波 audit) — 搜战略 doc (manifesto / patent / ...)
    if name == "catfish_search_docs":
        from . import search_docs  # noqa: PLC0415
        return search_docs.tool_search_docs(args)
    # P3.5.35 (6/18 鸿波 catch 'chat 没接 wiki_search') — 员工 wiki + 装机部门 wiki BM25
    if name == "catfish_wiki_search":
        from . import wiki_search  # noqa: PLC0415
        return wiki_search.tool_wiki_search(args)
    # BL-FIX-TIMEOUT-OUTPUTS (5/13 鸿波"做不出文档")
    if name == "catfish_list_my_outputs":
        from . import recent_outputs  # noqa: PLC0415
        return recent_outputs.tool_list_my_outputs(args)
    # BL-REMINDER (5/13 鸿波"macOS 提醒联动")
    if name == "catfish_create_reminder":
        from . import reminders  # noqa: PLC0415
        return reminders.tool_create_reminder(args)
    if name == "catfish_list_reminder_lists":
        from . import reminders  # noqa: PLC0415
        return reminders.tool_list_reminder_lists(args)
    # BL-CALENDAR (5/14 0:30 鸿波"ISO 会议 LLM 写脚本踩坑")
    if name == "catfish_create_calendar_event":
        from . import calendar_events  # noqa: PLC0415
        return calendar_events.tool_create_calendar_event(args)
    if name == "catfish_list_calendars":
        from . import calendar_events  # noqa: PLC0415
        return calendar_events.tool_list_calendars(args)
    # ── BL-ADVISOR (5/21 Phase 7): 6 个智能参谋 tool ──
    if name == "catfish_draft_email_reply":
        from . import advisor_drafts  # noqa: PLC0415
        return advisor_drafts.draft_email_reply(args)
    if name == "catfish_draft_meeting_brief":
        from . import advisor_drafts  # noqa: PLC0415
        return advisor_drafts.draft_meeting_brief(args)
    if name == "catfish_compose_followup_list":
        from . import advisor_drafts  # noqa: PLC0415
        return advisor_drafts.compose_followup_list(args)
    if name == "catfish_check_compliance":
        from . import advisor_scans  # noqa: PLC0415
        return advisor_scans.check_compliance(args)
    if name == "catfish_political_sensitivity_scan":
        from . import advisor_scans  # noqa: PLC0415
        return advisor_scans.political_sensitivity_scan(args)
    if name == "catfish_recall_decision_history":
        from . import advisor_recall  # noqa: PLC0415
        return advisor_recall.recall_decision_history(args)
    if name == "catfish_forget_about":
        from . import forget_about  # noqa: PLC0415
        return forget_about.forget_about(args)
    raise ValueError(f"unknown native tool: {name}")


# ─────────────────────────────────────────────────────────────
# BL-FED2.1 helpers — journal 加载 + LLM 调用
# ─────────────────────────────────────────────────────────────

def _load_employee_journal() -> str:
    """读 employee_journal.

    BL-FED2.4 (5/12) 修 path bug: 真路径 ~/.catfish/employee_journal.md (跟
    gateway employee_journal.py / session_summarizer / proactive.py 对齐).
    BL-FED2.1 第一版误写成 ~/.hermes/memories/employee_journal.md —
    用 hermes USER.md 的命名约定错搬过来.

    fallback: 老 path ~/.hermes/employee_journal.md (proactive.py 也有同款兼容).

    没有则返空串 (调用方会返 ok=False + '没东西可抽').
    """
    catfish_path = _home() / ".catfish" / "employee_journal.md"
    fallback_path = _home() / ".hermes" / "employee_journal.md"
    journal_path = catfish_path if catfish_path.exists() else fallback_path
    if not journal_path.exists():
        return ""
    try:
        return journal_path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return ""


def _expertise_llm_call(prompt: str) -> str:
    """走 gateway loopback POST /v1/chat/completions, model=role_resolver(chat_default).

    复用 browser_locate 同一套 GATEWAY_URL + id_token 模式. 不抛异常 — 失败返
    空串, 让 expertise.extract_from_journal 走"返非 JSON"分支自然降级.

    P3.5.29 Phase 5 (6/17 鸿波): model 真**role-resolved** —
    ``role_resolver.resolve("chat_default")`` 优先, 失败 fallback hardcoded
    ``catfish-private-main`` (客户改 roles.yaml 真**全代码跟着走**, 这里**0**
    硬编码改 sed 真**Companion / hermes / tool-bridge 同步**).
    """
    try:
        import httpx  # noqa: PLC0415
    except ImportError:
        return ""
    try:
        from .browser_locate import GATEWAY_URL, _read_id_token  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return ""
    token = _read_id_token()
    if not token:
        return ""
    # P3.5.29 Phase 8 (6/17 鸿波): model 真**chain** picker > role > 兜底.
    # picker_state.json 真**Companion chat.ts setModel 写**, 真**员工临时切影响这 LLM 调**.
    # 真**roles.yaml chat_default 真**中央默认 真**客户控制**.
    from . import picker_state, role_resolver  # noqa: PLC0415
    model_name = (
        picker_state.read_picker_model()
        or role_resolver.resolve("chat_default")
        or "catfish-private-main"
    )
    try:
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(
                f"{GATEWAY_URL}/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model_name,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens": 2000,
                    "stream": False,
                },
            )
        if resp.status_code != 200:
            return ""
        data = resp.json()
        return (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            or ""
        )
    except Exception:  # noqa: BLE001
        return ""
