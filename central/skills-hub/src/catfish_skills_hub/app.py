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

# Auth (BL-D2 5/10 改 — Phase 2)

不再自己验 dev_token. Phase 1 是 dev_token MVP, BL-FIX29 把员工 dev_token 通道
关掉之后, 老 require_token 永远 401, hub 跟 catfish 主链路完全断.

新模式: **信任 gateway 反代注入的 X-Catfish-User-Sub / -Dept / -Role header**.

  Companion → gateway /v1/hub/* (OIDC 验)  → skills_hub_proxy 注入 X-Catfish-User-* → hub /skills/*

跟 mcp-registry 同模式 (BL-D3 Phase 1). hub 不直接接受外部 Bearer (只信内网
gateway). 直接 curl hub :8997 不带 X-Catfish 头会被拒.

向后兼容: 老 `CATFISH_HUB_DEV_TOKEN` env 仍保留, 给开发期 curl 测试用 (设了就开
fallback 通道, 没设则只信 X-Catfish-User-Sub header).

# 启动

  $ cd central/skills-hub
  $ pip install -e .
  $ python -m catfish_skills_hub.app
  → http://localhost:8997
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

# BL-D2 Phase 2 (5/10): load .env 在 import storage 之前, 让 CATFISH_DB_URL 等
# 注入 process env. 跟 gateway / mcp-registry 同模式. dotenv optional —
# 没装也能跑, 只是要手动 export.
def _load_dotenv() -> Path | None:
    try:
        from dotenv import load_dotenv  # noqa: PLC0415  懒 import
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


# ── Auth (BL-D2 5/10: 信 gateway 反代 X-Catfish-User-* header) ─────


def _decode_header(value: str) -> str:
    """gateway mcp_registry_proxy._safe_header_value 的反向 — 把 percent-encoded
    UTF-8 中文恢复. 跟 mcp-registry app.py 同模式."""
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
    """信 gateway 注入身份, 或 dev_token fallback (向后兼容).

    返 {sub, dept, role}. published_by 用 sub.
    """
    if x_catfish_user_sub:
        return {
            "sub": _decode_header(x_catfish_user_sub),
            "dept": _decode_header(x_catfish_user_dept or ""),
            "role": _decode_header(x_catfish_user_role or "employee"),
        }
    # 向后兼容: 直 curl 测试时还能用 CATFISH_HUB_DEV_TOKEN 走老路
    expected = os.environ.get("CATFISH_HUB_DEV_TOKEN", "").strip()
    if expected and authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        if token == expected:
            return {"sub": f"hub-dev-{token[:8]}", "dept": "", "role": "admin"}
    raise HTTPException(
        status_code=401,
        detail="未通过 gateway 反代 (缺 X-Catfish-User-Sub) 且 CATFISH_HUB_DEV_TOKEN 未设/不匹配",
    )


def require_admin(user: dict = Depends(require_user)) -> dict:
    """role=admin 才能 delete. manager / employee 拒."""
    if user.get("role") != "admin":
        raise HTTPException(
            status_code=403,
            detail=f"admin only (你是 {user.get('role')})",
        )
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
    user: dict = Depends(require_user),
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

    result = storage.publish_skill(namespace, file_map, published_by=user["sub"])
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "publish 失败"))
    return result


@app.delete("/skills/{namespace}/{name}/{version}")
async def delete_skill_version_endpoint(
    namespace: str,
    name: str,
    version: str,
    reason: str = "",
    user: dict = Depends(require_admin),
) -> dict:
    """admin 删 skill 版本 (rare, 一般 deprecate 不删)."""
    result = storage.delete_skill_version(
        namespace, name, version, deleted_by=user["sub"], reason=reason,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("error", "删除失败"))
    return result


@app.get("/audit")
async def audit_endpoint(
    limit: int = 100,
    user: dict = Depends(require_admin),  # noqa: ARG001
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
