"""BL-CENTRAL-EDGE-TOOL-ARCHIVE (5/22 鸿波): tool_archive 搬 edge 重构.

# 真问题

5/17 BOUNDARY 纪律: 中央 PG 不允许装员工内容. gateway 老 tool_archive 把每条
超 4KB tool result 全文 + LLM summary 写到 PG tool_archives 表 (content TEXT
NOT NULL), 14 天 TTL. **严重违规** — 浏览器抓的 HTML / 文件读取内容 / API JSON
全在中央 PG.

5/22 鸿波拍板真重构, 完全搬到 edge, 不留 metadata-only 空架子.

# 新设计

- 写: catfish-tool-bridge 在 tool dispatch 返回前内嵌截胡 (替代 gateway 内嵌)
- 存: ~/.catfish/tool_archives/<date>/<ref>.json (按 date 分桶易清理 + 易打包)
- 索引: ~/.catfish/tool_archives/_index.sqlite (跨 date ref O(1) lookup)
- 读: catfish_read_tool_archive 直读本机 (不再 HTTP 走 gateway)

# Portable First

跨 Mac 带走 = `cp -r ~/.catfish/ /new/mac/`, 同 catfish OIDC sub 登 → ref lookup
立即通. session_id 不索引 (只存历史). catfish_user 字段做 portable key.

# 不变量 (跟 gateway 老逻辑一致, 保 ref 兼容)

- _compute_ref = sha256(content + ":" + tool_call_id)[:16]
- _is_archive_candidate: role=tool + str content + 没 archive 过 + 非 instructional skill
- 替换文本格式: [已归档: archive_ref=..., tool=..., XKB / N 行] + 摘要 + 头尾预览

# 设计稿: docs/CATFISH-TOOL-ARCHIVE-MIGRATION.md
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("catfish.tool_bridge.tool_archive_local")


# ── 配置 ────────────────────────────────────────────────────────────

#: 触发阈值字节. env 可调.
DEFAULT_THRESHOLD_BYTES = 4_000
#: 头尾预览字节数 (跟 gateway prompts.py 一致)
PREVIEW_HEAD_BYTES = 500
PREVIEW_TAIL_BYTES = 500
#: archive 保留天数 (本机 cron 清, 不靠 PG TTL)
DEFAULT_TTL_DAYS = 14


def _env_bool(key: str, default: bool) -> bool:
    v = os.environ.get(key, "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return default


def is_archive_enabled() -> bool:
    """edge 端 master 开关. 默认开 (跟 gateway 老 CATFISH_TOOL_ARCHIVE_ENABLED 一致)."""
    return _env_bool("CATFISH_TOOL_ARCHIVE_ENABLED", True)


def threshold_bytes() -> int:
    try:
        return max(1000, int(os.environ.get("CATFISH_TOOL_ARCHIVE_THRESHOLD", str(DEFAULT_THRESHOLD_BYTES))))
    except ValueError:
        return DEFAULT_THRESHOLD_BYTES


# ── 路径 ────────────────────────────────────────────────────────────

def _catfish_dir() -> Path:
    home = os.environ.get("CATFISH_HOME", "").strip()
    if home:
        return Path(home).expanduser()
    return Path.home() / ".catfish"


def _archives_dir() -> Path:
    return _catfish_dir() / "tool_archives"


def _db_path() -> Path:
    return _archives_dir() / "_index.sqlite"


def _date_dir(d: Optional[date] = None) -> Path:
    """按 date 分桶: ~/.catfish/tool_archives/2026-05-22/"""
    if d is None:
        d = date.today()
    return _archives_dir() / d.isoformat()


def _file_for_ref(ref: str, d: Optional[date] = None) -> Path:
    return _date_dir(d) / f"{ref}.json"


def _catfish_user() -> Optional[str]:
    """OIDC sub 做 portable key. 跨 Mac 同 sub 才能读.

    优先级: env CATFISH_OIDC_SUB > ~/.catfish/oauth/id_token decode > None
    """
    env_sub = os.environ.get("CATFISH_OIDC_SUB", "").strip()
    if env_sub:
        return env_sub
    # 读 Companion 写的 OAuth id_token (BL-FIX32, 同 read_tool_archive.py)
    id_token_path = _catfish_dir() / "oauth" / "id_token"
    if not id_token_path.exists():
        return None
    try:
        token = id_token_path.read_text(encoding="utf-8").strip()
        # JWT = header.payload.signature, base64url
        import base64  # noqa: PLC0415
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload_b64 = parts[1]
        # base64url padding
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return payload.get("sub")
    except (OSError, ValueError, json.JSONDecodeError) as e:
        logger.debug("read OIDC sub 失败 (走 None): %s", e)
        return None


# ── sqlite index ────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tool_archives_index (
    ref           TEXT PRIMARY KEY,
    file_path     TEXT NOT NULL,        -- 相对 _archives_dir 的路径, e.g. "2026-05-22/abc.json"
    tool_name     TEXT,
    content_bytes INTEGER NOT NULL,
    lines         INTEGER NOT NULL,
    has_summary   INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,        -- ISO-8601
    session_id    TEXT,                  -- 历史信息, 不索引
    catfish_user  TEXT,                  -- OIDC sub, portable key
    tool_call_id  TEXT
);
CREATE INDEX IF NOT EXISTS idx_tool_archives_created
    ON tool_archives_index(created_at);
CREATE INDEX IF NOT EXISTS idx_tool_archives_tool
    ON tool_archives_index(tool_name);
CREATE INDEX IF NOT EXISTS idx_tool_archives_user
    ON tool_archives_index(catfish_user);
"""


