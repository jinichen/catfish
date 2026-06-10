"""wiki-hub storage — PG 优先, FS fallback (P3.3.18, 6/10).

跟 skills-hub storage.py 同模式. 但服务 wiki markdown 笔记不是 skill 包,
有几个关键差异:

1. **没有 version 概念**: wiki 改了就直接 update (覆盖 body_md + updated_at).
   skill 是工具包要保留历史版本, wiki 是知识笔记, 历史版本意义低.
2. **没有 file 目录**: wiki 就是单 markdown 文件, frontmatter + body 都进 PG
   text 字段. 也镜像写一份到 FS `~/.catfish-hub/wiki/{ns}/{file_id}.md` 作 backup.
3. **unpublish 不硬删**: 公理 4 兼容设计 — 标 stale=true + 清 body_md, 留 row
   做 audit history. 客户端 fetch 时看到 stale, 不能再 install (body 已空).
4. **撤回不动员工本机**: manifesto 公理 3/4 禁止, 客户端自己显 stale 标决定怎么办.

PG 字段见 alembic/versions/202606101200_init_wiki.py.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("catfish.wiki_hub.storage")


# ── PG 跟 FS 路径 ─────────────────────────────────────────────


def _wiki_dir() -> Path:
    """FS 根目录 — 默认 ~/.catfish-hub/wiki/, 客户内网部署改 CATFISH_HUB_ROOT.
    跟 skills-hub 同一 hub root 下并列."""
    root = os.environ.get("CATFISH_HUB_ROOT", "")
    if not root:
        root = str(Path.home() / ".catfish-hub")
    d = Path(root).expanduser() / "wiki"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _audit_jsonl_path() -> Path:
    return _wiki_dir().parent / "wiki_audit.jsonl"


def _use_pg() -> bool:
    return bool(os.environ.get("CATFISH_DB_URL", "").strip())


def _pg_conn():
    import psycopg  # noqa: PLC0415  懒 import
    url = os.environ.get("CATFISH_DB_URL", "").strip()
    if url.startswith("postgresql+psycopg://"):
        url = "postgresql://" + url[len("postgresql+psycopg://"):]
    return psycopg.connect(url)


def _validate_seg(seg: str, name: str) -> None:
    """ns / file_id 不允许特殊字符, 防 path traversal."""
    if not seg:
        raise ValueError(f"{name} 不能空")
    if ".." in seg or "/" in seg or "\\" in seg:
        raise ValueError(f"{name} 不允许特殊字符: {seg!r}")
    if len(seg) > 200:
        raise ValueError(f"{name} 太长: {len(seg)}")


def _write_audit(event: dict) -> None:
    """audit 写 PG (优先) + jsonl fallback. 失败不阻塞."""
    if _use_pg():
        try:
            with _pg_conn() as conn:
                with conn.cursor() as cur:
                    ts_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
                    cur.execute(
                        """INSERT INTO wiki_audit
                           (ts_ms, action, namespace, file_id, by_user, meta)
                           VALUES (%s, %s, %s, %s, %s, %s::jsonb)""",
                        (
                            ts_ms,
                            event.get("event", ""),
                            event.get("namespace"),
                            event.get("file_id"),
                            event.get("by_user", ""),
                            json.dumps({
                                k: v for k, v in event.items()
                                if k not in {"event", "namespace", "file_id", "by_user", "ts"}
                            }),
                        ),
                    )
        except Exception as e:
            logger.warning("audit 写 PG 失败 (jsonl fallback): %s", e)

    # jsonl 兜底, dev 友好
    try:
        line = json.dumps(event, ensure_ascii=False)
        with open(_audit_jsonl_path(), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        logger.warning("audit 写 jsonl 失败: %s", e)


# ── publish ────────────────────────────────────────────────────


def publish_document(
    namespace: str,
    file_id: str,
    filename: str,
    title: str,
    kind: str,
    frontmatter_yaml: str,
    body_md: str,
    *,
    published_by: str,
) -> dict:
    """员工 publish / re-publish 一条 wiki. 同 namespace + file_id 已存在 → update.

    返 {ok, namespace, file_id, published_at, error?}
    """
    _validate_seg(namespace, "namespace")
    _validate_seg(file_id, "file_id")

    if kind not in ("entity", "concept", "query"):
        return {"ok": False, "error": f"kind 必须 entity/concept/query, 拿到 {kind!r}"}

    if len(body_md) > 1024 * 1024:  # 1 MB 单 wiki 上限
        return {"ok": False, "error": f"wiki body 超 1MB 上限 ({len(body_md)} bytes)"}

    description_preview = (body_md or "")[:200]
    size_bytes = len(body_md.encode("utf-8")) + len(frontmatter_yaml.encode("utf-8"))
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    # 1. FS 镜像 (备份)
    target_path = _wiki_dir() / namespace / f"{file_id}.md"
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        full_text = f"---\n{frontmatter_yaml.strip()}\n---\n\n{body_md}"
        target_path.write_text(full_text, encoding="utf-8")
    except Exception as e:
        return {"ok": False, "error": f"FS 写失败: {e}"}

    # 2. PG upsert (元数据 + 全文)
    if _use_pg():
        try:
            with _pg_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO wiki_documents
                           (namespace, file_id, filename, title, kind,
                            description_preview, frontmatter_yaml, body_md,
                            published_by, size_bytes,
                            stale_after_unpublish, unpublished_at, unpublished_reason)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                   false, NULL, NULL)
                           ON CONFLICT (namespace, file_id) DO UPDATE SET
                               filename = EXCLUDED.filename,
                               title = EXCLUDED.title,
                               kind = EXCLUDED.kind,
                               description_preview = EXCLUDED.description_preview,
                               frontmatter_yaml = EXCLUDED.frontmatter_yaml,
                               body_md = EXCLUDED.body_md,
                               size_bytes = EXCLUDED.size_bytes,
                               updated_at = NOW(),
                               stale_after_unpublish = false,
                               unpublished_at = NULL,
                               unpublished_reason = NULL
                        """,
                        (
                            namespace, file_id, filename, title, kind,
                            description_preview, frontmatter_yaml, body_md,
                            published_by, size_bytes,
                        ),
                    )
        except Exception as e:
            logger.warning("publish PG upsert 失败 (FS 已写): %s", e)

    _write_audit({
        "ts": now_iso,
        "event": "publish",
        "namespace": namespace,
        "file_id": file_id,
        "filename": filename,
        "title": title,
        "kind": kind,
        "by_user": published_by,
        "size_bytes": size_bytes,
    })

    return {
        "ok": True,
        "namespace": namespace,
        "file_id": file_id,
        "published_at": now_iso,
        "size_bytes": size_bytes,
    }


