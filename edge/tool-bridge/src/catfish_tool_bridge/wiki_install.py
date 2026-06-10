"""catfish_wiki_install / catfish_wiki_unpublish 工具实现 (P3.3.18 Phase 2, 6/10).

# install

从 wiki-hub 拉一条部门 wiki 装到员工本机:
  ~/.catfish/wiki-shared/<namespace>/<file_id>.md

跟个人 wiki (~/.catfish/wiki/entities|concepts|queries/) 隔离, 防互相污染.
WikiTree (Phase 3) 单独显 "📥 部门 wiki" 分组, read-only.

并写 install_meta:
  ~/.catfish/wiki-shared/<namespace>/<file_id>.meta.json
  {
    "file_id": "...",
    "namespace": "dept/finance",
    "filename": "wiki/entities/老李.md",  # 原 publisher 本机的 rel_path
    "title": "...",
    "kind": "entity",
    "published_by": "<原作者 sub>",
    "published_at": "2026-06-10T...",
    "installed_at": "2026-06-10T...",
    "size_bytes": 1234
  }

stale 检查: Companion 周期 fetch hub list, 看 stale_after_unpublish=true 的, 在
对应本机文件加 ".stale" sidecar 标记. (本工具不做 stale 检查, 留 Phase 4 polish.)

# unpublish

让员工撤回自己 publish 的某条 wiki. Manifesto 公理 4 兼容:
- 中央 PG row 保留 (audit), body 清零 + 标 stale_after_unpublish=true
- 中央 FS 镜像物理删
- 已 pull 副本不动 (manifesto 禁中央触及员工本机)
- 已 pull 员工下次拉 hub list 时看到 stale=true, 自己决定卸不卸
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import skill_publish  # 复用 GATEWAY_URL / OAUTH_ID_TOKEN_PATH / _read_id_token

logger = logging.getLogger("catfish.tool_bridge.wiki_install")


GATEWAY_URL = skill_publish.GATEWAY_URL
WIKI_SHARED_ROOT = Path.home() / ".catfish" / "wiki-shared"
WIKI_INSTALL_AUDIT = Path.home() / ".catfish" / "wiki_install_audit.jsonl"


def _read_id_token() -> str | None:
    return skill_publish._read_id_token()


def _validate_ns_and_id(namespace: str, file_id: str) -> str | None:
    """返 None 表 OK, 否则返 error msg."""
    if not re.fullmatch(r"dept/[a-z][a-z0-9_-]{0,40}", namespace):
        return f"namespace 不合法 (期望 dept/<部门>): {namespace!r}"
    if not re.fullmatch(r"[a-zA-Z0-9_\-]{6,64}", file_id):
        return f"file_id 不合法 (期望 6-64 char alphanumeric): {file_id!r}"
    return None


def _write_install_audit(event: dict) -> None:
    """append 一行 audit. 失败静默."""
    try:
        WIKI_INSTALL_AUDIT.parent.mkdir(parents=True, exist_ok=True)
        with open(WIKI_INSTALL_AUDIT, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("写 wiki_install_audit.jsonl 失败: %s", e)


def wiki_install(args: dict[str, Any]) -> dict[str, Any]:
    """tool entry — 从 wiki-hub 拉一条 wiki 装本机.

    参数:
      hub_namespace (必): dept/finance
      hub_file_id (必): file_id (服务端分配的 UUID)

    成功返:
      {ok:true, installed_path, namespace, file_id, title, summary}
    失败返:
      {ok:false, error}
    """
    namespace = (args.get("hub_namespace") or "").strip()
    file_id = (args.get("hub_file_id") or "").strip()

    err = _validate_ns_and_id(namespace, file_id)
    if err:
        return {"ok": False, "error": err}

    # GET hub 拿 doc
    token = _read_id_token()
    if not token:
        return {
            "ok": False,
            "error": f"OAuth id_token 不存在. 在 Companion 登录走 OIDC 流后重试.",
        }

    import httpx  # 懒 import

    url = f"{GATEWAY_URL}/v1/wiki/documents/{namespace}/{file_id}"
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(url, headers={"Authorization": f"Bearer {token}"})
    except httpx.RequestError as e:
        return {"ok": False, "error": f"调 gateway 失败: {e!r}"}

    if resp.status_code == 404:
        return {"ok": False, "error": f"wiki {namespace}/{file_id} 不存在"}
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text[:300]
        return {"ok": False, "error": f"gateway {resp.status_code}: {detail}"}

    try:
        doc = resp.json()
    except json.JSONDecodeError:
        return {"ok": False, "error": f"gateway 返非 json: {resp.text[:200]}"}

    # Stale 检查 — 原作者已撤回, 内容已清零, 不让装
    if doc.get("stale_after_unpublish"):
        return {
            "ok": False,
            "error": (
                f"wiki {namespace}/{file_id} 已被原作者撤回 (stale), "
                "中央 body 已清零, 不能 install. "
                f"原 publisher: {doc.get('published_by')}, "
                f"撤回时间: {doc.get('unpublished_at')}"
            ),
        }

    filename = doc.get("filename") or file_id
    title = doc.get("title") or file_id
    kind = doc.get("kind") or "entity"
    frontmatter_yaml = doc.get("frontmatter_yaml") or ""
    body_md = doc.get("body_md") or ""
    published_by = doc.get("published_by") or "?"
    published_at = doc.get("published_at") or ""

    # 写本机 ~/.catfish/wiki-shared/<ns>/<file_id>.md
    target_dir = WIKI_SHARED_ROOT / namespace
    target_dir.mkdir(parents=True, exist_ok=True)
    target_md = target_dir / f"{file_id}.md"

    full_text = f"---\n{frontmatter_yaml.strip()}\n---\n\n{body_md}"
    try:
        target_md.write_text(full_text, encoding="utf-8")
    except Exception as e:
        return {"ok": False, "error": f"写本机 wiki 文件失败: {e}"}

    # 写 install meta sidecar
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    meta = {
        "file_id": file_id,
        "namespace": namespace,
        "filename": filename,
        "title": title,
        "kind": kind,
        "published_by": published_by,
        "published_at": published_at,
        "installed_at": now_iso,
        "size_bytes": len(full_text.encode("utf-8")),
    }
    meta_path = target_dir / f"{file_id}.meta.json"
    try:
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8",
        )
    except Exception as e:
        logger.warning("写 install meta 失败 (不阻塞): %s", e)

    _write_install_audit({
        "ts": now_iso,
        "event": "install",
        "namespace": namespace,
        "file_id": file_id,
        "title": title,
        "published_by": published_by,
        "installed_path": str(target_md),
    })

    return {
        "ok": True,
        "installed_path": str(target_md),
        "namespace": namespace,
        "file_id": file_id,
        "title": title,
        "kind": kind,
        "published_by": published_by,
        "summary": (
            f"已装 wiki '{title}' (by {published_by}) 到 ~/.catfish/wiki-shared/"
            f"{namespace}/{file_id}.md. "
            f"在 Companion 知识体系 tab 下 '📥 部门 wiki' 分组能看到 (read-only)."
        ),
    }


def wiki_unpublish(args: dict[str, Any]) -> dict[str, Any]:
    """tool entry — 撤回自己 publish 的某条 wiki.

    参数:
      hub_namespace (必): dept/finance
      hub_file_id (必): 撤回的 file_id
      reason (可选): 原因, 写入 audit

    成功返:
      {ok:true, summary}
    """
    namespace = (args.get("hub_namespace") or "").strip()
    file_id = (args.get("hub_file_id") or "").strip()
    reason = (args.get("reason") or "").strip()

    err = _validate_ns_and_id(namespace, file_id)
    if err:
        return {"ok": False, "error": err}

    token = _read_id_token()
    if not token:
        return {"ok": False, "error": "OAuth id_token 不存在, 在 Companion 登录后重试."}

    import httpx  # 懒 import

    url = f"{GATEWAY_URL}/v1/wiki/documents/{namespace}/{file_id}/unpublish"
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                content=json.dumps({"reason": reason}, ensure_ascii=False).encode("utf-8"),
            )
    except httpx.RequestError as e:
        return {"ok": False, "error": f"调 gateway 失败: {e!r}"}

    if resp.status_code == 403:
        return {
            "ok": False,
            "error": "权限不够 — 你只能撤回自己 publish 的 wiki (admin 例外).",
        }
    if resp.status_code == 404:
        return {"ok": False, "error": f"wiki {namespace}/{file_id} 不存在或已撤回"}
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text[:300]
        return {"ok": False, "error": f"gateway {resp.status_code}: {detail}"}

    try:
        data = resp.json()
    except json.JSONDecodeError:
        return {"ok": False, "error": f"gateway 返非 json: {resp.text[:200]}"}

    return {
        "ok": True,
        "summary": data.get("summary") or (
            f"已撤回 {namespace}/{file_id}. 中央 body 已清零 + 标 stale. "
            "已 pull 副本不受影响 (manifesto 公理 4)."
        ),
        "manifesto_note": (
            "公理 4 兼容设计 — 已 pull 副本在其他员工本机仍存在, 中央无法触及. "
            "他们下次拉 hub list 时看到 stale 标, 自己决定怎么处理."
        ),
    }
