"""BL-HERMES013-4 (5/12 鸿波拍板) + BL-INFLIGHT-MEM (5/26 SaaS 化简) —
gateway in-flight stream tracking. **in-memory**.

# 真问题

gateway 在跑 SSE chat completion 时:
  1. **正常完成 / 客户端 abort**: 走 finally / cancel 标记 stream 结束
  2. **gateway 进程崩 (SIGKILL / OOM / 断电)**: Python 没机会跑 finally

老逻辑 (5/12 BL-HERMES013-4) 用 fs 文件存 `~/.catfish/inflight_streams/<req>.json`,  # noqa: BOUNDARY
gateway 重启 scan 残留 → 写 audit "interrupted_resumed" → unlink. fs 跨进程持
久化, 进程死了文件还在.

# 5/26 SaaS 化简 (BL-INFLIGHT-MEM)

BL-CENTRAL-EDGE-BOUNDARY: gateway 跑客户机房, 写不到员工本机 fs (~/.catfish/).  # noqa: BOUNDARY
fs 路径整体砍, 改 in-memory dict.

**牺牲了什么**:
  - 进程崩了 in-memory dict 一起死, 没法在重启时 audit "interrupted_resumed"
  - SaaS K8s 多 pod 时单 instance 状态不跨 pod (abort 打错 pod → 404)

**为什么可接受**:
  - 主用例 (员工 abort + 正常完成) 都在同一进程内, in-memory dict 工作正常
  - "崩了重启 audit" 是 nice-to-have, 不是 P0 需求 (员工看不到这条 audit,
    只有 ops 翻日志能见)
  - 多 pod 跨实例 abort 由后续 BL-INFLIGHT-REDIS 上 Redis pub/sub (Q4 K8s 阶段做)

# API 兼容

公共 API 不变 (mark_started / mark_finished / list_inflight / reap_interrupted),
调用方代码不动. reap_interrupted 永远返 0 (重启后 dict 已空).
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

logger = logging.getLogger("catfish.gateway.inflight_streams")


# 进程内 inflight 表 — 跨 thread 用 lock 防 race.
# 单 gateway instance 唯一状态, 重启清零.
_lock = threading.Lock()
_inflight: dict[str, dict[str, Any]] = {}


def mark_started(
    request_id: str,
    *,
    user: str = "",
    model: str = "",
    message_count: int = 0,
    extra: dict[str, Any] | None = None,
) -> bool:
    """stream 开始时调. 进 in-memory dict. 失败静默 (主流程 LLM 调用必须继续)."""
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
    with _lock:
        _inflight[request_id] = record
    return True


def mark_finished(request_id: str) -> bool:
    """stream 完成时调 (output_transforms chain 通过 InflightCleanupTransform 调).

    从 dict 移除. 不存在不报错 (mark_started 失败过 / 已被别处清掉).
    """
    if not request_id:
        return False
    with _lock:
        _inflight.pop(request_id, None)
    return True


def mark_aborted(request_id: str, *, reason: str = "client_disconnect") -> bool:
    """BL-ABORT-PROPAGATE (7/23 达华 POC): client 断开触发 gateway 主动关 upstream 时调.

    行为跟 mark_finished 一致 (从 dict 移除) · 但**打 warn log** 明确标记 · 便于 ops
    统计客户端 abort 频率 (若高 · 说明客户体验差 / 客户端 timeout 太短).

    reason 目前 2 种:
      - client_disconnect  · request.is_disconnected() 返 True (TCP 断)
      - cancelled          · asyncio.CancelledError 被 catch (fastapi 检测客户端断)
    """
    if not request_id:
        return False
    with _lock:
        rec = _inflight.pop(request_id, None)
    if rec:
        duration = time.time() - rec.get("started_at", time.time())
        logger.warning(
            "[abort] request_id=%s user=%s model=%s duration=%.1fs reason=%s",
            request_id,
            rec.get("user", "?"),
            rec.get("model", "?"),
            duration,
            reason,
        )
    return True


def list_inflight() -> list[dict[str, Any]]:
    """列当前 in-flight stream (cancel UI / ops 调试用). 返 snapshot list."""
    with _lock:
        return list(_inflight.values())


def reap_interrupted(audit_writer: Any | None = None) -> int:
    """gateway 启动时调.

    5/26 后改 in-memory dict, 重启自动清零, 这函数永远返 0. 留 API 防回归
    (有 caller 在 app.py lifespan startup 调). audit_writer 参数保留但不再用.

    早 (fs 时代): 重启扫 fs 残留 → audit interrupted_resumed + unlink. 现在
    in-memory 死了就是死了, 失去这条 audit. ops 翻日志看 LLM 调用未完成走
    upstream timeout error 路径 (有 audit), 不是这条专用 audit.
    """
    _ = audit_writer  # silence linter
    return 0


def clear() -> None:
    """单测用: 清空内部 dict."""
    with _lock:
        _inflight.clear()


__all__ = [
    "mark_started",
    "mark_finished",
    "mark_aborted",
    "list_inflight",
    "reap_interrupted",
    "clear",
]
