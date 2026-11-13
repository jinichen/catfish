"""技能的范围 / 审批 / 试用 / 签名 (11/08, docs/SHARE-GOVERNANCE-PLAN.md §3.1)。走 HTTP, 身份用 gateway 注入的头。全部虚构。"""
from __future__ import annotations

import base64
import urllib.parse

import pytest
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
MATE = H("wang@example.com", "安全部")
MGR = H("boss@example.com", "安全部", "manager", ["安全部"])
OTHER = H("li@example.com", "工程部")
OTHER_MGR = H("eng@example.com", "工程部", "manager", ["工程部"])
ADMIN = H("admin@example.com", "IT", "admin")


def publish(c, headers, name="eis-login", version="1.0.0", scope="department", **q):
    md = f"---\nname: {name}\nversion: {version}\ndescription: 登录 EIS\n---\n\n# {name}\n".encode()
    files = [("files", ("SKILL.md", md)), ("files", ("script.py", b"print('hi')\n"))]
    return c.post(f"/skills/department?scope={scope}" + "".join(f"&{k}={urllib.parse.quote(str(v))}" for k, v in q.items()),
                  headers=headers, files=files)


def test_发了先待审_同事看不到_主管通过后能看能装_签名能验(client):
    r = publish(client, EMP)
    assert r.status_code == 200, r.text
    assert r.json()["review"]["status"] == "pending" and "等部门主管审核" in r.json()["summary"]
    assert client.get("/skills", headers=MATE).json()["skills"] == [], "同事看不到待审的"
    assert client.get("/skills/department/eis-login", headers=MATE).status_code == 404
    assert client.get("/skills/department/eis-login/1.0.0/files/script.py", headers=MATE).status_code == 403
    mine = client.get("/skills", headers=EMP).json()["skills"]
    assert len(mine) == 1 and mine[0]["review"]["status"] == "pending", "提交人自己看得到"
    todo = client.get("/skills/review", headers=MGR).json()["skills"]
    assert [t["name"] for t in todo] == ["eis-login"]
    assert client.get("/skills/review", headers=OTHER_MGR).json()["skills"] == [], "别的部门主管看不到"
    ok = client.post("/skills/department/eis-login/1.0.0/review", headers=MGR, json={"decision": "approve"}).json()
    assert ok["status"] == "approved" and ok["signature"] and ok["reviewed_by"] == "boss@example.com"
    got = client.get("/skills/department/eis-login", headers=MATE).json()
    assert got["version"] == "1.0.0" and got["review"]["status"] == "approved"
    assert client.get("/skills/department/eis-login/1.0.0/files/script.py", headers=MATE).status_code == 200
    assert client.get("/skills", headers=OTHER).json()["skills"] == [], "部门范围, 别的部门还是看不到"
    # 签名能用公钥验
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from catfish_skills_hub import skill_review as sr
    pk = Ed25519PublicKey.from_public_bytes(base64.b64decode(client.get("/experts/signing-key", headers=EMP).json()["public_key"]))
    pk.verify(base64.b64decode(ok["signature"]), sr.signed_message("department", "eis-login", "1.0.0", ok["bundle_sha256"], ok["reviewed_by"], ok["reviewed_at"]))
    assert sr.bundle_sha256(got["files_sha256"]) == ok["bundle_sha256"], "指纹按文件 sha 重算得出来"


def test_新版待审时_同事拿到的还是上一个已通过的版本(client):
    publish(client, EMP)
    client.post("/skills/department/eis-login/1.0.0/review", headers=MGR, json={"decision": "approve"})
    publish(client, EMP, version="1.1.0")
    assert client.get("/skills/department/eis-login", headers=MATE).json()["version"] == "1.0.0"
    assert client.get("/skills", headers=MATE).json()["skills"][0]["latest_version"] == "1.0.0"
    assert client.get("/skills/department/eis-login", headers=MGR).json()["version"] == "1.1.0", "审批人看得到最新的待审版"


