"""Skills Hub 存储层 — BL-D2 Phase 2 PG 统一 (5/10).

# 双层存储 (跟 mcp-registry 同模式)

  PG (元数据):                                     FS (文件内容):
    skills_versions                                <CATFISH_HUB_ROOT>/skills/
      id, namespace, name, version,                  └── <namespace>/
      description, published_by,                         └── <name>/
      published_at, content_dir,                             └── <version>/
      file_count, total_bytes,                                   ├── SKILL.md
      deprecated, subscribe_count,                               ├── script.py
      rating_avg, rating_count                                   └── ...
    skills_audit
      id, ts_ms, action, namespace,
      name, version, by_user, meta JSONB

  PG 提供: 索引快查 / cross-service join / 真生产备份审计.
  FS 提供: 文件内容 (small skill, 1MB 内). Phase 3 切对象存储 S3/MinIO 时只
  改 _content_url() 函数返 's3://bucket/key' 即可.

# Backend 选择 (跟 mcp-registry db.py 同)

  env CATFISH_DB_URL 设了 → PG (psycopg sync, autocommit)
  没设 → 纯 FS + jsonl audit (dev / 单测兼容)

# 安全 / 验证

- namespace / name / version 路径检查 (无 .. / /)
- 文件 sha256 (跟客户端比对防中间人)
- 任何写操作走 audit (PG skills_audit 或 jsonl fallback)
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("catfish.skills_hub.storage")


# ── PG backend (BL-D2 Phase 2 5/10) ─────────────────────────────────────


def _pg_url() -> str | None:
    """env CATFISH_DB_URL → PG, 否则 None (走 FS only)."""
    return os.environ.get("CATFISH_DB_URL", "").strip() or None


def _pg_clean_url(url: str) -> str:
    """剥 SQLAlchemy driver prefix 给 psycopg.connect 用 (跟 mcp-registry 同).

    alembic 用 postgresql+psycopg://, libpq 直连不认前缀.
    """
    if url.startswith("postgresql+psycopg://"):
        return "postgresql://" + url[len("postgresql+psycopg://"):]
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://"):]
    return url


@contextlib.contextmanager
def _pg_conn():
    """开 psycopg sync 连接 + autocommit + dict_row. caller 用完自动 close."""
    import psycopg  # noqa: PLC0415  懒 import, 没装 psycopg 也能跑 FS 模式
    from psycopg.rows import dict_row  # noqa: PLC0415

    url = _pg_url()
    if not url:
        raise RuntimeError("CATFISH_DB_URL 没设, _pg_conn 不应被调用")
    conn = psycopg.connect(_pg_clean_url(url), row_factory=dict_row, autocommit=True)
    try:
        yield conn
    finally:
        conn.close()


def _use_pg() -> bool:
    return _pg_url() is not None

_NAME_RE = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")
_VERSION_RE = re.compile(r"^[a-zA-Z0-9._\-+]{1,32}$")


def hub_root() -> Path:
    """中央 hub 文件根. CATFISH_HUB_ROOT env 可 override (单测用)."""
    custom = os.environ.get("CATFISH_HUB_ROOT", "").strip()
    if custom:
        return Path(custom).expanduser()
    return Path.home() / ".catfish-hub"


def _skills_dir() -> Path:
    return hub_root() / "skills"


def _audit_path() -> Path:
    return hub_root() / "audit.jsonl"


def _validate_seg(seg: str, kind: str) -> None:
    """验证 namespace / name / version 段无 .. / / / 等危险字符."""
    if not seg or seg in ("..", ".") or "/" in seg or "\\" in seg:
        raise ValueError(f"{kind} 不合法: {seg!r}")
    pattern = _VERSION_RE if kind == "version" else _NAME_RE
    if not pattern.match(seg):
        raise ValueError(f"{kind} 含非法字符 (允许 [a-zA-Z0-9_-]+, version 加 .): {seg!r}")


@dataclass
class SkillVersion:
    """已发布 skill 的一个版本."""

    namespace: str
    name: str
    version: str
    published_at: str  # ISO8601
    published_by: str  # email
    deprecated: bool = False
    description: str = ""
    files: list[str] = field(default_factory=list)


def _write_audit(event: dict) -> None:
    """写一行 audit. PG 主, jsonl 兜底.

    BL-D2 Phase 2 (5/10): PG skills_audit 表是真源, jsonl 留 dev/无 PG 时兜底.
    跟 catfish-gateway gateway_audit / mcp-registry mcp_audit 同模式.
    失败静默不影响主流程.
    """
    # 1. PG (优先)
    if _use_pg():
        try:
            with _pg_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO skills_audit
                           (ts_ms, action, namespace, name, version, by_user, meta)
                           VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)""",
                        (
                            int(time.time() * 1000),
                            event.get("event") or event.get("action") or "unknown",
                            event.get("namespace"),
                            event.get("name"),
                            event.get("version"),
                            event.get("published_by") or event.get("deleted_by") or event.get("by_user") or "?",
                            json.dumps(
                                {k: v for k, v in event.items()
                                 if k not in ("event", "namespace", "name", "version",
                                              "published_by", "deleted_by", "by_user", "ts")},
                                ensure_ascii=False,
                            ),
                        ),
                    )
            return
        except Exception as e:
            logger.warning("audit 写 PG 失败 (fallback jsonl): %s", e)

    # 2. jsonl 兜底
    try:
        path = _audit_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("audit 写 jsonl 失败: %s", e)