def _get_conn() -> sqlite3.Connection:
    """WAL mode + busy_timeout, 多进程并发安全 (catfish-memory plugin 同款)."""
    _archives_dir().mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_db_path()), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


_db_initialized = False


def _ensure_db() -> None:
    """懒初始化 schema. 多次调用幂等 (CREATE TABLE IF NOT EXISTS)."""
    global _db_initialized
    if _db_initialized:
        return
    with _get_conn() as conn:
        conn.executescript(_SCHEMA)
    _db_initialized = True


# ── 不变量 (跟 gateway 老逻辑一致, 保 ref 兼容) ──────────────────────

def _compute_ref(content: str, tool_call_id: Optional[str]) -> str:
    """ref = sha256(content + ":" + tool_call_id)[:16]. 跟 gateway 老逻辑一致.

    16 字 (64 bit) 撞概率 < 1e-9 / 1B archive. 含 tool_call_id 避免不同调用同
    content 撞 ref (e.g. 同一 grep 命令两次跑出同 stdout).
    """
    h = hashlib.sha256()
    h.update(content.encode("utf-8"))
    h.update(b":")
    h.update((tool_call_id or "").encode("utf-8"))
    return h.hexdigest()[:16]


def _count_lines(content: str) -> int:
    return content.count("\n") + (0 if content.endswith("\n") else 1)


def _is_archive_candidate(m: dict) -> bool:
    """role=tool + content 是 str + 没被 archive 过 + 非 instructional skill.

    BL-ARCHIVE-SKIP-INSTRUCTIONAL (5/15 鸿波 'agent 拿到归档摘要以为完事'):
    catfish_run_skill 返回 instructional skill 指令 (含 `is_instructional: true`)
    时**永不归档**. 否则归档摘要把"这是指令型 skill, LLM 需要接力"的语义抹掉,
    写成"已生成 PPT" 误导 agent 死循环.
    """
    if not isinstance(m, dict):
        return False
    if m.get("role") != "tool":
        return False
    c = m.get("content")
    if not isinstance(c, str):
        return False
    if c.startswith("[已归档: archive_ref="):
        return False
    if m.get("name") == "catfish_run_skill" and '"is_instructional": true' in c:
        return False
    return True


