"""专家包提交 / 审核 / 上架 / 下载 / 签名 (10/18, docs/EXPERT-PACK-PLAN.md §5 §6)。走 HTTP (TestClient), 身份用 gateway 注入的头。"""
from __future__ import annotations

import base64
import io
import json
import tarfile
import urllib.parse

import pytest
import yaml
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_HUB_ROOT", str(tmp_path / "hub"))
    monkeypatch.delenv("CATFISH_DB_URL", raising=False)
    from catfish_skills_hub.app import app
    return TestClient(app)


def H(sub, dept, role="employee", managed=()):
    q = urllib.parse.quote
    return {"X-Catfish-User-Sub": sub, "X-Catfish-User-Dept": q(dept), "X-Catfish-User-Role": role,
            "X-Catfish-User-Managed-Depts": ",".join(q(m) for m in managed)}


EMP = H("zhou@example.com", "安全部")
MGR = H("boss@example.com", "安全部", "manager", ["安全部"])
OTHER_MGR = H("eng@example.com", "工程部", "manager", ["工程部"])
OTHER = H("li@example.com", "工程部")
ADMIN = H("admin@example.com", "IT", "admin")


def pack(pid="qual", version="1.0.0", extra=None):
    man = {"kind": "catfish-expert", "id": pid, "version": version, "name": "资质专员", "description": "管资质",
           "routes": [{"when": "审资质", "use": "workflow:qual-approve"}], "tool_groups": ["knowledge"]}
    files = {"expert.yaml": yaml.safe_dump(man, allow_unicode=True),
             "SOUL.md": "我是资质专员",
             "workflows/qual-approve/workflow.yaml": yaml.safe_dump({"kind": "workflow", "steps": [
                 {"id": "login", "skill": "pack-qual/eis-login"}, {"id": "push", "action": "wechat_push", "confirm": True}]}),
             "skills/eis-login/script.py": "_call('catfish_browser_goto', {'url': 'https://eis.example.com/login'})\n",
             **(extra or {})}
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, body in files.items():
            data = body.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def submit(c, headers, data, scope="department", depts=("安全部",)):
    return c.post("/experts", headers=headers, files={"pack": ("p.catfish-expert", data)},
                  data={"scope": scope, "departments": json.dumps(list(depts)), "note": "请审"})


def test_提交_主管审核通过_签名能验_本部门能下载(client):
    r = submit(client, EMP, pack())
    assert r.status_code == 200, r.text
    meta = r.json()
    assert meta["status"] == "pending" and meta["sites"] == ["https://eis.example.com/login"]
    assert meta["workflows"]["qual-approve"][1] == {"id": "push", "block": "action", "what": "wechat_push", "confirm": True}
    assert client.get("/experts", headers=EMP).json()["experts"] == [], "没审核前市场里没有"
    assert [m["id"] for m in client.get("/experts/review", headers=MGR).json()["experts"]] == ["qual"]
    assert client.get("/experts/review", headers=OTHER_MGR).json()["experts"] == []
    ok = client.post("/experts/qual/1.0.0/review", headers=MGR, json={"decision": "approve", "note": "可以"}).json()
    assert ok["status"] == "approved" and ok["signature"]

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from catfish_skills_hub.experts import signed_message
    pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(client.get("/experts/signing-key", headers=EMP).json()["public_key"]))
    pub.verify(base64.b64decode(ok["signature"]), signed_message("qual", "1.0.0", ok["sha256"], ok["reviewed_by"], ok["reviewed_at"]))

    assert [m["id"] for m in client.get("/experts", headers=EMP).json()["experts"]] == ["qual"]
    d = client.get("/experts/qual/1.0.0/download", headers=EMP)
    assert d.status_code == 200 and d.headers["X-Catfish-Sha256"] == ok["sha256"]


def test_别的部门看不到_直接下载也拿不到(client):
    submit(client, EMP, pack())
    client.post("/experts/qual/1.0.0/review", headers=MGR, json={"decision": "approve"})
    assert client.get("/experts", headers=OTHER).json()["experts"] == []
    assert client.get("/experts/qual/1.0.0/download", headers=OTHER).status_code == 404
    assert client.get("/experts/qual/1.0.0", headers=OTHER).status_code == 404


def test_驳回要写原因_回到提交人(client):
    submit(client, EMP, pack())
    assert client.post("/experts/qual/1.0.0/review", headers=MGR, json={"decision": "reject"}).status_code == 400
    r = client.post("/experts/qual/1.0.0/review", headers=MGR, json={"decision": "reject", "note": "选路不全"}).json()
    assert r["status"] == "rejected"
    mine = client.get("/experts/mine", headers=EMP).json()["experts"]
    assert mine[0]["status"] == "rejected" and mine[0]["review_note"] == "选路不全"


def test_别部门主管不能审_全公司范围只有管理员能审(client):
    submit(client, EMP, pack())
    assert client.post("/experts/qual/1.0.0/review", headers=OTHER_MGR, json={"decision": "approve"}).status_code == 403
    submit(client, EMP, pack("allco"), scope="company", depts=())
    assert client.post("/experts/allco/1.0.0/review", headers=MGR, json={"decision": "approve"}).status_code == 403
    assert client.post("/experts/allco/1.0.0/review", headers=ADMIN, json={"decision": "approve"}).json()["status"] == "approved"
    assert [m["id"] for m in client.get("/experts", headers=OTHER).json()["experts"]] == ["allco"]


def test_版本只能往上加_别人的包不能冒发(client):
    submit(client, EMP, pack())
    assert submit(client, EMP, pack(version="1.0.0")).status_code == 400
    assert "不比已有的" in submit(client, EMP, pack(version="0.9.0")).json()["detail"]
    assert submit(client, EMP, pack(version="1.1.0")).status_code == 200
    assert "别人发布的" in submit(client, OTHER, pack(version="2.0.0")).json()["detail"]


def test_市场只给最新上架版(client):
    submit(client, EMP, pack())
    client.post("/experts/qual/1.0.0/review", headers=MGR, json={"decision": "approve"})
    submit(client, EMP, pack(version="1.2.0"))
    assert client.get("/experts", headers=EMP).json()["experts"][0]["version"] == "1.0.0", "1.2.0 还没审"
    client.post("/experts/qual/1.2.0/review", headers=MGR, json={"decision": "approve"})
    assert client.get("/experts", headers=EMP).json()["experts"][0]["version"] == "1.2.0"


def test_撤回(client):
    submit(client, EMP, pack())
    assert client.post("/experts/qual/1.0.0/withdraw", headers=OTHER).status_code == 403
    assert client.post("/experts/qual/1.0.0/withdraw", headers=EMP).json()["status"] == "withdrawn"
    assert client.get("/experts/review", headers=MGR).json()["experts"] == []


@pytest.mark.parametrize("bad", [b"not a tar", None])
def test_坏包拒收(client, bad):
    if bad is None:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            info = tarfile.TarInfo("../evil.sh")
            info.size = 1
            tf.addfile(info, io.BytesIO(b"x"))
        bad = buf.getvalue()
    r = submit(client, EMP, bad)
    assert r.status_code == 400
    if b"not a tar" not in bad:
        assert "不安全" in r.json()["detail"], "是路径检查拦的, 不是碰巧缺 expert.yaml"


def test_没身份_401(client):
    assert client.get("/experts").status_code == 401