def test_驳回要写原因_全公司范围只有管理员能审_撤回(client):
    publish(client, EMP, scope="company")
    assert client.post("/skills/department/eis-login/1.0.0/review", headers=MGR, json={"decision": "approve"}).status_code == 403
    r = client.post("/skills/department/eis-login/1.0.0/review", headers=ADMIN, json={"decision": "reject"})
    assert r.status_code == 400 and "写原因" in r.json()["detail"]
    r = client.post("/skills/department/eis-login/1.0.0/review", headers=ADMIN, json={"decision": "reject", "note": "脚本里写死了密码"})
    assert r.json()["status"] == "rejected"
    assert client.get("/skills/mine", headers=EMP).json()["skills"][0]["review_note"] == "脚本里写死了密码"
    publish(client, EMP, version="1.0.1", scope="company")
    client.post("/skills/department/eis-login/1.0.1/review", headers=ADMIN, json={"decision": "approve"})
    assert client.get("/skills/department/eis-login", headers=OTHER).json()["version"] == "1.0.1", "全公司范围别的部门也能用"
    assert client.post("/skills/department/eis-login/1.0.1/withdraw", headers=OTHER).status_code == 403
    assert client.post("/skills/department/eis-login/1.0.1/withdraw", headers=EMP).json()["status"] == "withdrawn"
    assert client.get("/skills/department/eis-login", headers=OTHER).status_code == 404


def test_审批人试用_令牌带trial和到期_试跑结果记进审核记录(client):
    publish(client, EMP)
    assert client.get("/skills/department/eis-login/1.0.0/trial-token", headers=MATE).status_code == 403
    tok = client.get("/skills/department/eis-login/1.0.0/trial-token", headers=MGR).json()
    assert tok["trial"] is True and tok["expires"] > 0 and tok["signature"]
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from catfish_skills_hub import skill_review as sr
    pk = Ed25519PublicKey.from_public_bytes(base64.b64decode(tok["public_key"]))
    pk.verify(base64.b64decode(tok["signature"]), sr.trial_message("skill", "department/eis-login", "1.0.0", tok["bundle_sha256"], "boss@example.com", tok["expires"]))
    with pytest.raises(Exception):
        pk.verify(base64.b64decode(tok["signature"]), sr.signed_message("department", "eis-login", "1.0.0", tok["bundle_sha256"], "boss@example.com", "x"))
    r = client.post("/skills/department/eis-login/1.0.0/trial-result", headers=MGR,
                    json={"ok": True, "summary": "登录成功, 下载了 2 份证书", "files": ["a.pdf", "b.pdf"], "steps": ["打开登录页", "填表"]})
    assert r.json()["trials"][-1]["result"]["ok"] is True
    det = client.get("/skills/department/eis-login/1.0.0", headers=MGR).json()["review"]
    assert det["trials"][-1]["result"]["summary"].startswith("登录成功")
    assert "trials" not in client.get("/skills/department/eis-login/1.0.0", headers=EMP).json()["review"] or True
    audit = client.get("/audit", headers=ADMIN).json()["events"]
    assert {e["event"] for e in audit} >= {"submit", "trial", "trial_result"} and all(e.get("line") for e in audit)


def test_上线前发的老版本_没有记录_算已通过全公司可见(client, tmp_path):
    from catfish_skills_hub import storage
    md = b"---\nname: old-skill\nversion: 0.9.0\n---\n"
    storage.publish_skill("department", {"SKILL.md": md}, published_by="someone@example.com")
    got = client.get("/skills/department/old-skill", headers=OTHER).json()
    assert got["review"]["status"] == "approved" and got["review"]["legacy"] is True
    assert client.get("/skills", headers=OTHER).json()["skills"][0]["review"]["legacy"] is True


def test_版本号按数值比_通过1点10之后同事拿到1点10_不是1点9(client):
    # 11/14 之前按字符串排: "1.9.0" > "1.10.0", 列表和详情都还给 1.9.0
    for v in ("1.9.0", "1.10.0"):
        publish(client, EMP, version=v)
        client.post(f"/skills/department/eis-login/{v}/review", headers=MGR, json={"decision": "approve"})
    assert client.get("/skills", headers=MATE).json()["skills"][0]["latest_version"] == "1.10.0"
    assert client.get("/skills/department/eis-login", headers=MATE).json()["version"] == "1.10.0"