# ── 替换文本 (跟 gateway prompts.py 一致, 让 LLM 看的 prompt 不变) ──────

def _safe_preview(text: str, n_bytes: int, *, head: bool) -> str:
    encoded = text.encode("utf-8")
    if head:
        return encoded[:n_bytes].decode("utf-8", errors="ignore")
    return encoded[-n_bytes:].decode("utf-8", errors="ignore")


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n}B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f}KB"
    return f"{n / 1024 / 1024:.2f}MB"


def _indent_preview(text: str) -> str:
    return "\n".join(f"> {line}" for line in text.splitlines())


def build_replacement_text(
    *,
    ref: str,
    content: str,
    tool_name: Optional[str],
    content_bytes: int,
    lines: int,
    summary: Optional[str] = None,
    summary_error: Optional[str] = None,
) -> str:
    """构造替换文本. 跟 gateway prompts.build_replacement_text 输出格式一致."""
    head_preview = _safe_preview(content, PREVIEW_HEAD_BYTES, head=True)
    tail_preview = _safe_preview(content, PREVIEW_TAIL_BYTES, head=False)

    if summary:
        summary_line = f"📝 摘要 (haiku): {summary.strip()}"
    elif summary_error:
        summary_line = "📝 摘要 (生成失败, 看头尾或直接 read 拿全文)"
    else:
        summary_line = "📝 摘要 (生成中…若需中段, 直接调 read tool 不等摘要)"

    tool_str = tool_name or "tool"
    size_str = _fmt_size(content_bytes)
    return (
        f"[已归档: archive_ref={ref}, tool={tool_str}, {size_str} / {lines} 行]\n"
        f"\n"
        f"{summary_line}\n"
        f"\n"
        f"📂 头部 (前 {PREVIEW_HEAD_BYTES}B):\n"
        f"{_indent_preview(head_preview)}\n"
        f"\n"
        f"📂 尾部 (后 {PREVIEW_TAIL_BYTES}B):\n"
        f"{_indent_preview(tail_preview)}\n"
        f"\n"
        f"💡 看不全? 调 catfish_read_tool_archive(ref=\"{ref}\", grep=\"...\") "
        f"或 (ref, line_range=\"40-80\") 拿中段. **不要凭空编中段内容**."
    )


# ── 落盘: 写文件 + 索引 ──────────────────────────────────────────────

