"""BL-HERMES013-4 (5/12 鸿波拍板) — gateway atomic in-flight stream tracking.

# 真问题

gateway 在跑 SSE chat completion 时崩 (SIGKILL / OOM / 断电), Python 没机会跑
finally 块. 客户端那边 SSE 断了, 不知道流到哪一步. 重发 messages → LLM 重答 →
浪费 tokens + 用户感觉副手"忘事".

# 解法 (轻量级 — 不强求 client 续传)

1. **stream 开始**: 写 `~/.catfish/inflight_streams/<request_id>.json` (一个 JSON
   文件, 含 user/model/started_at/message_count). 同步 fsync 落盘.
2. **stream 完成**: output_transforms chain 跑 `InflightCleanupTransform.run()`
   时 unlink 这个文件.
3. **gateway 重启**: lifespan startup 扫 inflight_streams/ 目录, 残留文件:
     - 写一条 audit `{"status": "interrupted_resumed", "request_id": ...}`
     - unlink 该文件
   这样**残留即痕迹**: 哪怕 gateway 崩了, audit 表里也能看出"5/12 14:32 alice
   的 LLM 调用 X 没流完". Companion 之后可以读这个 audit 给员工提示重发.

# 跟 catfish-private-vision / catfish-private-main 哪些 path 接

只接最外层 `_stream_chat_completion` (那是 client SSE 入口, 真崩这里). a2a /
expert_consult / vision 各自的 LLM 调用走 LiteLLM 直接 await, 不是 SSE 给 client,
崩了客户端会拿到 HTTP 5xx 或者 transport error, 自己有重试逻辑, 不需要 inflight 跟踪.

# 不变量

- 文件格式: JSON 不是 jsonl (一个 file 对一个 stream, 重启扫目录看残留)
- 命名: `<request_id>.json`, request_id 用 uuid.uuid4().hex (短, 文件名安全)
- 失败静默: 写盘失败不阻塞主流程 (跟 audit hook 一致)
- 路径走 CATFISH_HOME 联动 (跟 employee_journal / a2a_notifications 一致, 多 agent demo)
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("catfish.gateway.inflight_streams")


def _inflight_dir() -> Path:
    """优先 CATFISH_HOME/inflight_streams/, fallback ~/.catfish/inflight_streams/."""
    catfish_home = os.environ.get("CATFISH_HOME", "").strip()
    if catfish_home:
        return Path(catfish_home).expanduser() / "inflight_streams"
    return Path.home() / ".catfish" / "inflight_streams"


def _inflight_path(request_id: str) -> Path:
    return _inflight_dir() / f"{request_id}.json"


def mark_started(
    request_id: str,
    *,
    user: str = "",
    model: str = "",
    message_count: int = 0,
    extra: dict[str, Any] | None = None,
) -> bool:
    """stream 开始时调. 写 inflight 文件 + fsync.

    返 True 写成功. 失败静默不抛 (主流程 LLM 调用必须继续).
    """
    if not request_id:
        return False
    record = {
        "request_id": request_id,
        "user": user,
        "model": model,
        "message_count": message_count,
        "started_at": time.time(),
        "started_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()),
    }
    if extra:
        record.update(extra)
    path = _inflight_path(request_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # atomic write: tmp + rename + fsync
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        tmp.replace(path)  # atomic rename on POSIX
        return True
    except OSError as e:
        logger.warning("BL-HERMES013-4: inflight mark_started 失败 %s: %s", path, e)
        return False


def mark_finished(request_id: str) -> bool:
    """stream 完成时调 (output_transforms chain 通过 InflightCleanupTransform 调).

    unlink inflight 文件. 失败静默 (重启 startup 扫到也只是写一条 'interrupted'
    audit, 不影响功能).
    """
    if not request_id:
        return False
    path = _inflight_path(request_id)
    try:
        path.unlink(missing_ok=True)
        return True
    except OSError as e:
        logger.warning("BL-HERMES013-4: inflight mark_finished 失败 %s: %s", path, e)
        return False


def list_inflight() -> list[dict[str, Any]]:
    """gateway lifespan startup 扫: 列出所有残留 inflight 文件 (= gateway 崩前
    没流完的 stream). 返 list, 不修改 / unlink. 调用方决定怎么处理."""
    d = _inflight_dir()
    if not d.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        for p in d.iterdir():
            if not p.is_file() or p.suffix != ".json":
                continue
            try:
                with p.open(encoding="utf-8") as f:
                    record = json.load(f)
                record["_path"] = str(p)
                out.append(record)
            except (OSError, json.JSONDecodeError) as e:
                logger.warning("BL-HERMES013-4: 读 %s 失败: %s", p, e)
    except OSError as e:
        logger.warning("BL-HERMES013-4: 扫 %s 失败: %s", d, e)
    return out


def reap_interrupted(audit_writer: Any | None = None) -> int:
    """gateway 启动时调. 扫 inflight 目录, 对每个残留:
      - audit_writer({...}) 写一条 audit (调用方传, 默认走 metrics.log_request_metadata)
      - unlink 文件

    返清理的数量.
    """
    items = list_inflight()
    if not items:
        return 0
    if audit_writer is None:
        # 默认 audit writer
        from . import metrics as _metrics  # noqa: PLC0415

        def audit_writer(record: dict[str, Any]) -> None:
            _metrics.log_request_metadata(
                user=record.get("user", ""),
                model=record.get("model", ""),
                prompt_tokens=0,
                completion_tokens=0,
                latency_ms=0.0,
                ttft_ms=None,
                status="interrupted_resumed",
                error=(
                    f"BL-HERMES013-4: gateway 崩前 stream 没流完 "
                    f"(request_id={record.get('request_id', '?')}, "
                    f"started_at={record.get('started_iso', '?')})"
                ),
                security_concern="",
            )

    cleaned = 0
    for item in items:
        try:
            audit_writer(item)
        except Exception as e:  # noqa: BLE001
            logger.warning("BL-HERMES013-4: reap audit 写失败 (静默): %s", e)
        # 不管 audit 写成功没成功, 都 unlink (防文件无限累积)
        path_str = item.get("_path")
        if path_str:
            try:
                Path(path_str).unlink(missing_ok=True)
                cleaned += 1
            except OSError:
                pass
    if cleaned:
        logger.info(
            "BL-HERMES013-4: gateway 启动 reap 清掉 %d 个 interrupted in-flight stream",
            cleaned,
        )
    return cleaned


__all__ = [
    "mark_started",
    "mark_finished",
    "list_inflight",
    "reap_interrupted",
]
