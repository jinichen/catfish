"""Wiki Hub FastAPI (P3.3.18, 6/10).

# 端点

  GET  /healthz                                      健康
  GET  /wiki/documents?include_stale=                    列本部门已发布 wiki (admin 看全部)
  GET  /wiki/documents/dept/{部门}/{file_id}             单条详情 + body
  POST /wiki/documents                                   发布 / 重发到自己部门 (员工)
  POST /wiki/documents/dept/{部门}/{file_id}/unpublish   撤回 (员工 self only)
  POST /wiki/documents/dept/{部门}/{file_id}/scope       改范围: 申请全公司可见 / 收回到本部门 (作者 / admin)
  POST /wiki/documents/dept/{部门}/{file_id}/review      审: approve / withdraw (部门主管) · approve_company / reject_company (admin)
  GET  /wiki/audit?limit=                                audit (admin)

# 部门隔离 (9/30)

namespace 固定是 `dept/<部门>`, <部门> 取自身份里的 department (见 namespaces.py).
9/30 之前 namespace 是 `{namespace}` 一个路径段, 而员工填的是 `dept/finance`
—— 含 `/`, 路由永远匹配不上, 发布一直是 405, 这条链路从来没通过.

# 全公司范围 (11/09)

页面始终存在发布人部门的 namespace 里; 审核记录里 scope=company 且管理员通过的, 其他部门也看得到、能装
(见 review.py 顶部)。选了全公司但还没通过的, 跟部门页一样只有本部门看得到。

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
from . import review, sales, storage   # 11/08 review: 知识页「待审」标 (docs/SHARE-GOVERNANCE-PLAN.md §3.2)
from .namespaces import can_see, is_admin, namespace_for, validate_dept

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
    x_catfish_user_managed: str | None = Header(default=None, alias="X-Catfish-User-Managed-Depts"),
    authorization: str | None = Header(default=None),
) -> dict:
    """信 gateway 注入 (主路径). dev token fallback (可选).

    Managed-Depts: manager 管的部门, 逗号分隔, 每个各自 percent-encode (10/12, 销售漏斗用)。
    """
    if x_catfish_user_sub:
        return {
            "sub": _decode_header(x_catfish_user_sub),
            "dept": _decode_header(x_catfish_user_dept or ""),
            "role": _decode_header(x_catfish_user_role or "employee"),
            "managed": [_decode_header(x) for x in (x_catfish_user_managed or "").split(",") if x],
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
    if not is_admin(user):
        raise HTTPException(status_code=403, detail=f"admin only (你是 {user.get('role')})")
    return user


# ── Endpoints ──────────────────────────────────────────────────


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok", "service": "catfish-wiki-hub", "version": __version__}


@app.get("/wiki/documents")
async def list_documents_endpoint(
    include_stale: bool = True,
    user: dict = Depends(require_user),
) -> dict:
    """列已发布 wiki — 只列调用者看得见的部门 (admin 看全部).

    stale 项默认含 (UI 显灰 + warning), include_stale=false 则过滤.
    9/30 之前这个端点连身份都不要, 直连 hub 就能列全部.
    """
    if is_admin(user):
        metas = storage.list_documents(None, include_stale=include_stale)
    else:
        dept = (user.get("dept") or "").strip()
        own = namespace_for(dept) if dept and validate_dept(dept) is None else None
        metas = storage.list_documents([own], include_stale=include_stale) if own else []
        # 11/09: 加上别的部门里管理员已通过、全公司可见的
        company = {k for k in review.company_keys() if k[0] != own}
        if company:
            others = storage.list_documents(sorted({ns for ns, _ in company}), include_stale=include_stale)
            metas += [m for m in others if (m["namespace"], m["file_id"]) in company]
    docs = [review.annotate(d) for d in metas if not sales.hidden_from(user, d)]
    return {"documents": docs, "count": len(docs)}


@app.get("/wiki/review")
async def review_list_endpoint(user: dict = Depends(require_user)) -> dict:
    """11/08 待我审的知识页 (部门主管: 管的部门; admin: 全部)。知识页发了就可见, 这里是「看过打钩 / 撤下」。"""
    return {"documents": review.pending_for(user)}


def _require_visible(user: dict, dept: str) -> str:
    err = validate_dept(dept)
    if err:
        raise HTTPException(status_code=400, detail=err)
    namespace = namespace_for(dept)
    if not can_see(user, namespace):
        # 跟"不存在"区分开: 员工要知道是权限问题, 不是条目没了
        raise HTTPException(status_code=403, detail=f"{namespace} 不是你的部门, 看不到")
    return namespace


def _require_doc_visible(user: dict, dept: str, file_id: str) -> str:
    """能看这一页: 本部门 / admin / 管理员已通过全公司可见的。"""
    err = validate_dept(dept)
    if err:
        raise HTTPException(status_code=400, detail=err)
    namespace = namespace_for(dept)
    if can_see(user, namespace):
        return namespace
    try:
        if review.company_visible(review.load(namespace, file_id)):
            return namespace
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    raise HTTPException(status_code=403, detail=f"{namespace} 不是你的部门, 这一页也没有全公司可见, 看不到")


@app.get("/wiki/documents/dept/{dept}/{file_id}")
async def get_document_endpoint(
    dept: str,
    file_id: str,
    user: dict = Depends(require_user),
) -> dict:
    """单条 wiki — 含 frontmatter + body. stale 项也返, 客户端按 stale_after_unpublish 字段判断.

    manager 能按 id 打开所管部门的条目 (中央 web 部门页看销售摘要原文用)。"""
    namespace = namespace_for(dept) if validate_dept(dept) is None and sales.can_manage(user, dept) \
        else _require_doc_visible(user, dept, file_id)
    try:
        doc = storage.get_document(namespace, file_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if doc is None:
        raise HTTPException(status_code=404, detail=f"{namespace}/{file_id} 不存在")
    if sales.hidden_from(user, doc):
        raise HTTPException(status_code=403, detail="这是同事的销售漏斗摘要, 只有本人和部门主管能看")
    return review.annotate(doc)


@app.get("/wiki/sales/dept/{dept}")
async def sales_funnel_endpoint(dept: str, user: dict = Depends(require_user)) -> dict:
    """部门销售漏斗 (阶段 3): 每个销售最新一份摘要 + 全组按阶段合计。只给 admin / 管这个部门的 manager。"""
    err = validate_dept(dept)
    if err:
        raise HTTPException(status_code=400, detail=err)
    if not sales.can_manage(user, dept):
        raise HTTPException(status_code=403, detail=f"只有 {dept} 的主管或管理员能看全组销售漏斗")
    namespace = namespace_for(dept)
    metas = [m for m in storage.list_documents([namespace], include_stale=False) if sales.is_sales_summary(m)]
    docs = [d for d in (storage.get_document(namespace, m["file_id"]) for m in metas) if d]
    return {"department": dept, **sales.department_funnel(docs)}


@app.post("/wiki/documents")
async def publish_document_endpoint(
    payload: dict = Body(...),
    user: dict = Depends(require_user),
) -> dict:
    """publish / re-publish wiki 到**发布者自己的部门**. payload 含:
      - file_id (可选, 没传则服务器分配 UUID; 重发传上次拿到的)
      - filename (本机原 rel_path, e.g. "wiki/entities/老李.md")
      - title (frontmatter title)
      - kind (entity | concept | query)
      - frontmatter_yaml (原 frontmatter 文本)
      - body_md (markdown body)
      - scope (可选, 11/09): department (默认, 本部门) | company (本部门马上可见 + 申请全公司可见, 管理员审)

    namespace 不从请求里拿, 由身份里的 department 推出 (见 namespaces.py).
    """
    dept = (user.get("dept") or "").strip()
    if not dept:
        raise HTTPException(
            status_code=400,
            detail="你的账号没有设置部门, 不能分享到部门知识库. 请联系管理员在用户管理里补上部门.",
        )
    err = validate_dept(dept)
    if err:
        raise HTTPException(status_code=400, detail=f"账号里的部门名不能用作知识库分组: {err}")
    namespace = namespace_for(dept)

    file_id = (payload.get("file_id") or "").strip() or uuid.uuid4().hex[:12]
    filename = (payload.get("filename") or "").strip()
    title = (payload.get("title") or "").strip()
    kind = (payload.get("kind") or "entity").strip()
    frontmatter_yaml = payload.get("frontmatter_yaml") or ""
    body_md = payload.get("body_md") or ""

    scope = (payload.get("scope") or "department").strip()
    if scope not in review.SCOPES:
        raise HTTPException(status_code=400, detail="scope 只能是 department (本部门) 或 company (全公司)")
    if not filename:
        raise HTTPException(status_code=400, detail="filename 必填")
    if scope == "company" and filename == sales.SALES_FILENAME:
        raise HTTPException(status_code=400, detail="销售漏斗摘要只给主管看, 不能分享到全公司")
    if not title:
        raise HTTPException(status_code=400, detail="title 必填 (frontmatter title)")

    # 重发只能覆盖自己发的 (之前 upsert 不看是谁, 知道 file_id 就能改别人的)
    try:
        existing = storage.get_document(namespace, file_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if existing is not None:
        owner = existing.get("published_by") or ""
        if owner and owner != user["sub"] and not is_admin(user):
            raise HTTPException(
                status_code=403,
                detail=f"{namespace}/{file_id} 是 {owner} 发的, 只有原作者能更新",
            )

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
    # 11/08: 发了即可见, 但标「待审」; 重发 (内容变了) 也回到待审
    rec = review.mark_pending(namespace, file_id, user["sub"], scope)
    note = "同部门同事现在就能看到, 页面上带「待审」标; 部门主管看过打钩后标消失"
    if scope == "company":
        note += "。全公司可见要等管理员审过, 之前其他部门看不到"
    return {**result, "review_status": rec["review_status"], "scope": scope,
            "company_status": rec.get("company_status"), "note": note}


@app.post("/wiki/documents/dept/{dept}/{file_id}/scope")
async def scope_document_endpoint(
    dept: str,
    file_id: str,
    payload: dict = Body(default={}),
    user: dict = Depends(require_user),
) -> dict:
    """11/09 不重发只改范围: {scope: company} 申请全公司可见 (管理员审) / {scope: department} 收回到只在本部门。作者或 admin。"""
    namespace = _require_visible(user, dept)
    existing = storage.get_document(namespace, file_id)
    if existing is None or existing.get("stale_after_unpublish"):
        raise HTTPException(status_code=404, detail=f"{namespace}/{file_id} 不存在或已撤回")
    if existing.get("published_by") != user["sub"] and not is_admin(user):
        raise HTTPException(status_code=403, detail="只有作者或管理员能改分享范围")
    scope = str(payload.get("scope") or "").strip()
    if scope == "company" and existing.get("filename") == sales.SALES_FILENAME:
        raise HTTPException(status_code=400, detail="销售漏斗摘要只给主管看, 不能分享到全公司")
    try:
        rec = review.set_scope(user, namespace, file_id, scope)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return {"ok": True, "namespace": namespace, "file_id": file_id, **review.annotate({k: v for k, v in existing.items() if k != "body_md"}, rec)}


@app.post("/wiki/documents/dept/{dept}/{file_id}/review")
async def review_document_endpoint(
    dept: str,
    file_id: str,
    payload: dict = Body(default={}),
    user: dict = Depends(require_user),
) -> dict:
    """11/08 主管 / admin: approve (看过, 没问题) 或 withdraw (撤下, 要写原因)。
    11/09 admin: approve_company (全公司可见) / reject_company (驳回, 要写原因; 页面仍在本部门)。"""
    err = validate_dept(dept)
    if err:
        raise HTTPException(status_code=400, detail=err)
    namespace = namespace_for(dept)
    if storage.get_document(namespace, file_id) is None:
        raise HTTPException(status_code=404, detail=f"{namespace}/{file_id} 不存在")
    try:
        rec = review.decide(user, namespace, file_id, str(payload.get("decision") or ""), str(payload.get("note") or ""))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return {"ok": True, "namespace": namespace, "file_id": file_id, **rec}


@app.post("/wiki/documents/dept/{dept}/{file_id}/unpublish")
async def unpublish_document_endpoint(
    dept: str,
    file_id: str,
    payload: dict = Body(default={}),
    user: dict = Depends(require_user),
) -> dict:
    """撤回 — 员工只能撤自己 publish 的, admin 例外.

    Manifesto 兼容设计: PG row 留 (audit), body 清零 + 标 stale. FS 删正文留元数据.
    已 pull 副本不动 (公理 3/4 禁中央触及员工本机).
    """
    namespace = _require_visible(user, dept)
    existing = storage.get_document(namespace, file_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"{namespace}/{file_id} 不存在")
    is_owner = existing.get("published_by") == user["sub"]
    if not is_owner and not is_admin(user):
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
    # 端口约定 (跟其他 catfish service 错开, P3.4.1 6/13 砍 8995):
    #   8642 hermes / 8996 mcp-registry /
    #   8997 skills-hub / 8998 identity-server / 8999 gateway
    #   8994 catfish-wiki-hub (本服务)
    #   ~~8995 secret-broker~~ (P3.4.1 砍, OAuth token 改员工本机存)
    port = int(os.environ.get("PORT", "8994"))
    host = os.environ.get("HOST", "127.0.0.1")
    if host == "0.0.0.0":
        print(f"[wiki-hub] ⚠️ HOST=0.0.0.0 — wiki-hub 暴露到所有网卡, 仅服务器部署用.", flush=True)
    print(f"[wiki-hub] starting on {host}:{port}", flush=True)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run()
