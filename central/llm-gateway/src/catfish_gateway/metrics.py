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
  - gateway audit (本模块): 中央侧, 只 metadata, 写 ~/.catfish/gateway_audit.jsonl,  # noqa: BOUNDARY (docstring 描述路径, 真代码走 CATFISH_AUDIT_PATH env 或 PG)
    Phase 2 客户合同里"中央只看用量"承诺的具体数据源, 提供给客户 IT 自审
  - tool-bridge audit (catfish_tool_bridge.audit): 边缘侧, 含 args_preview (员工本机,
    不外发), 给 Skill lifecycle 健康面板用

# 持久化路径
==========
默认 ~/.catfish/gateway_audit.jsonl (开发本机 / 私有部署 default)  # noqa: BOUNDARY (docstring 描述默认 path)
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
      2. ~/.catfish/gateway_audit.jsonl (默认)  # noqa: BOUNDARY (docstring 描述 fallback)
    """
    global _audit_path
    if _audit_path is None:
        env = os.environ.get("CATFISH_AUDIT_PATH")
        if env:
            _audit_path = Path(env).expanduser()
        else:
            # 合规说明: 网关进程自己的 home, 写的是**网关自己产生**的审计流水,
            # 不是读员工数据。异机部署时落到服务器 home, 语义正确。
            #
            # ⚠ 但目录名是 `.catfish` —— 同机开发时它就写进员工那个 .catfish 里,
            #   中央审计和员工数据混在一个目录。生产务必设 CATFISH_AUDIT_PATH。
            home = os.environ.get("HOME") or os.environ.get("USERPROFILE") or "."  # noqa: BOUNDARY
            _audit_path = Path(home) / ".catfish" / "gateway_audit.jsonl"
            # 8/13: 出声。原来这里是**静默**兜底 —— facts_router 同样的情形会
            # 打 warning, 这里不打。异机部署时"审计写去哪了"是运维要知道的事,
            # 不该只能靠读代码。只在首次 resolve 时打一次 (_audit_path 有缓存)。
            logger.warning(
                "metrics: CATFISH_AUDIT_PATH env 未配, 审计落到 %s (进程自己的 home). "
                "生产部署应显式设 env —— 否则同机开发时会跟员工 ~/.catfish 混在一起。",
                _audit_path,
            )
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
        # 8/8 (鸿波"中央端是严禁看到员工端的数据"): 存**分类码**, 不存上游原文。
        #
        # CENTRAL-EDGE-DATA-BOUNDARY 把这张表允许的字段列死了:
        #     (ts, user_email, dept, model, latency_ms, status, cache_*_tokens)
        # `error` 不在里面。而它原来存的是上游异常原文 —— 上一版的注释自己就
        # 写着"Truncate to avoid accidentally leaking upstream **prompt echoes**",
        # 也就是靠截断 200 字 + 脱凭据来兜住"可能带员工内容"。而这个字段还会
        # 显示在中央管理面板 (web QuotaEventsPage 既展示又导 CSV)。
        #
        # 8/8 实测 3058 条带 error 的记录里 0 条含员工内容 —— 但那是运气不是
        # 保证: 字段结构上不受控, 上游返什么就存什么。
        #
        # 换成封闭词表之后, 返回值是我们自己写死的常量, 上游再怎么回显也不可能
        # 变成其中之一。截断和脱敏做不到这一点 —— 它们处理的还是上游那串东西。
        #
        # 原文没丢: 它照常进网关自己的运行日志 (ops 排查用, 8/8 那条 DeepSeek
        # 402 的完整 traceback 就是在那儿看到的)。这跟 hermes v0.20 monitoring
        # 的分法一致: "rendered log messages are not exported"。
        #
        # 键名仍叫 error, 不改成 error_class —— 中央 web 的 QuotaEventsPage 读的
        # 就是这个键, 改名等于连带改前端, 而字段语义收窄本来就不需要动调用方。
        from .error_class import classify_upstream_error  # noqa: PLC0415
        record["error"] = classify_upstream_error(error)
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


# ─────────────────────────────────────────────
# P3.5.59 Phase 2 (6/22 鸿波 catch "是不是应该把中央端完成"):
# 单员工 LLM perf 聚合 — 走 gateway_audit 表 (有 latency_ms / ttft_ms).
#
# 跟 quota.audit_summary_user_since 区别:
#   - quota_events 是配额表, 不含 latency
#   - gateway_audit (本模块写的) 是 audit 表, 含 latency_ms / ttft_ms / status
#
# 用例: Companion PerfCard LLM section 调 /api/audit/me/perf 拿这数据.
# ─────────────────────────────────────────────


def _percentile_sorted(arr: list[float], p: float) -> float | None:
    """对已排序 arr 取 p 分位. 空返 None."""
    if not arr:
        return None
    idx = round((len(arr) - 1) * p)
    return arr[min(idx, len(arr) - 1)]


def query_perf_summary_user(user_email: str, cutoff_ms: int) -> dict:
    """单员工 LLM perf 聚合 — 走 gateway_audit 表 (PG 优先, JSONL fallback).

    返字段:
      - request_count / ok_count / error_count
      - total_tokens
      - latency_p50_ms / latency_p95_ms / latency_p99_ms
      - ttft_p50_ms / ttft_p95_ms (streaming 才有, NULL 跳)
      - by_model: [{model, count, total_tokens, p50_ms}]
      - source: 'pg' | 'jsonl' | 'none' (数据源诊断用)

    Privacy: 全 metadata, 跟 audit_summary_user_since 同合同 — 不返 prompt/response.

    缺数据返 None percentile + 空 list — caller UI 显 "—" 友好.
    """
    empty = {
        "request_count": 0,
        "ok_count": 0,
        "error_count": 0,
        "total_tokens": 0,
        "latency_p50_ms": None,
        "latency_p95_ms": None,
        "latency_p99_ms": None,
        "ttft_p50_ms": None,
        "ttft_p95_ms": None,
        "by_model": [],
        "source": "none",
    }

    # ─── PG 主路径 (生产 SaaS 走这条) ───
    if _use_pg():
        try:
            with _pg_conn() as conn:
                with conn.cursor() as cur:
                    # 总览 + 6 分位 (latency + ttft) 一次 query
                    cur.execute(
                        """SELECT
                            COUNT(*),
                            COUNT(*) FILTER (WHERE status = 'ok'),
                            COUNT(*) FILTER (WHERE status != 'ok'),
                            COALESCE(SUM(tokens_total), 0),
                            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY latency_ms),
                            PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms),
                            PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY latency_ms),
                            PERCENTILE_CONT(0.5) WITHIN GROUP (
                                ORDER BY ttft_ms
                            ) FILTER (WHERE ttft_ms IS NOT NULL),
                            PERCENTILE_CONT(0.95) WITHIN GROUP (
                                ORDER BY ttft_ms
                            ) FILTER (WHERE ttft_ms IS NOT NULL)
                           FROM gateway_audit
                           WHERE user_email = %s AND ts_ms >= %s""",
                        (user_email, cutoff_ms),
                    )
                    row = cur.fetchone() or (0, 0, 0, 0, None, None, None, None, None)

                    # by_model 分组
                    cur.execute(
                        """SELECT
                            model,
                            COUNT(*),
                            COALESCE(SUM(tokens_total), 0),
                            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY latency_ms)
                           FROM gateway_audit
                           WHERE user_email = %s AND ts_ms >= %s
                           GROUP BY model
                           ORDER BY COUNT(*) DESC
                           LIMIT 20""",
                        (user_email, cutoff_ms),
                    )
                    by_model = [
                        {
                            "model": r[0] or "",
                            "count": int(r[1] or 0),
                            "total_tokens": int(r[2] or 0),
                            "p50_ms": float(r[3]) if r[3] is not None else None,
                        }
                        for r in cur.fetchall()
                    ]

            return {
                "request_count": int(row[0] or 0),
                "ok_count": int(row[1] or 0),
                "error_count": int(row[2] or 0),
                "total_tokens": int(row[3] or 0),
                "latency_p50_ms": float(row[4]) if row[4] is not None else None,
                "latency_p95_ms": float(row[5]) if row[5] is not None else None,
                "latency_p99_ms": float(row[6]) if row[6] is not None else None,
                "ttft_p50_ms": float(row[7]) if row[7] is not None else None,
                "ttft_p95_ms": float(row[8]) if row[8] is not None else None,
                "by_model": by_model,
                "source": "pg",
            }
        except Exception as e:
            logger.warning("query_perf_summary_user PG 失败 (fallback jsonl): %s", e)

    # ─── JSONL fallback (dev / 私有部署没 PG 时走) ───
    try:
        path = audit_path()
        if not path.exists():
            return empty
        cutoff_s = cutoff_ms / 1000.0
        latencies: list[float] = []
        ttfts: list[float] = []
        ok = err = total_toks = 0
        by_model_agg: dict[str, dict] = {}

        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("user") != user_email:
                    continue
                ts = r.get("ts", 0)
                if isinstance(ts, str):
                    try:
                        ts = float(ts)
                    except ValueError:
                        continue
                if ts < cutoff_s:
                    continue

                if r.get("status") == "ok":
                    ok += 1
                else:
                    err += 1
                total_toks += int(r.get("total_tokens", 0))
                lat = r.get("latency_ms")
                if lat is not None and lat > 0:
                    latencies.append(float(lat))
                ttft = r.get("ttft_ms")
                if ttft is not None and ttft > 0:
                    ttfts.append(float(ttft))
                m = r.get("model", "")
                if m:
                    entry = by_model_agg.setdefault(
                        m, {"count": 0, "total_tokens": 0, "latencies": []}
                    )
                    entry["count"] += 1
                    entry["total_tokens"] += int(r.get("total_tokens", 0))
                    if lat is not None and lat > 0:
                        entry["latencies"].append(float(lat))

        latencies.sort()
        ttfts.sort()

        by_model = sorted(
            [
                {
                    "model": k,
                    "count": v["count"],
                    "total_tokens": v["total_tokens"],
                    "p50_ms": _percentile_sorted(sorted(v["latencies"]), 0.5),
                }
                for k, v in by_model_agg.items()
            ],
            key=lambda x: -x["count"],
        )[:20]

        return {
            "request_count": ok + err,
            "ok_count": ok,
            "error_count": err,
            "total_tokens": total_toks,
            "latency_p50_ms": _percentile_sorted(latencies, 0.5),
            "latency_p95_ms": _percentile_sorted(latencies, 0.95),
            "latency_p99_ms": _percentile_sorted(latencies, 0.99),
            "ttft_p50_ms": _percentile_sorted(ttfts, 0.5),
            "ttft_p95_ms": _percentile_sorted(ttfts, 0.95),
            "by_model": by_model,
            "source": "jsonl",
        }
    except OSError as e:
        logger.warning("query_perf_summary_user JSONL 失败: %s", e)
        return empty


# ─────────────────────────────────────────────
# P3.5.60 (6/22 鸿波 catch "继续完成"): 全公司 LLM perf 聚合 — 给 web /admin/perf
# 页 admin 看, 走 gateway_audit 表. 跟 query_perf_summary_user 区别: 不按 user
# 过滤, 加 by_department + active_users 全公司维度.
# ─────────────────────────────────────────────


def query_perf_summary_global(
    cutoff_ms: int,
    model_filter: str | None = None,
    dept_filter: str | None = None,
) -> dict:
    """全公司 LLM perf 聚合. admin RBAC 调用方守门.

    返字段: 同 query_perf_summary_user + by_department (按 dept 分聚合) +
    active_users (有调用的不同员工数) + active_departments.

    Privacy: 全 metadata, 跟 audit_summary_global_since 同合同.
    """
    empty = {
        "request_count": 0,
        "ok_count": 0,
        "error_count": 0,
        "total_tokens": 0,
        "active_users": 0,
        "active_departments": 0,
        "latency_p50_ms": None,
        "latency_p95_ms": None,
        "latency_p99_ms": None,
        "ttft_p50_ms": None,
        "ttft_p95_ms": None,
        "by_model": [],
        "by_department": [],
        "source": "none",
    }

    # PG 主路径
    if _use_pg():
        try:
            with _pg_conn() as conn:
                with conn.cursor() as cur:
                    where_parts = ["ts_ms >= %s"]
                    params: list = [cutoff_ms]
                    if model_filter:
                        where_parts.append("model = %s")
                        params.append(model_filter)
                    if dept_filter:
                        where_parts.append("department = %s")
                        params.append(dept_filter)
                    where = " AND ".join(where_parts)

                    cur.execute(
                        f"""SELECT
                            COUNT(*),
                            COUNT(*) FILTER (WHERE status = 'ok'),
                            COUNT(*) FILTER (WHERE status != 'ok'),
                            COALESCE(SUM(tokens_total), 0),
                            COUNT(DISTINCT user_email),
                            COUNT(DISTINCT department),
                            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY latency_ms),
                            PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms),
                            PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY latency_ms),
                            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY ttft_ms) FILTER (WHERE ttft_ms IS NOT NULL),
                            PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY ttft_ms) FILTER (WHERE ttft_ms IS NOT NULL)
                           FROM gateway_audit WHERE {where}""",
                        params,
                    )
                    row = cur.fetchone() or [0] * 11

                    cur.execute(
                        f"""SELECT
                            model,
                            COUNT(*),
                            COUNT(*) FILTER (WHERE status != 'ok'),
                            COALESCE(SUM(tokens_total), 0),
                            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY latency_ms),
                            PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY latency_ms)
                           FROM gateway_audit WHERE {where}
                           GROUP BY model ORDER BY COUNT(*) DESC LIMIT 20""",
                        params,
                    )
                    by_model = [
                        {
                            "model": r[0] or "",
                            "count": int(r[1] or 0),
                            "error_count": int(r[2] or 0),
                            "total_tokens": int(r[3] or 0),
                            "p50_ms": float(r[4]) if r[4] is not None else None,
                            "p99_ms": float(r[5]) if r[5] is not None else None,
                        }
                        for r in cur.fetchall()
                    ]

                    cur.execute(
                        f"""SELECT
                            department,
                            COUNT(*),
                            COUNT(*) FILTER (WHERE status != 'ok'),
                            COALESCE(SUM(tokens_total), 0),
                            COUNT(DISTINCT user_email),
                            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY latency_ms),
                            PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY latency_ms)
                           FROM gateway_audit WHERE {where}
                           GROUP BY department ORDER BY COUNT(*) DESC LIMIT 20""",
                        params,
                    )
                    by_department = [
                        {
                            "department": r[0] or "(未分组)",
                            "count": int(r[1] or 0),
                            "error_count": int(r[2] or 0),
                            "total_tokens": int(r[3] or 0),
                            "active_users": int(r[4] or 0),
                            "p50_ms": float(r[5]) if r[5] is not None else None,
                            "p99_ms": float(r[6]) if r[6] is not None else None,
                        }
                        for r in cur.fetchall()
                    ]

            return {
                "request_count": int(row[0] or 0),
                "ok_count": int(row[1] or 0),
                "error_count": int(row[2] or 0),
                "total_tokens": int(row[3] or 0),
                "active_users": int(row[4] or 0),
                "active_departments": int(row[5] or 0),
                "latency_p50_ms": float(row[6]) if row[6] is not None else None,
                "latency_p95_ms": float(row[7]) if row[7] is not None else None,
                "latency_p99_ms": float(row[8]) if row[8] is not None else None,
                "ttft_p50_ms": float(row[9]) if row[9] is not None else None,
                "ttft_p95_ms": float(row[10]) if row[10] is not None else None,
                "by_model": by_model,
                "by_department": by_department,
                "source": "pg",
            }
        except Exception as e:
            logger.warning("query_perf_summary_global PG 失败 (fallback jsonl): %s", e)

    # JSONL fallback
    try:
        path = audit_path()
        if not path.exists():
            return empty
        cutoff_s = cutoff_ms / 1000.0
        latencies: list[float] = []
        ttfts: list[float] = []
        ok = err = total_toks = 0
        active_users: set[str] = set()
        active_depts: set[str] = set()
        by_model_agg: dict[str, dict] = {}
        by_dept_agg: dict[str, dict] = {}

        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = r.get("ts", 0)
                if isinstance(ts, str):
                    try:
                        ts = float(ts)
                    except ValueError:
                        continue
                if ts < cutoff_s:
                    continue
                m = r.get("model", "")
                d = r.get("department", "")
                u = r.get("user", "")
                if model_filter and m != model_filter:
                    continue
                if dept_filter and d != dept_filter:
                    continue

                if r.get("status") == "ok":
                    ok += 1
                else:
                    err += 1
                total_toks += int(r.get("total_tokens", 0))
                if u:
                    active_users.add(u)
                if d:
                    active_depts.add(d)
                lat = r.get("latency_ms")
                if lat is not None and lat > 0:
                    latencies.append(float(lat))
                ttft = r.get("ttft_ms")
                if ttft is not None and ttft > 0:
                    ttfts.append(float(ttft))

                if m:
                    e_m = by_model_agg.setdefault(
                        m, {"count": 0, "error_count": 0, "total_tokens": 0, "lats": []}
                    )
                    e_m["count"] += 1
                    if r.get("status") != "ok":
                        e_m["error_count"] += 1
                    e_m["total_tokens"] += int(r.get("total_tokens", 0))
                    if lat is not None and lat > 0:
                        e_m["lats"].append(float(lat))

                dept_key = d or "(未分组)"
                e_d = by_dept_agg.setdefault(
                    dept_key,
                    {"count": 0, "error_count": 0, "total_tokens": 0, "users": set(), "lats": []},
                )
                e_d["count"] += 1
                if r.get("status") != "ok":
                    e_d["error_count"] += 1
                e_d["total_tokens"] += int(r.get("total_tokens", 0))
                if u:
                    e_d["users"].add(u)
                if lat is not None and lat > 0:
                    e_d["lats"].append(float(lat))

        latencies.sort()
        ttfts.sort()

        by_model = sorted(
            [
                {
                    "model": k,
                    "count": v["count"],
                    "error_count": v["error_count"],
                    "total_tokens": v["total_tokens"],
                    "p50_ms": _percentile_sorted(sorted(v["lats"]), 0.5),
                    "p99_ms": _percentile_sorted(sorted(v["lats"]), 0.99),
                }
                for k, v in by_model_agg.items()
            ],
            key=lambda x: -x["count"],
        )[:20]

        by_department = sorted(
            [
                {
                    "department": k,
                    "count": v["count"],
                    "error_count": v["error_count"],
                    "total_tokens": v["total_tokens"],
                    "active_users": len(v["users"]),
                    "p50_ms": _percentile_sorted(sorted(v["lats"]), 0.5),
                    "p99_ms": _percentile_sorted(sorted(v["lats"]), 0.99),
                }
                for k, v in by_dept_agg.items()
            ],
            key=lambda x: -x["count"],
        )[:20]

        return {
            "request_count": ok + err,
            "ok_count": ok,
            "error_count": err,
            "total_tokens": total_toks,
            "active_users": len(active_users),
            "active_departments": len(active_depts),
            "latency_p50_ms": _percentile_sorted(latencies, 0.5),
            "latency_p95_ms": _percentile_sorted(latencies, 0.95),
            "latency_p99_ms": _percentile_sorted(latencies, 0.99),
            "ttft_p50_ms": _percentile_sorted(ttfts, 0.5),
            "ttft_p95_ms": _percentile_sorted(ttfts, 0.95),
            "by_model": by_model,
            "by_department": by_department,
            "source": "jsonl",
        }
    except OSError as e:
        logger.warning("query_perf_summary_global JSONL 失败: %s", e)
        return empty