# ── list / get ────────────────────────────────────────────────


def list_documents(
    namespace_filter: str | None = None,
    *,
    include_stale: bool = True,
) -> list[dict]:
    """列已发布 wiki. include_stale=true 时 stale 项也返 (UI 显灰色 + warning).

    返 [{namespace, file_id, filename, title, kind, description_preview,
         published_by, published_at, updated_at, size_bytes,
         stale_after_unpublish, unpublished_at, unpublished_reason}]
    """
    if _use_pg():
        try:
            with _pg_conn() as conn:
                with conn.cursor() as cur:
                    where = []
                    params: list = []
                    if namespace_filter:
                        where.append("namespace = %s")
                        params.append(namespace_filter)
                    if not include_stale:
                        where.append("stale_after_unpublish = false")
                    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
                    cur.execute(
                        f"""SELECT namespace, file_id, filename, title, kind,
                                  description_preview, published_by,
                                  published_at, updated_at, size_bytes,
                                  stale_after_unpublish, unpublished_at,
                                  unpublished_reason
                           FROM wiki_documents
                           {where_sql}
                           ORDER BY updated_at DESC
                           LIMIT 1000""",
                        params,
                    )
                    rows = cur.fetchall()
                    return [
                        {
                            "namespace": r[0],
                            "file_id": r[1],
                            "filename": r[2],
                            "title": r[3],
                            "kind": r[4],
                            "description_preview": r[5],
                            "published_by": r[6],
                            "published_at": r[7].isoformat() if r[7] else None,
                            "updated_at": r[8].isoformat() if r[8] else None,
                            "size_bytes": r[9],
                            "stale_after_unpublish": r[10],
                            "unpublished_at": r[11].isoformat() if r[11] else None,
                            "unpublished_reason": r[12],
                        }
                        for r in rows
                    ]
        except Exception as e:
            logger.warning("list PG 失败 (FS fallback): %s", e)

    # FS fallback — 扫 hub root, frontmatter 解析浅
    out: list[dict] = []
    root = _wiki_dir()
    if namespace_filter:
        ns_dirs = [root / namespace_filter]
    else:
        ns_dirs = [d for d in root.iterdir() if d.is_dir()]
    for ns_dir in ns_dirs:
        if not ns_dir.exists():
            continue
        for md in ns_dir.glob("*.md"):
            try:
                text = md.read_text(encoding="utf-8")
            except Exception:
                continue
            file_id = md.stem
            out.append({
                "namespace": ns_dir.name,
                "file_id": file_id,
                "filename": file_id,
                "title": file_id,
                "kind": "entity",  # fallback 不解析
                "description_preview": text[:200],
                "published_by": "",
                "published_at": None,
                "updated_at": None,
                "size_bytes": len(text.encode("utf-8")),
                "stale_after_unpublish": False,
                "unpublished_at": None,
                "unpublished_reason": None,
            })
    return out


