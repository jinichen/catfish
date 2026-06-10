"""Wiki Hub FastAPI (P3.3.18, 6/10).

# 端点

  GET  /healthz                                      健康
  GET  /wiki/documents?namespace=&include_stale=     列已发布 wiki
  GET  /wiki/documents/{namespace}/{file_id}         单条详情 + body
  POST /wiki/documents/{namespace}                   发布 / 重发 wiki (员工)
  POST /wiki/documents/{namespace}/{file_id}/unpublish  撤回 (员工 self only)
  GET  /wiki/audit?limit=                            audit (admin)

# Auth

跟 skills-hub 同模式: 信 gateway 反代注入的 X-Catfish-User-Sub / -Dept / -Role.
不接受外部 Bearer. 仅 dev 期 CATFISH_WIKI_HUB_DEV_TOKEN env 走 fallback.

# Manifesto 兼容

- 公理 2: 是员工主动 push 例外 (CATFISH-CENTRAL-MANIFESTO line 34-36 加 wiki publish)
- 公理 3: 没有"中央 push wiki 到员工本机" endpoint
- 公理 4: unpublish 走 stale 软删, 已 pull 副本不受影响
"""
from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path

from fastapi import Body, Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware


# load .env 在 import storage 前 (跟 skills-hub / mcp-registry 同模式)
def _load_dotenv() -> Path | None:
    try:
        from dotenv import load_dotenv  # noqa: PLC0415
    except ImportError:
        return None
    for p in [
        Path.cwd() / ".env",
        Path(__file__).resolve().parent.parent.parent / ".env",
    ]:
        if p.exists():
            load_dotenv(p, override=False)
            return p
    return None


_ENV_FILE_LOADED = _load_dotenv()

from . import __version__
from . import storage

logger = logging.getLogger("catfish.wiki_hub")

