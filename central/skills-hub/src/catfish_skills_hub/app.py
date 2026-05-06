"""Skills Hub FastAPI MVP — 五一 sprint 5/2 收尾.

# 端点

  GET  /healthz                                     健康
  GET  /skills?namespace=                           列已发布 skill
  GET  /skills/{namespace}/{name}                   skill 元信息 + 最新版
  GET  /skills/{namespace}/{name}/{version}         指定版本元信息
  GET  /skills/{namespace}/{name}/{version}/files/{path}    下载文件
  POST /skills/{namespace}                          发布新版 (multipart files)
  DELETE /skills/{namespace}/{name}/{version}       删版本 (admin only)
  GET  /audit?limit=                                发布 / 删除审计

# Auth

跟 catfish-gateway 共享 dev_token (CATFISH_HUB_DEV_TOKEN env), 简化 MVP.
Phase 2 走真 OIDC + RBAC (admin only delete, manager+ publish).

# 启动

  $ cd central/skills-hub
  $ pip install -e .
  $ python -m catfish_skills_hub.app
  → http://localhost:8997
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from . import __version__
from . import storage

logger = logging.getLogger("catfish.skills_hub")

app = FastAPI(
    title="Catfish Skills Hub",
    version=__version__,
    description="员工发布 / 拉取 skill 的中央 hub",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Auth (MVP: 共享 dev_token) ─────────────────────────────────


def _expected_token() -> str:
    return os.environ.get("CATFISH_HUB_DEV_TOKEN", "dev-token-local").strip()


def require_token(authorization: str | None = Header(default=None)) -> str:
    """简单 bearer 验. 返 token (MVP 没真 user, 用 token preview 当 published_by)."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing/invalid Authorization Bearer")
    token = authorization[7:].strip()
    if token != _expected_token():
        raise HTTPException(status_code=401, detail="invalid token")
    # 给个可读身份 (token 前 8 字符 hash 一下当 user id)
    return f"hub-user-{token[:8]}"


def require_admin_token(authorization: str | None = Header(default=None)) -> str:
    """MVP: admin token = bearer + env CATFISH_HUB_ADMIN_TOKEN. 不设就跟 dev_token 同."""
    user = require_token(authorization)
    admin_token = os.environ.get("CATFISH_HUB_ADMIN_TOKEN", "").strip()
    if admin_token and authorization[7:].strip() != admin_token:
        raise HTTPException(status_code=403, detail="admin only")
    return user


# ── Endpoints ──────────────────────────────────────────────────


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok", "service": "catfish-skills-hub", "version": __version__}


@app.get("/skills")
async def list_skills_endpoint(namespace: str | None = None) -> dict:
    """列已发布 skill (按 ns/name 分组, 各取最新版本)."""
    skills = storage.list_skills(namespace_filter=namespace)
    return {"skills": skills, "count": len(skills)}


@app.get("/skills/{namespace}/{name}")
async def get_skill_latest(namespace: str, name: str) -> dict:
    """获取 skill 最新版元信息."""
    info = storage.get_skill(namespace, name, version="latest")
    if info is None:
        raise HTTPException(status_code=404, detail=f"skill {namespace}/{name} 不存在")
    return info


@app.get("/skills/{namespace}/{name}/{version}")
async def get_skill_version(namespace: str, name: str, version: str) -> dict:
    """获取 skill 指定版本元信息 + 文件列表 + SKILL.md 全文."""
    try:
        info = storage.get_skill(namespace, name, version=version)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if info is None:
        raise HTTPException(
            status_code=404, detail=f"skill {namespace}/{name}/{version} 不存在",
        )
    return info


@app.get("/skills/{namespace}/{name}/{version}/files/{file_path:path}")
async def download_skill_file(
    namespace: str, name: str, version: str, file_path: str,
) -> Response:
    """下载 skill 某个文件 (二进制). file_path 支持子目录 (e.g., 'tests/test_smoke.py')."""
    try:
        content = storage.download_file(namespace, name, version, file_path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if content is None:
        raise HTTPException(
            status_code=404, detail=f"file {file_path} 不存在 in {namespace}/{name}/{version}",
        )
    # Content-Type 简单按后缀推
    ct = "application/octet-stream"
    if file_path.endswith(".md") or file_path.endswith(".txt"):
        ct = "text/plain; charset=utf-8"
    elif file_path.endswith(".py"):
        ct = "text/x-python; charset=utf-8"
    elif file_path.endswith(".json"):
        ct = "application/json"
    elif file_path.endswith(".yaml") or file_path.endswith(".yml"):
        ct = "application/x-yaml; charset=utf-8"
    return Response(content=content, media_type=ct)


@app.post("/skills/{namespace}")
async def publish_skill_endpoint(
    namespace: str,
    files: list[UploadFile],
    user: str = Depends(require_token),
) -> dict:
    """发布 skill — multipart files. 必须含 SKILL.md.

    curl -X POST http://localhost:8997/skills/department \\
         -H "Authorization: Bearer dev-token-local" \\
         -F "files=@SKILL.md" \\
         -F "files=@script.py"
    """
    if not files:
        raise HTTPException(status_code=400, detail="files 必填")

    # 读 multipart
    file_map: dict[str, bytes] = {}
    for f in files:
        if not f.filename:
            continue
        # multipart 的 filename 就是相对路径 (foo/bar.py 也支持)
        rel = f.filename.lstrip("/")
        if ".." in rel:
            raise HTTPException(status_code=400, detail=f"file 路径不合法: {rel}")
        file_map[rel] = await f.read()

    if "SKILL.md" not in file_map:
        raise HTTPException(status_code=400, detail="必须含 SKILL.md 文件")

    result = storage.publish_skill(namespace, file_map, published_by=user)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "publish 失败"))
    return result


@app.delete("/skills/{namespace}/{name}/{version}")
async def delete_skill_version_endpoint(
    namespace: str,
    name: str,
    version: str,
    reason: str = "",
    user: str = Depends(require_admin_token),
) -> dict:
    """admin 删 skill 版本 (rare, 一般 deprecate 不删)."""
    result = storage.delete_skill_version(
        namespace, name, version, deleted_by=user, reason=reason,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("error", "删除失败"))
    return result


@app.get("/audit")
async def audit_endpoint(
    limit: int = 100,
    user: str = Depends(require_admin_token),  # noqa: ARG001
) -> dict:
    """admin 看发布 / 删除审计 (倒序最近 N 条)."""
    return {"events": storage.read_audit(limit=limit), "limit": limit}


def run() -> None:
    import uvicorn  # noqa: PLC0415
    port = int(os.environ.get("PORT", "8997"))
    # 5/6 安全 P0 G1: 默认仅本机. 客户内网部署 hub 服务器才显式 HOST=0.0.0.0.
    host = os.environ.get("HOST", "127.0.0.1")
    if host == "0.0.0.0":
        print(
            f"[skills-hub] ⚠️ HOST=0.0.0.0 — hub 暴露到所有网卡, 仅服务器部署用.",
            flush=True,
        )
    print(f"[skills-hub] starting on {host}:{port}", flush=True)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run()
