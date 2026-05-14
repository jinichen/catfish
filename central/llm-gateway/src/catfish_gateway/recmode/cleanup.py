"""BL-LEARN-RECMODE / 隐私 cleanup (5/15 V2 #67).

14 天自动删 ~/.catfish/recordings/<sid>/ 旧 session — 截图含业务数据
(EIS 资质名 / 维护人 / 公司名), 不能永久留. 跟 macOS '最近文件'  / iCloud
14 天回收同模式.

opt-in '保留':
- skill 文件夹 recmode_meta.json 含 "_keep_forever": true → 跳过 cleanup
- 用户在 PreviewModal 勾"保留作 ground truth" 触发 (V2 #67 同时加 UI)

清理触发:
1. gateway 启动时 reap 一次 (lifespan startup hook 同 BL-HERMES013-4)
2. 后台 task 每 24h 跑一次 (gateway 长跑场景)

返清了几个 + 跳了几个 (透明给 admin 看).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from pathlib import Path

logger = logging.getLogger("catfish.recmode.cleanup")

# 14 天 = 1209600 秒. 跟 macOS / iCloud 回收周期对齐.
DEFAULT_TTL_SECONDS = 14 * 24 * 3600

# 后台 task 跑频率 (24h 一次, 跟 day rotation 对齐)
DAEMON_INTERVAL_SECONDS = 24 * 3600


def _recordings_root() -> Path:
    """跟 cdp_listener._tasks_jsonl_path 同根 (CATFISH_HOME 优先)"""
    catfish_home = os.environ.get("CATFISH_HOME", "").strip()
    if catfish_home:
        return Path(catfish_home).expanduser() / "recordings"
    return Path.home() / ".catfish" / "recordings"


def _is_kept_forever(session_dir: Path) -> bool:
    """看 session_dir 内任意 skill_draft/<ns>/<name>/recmode_meta.json 有没标 _keep_forever.

    用户 PreviewModal 勾 '保留作 ground truth' 时, save_skill 写到 recmode_meta.json.
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
    """扫 ~/.catfish/recordings/<sid>/, 删超 TTL 的 session.

    Args:
        ttl_seconds: 默认 14 天
        dry_run: 只看不删 (admin audit 用)

    Returns:
        {"scanned": N, "deleted": M, "kept_forever": K, "still_fresh": F,
         "errors": [...], "freed_bytes": Y}
    """
    root = _recordings_root()
    if not root.exists():
        return {"scanned": 0, "deleted": 0, "kept_forever": 0, "still_fresh": 0, "errors": [], "freed_bytes": 0}

    cutoff = time.time() - ttl_seconds
    stats = {
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

        # opt-in 保留
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


async def cleanup_daemon():
    """gateway 长跑场景 — 后台 task 每 24h 跑一次 cleanup.

    跟 BL-HERMES013-4 reap_interrupted 同模式 (gateway lifespan startup
    spawn asyncio.create_task).
    """
    while True:
        try:
            stats = cleanup_old_recordings()
            if stats["deleted"] > 0 or stats["scanned"] > 0:
                logger.info(
                    "RecMode cleanup daemon: scanned=%d deleted=%d kept_forever=%d "
                    "still_fresh=%d freed=%.1f MB",
                    stats["scanned"], stats["deleted"], stats["kept_forever"],
                    stats["still_fresh"], stats["freed_bytes"] / 1024 / 1024,
                )
        except Exception:  # noqa: BLE001
            logger.exception("RecMode cleanup daemon 失败 (不致命, 24h 后再试)")
        await asyncio.sleep(DAEMON_INTERVAL_SECONDS)


__all__ = [
    "cleanup_old_recordings",
    "cleanup_daemon",
    "DEFAULT_TTL_SECONDS",
]