app = FastAPI(
    title="Catfish Wiki Hub",
    version=__version__,
    description="员工 publish / pull 部门 wiki 笔记的中央 hub (P3.3.18)",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Auth ──────────────────────────────────────────────────────


def _decode_header(value: str) -> str:
    if not value:
        return ""
    try:
        import urllib.parse as _p
        return _p.unquote(value)
    except Exception:
        return value


def require_user(
    x_catfish_user_sub: str | None = Header(default=None, alias="X-Catfish-User-Sub"),
    x_catfish_user_dept: str | None = Header(default=None, alias="X-Catfish-User-Dept"),
    x_catfish_user_role: str | None = Header(default=None, alias="X-Catfish-User-Role"),
    authorization: str | None = Header(default=None),
) -> dict:
    """信 gateway 注入 (主路径). dev token fallback (可选)."""
    if x_catfish_user_sub:
        return {
            "sub": _decode_header(x_catfish_user_sub),
            "dept": _decode_header(x_catfish_user_dept or ""),
            "role": _decode_header(x_catfish_user_role or "employee"),
        }
    expected = os.environ.get("CATFISH_WIKI_HUB_DEV_TOKEN", "").strip()
    if expected and authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        if token == expected:
            return {"sub": f"wiki-hub-dev-{token[:8]}", "dept": "", "role": "admin"}
    raise HTTPException(
        status_code=401,
        detail="未通过 gateway 反代 (缺 X-Catfish-User-Sub) 且 CATFISH_WIKI_HUB_DEV_TOKEN 未设/不匹配",
    )


def require_admin(user: dict = Depends(require_user)) -> dict:
    if user.get("role") not in ("admin", "sysadmin"):
        raise HTTPException(status_code=403, detail=f"admin only (你是 {user.get('role')})")
    return user


# ── Endpoints ──────────────────────────────────────────────────


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok", "service": "catfish-wiki-hub", "version": __version__}


@app.get("/wiki/documents")
async def list_documents_endpoint(
    namespace: str | None = None,
    include_stale: bool = True,
) -> dict:
    """列已发布 wiki. stale 项默认含 (UI 显灰 + warning), include_stale=false 则过滤."""
    docs = storage.list_documents(namespace_filter=namespace, include_stale=include_stale)
    return {"documents": docs, "count": len(docs)}


@app.get("/wiki/documents/{namespace}/{file_id}")
async def get_document_endpoint(namespace: str, file_id: str) -> dict:
    """单条 wiki — 含 frontmatter + body. stale 项也返, 客户端按 stale_after_unpublish 字段判断."""
    try:
        doc = storage.get_document(namespace, file_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if doc is None:
        raise HTTPException(status_code=404, detail=f"{namespace}/{file_id} 不存在")
    return doc


@app.post("/wiki/documents/{namespace}")
async def publish_document_endpoint(
    namespace: str,
    payload: dict = Body(...),
    user: dict = Depends(require_user),
) -> dict:
    """publish / re-publish wiki. payload 含:
      - file_id (可选, 没传则服务器分配 UUID)
      - filename (本机原 rel_path, e.g. "wiki/entities/老李.md")
      - title (frontmatter title)
      - kind (entity | concept | query)
      - frontmatter_yaml (原 frontmatter 文本)
      - body_md (markdown body)
    """
    file_id = (payload.get("file_id") or "").strip() or uuid.uuid4().hex[:12]
    filename = (payload.get("filename") or "").strip()
    title = (payload.get("title") or "").strip()
    kind = (payload.get("kind") or "entity").strip()
    frontmatter_yaml = payload.get("frontmatter_yaml") or ""
    body_md = payload.get("body_md") or ""

    if not filename:
        raise HTTPException(status_code=400, detail="filename 必填")
    if not title:
        raise HTTPException(status_code=400, detail="title 必填 (frontmatter title)")

    result = storage.publish_document(
        namespace=namespace,
        file_id=file_id,
        filename=filename,
        title=title,
        kind=kind,
        frontmatter_yaml=frontmatter_yaml,
        body_md=body_md,
        published_by=user["sub"],
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "publish 失败"))
    return result


@app.post("/wiki/documents/{namespace}/{file_id}/unpublish")
async def unpublish_document_endpoint(
    namespace: str,
    file_id: str,
    payload: dict = Body(default={}),
    user: dict = Depends(require_user),
) -> dict:
    """撤回 — 员工只能撤自己 publish 的, admin 例外.

    Manifesto 兼容设计: PG row 留 (audit), body 清零 + 标 stale. FS 镜像物理删.
    已 pull 副本不动 (公理 3/4 禁中央触及员工本机).
    """
    # 看 publish_by 决定权限
    existing = storage.get_document(namespace, file_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"{namespace}/{file_id} 不存在")
    is_owner = existing.get("published_by") == user["sub"]
    is_admin = user.get("role") in ("admin", "sysadmin")
    if not is_owner and not is_admin:
        raise HTTPException(
            status_code=403,
            detail=f"只能撤回自己 publish 的 wiki (这条是 {existing.get('published_by')} 发的)",
        )

    reason = (payload.get("reason") or "").strip()
    result = storage.unpublish_document(
        namespace=namespace,
        file_id=file_id,
        unpublished_by=user["sub"],
        reason=reason,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "撤回失败"))
    return result


@app.get("/wiki/audit")
async def audit_endpoint(
    limit: int = 100,
    user: dict = Depends(require_admin),  # noqa: ARG001
) -> dict:
    return {"events": storage.read_audit(limit=limit), "limit": limit}


def run() -> None:
    import uvicorn  # noqa: PLC0415
    # 端口约定 (跟其他 catfish service 错开):
    #   8642 hermes / 8995 secret-broker / 8996 mcp-registry /
    #   8997 skills-hub / 8998 identity-server / 8999 gateway
    #   8994 catfish-wiki-hub (本服务)
    port = int(os.environ.get("PORT", "8994"))
    host = os.environ.get("HOST", "127.0.0.1")
    if host == "0.0.0.0":
        print(f"[wiki-hub] ⚠️ HOST=0.0.0.0 — wiki-hub 暴露到所有网卡, 仅服务器部署用.", flush=True)
    print(f"[wiki-hub] starting on {host}:{port}", flush=True)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run()
