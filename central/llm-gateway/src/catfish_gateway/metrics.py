"""Metadata-only logging + JSONL persistence.

# 边界 (写进合同的承诺, 别越界)
==================================
**NEVER** log:
  - prompt content
  - completion content
  - tool call arguments (即使是工具名也不记 args)
  - raw headers
  - 任何能反推回员工对话内容的字段

**ONLY** log:
  - 谁 (user id)
  - 什么模型 (实际 fallback 后用的 model)
  - token 数 (input / output / total)
  - 延迟 (latency_ms)
  - 状态 (ok / error) + 错误码 (短文本, 截 200 字)

跟边缘 tool-bridge 的 audit.jsonl 严格分开:
  - gateway audit (本模块): 中央侧, 只 metadata, 写 ~/.catfish/gateway_audit.jsonl,
    Phase 2 客户合同里"中央只看用量"承诺的具体数据源, 提供给客户 IT 自审
  - tool-bridge audit (catfish_tool_bridge.audit): 边缘侧, 含 args_preview (员工本机,
    不外发), 给 Skill lifecycle 健康面板用

# 持久化路径
==========
默认 ~/.catfish/gateway_audit.jsonl (开发本机 / 私有部署 default)
生产可配 CATFISH_AUDIT_PATH env var (例: /var/log/catfish/gateway_audit.jsonl)

# 写失败永远不抛
==============
gateway 高频写 audit, 任何 OSError (磁盘满 / 权限错 / 路径不存在) 都不能影响
LLM 请求主流程. 失败 log warning 让 ops 看到, 不抛.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

logger = logging.getLogger("catfish.metrics")

#: 写文件用的锁. uvicorn 多 worker / asyncio 并发调时防 partial line.
_write_lock = threading.Lock()

#: audit 文件路径缓存 (lazy resolve).
_audit_path: Path | None = None


def audit_path() -> Path:
    """audit 文件路径, lazy resolve.

    优先级:
      1. CATFISH_AUDIT_PATH env var (生产部署用)
      2. ~/.catfish/gateway_audit.jsonl (默认)
    """
    global _audit_path
    if _audit_path is None:
        env = os.environ.get("CATFISH_AUDIT_PATH")
        if env:
            _audit_path = Path(env).expanduser()
        else:
            home = os.environ.get("HOME") or os.environ.get("USERPROFILE") or "."
            _audit_path = Path(home) / ".catfish" / "gateway_audit.jsonl"
    return _audit_path


def _set_audit_path(path: Path | None) -> None:
    """测试用 — 改 audit 路径或重置成 None 让 lazy 重 resolve."""
    global _audit_path
    _audit_path = path


def _persist_record(record: dict) -> None:
    """写一行 JSONL 到 audit 文件. 失败永远不抛."""
    try:
        line = json.dumps(record, ensure_ascii=False) + "\n"
    except Exception:  # noqa: BLE001
        logger.warning("metrics: 序列化 audit record 失败")
        return

    path = audit_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _write_lock:
            with path.open("a", encoding="utf-8") as f:
                f.write(line)
    except OSError as e:
        # 磁盘满 / 权限错 / 路径不存在 — 不影响 LLM 请求主流程
        logger.warning("metrics: 写 %s 失败: %s", path, e)


def log_request_metadata(
    *,
    user: str,
    model: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    latency_ms: float = 0.0,
    status: str = "ok",
    error: str = "",
    security_concern: str | None = None,
) -> None:
    """Emit a single structured log line + persist 到 JSONL.

    Includes ONLY:
      - who (user id)
      - what model (实际 fallback 后用的)
      - token counts
      - latency
      - status / error code
      - security_concern (例: 'prompt_credential_detected', 防员工 IT 漏审计)

    Explicitly EXCLUDES:
      - prompt content
      - completion content
      - tool call arguments
      - raw headers
      - 任何凭据真值 (security_concern 只是标记字符串, 不含真密码)
    """
    record = {
        "ts": int(time.time()),
        "type": "llm_request",
        "user": user,
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "latency_ms": round(latency_ms, 1),
        "status": status,
    }
    if error:
        # Truncate to avoid accidentally leaking upstream prompt echoes in errors
        record["error"] = error[:200]
    if security_concern:
        # 标记字段, 例 'prompt_credential_detected'. 不含真密码值, 只标记类型.
        record["security_concern"] = security_concern[:100]

    # 1. stderr log (实时可见, 给 ops 看)
    logger.info(json.dumps(record, ensure_ascii=False))

    # 2. 持久化到 JSONL (给客户 IT 审计 + 长期分析)
    _persist_record(record)


def read_events(
    *,
    since_unix: int | None = None,
    user_filter: str | None = None,
    model_filter: str | None = None,
    status_filter: str | None = None,
    limit: int = 1000,
) -> list[dict]:
    """读 audit log, 给上层 (admin 后台 / 客户 IT 自审 / billing) 用.

    Args:
        since_unix: 只要 ts >= 这个 unix 秒的事件. None = 所有
        user_filter: 只看某个 user id 的事件. None = 所有
        model_filter: 只看某个 model 的事件
        status_filter: 只看 status='ok' 或 'error' 等. None = 所有
        limit: 最多返回多少条 (从最新算起).

    Returns:
        事件 dict 列表, 按时间倒序 (最新在前).

    永远不抛. 读失败返空列表.
    """
    path = audit_path()
    if not path.is_file():
        return []

    try:
        with path.open("r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError as e:
        logger.warning("metrics: 读 %s 失败: %s", path, e)
        return []

    out: list[dict] = []
    for line in reversed(lines):
        if len(out) >= limit:
            break
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if since_unix is not None and event.get("ts", 0) < since_unix:
            continue
        if user_filter and event.get("user") != user_filter:
            continue
        if model_filter and event.get("model") != model_filter:
            continue
        if status_filter and event.get("status") != status_filter:
            continue
        out.append(event)
    return out
