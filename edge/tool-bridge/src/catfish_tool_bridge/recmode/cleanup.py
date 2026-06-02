"""BL-LEARN-RECMODE / 录屏清理 utility (5/15 v2 ship, 5/25 撤 daemon, 5/26 搬 edge).

# 5/26 BL-RECMODE-MIGRATE-TO-EDGE batch 1 (E.2): 跟 cdp_listener 同批从
# central/llm-gateway/.../recmode/ 搬这里. 详见同目录 cdp_listener.py 头部.

# 5/25 鸿波"录屏本来就在本机, catfish 凭啥后台删?" 哲学修正

## 旧设计 (5/15 V2 #67, ship 但 5/25 撤掉自动化)

- `cleanup_old_recordings` + `cleanup_daemon` 后台 24h 跑一次, 自动删
  ~/.catfish/recordings/<sid>/ 超 14 天的 session
- 理由当时是"截图含业务数据 (EIS 资质 / 维护人 / 公司名), 不能永久留"
- 跟 macOS '最近文件' / iCloud 14 天回收"心智对齐"

## 5/25 撤掉的真原因 (鸿波抓出来的)

- catfish 给员工的 talking point 是 "录屏 100% 在你电脑本机, 中央 0 字节"
- 但同时后台 daemon 自动删用户文件 = 嘴上不上传 + 暗中动你硬盘 = 信任撕裂
- 真正的设计哲学: **中央不存 = 中央不管. 员工本机数据员工主权**
- catfish 不是 OS 文件管理器, 不该代员工决定何时删本机文件
- 录屏含敏感是员工的责任 (跟 Word 文档含客户合同条款一样, Word 也不会自动删)

## 现在的位置

`cleanup_old_recordings()` 函数保留作 **显式 utility**:
- 员工 Dashboard "我的录屏" 区点 "全部清 N 天前" 按钮触发 (TODO #75)
- admin 跑 `catfish-recordings prune --older-than 30d` CLI 触发 (TODO follow-up)
- 测试 / 排错时手动调

**不再自动跑** — gateway app.py 启动 hook 删了 `cleanup_daemon()` 调度.

老的 `_is_kept_forever` 标志 + `.keep_forever` flag 文件保留作**向后兼容** (员工
历史录屏可能勾过"保留作 ground truth"), 但**新录屏默认就不删**, 这标志成 cosmetic.

# 6/2 BL-RECMODE-AUTO-CLEAN-RAW (鸿波 6/2 凌晨追加)

5/25 哲学保留 — 整 session 不自动删. 但**拆开看**: 训练原料 (events.jsonl + 截图
+ 转写) 跟员工成果 (skill 本身 + skill_draft + meta.json) 是两类东西. 原料消费完
就是历史冗余 (5-50 MB / session 占员工硬盘大头, selector_repair 也用不到, skill
runtime 看 SKILL.md + main.py).

新增 `cleanup_consumed_raw(session_dir)`:
- save_skill RPC (员工点"保存") 完成后**自动调一次**
- 只删原料 3 文件/目录 (events.jsonl / screenshots/ / transcripts.jsonl)
- 保留员工成果 (meta.json / skill_draft/)
- .keep_forever 标过的整 session 跳 (员工opt-out 主权)

不破"员工主权" — 没动员工"做的东西", 只清"训练用的原料". 跟"Word 自动清剪贴板"
不删 .docx 本身同理.

# Returns

`cleanup_old_recordings()` 返 dict {scanned, deleted, kept_forever, still_fresh,
errors, freed_bytes, deleted_session_ids} 让 caller (Dashboard / CLI) 打 summary.

`cleanup_consumed_raw(session_dir)` 返 dict {ok, has_saved_skill, removed_files,
freed_bytes, error} 让 caller (save_skill RPC / Companion) 反馈"释放 X MB".
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
from pathlib import Path

logger = logging.getLogger("catfish.recmode.cleanup")

# 5/25 BL-RECMODE-NO-AUTO-DELETE: 默认 TTL 留着, 但**不再有 daemon 自动触发**.
# 仅在员工/admin 显式调 cleanup_old_recordings(ttl_seconds=N*86400) 时生效.
DEFAULT_TTL_SECONDS = 14 * 24 * 3600


def _recordings_root() -> Path:
    """跟 cdp_listener._tasks_jsonl_path 同根 (CATFISH_HOME 优先)"""
    catfish_home = os.environ.get("CATFISH_HOME", "").strip()
    if catfish_home:
        return Path(catfish_home).expanduser() / "recordings"
    return Path.home() / ".catfish" / "recordings"


def _is_kept_forever(session_dir: Path) -> bool:
    """老元数据兼容 — V2 #67 时代员工勾"保留作 ground truth" 会写 _keep_forever.

    5/25 BL-RECMODE-NO-AUTO-DELETE 后语义变化:
    - 老: 没标 → 14 天删. 标了 → 永久留 (opt-in 保留)
    - 新: 没标 → 永久留 (新 default). 标了 → 仍永久留 (兼容历史录屏)

    显式 `cleanup_old_recordings(ttl=N天)` 触发时, 标了的录屏仍跳过 (保护员工
    主动标记的"重要" session 不被一锅烩删掉).
    """
    # 1. session_dir 自己有 KEEP flag 文件
    if (session_dir / ".keep_forever").exists():
        return True
    # 2. 任意 draft skill 标了 _keep_forever
    for meta in session_dir.glob("skill_draft/*/*/recmode_meta.json"):
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
            if data.get("_keep_forever") is True:
                return True
        except (OSError, json.JSONDecodeError):
            continue
    return False


def cleanup_old_recordings(
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    dry_run: bool = False,
) -> dict:
    """显式清理 ~/.catfish/recordings/<sid>/, 删超 TTL 的 session.

    5/25 BL-RECMODE-NO-AUTO-DELETE: **不再被 daemon 自动调用** —
    员工 Dashboard "我的录屏" 区 / admin CLI 显式触发.

    Args:
        ttl_seconds: 默认 14 天. caller 可传 7/30/90/180 等任意值
        dry_run: 只看不删 (admin audit 用)

    Returns:
        {"scanned": N, "deleted": M, "kept_forever": K, "still_fresh": F,
         "errors": [...], "freed_bytes": Y, "deleted_session_ids": [...]}
    """
    root = _recordings_root()
    if not root.exists():
        return {
            "scanned": 0, "deleted": 0, "kept_forever": 0, "still_fresh": 0,
            "errors": [], "freed_bytes": 0, "deleted_session_ids": [],
        }

    cutoff = time.time() - ttl_seconds
    stats: dict = {
        "scanned": 0,
        "deleted": 0,
        "kept_forever": 0,
        "still_fresh": 0,
        "errors": [],
        "freed_bytes": 0,
        "deleted_session_ids": [],
    }

    for sd in root.iterdir():
        if not sd.is_dir():
            continue
        stats["scanned"] += 1

        # 拿 session 起始时间 (meta.json 优先, 没就 dir mtime fallback)
        started_at = None
        meta_path = sd / "meta.json"
        if meta_path.exists():
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
                started_at = float(data.get("started_at") or 0)
            except (OSError, json.JSONDecodeError):
                pass
        if started_at is None or started_at <= 0:
            try:
                started_at = sd.stat().st_mtime
            except OSError:
                started_at = time.time()  # 算新文件不删

        # 还在 TTL 内
        if started_at > cutoff:
            stats["still_fresh"] += 1
            continue

        # opt-in 保留 (历史员工显式标过的不一锅烩删)
        if _is_kept_forever(sd):
            stats["kept_forever"] += 1
            logger.debug("RecMode cleanup 跳过 %s (_keep_forever)", sd.name)
            continue

        # 算大小给 freed_bytes 统计 (优雅: 失败默 0)
        size = 0
        try:
            for f in sd.rglob("*"):
                if f.is_file():
                    try:
                        size += f.stat().st_size
                    except OSError:
                        pass
        except OSError:
            pass

        if dry_run:
            stats["deleted"] += 1
            stats["freed_bytes"] += size
            stats["deleted_session_ids"].append(sd.name)
            logger.info(
                "RecMode cleanup [dry_run] 会删 %s (started %.0fh ago, %.1f MB)",
                sd.name, (time.time() - started_at) / 3600, size / 1024 / 1024,
            )
            continue

        try:
            shutil.rmtree(sd)
            stats["deleted"] += 1
            stats["freed_bytes"] += size
            stats["deleted_session_ids"].append(sd.name)
            logger.info(
                "RecMode cleanup 删 %s (started %.0fh ago, 释放 %.1f MB)",
                sd.name, (time.time() - started_at) / 3600, size / 1024 / 1024,
            )
        except OSError as e:
            stats["errors"].append({"session_id": sd.name, "error": str(e)})
            logger.warning("RecMode cleanup 删 %s 失败: %s", sd.name, e)

    return stats


# ── 6/2 BL-RECMODE-AUTO-CLEAN-RAW (鸿波 6/2 凌晨) ────────────────────────
#
# 5/25 BL-RECMODE-NO-AUTO-DELETE 撤了"后台 daemon 全删整个 session", 设计哲学
# "员工主权, catfish 不删". 6/2 鸿波重新审视: 训练原料 (events.jsonl + 截图 +
# 转写) skill 真"保存"到 ~/.catfish/skills/ 后变成历史冗余, 5-50 MB / session
# 占员工硬盘大头. 跟"员工成果" (skill 本身 + skill_draft 草稿 + meta.json) 不
# 同性质 — 原料是消费完的工业垃圾.
#
# 拆分清理: skill 真保存后, 删该 session 的**原料**, 保留**成果**.
# 不破"员工主权" — 没删员工"做的东西", 只清"训练用的原料".

# 训练原料文件名 (相对 session_dir 的 path). _consumable_raw = 跑完可以丢的.
# screenshots/ 是目录, 删整个; events.jsonl / transcripts.jsonl 是单文件.
_CONSUMABLE_RAW_TARGETS = ("events.jsonl", "transcripts.jsonl", "screenshots")


def _skills_root() -> Path:
    """~/.catfish/skills/ — 员工真"保存" 的 skill 落地处."""
    catfish_home = os.environ.get("CATFISH_HOME", "").strip()
    if catfish_home:
        return Path(catfish_home).expanduser() / "skills"
    return Path.home() / ".catfish" / "skills"


def _session_has_saved_skill(session_dir: Path, skills_root: Path) -> bool:
    """查 session_dir/skill_draft/<ns>/<name>/ 是否至少有 1 个真"保存" 到 skills_root.

    "保存" 的判定: skills_root/<ns>/<name>/SKILL.md 真存在.
    save_skill RPC 用 shutil.copytree 复制整个 draft 到 skills_root, SKILL.md
    是 write_skill_files 一定写的 file, 用它判断 skill 真在.
    """
    draft_root = session_dir / "skill_draft"
    if not draft_root.exists():
        return False
    # session_dir/skill_draft/<ns>/<name>/SKILL.md (2 层 glob)
    for skill_md in draft_root.glob("*/*/SKILL.md"):
        # skill_md.parent.parent = <ns>, skill_md.parent = <name>
        ns = skill_md.parent.parent.name
        name = skill_md.parent.name
        if (skills_root / ns / name / "SKILL.md").exists():
            return True
    return False


def cleanup_consumed_raw(
    session_dir: Path,
    skills_root: Path | None = None,
    dry_run: bool = False,
) -> dict:
    """删 session 的训练原料 (events.jsonl + screenshots/ + transcripts.jsonl).

    6/2 BL-RECMODE-AUTO-CLEAN-RAW: save_skill RPC 完成后自动调一次. skill 真
    "保存" 后, 原料是历史冗余, 不属于"员工成果" 范畴, 清掉省员工硬盘.

    保留 (员工成果):
    - meta.json (session 元数据, KB 级, 给 Dashboard 列录屏用)
    - skill_draft/ (没保存的草稿可能还在, 员工后续可能补存)
    - .keep_forever (员工显式标记 — 整 session 跳过任何清理)

    删 (训练原料, 已消费):
    - events.jsonl (CDP 操作流, ~50-500 KB)
    - screenshots/ (截图 PNG, 大头 5-50 MB)
    - transcripts.jsonl (语音转写, KB 级)

    Args:
        session_dir: ~/.catfish/recordings/<sid>/
        skills_root: ~/.catfish/skills/ (默认 home, 单测可传 tmp 路径)
        dry_run: 只看不删

    Returns:
        {ok: bool, has_saved_skill: bool, removed_files: [...],
         freed_bytes: int, error: str | None}

    Safety:
    - session 没真"保存" skill (skills_root/<ns>/<name>/ 都不在) → 不删, ok=False
    - .keep_forever 标了 → 跳过, ok=False
    - 文件不存在 → 跳过 (不算错)
    - 路径必须在 session_dir 范围内 (防 symlink/.. 越界)
    """
    result: dict = {
        "ok": False,
        "has_saved_skill": False,
        "removed_files": [],
        "freed_bytes": 0,
        "error": None,
    }

    if not session_dir.exists() or not session_dir.is_dir():
        result["error"] = f"session_dir 不存在或不是目录: {session_dir}"
        return result

    sr = (skills_root or _skills_root()).resolve()
    sd = session_dir.resolve()

    # opt-out: 员工显式标了 .keep_forever 整 session 都跳
    if _is_kept_forever(sd):
        result["error"] = "session 标了 .keep_forever, 跳过清原料"
        logger.info("cleanup_consumed_raw 跳过 %s (.keep_forever)", sd.name)
        return result

    has_saved = _session_has_saved_skill(sd, sr)
    result["has_saved_skill"] = has_saved
    if not has_saved:
        result["error"] = "session 没任何 skill 真保存到 skills_root, 不清原料"
        logger.debug("cleanup_consumed_raw 跳过 %s (no saved skill)", sd.name)
        return result

    # 真删原料 (3 个 target)
    freed = 0
    removed: list[str] = []
    for target in _CONSUMABLE_RAW_TARGETS:
        p = sd / target
        if not p.exists():
            continue
        # 路径越界保险 (防 symlink 跳出 sd) — resolve 后必须仍在 sd 子树
        try:
            if not str(p.resolve()).startswith(str(sd)):
                logger.warning("cleanup_consumed_raw: %s resolve 越界 %s, 跳", target, sd)
                continue
        except OSError:
            continue
        # 算大小
        size = 0
        if p.is_dir():
            try:
                for f in p.rglob("*"):
                    if f.is_file():
                        try:
                            size += f.stat().st_size
                        except OSError:
                            pass
            except OSError:
                pass
        else:
            try:
                size = p.stat().st_size
            except OSError:
                pass
        if dry_run:
            removed.append(str(p))
            freed += size
            continue
        try:
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
            removed.append(str(p))
            freed += size
        except OSError as e:
            logger.warning("cleanup_consumed_raw: 删 %s 失败 (不致命): %s", p, e)

    result["ok"] = True
    result["removed_files"] = removed
    result["freed_bytes"] = freed
    logger.info(
        "cleanup_consumed_raw %s%s: skill 已保存, 删 %d 个原料文件/目录, 释放 %.1f MB",
        sd.name, " [dry_run]" if dry_run else "",
        len(removed), freed / 1024 / 1024,
    )
    return result


def list_recordings_with_meta() -> list[dict]:
    """5/25 BL-RECMODE-NO-AUTO-DELETE: 给 Dashboard "我的录屏" 区用的 inventory.

    返每个 session 的: id, started_at, size_bytes, kept_forever, skill_drafts.
    员工自己看 + 决定何时清.
    """
    root = _recordings_root()
    if not root.exists():
        return []

    out: list[dict] = []
    for sd in sorted(root.iterdir(), reverse=True):  # 新的在前
        if not sd.is_dir():
            continue

        started_at = 0.0
        meta_path = sd / "meta.json"
        if meta_path.exists():
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
                started_at = float(data.get("started_at") or 0)
            except (OSError, json.JSONDecodeError):
                pass
        if started_at <= 0:
            try:
                started_at = sd.stat().st_mtime
            except OSError:
                started_at = 0.0

        size = 0
        try:
            for f in sd.rglob("*"):
                if f.is_file():
                    try:
                        size += f.stat().st_size
                    except OSError:
                        pass
        except OSError:
            pass

        # 罗列 skill_drafts (帮员工识别"这录屏当初学的啥 skill")
        skill_drafts = []
        for draft in sd.glob("skill_draft/*/*/SKILL.md"):
            ns_name = "/".join(draft.parent.relative_to(sd / "skill_draft").parts)
            skill_drafts.append(ns_name)

        out.append({
            "session_id": sd.name,
            "started_at": started_at,
            "size_bytes": size,
            "kept_forever": _is_kept_forever(sd),
            "skill_drafts": skill_drafts,
            "path": str(sd),
        })
    return out


__all__ = [
    "cleanup_old_recordings",
    "list_recordings_with_meta",
    "DEFAULT_TTL_SECONDS",
]