def upsert_archive(row: dict[str, Any]) -> bool:
    """写一条 archive (文件 + sqlite 索引). 返 ok.

    幂等 — 同 ref 二次写 OVERWRITE (不报错). caller 已在 archiver 里去重.
    """
    try:
        _ensure_db()
        ref = row["ref"]
        content = row["content"]
        d = date.today()
        date_dir = _date_dir(d)
        date_dir.mkdir(parents=True, exist_ok=True)
        file_path = _file_for_ref(ref, d)
        rel_path = f"{d.isoformat()}/{ref}.json"

        # 1. 写文件 (full content + metadata, 跨进程 read 不依赖 sqlite 索引)
        payload = {
            "ref": ref,
            "tool_name": row.get("tool_name"),
            "tool_call_id": row.get("tool_call_id"),
            "session_id": row.get("session_id"),
            "catfish_user": row.get("catfish_user"),
            "content": content,
            "content_bytes": row["content_bytes"],
            "lines": row["lines"],
            "summary": row.get("summary"),
            "summary_model": row.get("summary_model"),
            "summary_at": row.get("summary_at"),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        file_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # 2. upsert sqlite 索引
        with _get_conn() as conn:
            conn.execute(
                """
                INSERT INTO tool_archives_index (
                    ref, file_path, tool_name, content_bytes, lines,
                    has_summary, created_at, session_id, catfish_user, tool_call_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ref) DO UPDATE SET
                    file_path = excluded.file_path,
                    has_summary = excluded.has_summary,
                    created_at = excluded.created_at
                """,
                (
                    ref,
                    rel_path,
                    row.get("tool_name"),
                    row["content_bytes"],
                    row["lines"],
                    1 if row.get("summary") else 0,
                    payload["created_at"],
                    row.get("session_id"),
                    row.get("catfish_user"),
                    row.get("tool_call_id"),
                ),
            )
        return True
    except (OSError, sqlite3.Error, KeyError) as e:
        logger.warning("[tool_archive_local] upsert 失败 ref=%s: %s", row.get("ref"), e)
        return False


def read_archive(ref: str) -> Optional[dict[str, Any]]:
    """按 ref 查回 full archive payload. 没找到返 None."""
    if not ref or len(ref) > 64:
        return None
    try:
        _ensure_db()
        with _get_conn() as conn:
            cur = conn.execute(
                "SELECT file_path FROM tool_archives_index WHERE ref = ?",
                (ref,),
            )
            row = cur.fetchone()
        if not row:
            return None
        rel_path = row[0]
        file_path = _archives_dir() / rel_path
        if not file_path.exists():
            logger.warning(
                "[tool_archive_local] 索引存在但文件丢: ref=%s path=%s",
                ref, file_path,
            )
            return None
        return json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, sqlite3.Error, json.JSONDecodeError) as e:
        logger.warning("[tool_archive_local] read 失败 ref=%s: %s", ref, e)
        return None


def update_summary(ref: str, summary: str, summary_model: str) -> bool:
    """异步 worker 跑完 summary 回写. 改文件 + 索引 has_summary 标."""
    payload = read_archive(ref)
    if payload is None:
        return False
    payload["summary"] = summary
    payload["summary_model"] = summary_model
    payload["summary_at"] = datetime.now(timezone.utc).isoformat()
    try:
        # 文件覆盖写
        d_str = payload["created_at"][:10]  # "YYYY-MM-DD"
        file_path = _archives_dir() / d_str / f"{ref}.json"
        file_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        # 索引标 has_summary=1
        with _get_conn() as conn:
            conn.execute(
                "UPDATE tool_archives_index SET has_summary = 1 WHERE ref = ?",
                (ref,),
            )
        return True
    except (OSError, sqlite3.Error) as e:
        logger.warning("[tool_archive_local] update_summary 失败 ref=%s: %s", ref, e)
        return False


# ── archiver 主入口 ──────────────────────────────────────────────────

def derive_session_id(
    messages: Optional[list[dict]] = None,
    conversation_id: Optional[str] = None,
) -> str:
    """派生 session_id (不再绑 user_email — Phase 2 portable 要求).

    优先级:
      1. conversation_id 显式传
      2. messages 首条 user message 前 500 字 hash
      3. 当天日期 (兜底)
    """
    if conversation_id:
        return f"conv:{conversation_id}"

    if messages:
        first_user = next(
            (
                m.get("content", "") for m in messages
                if isinstance(m, dict) and m.get("role") == "user"
                and isinstance(m.get("content"), str)
            ),
            None,
        )
        if first_user:
            h = hashlib.sha256(first_user[:500].encode("utf-8")).hexdigest()[:12]
            return f"hash:{h}"

    return f"day:{date.today().isoformat()}"