def _parse_skill_md(skill_md_text: str) -> dict:
    """mini parse SKILL.md frontmatter — name / version / description / deprecated."""
    out = {"name": "", "version": "", "description": "", "deprecated": False}
    if not skill_md_text.startswith("---"):
        return out
    end = skill_md_text.find("\n---", 3)
    if end < 0:
        return out
    fm = skill_md_text[3:end].strip()
    for line in fm.split("\n"):
        ls = line.strip()
        if ":" not in ls or ls.startswith("#"):
            continue
        if line.startswith(" ") or line.startswith("\t"):
            continue
        key, _, val = ls.partition(":")
        key = key.strip()
        val = val.strip().strip("'\"|").strip()
        if key == "name":
            out["name"] = val
        elif key == "version":
            out["version"] = val
        elif key == "description":
            out["description"] = val[:500]
        elif key == "deprecated":
            out["deprecated"] = val.lower() in ("true", "yes", "1")
    return out


def list_skills(namespace_filter: str | None = None) -> list[dict]:
    """列已发布 skill (按 ns/name 分组, 各取最新版本).

    BL-D2 Phase 2 (5/10): PG 优先 (索引快, dashboard 列 100+ skill 也秒级).
    PG 失败 / 没配 → FS 扫描兜底.
    """
    # 1. PG 优先 (按 namespace+name groupby, 取每组最新 published_at 的 version)
    if _use_pg():
        try:
            with _pg_conn() as conn:
                with conn.cursor() as cur:
                    sql = """
                        SELECT DISTINCT ON (namespace, name)
                            namespace, name, version, description, deprecated,
                            published_by, published_at, file_count, total_bytes,
                            subscribe_count, rating_avg, rating_count
                        FROM skills_versions
                        {where}
                        ORDER BY namespace, name, published_at DESC
                    """
                    if namespace_filter:
                        cur.execute(
                            sql.format(where="WHERE namespace = %s"),
                            (namespace_filter,),
                        )
                    else:
                        cur.execute(sql.format(where=""))
                    rows = list(cur.fetchall())
            out = []
            for r in rows:
                published_at = r.get("published_at")
                out.append({
                    "namespace": r["namespace"],
                    "name": r["name"],
                    "latest_version": r["version"],
                    "all_versions": [r["version"]],  # FS 兜底才扫全部, PG 模式只返最新
                    "description": r.get("description") or "",
                    "deprecated": bool(r.get("deprecated")),
                    "published_by": r.get("published_by"),
                    "published_at": (
                        published_at.isoformat() if published_at else None
                    ),
                    "file_count": r.get("file_count") or 0,
                    "total_bytes": r.get("total_bytes") or 0,
                    "subscribe_count": r.get("subscribe_count") or 0,
                    "rating_avg": r.get("rating_avg"),
                    "rating_count": r.get("rating_count") or 0,
                })
            return out
        except Exception as e:
            logger.warning("list_skills PG 失败 (fallback FS 扫描): %s", e)

    # 2. FS 扫描兜底 (原逻辑, dev / 没 PG 时用)
    out: list[dict] = []
    root = _skills_dir()
    if not root.exists():
        return out

    for ns_dir in sorted(root.iterdir()):
        if not ns_dir.is_dir() or ns_dir.name.startswith("."):
            continue
        if namespace_filter and ns_dir.name != namespace_filter:
            continue
        for skill_dir in sorted(ns_dir.iterdir()):
            if not skill_dir.is_dir() or skill_dir.name.startswith("."):
                continue
            versions = sorted(
                [v.name for v in skill_dir.iterdir() if v.is_dir() and not v.name.startswith(".")],
                reverse=True,
            )
            if not versions:
                continue
            latest_dir = skill_dir / versions[0]
            skill_md = latest_dir / "SKILL.md"
            meta = {}
            if skill_md.exists():
                try:
                    meta = _parse_skill_md(skill_md.read_text(encoding="utf-8"))
                except Exception:
                    pass
            out.append({
                "namespace": ns_dir.name,
                "name": skill_dir.name,
                "latest_version": versions[0],
                "all_versions": versions,
                "description": meta.get("description", ""),
                "deprecated": meta.get("deprecated", False),
            })
    return out


