"""wiki-hub 文件存储 (没配 PG 时的主存储, 配了 PG 时的镜像备份).

布局:

    <hub root>/wiki/dept/<部门>/<file_id>.md          正文 (frontmatter + body)
    <hub root>/wiki/dept/<部门>/<file_id>.meta.json   元数据

9/30 之前 FS 只存 .md, 列表和详情里 published_by / updated_at / stale 全是空的:

- 没有 published_by → 撤回时的"只能撤自己发的"判定永远不成立, 谁都撤不了
- 没有 updated_at  → 装了的人判断不了有没有新版本
- 撤回是直接删文件 → 装了的人永远看不到"已撤回", 只会看到这条消失了

所以元数据单独落一个 sidecar, 撤回时留 sidecar (标 stale) 只删正文,
跟 PG 那边"留 row, 清 body"是同一个语义。
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("catfish.wiki_hub.storage_fs")


def wiki_dir() -> Path:
    """FS 根目录 — 默认 ~/.catfish-hub/wiki/, 客户内网部署改 CATFISH_HUB_ROOT."""
    root = os.environ.get("CATFISH_HUB_ROOT", "")
    if not root:
        root = str(Path.home() / ".catfish-hub")
    d = Path(root).expanduser() / "wiki"
    d.mkdir(parents=True, exist_ok=True)
    return d


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _paths(namespace: str, file_id: str) -> tuple[Path, Path]:
    d = wiki_dir() / namespace
    return d / f"{file_id}.md", d / f"{file_id}.meta.json"


def _read_meta(meta_path: Path) -> dict | None:
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.warning("读 %s 失败: %s", meta_path, e)
        return None


def _split_frontmatter(text: str) -> tuple[str, str]:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            return text[3:end].strip(), text[end + 4:].lstrip("\n")
    return "", text


def write_document(
    *,
    namespace: str,
    file_id: str,
    filename: str,
    title: str,
    kind: str,
    frontmatter_yaml: str,
    body_md: str,
    published_by: str,
    size_bytes: int,
) -> dict:
    """写正文 + 元数据. 重发时保留第一次的 published_at. 返写入后的元数据."""
    md_path, meta_path = _paths(namespace, file_id)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(
        f"---\n{frontmatter_yaml.strip()}\n---\n\n{body_md}", encoding="utf-8",
    )
    prev = _read_meta(meta_path) or {}
    ts = now_iso()
    meta = {
        "namespace": namespace,
        "file_id": file_id,
        "filename": filename,
        "title": title,
        "kind": kind,
        "description_preview": (body_md or "")[:200],
        "published_by": published_by,
        "published_at": prev.get("published_at") or ts,
        "updated_at": ts,
        "size_bytes": size_bytes,
        "stale_after_unpublish": False,
        "unpublished_at": None,
        "unpublished_reason": None,
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def _meta_for(md_or_meta_dir: Path, file_id: str, namespace: str) -> dict | None:
    """读元数据; 老数据只有 .md 没有 sidecar 时拼一个最小的."""
    meta = _read_meta(md_or_meta_dir / f"{file_id}.meta.json")
    if meta is not None:
        return meta
    md = md_or_meta_dir / f"{file_id}.md"
    if not md.exists():
        return None
    text = md.read_text(encoding="utf-8")
    _, body = _split_frontmatter(text)
    return {
        "namespace": namespace,
        "file_id": file_id,
        "filename": file_id,
        "title": file_id,
        "kind": "entity",
        "description_preview": body[:200],
        "published_by": "",
        "published_at": None,
        "updated_at": None,
        "size_bytes": len(text.encode("utf-8")),
        "stale_after_unpublish": False,
        "unpublished_at": None,
        "unpublished_reason": None,
    }


def list_documents(namespaces: list[str] | None, *, include_stale: bool) -> list[dict]:
    root = wiki_dir() / "dept"
    if namespaces is None:
        dirs = [d for d in root.iterdir() if d.is_dir()] if root.is_dir() else []
    else:
        dirs = [wiki_dir() / ns for ns in namespaces]
    out: list[dict] = []
    for d in dirs:
        if not d.is_dir():
            continue
        namespace = f"dept/{d.name}"
        ids = {p.name[: -len(".meta.json")] for p in d.glob("*.meta.json")}
        ids |= {p.stem for p in d.glob("*.md")}
        for file_id in sorted(ids):
            meta = _meta_for(d, file_id, namespace)
            if meta is None:
                continue
            if not include_stale and meta.get("stale_after_unpublish"):
                continue
            out.append(meta)
    out.sort(key=lambda m: m.get("updated_at") or "", reverse=True)
    return out


def get_document(namespace: str, file_id: str) -> dict | None:
    md_path, _ = _paths(namespace, file_id)
    meta = _meta_for(md_path.parent, file_id, namespace)
    if meta is None:
        return None
    doc = dict(meta)
    fm_yaml, body = "", ""
    if md_path.exists() and not meta.get("stale_after_unpublish"):
        fm_yaml, body = _split_frontmatter(md_path.read_text(encoding="utf-8"))
    doc["frontmatter_yaml"] = fm_yaml
    doc["body_md"] = body
    return doc


def unpublish_document(namespace: str, file_id: str, *, reason: str) -> bool:
    """删正文, 元数据标 stale. 返是否真撤了 (已撤过 / 不存在 → False)."""
    md_path, meta_path = _paths(namespace, file_id)
    meta = _meta_for(md_path.parent, file_id, namespace)
    if meta is None or meta.get("stale_after_unpublish"):
        return False
    meta.update({
        "stale_after_unpublish": True,
        "description_preview": "[已被原作者撤回]",
        "unpublished_at": now_iso(),
        "unpublished_reason": reason,
        "size_bytes": 0,
    })
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    if md_path.exists():
        md_path.unlink()
    return True
