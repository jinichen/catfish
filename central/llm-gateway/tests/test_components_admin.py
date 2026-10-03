"""中央组件上传 / 发布 (10/3, components_admin.py)。

验: 分块上传 + 续传、坏块被拒、传完校验 (gzip 完整 / pack.json 跟文件名对得上)、放进目录
并重新生成清单、权限、下架; 以及清单跟 delivery 里的 build_component_manifest.py 逐项一致
(两份实现, 规则改一边忘了另一边就在这里红)。
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import tarfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

TOOL = Path(__file__).resolve().parents[3] / "delivery" / "catfish-poc" / "tools" / "build_component_manifest.py"


def _pack(meta: dict | None, payload: bytes | None = None) -> bytes:
    payload = payload if payload is not None else os.urandom(3000)  # 随机 = 压不小, 才测得出分块
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        if meta is not None:
            data = json.dumps(meta).encode()
            ti = tarfile.TarInfo("pack.json")
            ti.size = len(data)
            tar.addfile(ti, io.BytesIO(data))
        ti = tarfile.TarInfo("models/big.bin")
        ti.size = len(payload)
        tar.addfile(ti, io.BytesIO(payload))
    return buf.getvalue()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import logging

    logging.disable(logging.CRITICAL)
    from catfish_gateway.app import app
    from catfish_gateway.auth import User, get_current_user

    monkeypatch.setenv("CATFISH_COMPONENTS_DIR", str(tmp_path))

    def as_role(role):
        async def _u() -> User:
            return User(sub="it@x.com", role=role, department="d")
        return _u

    app.dependency_overrides[get_current_user] = as_role("admin")
    yield TestClient(app), tmp_path, app, get_current_user, as_role
    app.dependency_overrides.clear()


def _upload(c, name: str, data: bytes, chunk: int = 1000) -> dict:
    r = c.post("/api/admin/components/uploads", json={"file": name, "size": len(data)})
    assert r.status_code == 200, r.text
    off = r.json()["received"]
    while off < len(data):
        piece = data[off:off + chunk]
        r = c.put(f"/api/admin/components/uploads/{name}", params={"offset": off}, content=piece,
                  headers={"x-chunk-sha256": hashlib.sha256(piece).hexdigest()})
        assert r.status_code == 200, r.text
        off = r.json()["received"]
    return c.post(f"/api/admin/components/uploads/{name}/complete").json()


def test_upload_publish_and_manifest(env):
    c, d, *_ = env
    data = _pack({"name": "meeting-asr", "version": "1.0.2", "platform": "windows-x64"})
    out = _upload(c, "meeting-asr-1.0.2-windows-x64.tar.gz", data)
    assert out["published"]["sha256"] == hashlib.sha256(data).hexdigest()
    assert (d / "meeting-asr-1.0.2-windows-x64.tar.gz").read_bytes() == data
    on_disk = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    assert [x["file"] for x in on_disk["components"]] == ["meeting-asr-1.0.2-windows-x64.tar.gz"]
    assert c.get("/api/admin/components").json()["uploads"] == [], ".uploads 里不留东西"


def test_resume_after_interruption(env):
    c, d, *_ = env
    name, data = "bge-m3-2.0.0-any.tar.gz", _pack(None, os.urandom(5000))
    c.post("/api/admin/components/uploads", json={"file": name, "size": len(data)})
    c.put(f"/api/admin/components/uploads/{name}", params={"offset": 0}, content=data[:1500])
    # 页面关了, 重选同一个文件: 服务端告诉从哪接着传
    assert c.post("/api/admin/components/uploads", json={"file": name, "size": len(data)}).json()["received"] == 1500
    assert c.get("/api/admin/components").json()["uploads"] == [{"file": name, "size": len(data), "received": 1500}]
    # offset 对不上 (比如重复发了上一块) → 409 带上真实进度
    r = c.put(f"/api/admin/components/uploads/{name}", params={"offset": 0}, content=data[:10])
    assert r.status_code == 409 and r.json()["detail"]["received"] == 1500
    assert _upload(c, name, data)["published"]["platform"] == "any"


def test_corrupt_chunk_and_bad_package_rejected(env):
    c, d, *_ = env
    name = "meeting-asr-1.0.3-mac-arm64.tar.gz"
    data = _pack({"name": "meeting-asr", "version": "9.9.9", "platform": "mac-arm64"})
    c.post("/api/admin/components/uploads", json={"file": name, "size": len(data)})
    r = c.put(f"/api/admin/components/uploads/{name}", params={"offset": 0}, content=data[:100],
              headers={"x-chunk-sha256": "0" * 64})
    assert r.status_code == 422 and "传坏" in r.json()["detail"]
    for off in range(0, len(data), 1000):
        c.put(f"/api/admin/components/uploads/{name}", params={"offset": off}, content=data[off:off + 1000])
    r = c.post(f"/api/admin/components/uploads/{name}/complete")
    assert r.status_code == 422 and "version" in r.json()["detail"], "pack.json 跟文件名对不上不收"
    assert not (d / name).exists()

    trunc = "foo-1.0.0-any.tar.gz"
    body = _pack(None)[:-30]  # 截断的 gzip
    c.post("/api/admin/components/uploads", json={"file": trunc, "size": len(body)})
    c.put(f"/api/admin/components/uploads/{trunc}", params={"offset": 0}, content=body)
    r = c.post(f"/api/admin/components/uploads/{trunc}/complete")
    assert r.status_code == 422 and "完整" in r.json()["detail"]


def test_names_permissions_overwrite_delete(env):
    c, d, app, dep, as_role = env
    for bad in ("meeting-asr.tar.gz", "x-1.0-mac-arm64.tar.gz", "x-1.0.0-linux.tar.gz", "../x-1.0.0-any.tar.gz"):
        assert c.post("/api/admin/components/uploads", json={"file": bad, "size": 10}).status_code in (400, 404, 422)
    name = "foo-1.0.0-any.tar.gz"
    _upload(c, name, _pack(None))
    assert c.post("/api/admin/components/uploads", json={"file": name, "size": 10}).status_code == 409
    assert c.post("/api/admin/components/uploads", json={"file": name, "size": 10, "overwrite": True}).status_code == 200
    c.delete(f"/api/admin/components/uploads/{name}")
    app.dependency_overrides[dep] = as_role("employee")
    assert c.get("/api/admin/components").status_code == 403
    assert c.delete(f"/api/admin/components/{name}").status_code == 403
    app.dependency_overrides[dep] = as_role("sysadmin")
    r = c.delete(f"/api/admin/components/{name}")
    assert r.status_code == 200 and r.json()["manifest"]["components"] == []
    assert not (d / name).exists()


def test_manifest_identical_to_delivery_tool(env):
    c, d, *_ = env
    spec = importlib.util.spec_from_file_location("bcm", TOOL)
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    from catfish_gateway import components_admin as ca

    assert tool.PLATFORMS == ca.PLATFORMS
    for name in ("meeting-asr-1.0.0-mac-arm64.tar.gz", "meeting-asr-1.0.10-mac-arm64.tar.gz",
                 "meeting-asr-1.0.2-windows-x64.tar.gz", "bge-m3-1.0.0-any.tar.gz"):
        (d / name).write_bytes(name.encode())
    (d / ".uploads").mkdir(exist_ok=True)
    (d / ".uploads" / "half.part").write_bytes(b"x")
    strip = lambda m: [dict(x) for x in m["components"]]  # noqa: E731
    assert strip(ca.build_manifest(d)) == strip(tool.build_manifest(d))
    assert [x["version"] for x in ca.build_manifest(d)["components"] if x["platform"] == "mac-arm64"] == ["1.0.10"]
    (d / "stray.zip").write_bytes(b"x")
    for impl in (ca, tool):
        with pytest.raises(ValueError, match="stray.zip"):
            impl.build_manifest(d)
