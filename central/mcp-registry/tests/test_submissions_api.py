"""11/08 员工提交连接器 → 审批 → 上架; 审批人试用 (docs/SHARE-GOVERNANCE-PLAN.md §3.4)。全部虚构。"""
from __future__ import annotations

import shutil
import urllib.parse
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from catfish_mcp_registry.app import app
from catfish_mcp_registry.db import SubscriptionDB
from catfish_mcp_registry.loader import ManifestRegistry
from catfish_mcp_registry.submissions import Submissions


def H(sub, dept, role="employee", managed=()):
    q = urllib.parse.quote
    return {"X-Catfish-User-Sub": sub, "X-Catfish-User-Dept": q(dept, safe=""), "X-Catfish-User-Role": role,
            "X-Catfish-User-Managed-Depts": ",".join(q(m, safe="") for m in managed)}


EMP = H("zhou@x", "安全部")
MATE = H("wang@x", "安全部")
MGR = H("boss@x", "安全部", "manager", ["安全部"])
OTHER_MGR = H("eng@x", "工程部", "manager", ["工程部"])
ADMIN = H("admin@x", "IT", "admin")

MANIFEST = """
id: {id}
name: 工单系统
version: "1.0.0"
description: 查工单
provider: 安全部
status: active
allowed_dept: []
mcp_command:
  type: python
  module: catfish_tool_bridge.connectors.time_server
  args: []
  env: {{}}
tools:
  - name: list_tickets
    description: 列工单
auth_type: none
"""


@pytest.fixture()
def client(tmp_path: Path):
    real = Path(__file__).resolve().parent.parent / "manifests"
    mdir = tmp_path / "manifests"
    shutil.copytree(real, mdir)
    reg = ManifestRegistry(mdir)
    reg.load_all()
    app.state.registry = reg
    app.state.db = SubscriptionDB(backend="sqlite", sqlite_path=tmp_path / "t.db")
    app.state.submissions = Submissions(mdir)
    return TestClient(app)


def submit(c, headers, sid="tickets", scope="department", **extra):
    return c.post("/v1/mcp/submit", headers=headers,
                  json={"manifest_yaml": MANIFEST.format(id=sid), "scope": scope, "note": "请审", **extra})


def test_提交进待审_主管通过后上架_同事能订阅(client):
    r = submit(client, EMP)
    assert r.status_code == 200, r.text
    assert r.json()["submission"]["status"] == "pending" and "等部门主管审核" in r.json()["note"]
    assert client.get("/v1/mcp/manifest/tickets", headers=MATE).status_code == 404, "待审的同事看不到"
    assert client.post("/v1/mcp/subscribe", headers=MATE, json={"connector_id": "tickets"}).status_code == 404
    assert [s["id"] for s in client.get("/v1/mcp/review", headers=MGR).json()["submissions"]] == ["tickets"]
    assert client.get("/v1/mcp/review", headers=OTHER_MGR).json()["submissions"] == []
    assert client.post("/v1/mcp/submissions/tickets/review", headers=MATE, json={"decision": "approve"}).status_code == 403
    ok = client.post("/v1/mcp/submissions/tickets/review", headers=MGR, json={"decision": "approve"}).json()["submission"]
    assert ok["status"] == "approved" and ok["reviewed_by"] == "boss@x"
    man = client.get("/v1/mcp/manifest/tickets", headers=MATE).json()
    assert man["manifest"]["allowed_dept"] == ["安全部"] and man["trial"] is False
    assert client.post("/v1/mcp/subscribe", headers=MATE, json={"connector_id": "tickets"}).json()["next_step"] == "ready"
    assert "tickets" not in [c["id"] for c in client.get("/v1/mcp/registry", headers=H("li@x", "工程部")).json()["connectors"]]
    assert submit(client, EMP).status_code == 400, "已上架的 id 不能再提交"


def test_驳回要写原因_全公司只有管理员审_我提交的能看到原因(client):
    submit(client, EMP, scope="company")
    assert client.post("/v1/mcp/submissions/tickets/review", headers=MGR, json={"decision": "approve"}).status_code == 403
    r = client.post("/v1/mcp/submissions/tickets/review", headers=ADMIN, json={"decision": "reject"})
    assert r.status_code == 400 and "写原因" in r.json()["detail"]
    client.post("/v1/mcp/submissions/tickets/review", headers=ADMIN, json={"decision": "reject", "note": "连的是生产库"})
    mine = client.get("/v1/mcp/mine", headers=EMP).json()["submissions"]
    assert mine[0]["status"] == "rejected" and mine[0]["review_note"] == "连的是生产库"
    assert client.get("/v1/mcp/submissions/tickets", headers=EMP).json()["manifest_yaml"].strip().startswith("id: tickets")
    assert client.get("/v1/mcp/submissions/tickets", headers=MATE).status_code == 404


def test_审批人试用_要有测试账号说明_同事不能试(client):
    submit(client, EMP, sid="nocreds")
    r = client.post("/v1/mcp/subscribe", headers=MGR, json={"connector_id": "nocreds", "trial": True})
    assert r.status_code == 403 and "测试账号" in r.json()["detail"]
    submit(client, EMP, sid="withcreds", trial_credentials="测试账号 test/test, 只连测试环境")
    assert client.post("/v1/mcp/subscribe", headers=MATE, json={"connector_id": "withcreds", "trial": True}).status_code == 403
    man = client.get("/v1/mcp/manifest/withcreds", headers=MGR).json()
    assert man["trial"] is True and man["manifest"]["id"] == "withcreds"
    r = client.post("/v1/mcp/subscribe", headers=MGR, json={"connector_id": "withcreds", "trial": True})
    assert r.status_code == 200 and r.json()["next_step"] == "ready"
    rec = client.get("/v1/mcp/submissions/withcreds", headers=MGR).json()["submission"]
    assert rec["trials"][0]["by"] == "boss@x"
    audit = app.state.db.list_audit(limit=20)
    assert any(a["action"] == "subscribe_trial" for a in audit)