def archive_tool_messages(
    messages: list[dict[str, Any]],
    *,
    session_id: Optional[str] = None,
    threshold: Optional[int] = None,
) -> list[dict[str, Any]]:
    """对超阈值的 role=tool message 写 archive + 替换 content.

    返新 list (deepcopy). 不改原 messages.
    """
    if not messages:
        return messages

    thr = threshold if threshold is not None else threshold_bytes()
    out = deepcopy(messages)
    archived = 0
    saved_bytes = 0
    user_sub = _catfish_user()
    sid = session_id or derive_session_id(messages=messages)

    for i, m in enumerate(out):
        if not _is_archive_candidate(m):
            continue
        content: str = m["content"]
        nbytes = len(content.encode("utf-8"))
        if nbytes < thr:
            continue

        tool_call_id = m.get("tool_call_id") or m.get("id")
        tool_name = m.get("name")
        ref = _compute_ref(content, tool_call_id)

        ok = upsert_archive({
            "ref": ref,
            "session_id": sid,
            "catfish_user": user_sub,
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "content": content,
            "content_bytes": nbytes,
            "lines": _count_lines(content),
        })
        if not ok:
            logger.error(
                "[tool_archive_local] 写挂 ref=%s tool=%s — 跳过, content 不动",
                ref, tool_name,
            )
            continue

        replacement = build_replacement_text(
            ref=ref,
            content=content,
            tool_name=tool_name,
            content_bytes=nbytes,
            lines=_count_lines(content),
        )
        out[i]["content"] = replacement
        archived += 1
        saved_bytes += nbytes - len(replacement.encode("utf-8"))

    if archived > 0:
        logger.info(
            "[tool_archive_local] 归档 %d 条, 省 %d 字节 (~%dK tokens) "
            "session=%s threshold=%d",
            archived, saved_bytes, saved_bytes // 4000,
            sid[:40], thr,
        )
    return out


def prepare_tool_messages(
    messages: list[dict[str, Any]],
    *,
    session_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """统一入口. enabled & 写成功 → archive; 否则原样过."""
    if not is_archive_enabled():
        return messages
    sid = session_id or derive_session_id(messages=messages, conversation_id=conversation_id)
    try:
        return archive_tool_messages(messages, session_id=sid)
    except Exception as e:  # noqa: BLE001
        logger.warning("[tool_archive_local] archive 异常, 原样返: %s", e)
        return messages


# ── 维护: TTL 清理 ──────────────────────────────────────────────────

def cleanup_old_archives(ttl_days: int = DEFAULT_TTL_DAYS) -> dict[str, int]:
    """清 ttl_days 前的 archive (文件 + 索引). 由 cron / 启动时调.

    返 {"deleted_files": N, "deleted_rows": M}.
    """
    if ttl_days < 1:
        return {"deleted_files": 0, "deleted_rows": 0}
    cutoff = (date.today() - __import__("datetime").timedelta(days=ttl_days)).isoformat()
    deleted_files = 0
    deleted_rows = 0
    try:
        _ensure_db()
        # 1. 删 date 目录 < cutoff
        if _archives_dir().exists():
            for d in _archives_dir().iterdir():
                if not d.is_dir():
                    continue
                if not (len(d.name) == 10 and d.name[4] == "-" and d.name[7] == "-"):
                    continue  # 不是 "YYYY-MM-DD" 格式
                if d.name >= cutoff:
                    continue
                for f in d.iterdir():
                    f.unlink()
                    deleted_files += 1
                d.rmdir()
        # 2. 删 sqlite 索引
        with _get_conn() as conn:
            cur = conn.execute(
                "DELETE FROM tool_archives_index WHERE created_at < ?",
                (cutoff,),
            )
            deleted_rows = cur.rowcount or 0
    except (OSError, sqlite3.Error) as e:
        logger.warning("[tool_archive_local] cleanup 失败 (部分清): %s", e)
    return {"deleted_files": deleted_files, "deleted_rows": deleted_rows}


__all__ = [
    "DEFAULT_THRESHOLD_BYTES",
    "PREVIEW_HEAD_BYTES",
    "PREVIEW_TAIL_BYTES",
    "DEFAULT_TTL_DAYS",
    "is_archive_enabled",
    "threshold_bytes",
    "build_replacement_text",
    "upsert_archive",
    "read_archive",
    "update_summary",
    "derive_session_id",
    "archive_tool_messages",
    "prepare_tool_messages",
    "cleanup_old_archives",
]
