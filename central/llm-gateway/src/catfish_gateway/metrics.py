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


def _use_pg() -> bool:
    """有 CATFISH_DB_URL → PG. CATFISH_AUDIT_PATH 仍优先走 jsonl (单测 / 客户特意要 jsonl)."""
    if os.environ.get("CATFISH_AUDIT_PATH"):
        return False
    return bool(os.environ.get("CATFISH_DB_URL", "").strip())


def _pg_conn():
    """psycopg sync 连接, 一次性. 五一 sprint 5/2 收尾加."""
    import psycopg  # 懒 import
    return psycopg.connect(os.environ["CATFISH_DB_URL"])


def _persist_record_pg(record: dict) -> bool:
    """PG 写一行到 gateway_audit 表. 返 True 成功. 失败 caller fallback jsonl."""
    try:
        with _pg_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO gateway_audit
                       (ts_ms, user_email, model, tokens_in, tokens_out, tokens_total,
                        latency_ms, ttft_ms, status, error_code, error_msg,
                        auth_method, security_concerns, extra)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)""",
                    (
                        int(record.get("ts", time.time())) * 1000,
                        record.get("user", ""),
                        record.get("model", ""),
                        int(record.get("prompt_tokens", 0)),
                        int(record.get("completion_tokens", 0)),
                        int(record.get("total_tokens", 0)),
                        int(round(record.get("latency_ms", 0))),
                        int(record["ttft_ms"]) if record.get("ttft_ms") is not None else None,
                        record.get("status", "ok"),
                        "",  # error_code 暂不拆 (老 jsonl 没拆)
                        record.get("error", "")[:500],
                        "unknown",  # auth_method 暂不传 (caller 还没传过来, 后续拓展)
                        json.dumps(
                            [record["security_concern"]] if record.get("security_concern") else []
                        ),
                        json.dumps({k: v for k, v in record.items() if k not in {
                            "ts", "type", "user", "model",
                            "prompt_tokens", "completion_tokens", "total_tokens",
                            "latency_ms", "ttft_ms", "status", "error", "security_concern",
                        }}),
                    ),
                )
            conn.commit()
        return True
    except Exception as e:
        logger.warning("metrics: PG 写 gateway_audit 失败: %s", e)
        return False


def _persist_record_jsonl(record: dict) -> None:
    """老 jsonl 路径 (sqlite/dev fallback). 失败永远不抛.

    BL-HERMES013-4 (5/12): atomic 写 — write + flush + fsync.
    没 fsync 时 OS page cache 可能残留 buffer (跑机器 SIGKILL 或断电会丢最后几条
    audit). 加 fsync 让每条 audit 真落盘 (代价: ~1ms per write, 跟 LLM 调
    几秒的延迟比可忽略). PG 主路径已是 commit() 走 ACID, 不动.
    """
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
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    # 一些 FS (例 NFS / tmpfs) 不支持 fsync, 不抛
                    pass
    except OSError as e:
        logger.warning("metrics: 写 %s 失败: %s", path, e)


def _persist_record(record: dict) -> None:
    """五一 sprint 5/2 收尾: PG 主, jsonl 兜底.

    PG 配置了走 PG; PG 失败 fallback jsonl 不丢数据; 没 PG 走 jsonl 老路径.
    """
    if _use_pg():
        if _persist_record_pg(record):
            return
        # PG 失败 — 兜到 jsonl 别丢
        logger.warning("metrics: PG 失败, fallback jsonl")
    _persist_record_jsonl(record)


def log_request_metadata(
    *,
    user: str,
    model: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    latency_ms: float = 0.0,
    ttft_ms: float | None = None,
    status: str = "ok",
    error: str = "",
    security_concern: str | None = None,
    # BL-CACHE-AUDIT (5/17): Anthropic prompt cache metrics. 默认 0 兼容
    # 没拿到 cache_* 字段的 provider (OpenAI/DeepSeek/Gemini).
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
    # BL-RBAC-DAY4-HARDENING (5/17, hermes 0.14 #23194 ctx.llm 防御):
    # X-Catfish-Source header. 'companion' / 'plugin:<name>' / 'unknown' / 'cron'
    source: str = "unknown",
) -> None:
    """Emit a single structured log line + persist 到 JSONL.

    Includes ONLY:
      - who (user id)
      - what model (实际 fallback 后用的)
      - token counts
      - latency_ms (总耗时, 含 fallback 等待 + 流式所有 chunks)
      - ttft_ms (time-to-first-token, streaming 才有, 看上游慢不慢)
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
    if ttft_ms is not None:
        # 区分上游慢 (TTFT 长) vs 输出长 (latency 长 但 TTFT 正常). 关键运维信号.
        record["ttft_ms"] = round(ttft_ms, 1)
    if error:
        # Truncate to avoid accidentally leaking upstream prompt echoes in errors.
        # BL-HERMES013-1 (5/11): scrub credential patterns 在 truncate 前一道,
        # 修上游 LLM 回显 prompt 片段含 'password=xxx' 等模式时漏到 audit log.
        from .prompt_security import scrub_credentials_in_text  # noqa: PLC0415
        record["error"] = scrub_credentials_in_text(error)[:200]
    if security_concern:
        # 标记字段, 例 'prompt_credential_detected'. 不含真密码值, 只标记类型.
        record["security_concern"] = security_concern[:100]

    # BL-CACHE-AUDIT (5/17): 只有 Anthropic 系 model 才返这俩字段, 0 时不记
    # 让 JSON 短一点, 客户 IT audit 看 cache hit 比例时 grep 这字段即可.
    if cache_creation_tokens or cache_read_tokens:
        record["cache_creation_tokens"] = cache_creation_tokens
        record["cache_read_tokens"] = cache_read_tokens

    # BL-RBAC-DAY4-HARDENING (5/17): X-Catfish-Source header audit.
    # 默认 'unknown' 不写字段减少噪音, 显式标的 (companion / plugin:xxx) 才记.
    if source and source != "unknown":
        record["source"] = source[:50]

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
    dept_filter: str | None = None,  # BL-ADMIN-AUDIT (5/12) 按部门过滤
    limit: int = 1000,
    offset: int = 0,  # BL-ADMIN-AUDIT (5/12) 分页用
) -> list[dict]:
    """读 audit log, 给上层 (admin 后台 / 客户 IT 自审 / billing) 用.

    五一 sprint 5/2 收尾: PG 配了从 PG 读 (索引快 100x), 否则 jsonl 老路径.
    BL-ADMIN-AUDIT (5/12 鸿波): 加 dept_filter + offset 给 /admin/quota/events 分页.

    Args:
        since_unix: 只要 ts >= 这个 unix 秒的事件. None = 所有
        user_filter: 只看某个 user id 的事件. None = 所有
        model_filter: 只看某个 model 的事件
        status_filter: 只看 status='ok' 或 'error' 等. None = 所有
        dept_filter: 只看某个 department 的事件
        limit: 最多返回多少条 (从最新算起).
        offset: 跳过前 N 条 (分页用)

    Returns:
        事件 dict 列表, 按时间倒序 (最新在前).

    永远不抛. 读失败返空列表.
    """
    if _use_pg():
        return _read_events_pg(
            since_unix=since_unix,
            user_filter=user_filter,
            model_filter=model_filter,
            status_filter=status_filter,
            dept_filter=dept_filter,
            limit=limit,
            offset=offset,
        )

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
    skipped = 0
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
        if dept_filter and event.get("department") != dept_filter:
            continue
        if skipped < offset:
            skipped += 1
            continue
        out.append(event)
    return out