def get_document(namespace: str, file_id: str) -> dict | None:
    """拉单条 wiki 元 + 完整 body. stale 也返, 但客户端按 stale_after_unpublish=true
    判断不要装."""
    _validate_seg(namespace, "namespace")
    _validate_seg(file_id, "file_id")

    if _use_pg():
        try:
            with _pg_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT namespace, file_id, filename, title, kind,
                                  description_preview, frontmatter_yaml, body_md,
                                  published_by, published_at, updated_at, size_bytes,
                                  stale_after_unpublish, unpublished_at, unpublished_reason
                           FROM wiki_documents
                           WHERE namespace = %s AND file_id = %s""",
                        (namespace, file_id),
                    )
                    r = cur.fetchone()
                    if not r:
                        return None
                    return {
                        "namespace": r[0],
                        "file_id": r[1],
                        "filename": r[2],
                        "title": r[3],
                        "kind": r[4],
                        "description_preview": r[5],
                        "frontmatter_yaml": r[6],
                        "body_md": r[7],
                        "published_by": r[8],
                        "published_at": r[9].isoformat() if r[9] else None,
                        "updated_at": r[10].isoformat() if r[10] else None,
                        "size_bytes": r[11],
                        "stale_after_unpublish": r[12],
                        "unpublished_at": r[13].isoformat() if r[13] else None,
                        "unpublished_reason": r[14],
                    }
        except Exception as e:
            logger.warning("get PG 失败 (FS fallback): %s", e)

    # FS fallback
    path = _wiki_dir() / namespace / f"{file_id}.md"
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None

    # 浅解 frontmatter
    fm_yaml = ""
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            fm_yaml = text[3:end].strip()
            body = text[end + 4:].lstrip("\n")

    return {
        "namespace": namespace,
        "file_id": file_id,
        "filename": file_id,
        "title": file_id,
        "kind": "entity",
        "description_preview": body[:200],
        "frontmatter_yaml": fm_yaml,
        "body_md": body,
        "published_by": "",
        "published_at": None,
        "updated_at": None,
        "size_bytes": len(text.encode("utf-8")),
        "stale_after_unpublish": False,
        "unpublished_at": None,
        "unpublished_reason": None,
    }


# ── unpublish (P3.3.18: manifesto 公理 4 兼容 — 标 stale, 留 audit row) ──


def unpublish_document(
    namespace: str,
    file_id: str,
    *,
    unpublished_by: str,
    reason: str = "",
) -> dict:
    """员工撤回. 公理 4 兼容设计:

    - PG row 保留 (audit history 需要), 但:
      - 标 stale_after_unpublish=true
      - 清 body_md / frontmatter_yaml (隐私清零)
      - 标 unpublished_at / unpublished_reason
    - FS 镜像物理删 (FS 是 backup, 没保留必要)
    - 已 pull 员工本机副本: 不动 (manifesto 公理 3/4 禁止)
      客户端下次 list_documents 看到 stale=true, 自己决定怎么办.
    """
    _validate_seg(namespace, "namespace")
    _validate_seg(file_id, "file_id")

    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    # PG 标 stale + 清内容
    affected = 0
    if _use_pg():
        try:
            with _pg_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE wiki_documents
                           SET stale_after_unpublish = true,
                               body_md = '',
                               frontmatter_yaml = '',
                               description_preview = '[已被原作者撤回]',
                               unpublished_at = NOW(),
                               unpublished_reason = %s,
                               size_bytes = 0
                           WHERE namespace = %s AND file_id = %s
                             AND stale_after_unpublish = false""",
                        (reason, namespace, file_id),
                    )
                    affected = cur.rowcount
        except Exception as e:
            logger.warning("unpublish PG 失败: %s", e)

    # FS 删
    path = _wiki_dir() / namespace / f"{file_id}.md"
    if path.exists():
        try:
            path.unlink()
        except Exception as e:
            logger.warning("unpublish FS 删失败: %s", e)

    if affected == 0 and not path.exists():
        return {"ok": False, "error": f"{namespace}/{file_id} 不存在或已撤回"}

    _write_audit({
        "ts": now_iso,
        "event": "unpublish",
        "namespace": namespace,
        "file_id": file_id,
        "by_user": unpublished_by,
        "reason": reason,
    })

    return {
        "ok": True,
        "summary": (
            f"已撤回 {namespace}/{file_id}. 中央 row 留作 audit, body 已清零, "
            f"stale 标已置. 已 pull 副本不受影响 (manifesto 公理 4)."
        ),
    }


# ── audit ─────────────────────────────────────────────────────


def read_audit(limit: int = 100) -> list[dict]:
    """读最近 N 行 audit (倒序). PG 优先, jsonl fallback."""
    if _use_pg():
        try:
            with _pg_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT ts_ms, action, namespace, file_id, by_user, meta
                           FROM wiki_audit ORDER BY ts_ms DESC LIMIT %s""",
                        (limit,),
                    )
                    rows = cur.fetchall()
                    return [
                        {
                            "ts_ms": r[0],
                            "action": r[1],
                            "namespace": r[2],
                            "file_id": r[3],
                            "by_user": r[4],
                            "meta": r[5],
                        }
                        for r in rows
                    ]
        except Exception as e:
            logger.warning("read_audit PG 失败 (jsonl fallback): %s", e)

    out: list[dict] = []
    p = _audit_jsonl_path()
    if not p.exists():
        return []
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return []
    out.reverse()
    return out[:limit]
