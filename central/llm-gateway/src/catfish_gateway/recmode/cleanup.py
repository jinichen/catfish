"""BL-LEARN-RECMODE / 录屏清理 utility (5/15 V2 #67 ship, 5/25 重定位).

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

# Returns

`cleanup_old_recordings()` 返 dict {scanned, deleted, kept_forever, still_fresh,
errors, freed_bytes, deleted_session_ids} 让 caller (Dashboard / CLI) 打 summary.
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
