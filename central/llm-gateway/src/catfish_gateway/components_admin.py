"""中央组件 (模型 / 运行包) 的上传与发布 —— 管理员在门户上传 (10/3)。

10/1 的组件分发 (P0) 只有下载通道: 管理员要把两个多 G 的包拷到服务器的 components/ 目录,
再登服务器跑 delivery/catfish-poc/tools/build_component_manifest.py。鸿波: "应该支持
管理员上传相应的包或者模型"。

这里给门户用的接口 (admin / sysadmin):
    GET    /api/admin/components                       清单 + 没传完的上传
    POST   /api/admin/components/uploads               开始 / 续传 {file, size} → 已收多少字节
    PUT    /api/admin/components/uploads/{file}?offset 追加一块 (≤ 16MB, 头里带这块的 sha256)
    POST   /api/admin/components/uploads/{file}/complete  校验 → 放进目录 → 重新生成清单
    DELETE /api/admin/components/uploads/{file}        放弃没传完的
    DELETE /api/admin/components/{file}                下架已发布的包

为什么分块: web 前面的 nginx client_max_body_size 是 20M; 而且两个多 G 走浏览器, 断一次
从头再来不现实 —— 分块 + 按 offset 续传, 页面关了重选同一个文件就接着传。

没传完的放 <目录>/.uploads/ (点开头, 清单生成会跳过; nginx 也不会把它当组件)。
文件名规则 / 清单格式跟 delivery/catfish-poc/tools/build_component_manifest.py 一模一样
(tests/test_components_admin.py 拿两边对同一个目录生成的清单逐项比)。

目录: env CATFISH_COMPONENTS_DIR (docker 里挂成跟 web 同一个宿主机目录, 读写; 开发机在 .env
里配成 central/web/vite-components.ts 托管的同一个目录)。没配 → 接口返 503 说清楚。
"""
from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import logging
import os
import re
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi import Depends
from pydantic import BaseModel

from .auth import User, get_current_user

logger = logging.getLogger("catfish.gateway.components_admin")

SCHEMA = 1
PLATFORMS = ("mac-arm64", "mac-x64", "windows-x64", "any")
_NAME_RE = re.compile(
    r"^(?P<name>[a-z0-9]+(?:-[a-z0-9]+)*?)"
    r"-(?P<version>\d+\.\d+\.\d+)"
    r"-(?P<platform>" + "|".join(re.escape(p) for p in PLATFORMS) + r")"
    r"\.tar\.gz$"
)
#: 单块上限: 比 web nginx 的 client_max_body_size 20M 小
MAX_CHUNK = 16 * 1024 * 1024
#: 单个包上限 (现在最大的会议转写包 2.3G)
MAX_FILE = 20 * 1024 * 1024 * 1024
UPLOADS = ".uploads"
_SHA_CACHE = ".sha256-cache.json"

_locks: dict[str, asyncio.Lock] = {}


def parse_filename(filename: str) -> dict | None:
    m = _NAME_RE.match(filename)
    return m.groupdict() if m else None


def _version_key(v: str) -> tuple[int, int, int]:
    a, b, c = v.split(".")
    return int(a), int(b), int(c)


def components_dir() -> Path | None:
    """没配 = None (门户上提示去配)。不猜 home 下的目录: 中央端不碰任何人的 home
    (test_central_edge_boundary)。开发机在 central/llm-gateway/.env 里配成跟 vite 托管的
    同一个目录: CATFISH_COMPONENTS_DIR=~/.catfish-hub/components。"""
    env = os.environ.get("CATFISH_COMPONENTS_DIR", "").strip()
    return Path(env).expanduser() if env else None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _cached_sha256(directory: Path, path: Path) -> str:
    """两个多 G 的包算一次 sha256 要十来秒; 按 (大小, 修改时间) 缓存, 发布一个包不用把所有包重算一遍。"""
    cache_file = directory / _SHA_CACHE
    try:
        cache = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    st = path.stat()
    key = f"{path.name}|{st.st_size}|{st.st_mtime_ns}"
    if key not in cache:
        cache = {k: v for k, v in cache.items() if not k.startswith(path.name + "|")}
        cache[key] = _sha256(path)
        tmp = directory / (_SHA_CACHE + ".tmp")
        tmp.write_text(json.dumps(cache), encoding="utf-8")
        tmp.replace(cache_file)
    return cache[key]


