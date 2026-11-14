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
    "hub_updated_at": "2026-06-10T...",  # 装的是 hub 上哪个版本 (9/30 加)
    "installed_at": "2026-06-10T...",
    "size_bytes": 1234
  }

stale / 更新检查在 Companion 知识库卡片里做: hub list 的 updated_at 比本机
hub_updated_at 新 → 显"有更新", 点更新就是再调一次本工具覆盖安装.

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
from .wiki_publish import wiki_doc_url

logger = logging.getLogger("catfish.tool_bridge.wiki_install")


GATEWAY_URL = skill_publish.GATEWAY_URL
# 11/08: 跟搜索 / 读取同一个根 (CATFISH_HOME 设了就用它; 以前这里写死 ~/.catfish, 装进来的页搜不到)
from .wiki_files import _catfish_home as _home  # noqa: E402


def _shared_root() -> Path:
    return _home() / "wiki-shared"


def _audit_path() -> Path:
    return _home() / "wiki_install_audit.jsonl"


def _read_id_token() -> str | None:
    return skill_publish._read_id_token()


def _validate_ns_and_id(namespace: str, file_id: str) -> str | None:
    """返 None 表 OK, 否则返 error msg."""
    # 部门名来自身份里的 department, 可以是中文; 只挡路径 / URL 上会出事的字符
    # (规则跟 central/wiki-hub/.../namespaces.py 的 validate_dept 一致)
    dept = namespace[len("dept/"):] if namespace.startswith("dept/") else ""
    if (
        not dept
        or dept != dept.strip()
        or len(dept) > 64
        or dept.startswith(".")
        or ".." in dept
        or any(c in '/\\<>:"|?*#%' or ord(c) < 32 for c in dept)
    ):
        return f"namespace 不合法 (期望 dept/<部门>): {namespace!r}"
    if not re.fullmatch(r"[a-zA-Z0-9_\-]{6,64}", file_id):
        return f"file_id 不合法 (期望 6-64 char alphanumeric): {file_id!r}"
    return None


#: 11/08 分享治理: hub 返回的审核字段, 原样记进 .meta.json (docs/SHARE-GOVERNANCE-PLAN.md §3.2)
_REVIEW_KEYS = ("review_status", "reviewed_by", "reviewed_at", "review_note", "withdrawn_by", "withdrawn_at", "legacy",
                "scope", "visible_to")   # 11/09 全公司范围: 装的时候这页对谁可见


def _review_fields(doc: dict[str, Any]) -> dict[str, Any]:
    out = {k: doc[k] for k in _REVIEW_KEYS if doc.get(k) not in (None, "")}
    out["review_status"] = str(doc.get("review_status") or "approved")   # 老 hub / 老页面没这个字段 = 视作已审
    return out


def _set_frontmatter_field(text: str, key: str, value: str) -> str:
    """frontmatter 里设一个顶层 `key: value` (有就替换, 没有就加在闭合 --- 前); 没 frontmatter 就补一个。"""
    if not text.startswith("---\n"):
        return f"---\n{key}: {value}\n---\n\n{text}"
    end = text.find("\n---", 4)
    if end < 0:
        return text
    lines = text[4:end].split("\n")
    kept = [ln for ln in lines if not ln.startswith(f"{key}:")]
    kept.append(f"{key}: {value}")
    return "---\n" + "\n".join(kept) + text[end:]


def _mark_local_withdrawn(target_dir: Path, file_id: str, doc: dict[str, Any]) -> dict[str, Any]:
    """主管 / 发布人撤下了: 员工本机那份不删 (公理 4), 只标 review_status: withdrawn (搜索默认不列, 卡片显「已撤回」)。"""
    md = target_dir / f"{file_id}.md"
    meta_path = target_dir / f"{file_id}.meta.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    except (OSError, ValueError):
        meta = {}
    try:
        before = md.read_text(encoding="utf-8")
        after = _set_frontmatter_field(before, "review_status", "withdrawn")
        md.write_text(after, encoding="utf-8")
    except OSError as e:
        return {"ok": False, "error": f"标记本机副本失败: {e}"}
    if meta.get("local_sha256") == _sha(before):   # 11/14: 是我们自己改的那一行, 别让下次更新误以为员工改过
        meta["local_sha256"] = _sha(after)
    meta.update(_review_fields(doc), review_status="withdrawn")
    if doc.get("unpublished_reason") and not meta.get("review_note"):
        meta["review_note"] = doc["unpublished_reason"]
    try:
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        logger.warning("写 install meta 失败 (不阻塞): %s", e)
    _write_install_audit({"ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "event": "withdrawn",
                          "namespace": doc.get("namespace"), "file_id": file_id, "by": doc.get("withdrawn_by")})
    return {"ok": True, "withdrawn": True, "installed_path": str(md), "review_status": "withdrawn",
            "review_note": meta.get("review_note") or "",
            "summary": f"这页已被{'主管' if doc.get('withdrawn_by') else '发布人'}撤回. 你本机的副本没删, 只标成「已撤回」: "
                       "搜索默认不再列出, 知识库页面上能看到标记; 不想留就自己删。"}