def _read_events_pg(
    *,
    since_unix: int | None,
    user_filter: str | None,
    model_filter: str | None,
    status_filter: str | None,
    dept_filter: str | None = None,
    limit: int,
    offset: int = 0,
) -> list[dict]:
    """PG 路径 — 走索引, where + order by + limit 都在 DB 侧, 比 jsonl 快很多."""
    where = []
    params: list = []
    if since_unix is not None:
        where.append("ts_ms >= %s")
        params.append(since_unix * 1000)
    if user_filter:
        where.append("user_email = %s")
        params.append(user_filter)
    if model_filter:
        where.append("model = %s")
        params.append(model_filter)
    if status_filter:
        where.append("status = %s")
        params.append(status_filter)
    if dept_filter:
        where.append("department = %s")
        params.append(dept_filter)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    sql = (
        f"SELECT ts_ms, user_email, department, model, tokens_in, tokens_out, "
        f"       tokens_total, latency_ms, ttft_ms, status, error_msg, "
        f"       security_concerns, extra "
        f"FROM gateway_audit {where_sql} "
        f"ORDER BY ts_ms DESC LIMIT %s OFFSET %s"
    )
    params.append(limit)
    params.append(max(0, int(offset)))

    try:
        with _pg_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
    except Exception as e:
        logger.warning("metrics: PG 读 gateway_audit 失败: %s", e)
        return []

    out = []
    for row in rows:
        ts_ms, user, dept, model, tin, tout, ttot, lat, ttft, status, err, concerns, extra = row
        # 还原成 jsonl 老格式 (跟 _persist_record_jsonl 写的一致), caller 不知道 backend 切了
        ev: dict = {
            "ts": int(ts_ms / 1000),
            "type": "llm_request",
            "user": user,
            "department": dept or "",  # BL-ADMIN-AUDIT (5/12) 暴露 department 给 admin UI
            "model": model,
            "prompt_tokens": tin,
            "completion_tokens": tout,
            "total_tokens": ttot,
            "latency_ms": float(lat),
            "status": status,
        }
        if ttft is not None:
            ev["ttft_ms"] = float(ttft)
        if err:
            ev["error"] = err
        # PG 里 concerns 是 jsonb 数组; jsonl 老格式只存第一个 (字符串). 取第一保兼容.
        if concerns:
            try:
                arr = concerns if isinstance(concerns, list) else json.loads(concerns)
                if arr:
                    ev["security_concern"] = arr[0]
            except Exception:
                pass
        if extra:
            try:
                e_dict = extra if isinstance(extra, dict) else json.loads(extra)
                ev.update({k: v for k, v in e_dict.items() if k not in ev})
            except Exception:
                pass
        out.append(ev)
    return out