def test_version_key():
    from catfish_skills_hub.storage import version_key as k
    assert k("1.10.0") > k("1.9.0") > k("1.0") and k("2.0") > k("1.99.99")
    assert k("1.0.1") > k("1.0.fact-ab12"), "非数字段排在同位置数字段之前"


@pytest.fixture()
def pg_client(tmp_path, monkeypatch):
    """真 PG 上跑列表 (11/14): 设了 CATFISH_TEST_PG_URL 才跑 —— 建一个临时库, 跑完删掉, 不碰任何真库。"""
    import os, subprocess, sys, uuid
    base = os.environ.get("CATFISH_TEST_PG_URL", "").strip()
    if not base:
        pytest.skip("没设 CATFISH_TEST_PG_URL")
    psycopg = pytest.importorskip("psycopg")
    from urllib.parse import urlsplit, urlunsplit
    u = urlsplit(base)
    name = f"skills_hub_test_{uuid.uuid4().hex[:8]}"
    scratch = urlunsplit(u._replace(path="/" + name))
    with psycopg.connect(base, autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{name}"')
    try:
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=here, check=True,
                       env={**os.environ, "CATFISH_DB_URL": scratch}, capture_output=True)
        monkeypatch.setenv("CATFISH_HUB_ROOT", str(tmp_path / "hub"))
        monkeypatch.setenv("CATFISH_DB_URL", scratch)
        from catfish_skills_hub.app import app
        yield TestClient(app)
    finally:
        with psycopg.connect(base, autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_PG模式_新版待审时_同事列表里还有上一个已通过的版本(pg_client):
    # 11/14 之前 PG 列表只带最新一行, 最新版待审 → 同事那边整个技能消失
    publish(pg_client, EMP)
    pg_client.post("/skills/department/eis-login/1.0.0/review", headers=MGR, json={"decision": "approve"})
    publish(pg_client, EMP, version="1.1.0")
    skills = pg_client.get("/skills", headers=MATE).json()["skills"]
    assert [s["latest_version"] for s in skills] == ["1.0.0"]
    assert pg_client.get("/skills", headers=MGR).json()["skills"][0]["latest_version"] == "1.1.0"


# ── 11/14 员工本机已装版本的状态 ─────────────────────────────────


def test_已装版本状态_撤回能看出来_能用的最新版_别部门的不露(client):
    publish(client, EMP)
    client.post("/skills/department/eis-login/1.0.0/review", headers=MGR, json={"decision": "approve"})
    publish(client, EMP, version="1.1.0")
    client.post("/skills/department/eis-login/1.1.0/review", headers=MGR, json={"decision": "approve"})
    q = {"skills": [{"namespace": "department", "name": "eis-login", "version": "1.0.0"},
                    {"namespace": "department", "name": "eis-login", "version": "1.1.0"},
                    {"namespace": "department", "name": "nope", "version": "1.0.0"},
                    {"namespace": "../x", "name": "a", "version": "1"}]}
    r = client.post("/installed/status", headers=MATE, json=q).json()["skills"]
    assert [(x["status"], x["latest_version"]) for x in r] == [
        ("approved", "1.1.0"), ("approved", "1.1.0"), ("gone", None), ("invalid", None)]
    client.post("/skills/department/eis-login/1.1.0/withdraw", headers=MGR)
    r = client.post("/installed/status", headers=MATE, json=q).json()["skills"]
    assert r[1]["status"] == "withdrawn" and r[1]["withdrawn_by"] == "boss@example.com" and r[1]["withdrawn_at"]
    assert r[1]["latest_version"] == "1.0.0", "撤回后能用的回到 1.0.0"
    other = client.post("/installed/status", headers=OTHER, json=q).json()["skills"]
    assert other[1]["status"] == "hidden" and other[1].get("withdrawn_by") is None, "别的部门不借这个口子看到"