def _write_install_audit(event: dict) -> None:
    """append 一行 audit. 失败静默."""
    try:
        _audit_path().parent.mkdir(parents=True, exist_ok=True)
        with open(_audit_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("写 wiki_install_audit.jsonl 失败: %s", e)


def _install_linked(namespace: str, file_id: str, published_by: str, frontmatter_yaml: str, body_md: str) -> list[str]:
    """11/09: 装一页时, 同一个人分享的、这页关联到的页 (关系 + [[原件 · 名称]]) 一起装上, 只装一层。

    不这么做的话, 同事装了「市场部」, 它关联的「中电福富」、来源「原件 · 福富组织架构」都在中央上,
    他本机却没有 —— 点哪个都是死链, 或者按名字连到他自己的同名条目上 (内容可能完全不同)。
    只认同一个发布人的同名页: 别人分享的同名页不算 (那是另一份东西)。拉不到列表 / 装失败都不影响这一页。"""
    from .wiki_files import _merge_related_with_body, _parse_related  # noqa: PLC0415
    from .wiki_share import _norm  # noqa: PLC0415
    names = {_norm(str(r["name"])) for r in _merge_related_with_body(_parse_related(frontmatter_yaml), body_md)}
    names.discard("")
    if not names or not published_by or published_by == "?":
        return []
    token = _read_id_token()
    if not token:
        return []
    import httpx  # noqa: PLC0415
    try:
        with httpx.Client(timeout=15.0) as client:
            r = client.get(f"{GATEWAY_URL}/v1/wiki/documents?include_stale=false", headers={"Authorization": f"Bearer {token}"})
        docs = (r.json().get("documents") or []) if r.status_code == 200 else []
    except Exception as e:  # noqa: BLE001
        logger.info("装关联页: 拉部门知识库列表失败, 跳过: %s", e)
        return []
    out: list[str] = []
    for d in docs:
        fid = str(d.get("file_id") or "")
        if (d.get("namespace") != namespace or fid == file_id or d.get("published_by") != published_by
                or d.get("stale_after_unpublish") or _norm(str(d.get("title") or "")) not in names):
            continue
        if (_shared_root() / namespace / f"{fid}.md").exists():
            continue
        res = wiki_install({"hub_namespace": namespace, "hub_file_id": fid, "_linked": True})
        if res.get("ok"):
            out.append(str(d.get("title") or fid))
    return out


def _sha(text: str) -> str:
    import hashlib  # noqa: PLC0415
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _backup_local_edits(target_dir: Path, file_id: str, new_text: str) -> str | None:
    """11/14: 「拉更新」会整页覆盖本机副本。本机副本跟上次装进来的不一样 (员工或小鲶改过) 就先备份,
    备份在 ~/.catfish/backups/wiki-shared/<ns>/<file_id>-<时间>.md (不放进 wiki-shared, 免得搜索里出现两份)。
    老安装没记 local_sha256 → 判断不了改没改, 跟新内容不一样就备份。返备份路径, 没备份返 None。"""
    md = target_dir / f"{file_id}.md"
    if not md.exists():
        return None
    try:
        cur = md.read_text(encoding="utf-8")
        recorded = (json.loads((target_dir / f"{file_id}.meta.json").read_text(encoding="utf-8")) or {}).get("local_sha256")
    except (OSError, ValueError):
        cur, recorded = (md.read_text(encoding="utf-8") if md.exists() else ""), None
    if cur == new_text or (recorded and recorded == _sha(cur)):
        return None
    ns = target_dir.relative_to(_shared_root())
    dest = _home() / "backups" / "wiki-shared" / ns / f"{file_id}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(cur, encoding="utf-8")
    except OSError as e:
        logger.warning("备份本机改动失败 (照样更新): %s", e)
        return None
    return str(dest)


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

    url = wiki_doc_url(namespace, file_id)
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(url, headers={"Authorization": f"Bearer {token}"})
    except httpx.RequestError as e:
        return {"ok": False, "error": f"调 gateway 失败: {e!r}"}

    if resp.status_code == 404:
        return {"ok": False, "error": f"wiki {namespace}/{file_id} 不存在"}
    if resp.status_code == 403:
        return {"ok": False, "error": f"{namespace} 不是你的部门, 不能安装"}
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

    target_dir = _shared_root() / namespace
    target_md = target_dir / f"{file_id}.md"
    review = _review_fields(doc)

    # Stale / 已撤回 — 内容已清零, 不让装; 11/08: 本机已有副本的 (「拉更新」) 只标「已撤回」, 不删员工的文件
    if doc.get("stale_after_unpublish") or review["review_status"] == "withdrawn":
        if target_md.exists():
            return _mark_local_withdrawn(target_dir, file_id, {**doc, "namespace": namespace})
        return {
            "ok": False,
            "error": (
                f"wiki {namespace}/{file_id} 已被{'主管' if doc.get('withdrawn_by') else '原作者'}撤回, "
                "中央 body 已清零, 不能 install. "
                f"原 publisher: {doc.get('published_by')}, "
                f"撤回时间: {doc.get('withdrawn_at') or doc.get('unpublished_at')}"
                + (f", 原因: {doc.get('review_note') or doc.get('unpublished_reason')}" if (doc.get('review_note') or doc.get('unpublished_reason')) else "")
            ),
        }

    filename = doc.get("filename") or file_id
    title = doc.get("title") or file_id
    kind = doc.get("kind") or "entity"
    frontmatter_yaml = doc.get("frontmatter_yaml") or ""
    body_md = doc.get("body_md") or ""
    published_by = doc.get("published_by") or "?"
    published_at = doc.get("published_at") or ""
    hub_updated_at = doc.get("updated_at") or ""

    # 写本机 ~/.catfish/wiki-shared/<ns>/<file_id>.md (frontmatter 带 review_status, 搜索 / 页面按它标「待审」)
    target_dir.mkdir(parents=True, exist_ok=True)

    full_text = _set_frontmatter_field(f"---\n{frontmatter_yaml.strip()}\n---\n\n{body_md}", "review_status", review["review_status"])
    backup = _backup_local_edits(target_dir, file_id, full_text)
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
        "hub_updated_at": hub_updated_at,
        "installed_at": now_iso,
        "size_bytes": len(full_text.encode("utf-8")),
        "local_sha256": _sha(full_text),   # 11/14: 下次更新前比一下, 本机改过就先备份
        **review,
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

    backup_note = f" 本机原来那份跟中央上次的不一样, 已备份到 {backup}." if backup else ""
    pending_note = " 这页主管还没审过 (标「待审」), 内容以主管审过为准." if review["review_status"] == "pending" else ""
    also = [] if args.get("_linked") else _install_linked(namespace, file_id, published_by, frontmatter_yaml, body_md)
    linked_note = f" 它关联的 {len(also)} 页 ({'、'.join(also[:5])}{' 等' if len(also) > 5 else ''}) 也一起装上了." if also else ""
    return {
        "ok": True,
        "installed_path": str(target_md),
        "namespace": namespace,
        "file_id": file_id,
        "title": title,
        "kind": kind,
        "published_by": published_by,
        "review_status": review["review_status"],
        "also_installed": also,
        **({"local_backup": backup} if backup else {}),
        "summary": (
            f"已装 wiki '{title}' (by {published_by}) 到 ~/.catfish/wiki-shared/"
            f"{namespace}/{file_id}.md. "
            f"在 Companion 资料库下 '📥 部门 wiki' 分组能看到 (read-only).{pending_note}{linked_note}{backup_note}"
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

    url = wiki_doc_url(namespace, file_id, "/unpublish")
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