def count_events(
    *,
    since_unix: int | None = None,
    user_filter: str | None = None,
    model_filter: str | None = None,
    status_filter: str | None = None,
    dept_filter: str | None = None,
) -> int:
    """BL-ADMIN-AUDIT (5/12) — 跟 read_events 同筛选, 返总数 (分页 total).

    PG 走 SELECT COUNT(*) (走索引快); jsonl 路径退化为读全表数.
    永远不抛, 失败返 0.
    """
    if _use_pg():
        where = []
        params: list = []
        if since_unix is not None:
            where.append("ts_ms >= %s")
            params.append(since_unix * 1000)
        if user_filter:
            where.append("user_email = %s")
            params.append(user_filter)
        if model_filter:
            where.append("model = %s")
            params.append(model_filter)
        if status_filter:
            where.append("status = %s")
            params.append(status_filter)
        if dept_filter:
            where.append("department = %s")
            params.append(dept_filter)
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        try:
            with _pg_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(f"SELECT COUNT(*) FROM gateway_audit {where_sql}", params)
                    row = cur.fetchone()
                    return int(row[0]) if row else 0
        except Exception as e:
            logger.warning("metrics: PG count_events 失败: %s", e)
            return 0

    # jsonl fallback
    path = audit_path()
    if not path.is_file():
        return 0
    try:
        with path.open("r", encoding="utf-8") as f:
            count = 0
            for line in f:
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
                if dept_filter and event.get("department") != dept_filter:
                    continue
                count += 1
        return count
    except OSError as e:
        logger.warning("metrics: count jsonl 失败: %s", e)
        return 0