def build_manifest(directory: Path) -> dict:
    """跟 build_component_manifest.build_manifest 同一个结果 (不认识的文件名同样报错)。"""
    bad: list[str] = []
    newest: dict[tuple[str, str], dict] = {}
    for p in sorted(directory.iterdir()):
        if not p.is_file() or p.name == "manifest.json" or p.name.startswith("."):
            continue
        meta = parse_filename(p.name)
        if meta is None:
            bad.append(p.name)
            continue
        key = (meta["name"], meta["platform"])
        cur = newest.get(key)
        if cur is None or _version_key(meta["version"]) > _version_key(cur["version"]):
            newest[key] = {**meta, "path": p}
    if bad:
        raise ValueError(
            "这些文件不符合 <name>-<x.y.z>-<platform>.tar.gz 约定 "
            f"(platform ∈ {', '.join(PLATFORMS)}): " + ", ".join(bad)
        )
    components = []
    for (name, platform), meta in sorted(newest.items()):
        path: Path = meta["path"]
        components.append({
            "name": name, "version": meta["version"], "platform": platform, "file": path.name,
            "size": path.stat().st_size, "sha256": _cached_sha256(directory, path),
        })
    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "components": components,
    }


def write_manifest(directory: Path) -> dict:
    manifest = build_manifest(directory)
    tmp = directory / ".manifest.json.tmp"
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(directory / "manifest.json")
    return manifest


def check_package(path: Path, meta: dict) -> None:
    """传完的包: 必须是完整的 gzip tar; 包里有 pack.json 的 (会议转写包这类) 名字 / 版本 / 平台要跟文件名对得上。

    只读到 pack.json 为止 (打包时它放第一个), 不把两个多 G 全解一遍; 完整性靠
    gzip 尾部校验 —— 读到底的事交给客户端安装时做 (它会校验 sha256 再解包)。
    """
    try:
        with gzip.open(path, "rb") as g:
            g.seek(0, os.SEEK_END)  # 读到底才会校验 gzip 尾部 CRC, 截断的包在这里报错
    except (OSError, EOFError) as e:
        raise ValueError(f"不是完整的 .tar.gz (可能没传完或文件坏了): {e}") from e
    try:
        with tarfile.open(path, "r:gz") as tar:
            for i, member in enumerate(tar):
                if member.name.lstrip("./") == "pack.json":
                    f = tar.extractfile(member)
                    pack = json.loads(f.read().decode("utf-8")) if f else {}
                    for k in ("name", "version", "platform"):
                        if k in pack and str(pack[k]) != meta[k]:
                            raise ValueError(f"包里 pack.json 的 {k} 是 {pack[k]!r}, 文件名里是 {meta[k]!r}, 对不上")
                    return
                if i >= 20:
                    return
    except tarfile.TarError as e:
        raise ValueError(f"不是 tar 包: {e}") from e


# ── HTTP ─────────────────────────────────────────────────────────────


class StartUpload(BaseModel):
    file: str
    size: int
    overwrite: bool = False


def _require_admin(user: User) -> None:
    if user.role not in ("admin", "sysadmin"):
        raise HTTPException(status_code=403, detail=f"role={user.role} 不能发布组件 (admin / sysadmin)")


def _dir_or_503() -> Path:
    d = components_dir()
    if d is None:
        raise HTTPException(status_code=503, detail="中央没配组件目录 (CATFISH_COMPONENTS_DIR), 门户上传不可用")
    try:
        (d / UPLOADS).mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise HTTPException(status_code=503, detail=f"组件目录不可写: {d} ({e}) —— 检查 CATFISH_COMPONENTS_DIR 挂载和权限") from e
    return d


def _meta_or_400(file: str) -> dict:
    meta = parse_filename(file)
    if meta is None or "/" in file or "\\" in file:
        raise HTTPException(
            status_code=400,
            detail=f"文件名要是 <名字>-<x.y.z>-<平台>.tar.gz, 平台 ∈ {', '.join(PLATFORMS)}; 收到 {file!r}",
        )
    return meta


def _part(d: Path, file: str) -> Path:
    return d / UPLOADS / f"{file}.part"


def _size_file(d: Path, file: str) -> Path:
    return d / UPLOADS / f"{file}.size"


def _pending(d: Path) -> list[dict[str, Any]]:
    out = []
    for p in sorted((d / UPLOADS).glob("*.part")):
        file = p.name[: -len(".part")]
        try:
            size = int(_size_file(d, file).read_text())
        except (OSError, ValueError):
            size = 0
        out.append({"file": file, "size": size, "received": p.stat().st_size})
    return out