def get_skill(namespace: str, name: str, version: str = "latest") -> dict | None:
    """获取 skill 元信息 + 文件列表 + SKILL.md 全文.

    version='latest' 取最新版.
    返 None = 不存在.
    """
    _validate_seg(namespace, "namespace")
    _validate_seg(name, "name")

    skill_dir = _skills_dir() / namespace / name
    if not skill_dir.is_dir():
        return None

    if version == "latest":
        versions = sorted(
            [v.name for v in skill_dir.iterdir() if v.is_dir() and not v.name.startswith(".")],
            reverse=True,
        )
        if not versions:
            return None
        version = versions[0]
    else:
        _validate_seg(version, "version")

    version_dir = skill_dir / version
    if not version_dir.is_dir():
        return None

    skill_md = version_dir / "SKILL.md"
    skill_md_text = ""
    meta: dict = {}
    if skill_md.exists():
        try:
            skill_md_text = skill_md.read_text(encoding="utf-8")
            meta = _parse_skill_md(skill_md_text)
        except Exception as e:
            logger.warning("读 %s 失败: %s", skill_md, e)

    # 列文件 + 算 sha256 (5/6 安全 G2: 客户端拉文件时校验防中间人/篡改)
    files: list[str] = []
    files_sha256: dict[str, str] = {}
    for child in sorted(version_dir.rglob("*")):
        if child.is_file():
            rel = str(child.relative_to(version_dir))
            files.append(rel)
            try:
                # skill 文件最大几 MB (绝大多数 KB 级), 直接 read_bytes 算 hash
                files_sha256[rel] = hashlib.sha256(child.read_bytes()).hexdigest()
            except OSError as e:
                # 读不了就不放 hash, 客户端会当 unsigned 处理
                logger.warning("sha256 算 %s 失败: %s", child, e)

    return {
        "namespace": namespace,
        "name": name,
        "version": version,
        "description": meta.get("description", ""),
        "deprecated": meta.get("deprecated", False),
        "files": files,
        "files_sha256": files_sha256,  # 5/6 G2: 客户端 _install_from_hub 比对
        "skill_md": skill_md_text,
    }


def download_file(namespace: str, name: str, version: str, file_path: str) -> bytes | None:
    """下载 skill 某个文件 (二进制). 路径检查防 traversal."""
    _validate_seg(namespace, "namespace")
    _validate_seg(name, "name")
    _validate_seg(version, "version")

    if ".." in file_path or file_path.startswith("/"):
        raise ValueError(f"file_path 不合法: {file_path!r}")

    target = _skills_dir() / namespace / name / version / file_path
    if not target.is_file():
        return None
    # 双重保险: resolve 后必须仍在 version_dir 下
    version_dir = (_skills_dir() / namespace / name / version).resolve()
    try:
        if not str(target.resolve()).startswith(str(version_dir)):
            raise ValueError("路径越界")
    except Exception:
        raise ValueError(f"file_path 越界: {file_path!r}")

    return target.read_bytes()


