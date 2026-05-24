"""BL-ADVISOR-FORGET (5/22 鸿波): 跨源清除特定关键词的记忆.

# 真问题

5/22 鸿波说"老李是测试数据", catfish 11:55 调 catfish-journal skill 清了 journal,
但 distilled_facts.md 没动, employee_journal 后续新 session 又把老李蒸馏进来.
反复循环.

之前打算加"排除清单" 补丁, 但鸿波点中: 未来真有老李 (公司新同事) 会被永久挡.
正解 = **物理清除**, 不留补丁. 未来真有老李, 从邮件/日历重新学习, 自然进系统.

# 工作流程

员工跟 catfish 说: "忘了老李" / "老李是测试数据, 清干净所有记忆"
       ↓
LLM 调 catfish_forget_about(keyword="老李")
       ├─ 备份所有要改的文件到 ~/.catfish/.forget_backup/<ts>/
       ├─ 扫 ~/.catfish/distilled_facts.md → 按行删
       ├─ 扫 ~/.catfish/employee_journal.md → 按 "## " 段删 (含 keyword 的整段)
       ├─ 扫 ~/.hermes/memories/*.md  → 按行删每个文件
       ├─ 扫 ~/.catfish/decisions.jsonl → 按行 JSON 删整行
       ├─ 扫 ~/.catfish/profile.json → 删 keyPeople/keyProjects 含 keyword 的项
       └─ 清 ~/.catfish/advisor_cache.json → 强制下次 advisor 重算
       ↓
报 {keyword, removed: {...}, backup_dir}

# 安全
- 任何删除前先备份, 失败可恢复
- profile_hints.md 不动 (员工显式标的, 不该自动清)
- session_goal.txt 不动 (太短, 含 keyword 概率低, 不值得动)
- ~/.hermes/state.db 不动 (sqlite 删条目复杂, 而且 session_summarizer 已经把内容抽到 journal,
   清 journal 就够; state.db 留着只供历史回看)
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("catfish.tool_bridge.forget_about")


def _catfish_dir() -> Path:
    home = os.environ.get("CATFISH_HOME", "").strip()
    if home:
        return Path(home).expanduser()
    return Path.home() / ".catfish"


def _hermes_memories_dir() -> Path:
    return Path.home() / ".hermes" / "memories"


def _backup_dir() -> Path:
    ts = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%dT%H-%M-%S")
    d = _catfish_dir() / ".forget_backup" / ts
    d.mkdir(parents=True, exist_ok=True)
    return d


def _backup_file(src: Path, backup_root: Path) -> None:
    """复制原文件到备份目录, 保持相对路径."""
    if not src.exists():
        return
    # 用 absolute path 子串当备份相对名
    rel_name = str(src).lstrip("/").replace("/", "__")
    dest = backup_root / rel_name
    try:
        shutil.copy2(src, dest)
    except OSError as e:
        logger.warning("[forget_about] 备份 %s 失败: %s", src, e)


def forget_about(args: dict[str, Any]) -> dict[str, Any]:
    """跨源清除特定关键词的记忆.

    Args:
      args.keyword: str — 要清的关键词 (人名 / 项目名 / 客户名 / 任何标识)
      args.confirm: bool (optional, 默认 True) — 安全确认. False 时 dry-run, 只统计不真删

    Returns:
      {
        "ok": True,
        "keyword": "老李",
        "dry_run": false,
        "removed": {
          "distilled_facts_lines": 1,
          "employee_journal_sections": 12,
          "hermes_memories_lines": 0,
          "decisions_entries": 0,
          "profile_keyPeople": 0,
          "profile_keyProjects": 0
        },
        "backup_dir": "/Users/xx/.catfish/.forget_backup/2026-05-22T14-30-00/",
        "advisor_cache_cleared": true
      }
    """
    keyword = str(args.get("keyword", "")).strip()
    if not keyword:
        return {"ok": False, "error": "keyword 不能空"}
    if len(keyword) > 100:
        return {"ok": False, "error": "keyword 太长 (>100 字), 太宽匹配会误删"}
    if len(keyword) < 2:
        return {"ok": False, "error": "keyword 太短 (<2 字), 会误命中. 至少 2 字"}

    dry_run = args.get("confirm") is False
    backup_root = None if dry_run else _backup_dir()

    removed: dict[str, int] = {
        "distilled_facts_lines": 0,
        "employee_journal_sections": 0,
        "hermes_memories_lines": 0,
        "decisions_entries": 0,
        "profile_keyPeople": 0,
        "profile_keyProjects": 0,
    }

    # ── 1. distilled_facts.md (按行删) ─────────────────────────────
    df_path = _catfish_dir() / "distilled_facts.md"
    if df_path.exists():
        try:
            text = df_path.read_text(encoding="utf-8")
            lines = text.splitlines(keepends=True)
            kept = [line for line in lines if keyword not in line]
            removed_count = len(lines) - len(kept)
            removed["distilled_facts_lines"] = removed_count
            if removed_count > 0 and not dry_run:
                if backup_root is not None:
                    _backup_file(df_path, backup_root)
                df_path.write_text("".join(kept), encoding="utf-8")
        except OSError as e:
            logger.warning("[forget_about] distilled_facts 处理失败: %s", e)

    # ── 2. employee_journal.md (按 "## " 段删) ───────────────────
    ej_path = _catfish_dir() / "employee_journal.md"
    if ej_path.exists():
        try:
            text = ej_path.read_text(encoding="utf-8")
            # split 用 "\n## " (注意保留最前面那个 "## " 也分得到)
            sections = text.split("\n## ")
            if sections:
                head = sections[0]
                body_sections = sections[1:]
                kept_sections = [s for s in body_sections if keyword not in s]
                removed_count = len(body_sections) - len(kept_sections)
                removed["employee_journal_sections"] = removed_count
                if removed_count > 0 and not dry_run:
                    if backup_root is not None:
                        _backup_file(ej_path, backup_root)
                    new_text = head
                    if kept_sections:
                        new_text += "\n## " + "\n## ".join(kept_sections)
                    ej_path.write_text(new_text, encoding="utf-8")
        except OSError as e:
            logger.warning("[forget_about] employee_journal 处理失败: %s", e)

    # ── 3. ~/.hermes/memories/*.md 或 *.txt (按行删每个文件) ───
    hm_dir = _hermes_memories_dir()
    if hm_dir.exists() and hm_dir.is_dir():
        for hm_file in hm_dir.iterdir():
            if not hm_file.is_file():
                continue
            if hm_file.suffix.lower() not in {".md", ".txt"}:
                continue
            try:
                text = hm_file.read_text(encoding="utf-8")
                lines = text.splitlines(keepends=True)
                kept = [line for line in lines if keyword not in line]
                file_removed = len(lines) - len(kept)
                if file_removed > 0:
                    removed["hermes_memories_lines"] += file_removed
                    if not dry_run:
                        if backup_root is not None:
                            _backup_file(hm_file, backup_root)
                        hm_file.write_text("".join(kept), encoding="utf-8")
            except OSError as e:
                logger.warning("[forget_about] hermes_memory %s 处理失败: %s", hm_file, e)

    # ── 4. decisions.jsonl (按行 JSON, 删含 keyword 的行) ────────
    dec_path = _catfish_dir() / "decisions.jsonl"
    if dec_path.exists():
        try:
            text = dec_path.read_text(encoding="utf-8")
            lines = text.splitlines(keepends=True)
            kept = []
            for line in lines:
                # 简单: 任何 line 含 keyword 整行删
                # 严格: parse JSON 看具体字段 — 不做, 因为 decision 是结构化的, 整体删更稳
                if keyword in line:
                    continue
                kept.append(line)
            removed_count = len(lines) - len(kept)
            removed["decisions_entries"] = removed_count
            if removed_count > 0 and not dry_run:
                if backup_root is not None:
                    _backup_file(dec_path, backup_root)
                dec_path.write_text("".join(kept), encoding="utf-8")
        except OSError as e:
            logger.warning("[forget_about] decisions 处理失败: %s", e)

    # ── 5. profile.json 的 keyPeople / keyProjects ───────────────
    profile_path = _catfish_dir() / "profile.json"
    if profile_path.exists():
        try:
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
            kp_before = profile.get("keyPeople", []) or []
            kpj_before = profile.get("keyProjects", []) or []
            if isinstance(kp_before, list):
                kp_after = [
                    p for p in kp_before
                    if not (isinstance(p, dict) and keyword in json.dumps(p, ensure_ascii=False))
                ]
                removed["profile_keyPeople"] = len(kp_before) - len(kp_after)
            else:
                kp_after = kp_before
            if isinstance(kpj_before, list):
                kpj_after = [
                    p for p in kpj_before
                    if not (isinstance(p, dict) and keyword in json.dumps(p, ensure_ascii=False))
                ]
                removed["profile_keyProjects"] = len(kpj_before) - len(kpj_after)
            else:
                kpj_after = kpj_before
            total_removed = removed["profile_keyPeople"] + removed["profile_keyProjects"]
            if total_removed > 0 and not dry_run:
                if backup_root is not None:
                    _backup_file(profile_path, backup_root)
                profile["keyPeople"] = kp_after
                profile["keyProjects"] = kpj_after
                profile_path.write_text(
                    json.dumps(profile, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("[forget_about] profile 处理失败: %s", e)

    total = sum(removed.values())

    # ── 6. advisor_cache.json 清掉, 触发下次重算 ─────────────────
    # 5/22 鸿波撞 bug 修: **只在真删了东西 (total > 0) 时才清 cache**.
    # 之前 0 命中也清, 浪费下次 advisor LLM 调用重算.
    cache_path = _catfish_dir() / "advisor_cache.json"
    cache_cleared = False
    if total > 0 and cache_path.exists() and not dry_run:
        try:
            cache_path.unlink()
            cache_cleared = True
        except OSError as e:
            logger.warning("[forget_about] cache 清挂: %s", e)

    # 5/22 鸿波撞 bug 修: 0 命中时清理空 backup_dir, 不留垃圾.
    if backup_root is not None and total == 0:
        try:
            if backup_root.exists() and not list(backup_root.iterdir()):
                backup_root.rmdir()
                backup_root = None
        except OSError as e:
            logger.warning("[forget_about] 清空 backup 失败: %s", e)

    logger.info(
        "[forget_about] keyword='%s' dry_run=%s total=%d removed=%s",
        keyword, dry_run, total, removed,
    )

    result: dict[str, Any] = {
        "ok": True,
        "keyword": keyword,
        "dry_run": dry_run,
        "removed": removed,
        "total_removed": total,
        "advisor_cache_cleared": cache_cleared,
    }
    if backup_root is not None:
        result["backup_dir"] = str(backup_root)
    return result


__all__ = ["forget_about"]