def register_component_admin_routes(app: FastAPI) -> None:
    @app.get("/api/admin/components")
    async def list_components(user: User = Depends(get_current_user)) -> dict:
        _require_admin(user)
        d = _dir_or_503()
        try:
            manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            manifest = {"schema": SCHEMA, "generated_at": "", "components": []}
        return {"dir": str(d), "manifest": manifest, "uploads": _pending(d)}

    @app.post("/api/admin/components/uploads")
    async def start_upload(req: StartUpload, user: User = Depends(get_current_user)) -> dict:
        _require_admin(user)
        _meta_or_400(req.file)
        if not 0 < req.size <= MAX_FILE:
            raise HTTPException(status_code=400, detail=f"大小不对: {req.size} (上限 20GB)")
        d = _dir_or_503()
        if (d / req.file).exists() and not req.overwrite:
            raise HTTPException(status_code=409, detail=f"已经发布过 {req.file}; 要替换请勾选覆盖, 或者换个版本号")
        part, size_file = _part(d, req.file), _size_file(d, req.file)
        try:
            prev = int(size_file.read_text())
        except (OSError, ValueError):
            prev = None
        if prev != req.size and part.exists():
            part.unlink()  # 同名但大小不同 = 换了文件, 不能接着旧的传
        size_file.write_text(str(req.size))
        part.touch(exist_ok=True)
        logger.info("[components] %s 开始上传 %s (%d 字节, 已有 %d)", user.sub, req.file, req.size, part.stat().st_size)
        return {"file": req.file, "size": req.size, "received": part.stat().st_size}

    @app.put("/api/admin/components/uploads/{file}")
    async def put_chunk(file: str, offset: int, request: Request, user: User = Depends(get_current_user)) -> dict:
        _require_admin(user)
        _meta_or_400(file)
        d = _dir_or_503()
        part = _part(d, file)
        if not part.exists():
            raise HTTPException(status_code=404, detail="没有这个上传, 先开始上传")
        data = await request.body()
        if not data or len(data) > MAX_CHUNK:
            raise HTTPException(status_code=400, detail=f"每块 1 字节 ~ {MAX_CHUNK // (1 << 20)}MB, 收到 {len(data)}")
        want = request.headers.get("x-chunk-sha256", "").lower()
        if want and hashlib.sha256(data).hexdigest() != want:
            raise HTTPException(status_code=422, detail="这一块传坏了 (sha256 对不上), 重传这一块")
        async with _locks.setdefault(file, asyncio.Lock()):
            have = part.stat().st_size
            if offset != have:
                # 两个标签页同时传 / 上一块其实已经写进去了: 告诉前端从哪接着传
                raise HTTPException(status_code=409, detail={"message": "offset 不对", "received": have})
            total = int(_size_file(d, file).read_text())
            if have + len(data) > total:
                raise HTTPException(status_code=400, detail="超出文件大小")
            with part.open("ab") as f:
                f.write(data)
            return {"received": have + len(data)}

    @app.post("/api/admin/components/uploads/{file}/complete")
    async def complete(file: str, user: User = Depends(get_current_user)) -> dict:
        _require_admin(user)
        meta = _meta_or_400(file)
        d = _dir_or_503()
        part = _part(d, file)
        async with _locks.setdefault(file, asyncio.Lock()):
            if not part.exists():
                raise HTTPException(status_code=404, detail="没有这个上传")
            total = int(_size_file(d, file).read_text())
            if part.stat().st_size != total:
                raise HTTPException(status_code=409, detail=f"还没传完: {part.stat().st_size}/{total}")
            try:
                await asyncio.to_thread(check_package, part, meta)
            except ValueError as e:
                raise HTTPException(status_code=422, detail=str(e)) from e
            part.replace(d / file)
            _size_file(d, file).unlink(missing_ok=True)
            try:
                manifest = await asyncio.to_thread(write_manifest, d)
            except ValueError as e:
                raise HTTPException(status_code=500, detail=f"包已放进目录, 但生成清单失败: {e}") from e
        logger.info("[components] %s 发布了 %s", user.sub, file)
        entry = next((c for c in manifest["components"] if c["file"] == file), None)
        return {"published": entry, "manifest": manifest}

    @app.delete("/api/admin/components/uploads/{file}")
    async def cancel_upload(file: str, user: User = Depends(get_current_user)) -> dict:
        _require_admin(user)
        _meta_or_400(file)
        d = _dir_or_503()
        _part(d, file).unlink(missing_ok=True)
        _size_file(d, file).unlink(missing_ok=True)
        return {"ok": True}

    @app.delete("/api/admin/components/{file}")
    async def delete_component(file: str, user: User = Depends(get_current_user)) -> dict:
        _require_admin(user)
        _meta_or_400(file)
        d = _dir_or_503()
        target = d / file
        if not target.is_file():
            raise HTTPException(status_code=404, detail=f"没有 {file}")
        target.unlink()
        manifest = await asyncio.to_thread(write_manifest, d)
        logger.info("[components] %s 下架了 %s", user.sub, file)
        return {"manifest": manifest}