def publish_skill(
    namespace: str,
    files: dict[str, bytes],
    *,
    published_by: str,
) -> dict:
    """发布一个 skill — 写入 hub/skills/<ns>/<name>/<version>/.

    files: dict, 相对路径 → bytes. 必须含 SKILL.md.
    name + version 从 SKILL.md frontmatter 解析.

    返 {ok, namespace, name, version, files_count, error?}
    """
    _validate_seg(namespace, "namespace")

    if "SKILL.md" not in files:
        return {"ok": False, "error": "files 缺 SKILL.md"}

    try:
        skill_md_text = files["SKILL.md"].decode("utf-8")
    except UnicodeDecodeError:
        return {"ok": False, "error": "SKILL.md 不是 UTF-8"}

    meta = _parse_skill_md(skill_md_text)
    name = meta.get("name", "").strip()
    version = meta.get("version", "").strip()
    if not name or not version:
        return {"ok": False, "error": "SKILL.md frontmatter 缺 name 或 version"}

    try:
        _validate_seg(name, "name")
        _validate_seg(version, "version")
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    target_dir = _skills_dir() / namespace / name / version
    if target_dir.exists():
        return {
            "ok": False,
            "error": f"{namespace}/{name}/{version} 已发布过, 改 version 字段后重发",
        }

    # 写文件
    target_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    try:
        for rel_path, content in files.items():
            if ".." in rel_path or rel_path.startswith("/"):
                raise ValueError(f"file 路径不合法: {rel_path}")
            target_file = target_dir / rel_path
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_bytes(content)
            written.append(rel_path)
    except Exception as e:
        # rollback
        try:
            shutil.rmtree(target_dir)
        except Exception:
            pass
        return {"ok": False, "error": f"写文件失败: {e}"}

    # BL-D2 Phase 2 (5/10): 写元数据进 PG skills_versions. 失败仍返成功 (FS
    # 已写入, 下次 list_skills FS 兜底也能扫到), 但 log warning. 真生产看 PG
    # warning 应立即报警.
    description = meta.get("description", "")[:500]
    total_bytes = sum(len(c) for c in files.values())
    content_dir_rel = f"{namespace}/{name}/{version}"
    if _use_pg():
        try:
            with _pg_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO skills_versions
                           (namespace, name, version, description, published_by,
                            content_dir, file_count, total_bytes, deprecated)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                           ON CONFLICT (namespace, name, version) DO NOTHING""",
                        (
                            namespace, name, version, description, published_by,
                            content_dir_rel, len(written), total_bytes,
                            bool(meta.get("deprecated", False)),
                        ),
                    )
        except Exception as e:
            logger.warning("publish PG 元数据写失败 (FS 已写, list_skills 仍能扫到): %s", e)

    _write_audit({
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "event": "publish",
        "namespace": namespace,
        "name": name,
        "version": version,
        "published_by": published_by,
        "files_count": len(written),
    })

    return {
        "ok": True,
        "namespace": namespace,
        "name": name,
        "version": version,
        "files_count": len(written),
        "summary": f"已发布 {namespace}/{name}/{version} ({len(written)} 个文件)",
    }


def delete_skill_version(
    namespace: str, name: str, version: str, *, deleted_by: str, reason: str = "",
) -> dict:
    """删除一个版本 (整个 version 目录). 不删整个 skill (其他版本保留)."""
    _validate_seg(namespace, "namespace")
    _validate_seg(name, "name")
    _validate_seg(version, "version")

    version_dir = _skills_dir() / namespace / name / version
    if not version_dir.is_dir():
        return {"ok": False, "error": f"{namespace}/{name}/{version} 不存在"}

    try:
        shutil.rmtree(version_dir)
    except Exception as e:
        return {"ok": False, "error": f"删除失败: {e}"}

    # 如果该 skill 已无任何 version, 把 skill 目录也清掉
    skill_dir = version_dir.parent
    try:
        if skill_dir.exists() and not any(skill_dir.iterdir()):
            skill_dir.rmdir()
    except Exception:
        pass

    # BL-D2 Phase 2 (5/10): 删 PG skills_versions row (硬删, 因为 FS 也物理删).
    # 真上线如果想保留历史可改 deprecated=true. 现在硬删跟 FS 一致.
    if _use_pg():
        try:
            with _pg_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM skills_versions "
                        "WHERE namespace=%s AND name=%s AND version=%s",
                        (namespace, name, version),
                    )
        except Exception as e:
            logger.warning("delete PG row 失败 (FS 已删): %s", e)

    _write_audit({
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "event": "delete_version",
        "namespace": namespace,
        "name": name,
        "version": version,
        "deleted_by": deleted_by,
        "reason": reason,
    })

    return {
        "ok": True,
        "summary": f"已删除 {namespace}/{name}/{version}",
    }


def read_audit(limit: int = 100) -> list[dict]:
    """读最近 N 行 audit (倒序). PG 优先, jsonl fallback (BL-D2 Phase 2 5/10)."""
    # 1. PG 优先
    if _use_pg():
        try:
            with _pg_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT ts_ms, action, namespace, name, version,
                                  by_user, meta
                           FROM skills_audit ORDER BY ts_ms DESC LIMIT %s""",
                        (limit,),
                    )
                    rows = list(cur.fetchall())
            out = []
            for r in rows:
                ts_iso = datetime.fromtimestamp(
                    r["ts_ms"] / 1000.0, tz=timezone.utc,
                ).isoformat().replace("+00:00", "Z")
                event = {
                    "ts": ts_iso,
                    "event": r["action"],
                    "namespace": r["namespace"],
                    "name": r["name"],
                    "version": r["version"],
                    "by_user": r["by_user"],
                }
                if r.get("meta"):
                    event.update(r["meta"])
                out.append(event)
            return out
        except Exception as e:
            logger.warning("读 PG audit 失败 (fallback jsonl): %s", e)

    # 2. jsonl 兜底
    path = _audit_path()
    if not path.exists():
        return []
    try:
        with path.open(encoding="utf-8") as f:
            lines = f.readlines()
    except Exception as e:
        logger.warning("读 audit 失败: %s", e)
        return []
    out = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
        if len(out) >= limit:
            break
    return out
