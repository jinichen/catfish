"""wiki-hub 端到端.

9/30 之前这条链路一次都没通过: 员工填的 namespace 是 `dept/finance`, 路由是
`/wiki/documents/{namespace}` 一个路径段, POST 一律 405。这里用的就是客户端
真实会发的 URL 形态, 防止再出现"两边各自测过, 拼起来不通"。

每条测试跑两遍: FS 存储 (没配 PG 的部署) 和 PG 存储。PG 那遍要
CATFISH_TEST_PG_URL 指向一个已 `alembic upgrade head` 的库, 没设就跳过。
"""
from __future__ import annotations

import os
import urllib.parse

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(params=["fs", "pg"])
def client(request, tmp_path, monkeypatch):
    # app import 时会 load .env (override=False); 先 import 再设环境, 否则开发机上
    # .env 里的 CATFISH_DB_URL 会让 FS 那遍测试打到真 PG
    from catfish_wiki_hub.app import app

    monkeypatch.setenv("CATFISH_HUB_ROOT", str(tmp_path))
    if request.param == "fs":
        monkeypatch.delenv("CATFISH_DB_URL", raising=False)
    else:
        pg_url = os.environ.get("CATFISH_TEST_PG_URL", "").strip()
        if not pg_url:
            pytest.skip("CATFISH_TEST_PG_URL 未设")
        monkeypatch.setenv("CATFISH_DB_URL", pg_url)
        from catfish_wiki_hub import storage

        with storage._pg_conn() as conn:
            conn.execute("TRUNCATE wiki_documents, wiki_audit")

    return TestClient(app)


def _h(sub: str, dept: str, role: str = "employee") -> dict:
    # gateway 对非 ASCII 做 percent-encode (wiki_hub_proxy._safe_header_value)
    return {
        "X-Catfish-User-Sub": sub,
        "X-Catfish-User-Dept": urllib.parse.quote(dept, safe=""),
        "X-Catfish-User-Role": role,
    }


ALICE = _h("alice@x", "研发部")
BOB = _h("bob@x", "研发部")
CAROL = _h("carol@x", "销售部")
ADMIN = _h("root@x", "", "admin")
NODEPT = _h("dave@x", "")


def _publish(client, headers, file_id=None, body="正文 v1", title="老李"):
    payload = {
        "filename": "wiki/entities/老李.md",
        "title": title,
        "kind": "entity",
        "frontmatter_yaml": f"title: {title}",
        "body_md": body,
    }
    if file_id:
        payload["file_id"] = file_id
    return client.post("/wiki/documents", json=payload, headers=headers)


def _doc_url(dept: str, file_id: str) -> str:
    return f"/wiki/documents/dept/{urllib.parse.quote(dept)}/{file_id}"


def test_publish_goes_to_publishers_own_dept(client):
    r = _publish(client, ALICE)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["namespace"] == "dept/研发部"
    assert data["updated_at"]


def test_publish_without_dept_rejected(client):
    r = _publish(client, NODEPT)
    assert r.status_code == 400
    assert "部门" in r.json()["detail"]


def test_list_is_scoped_to_own_dept(client):
    _publish(client, ALICE)
    _publish(client, CAROL, title="客户甲")

    def titles(h):
        return [d["title"] for d in client.get("/wiki/documents", headers=h).json()["documents"]]

    assert titles(BOB) == ["老李"]
    assert titles(CAROL) == ["客户甲"]
    assert sorted(titles(ADMIN)) == ["客户甲", "老李"]
    assert titles(NODEPT) == []


def test_list_requires_identity(client):
    assert client.get("/wiki/documents").status_code == 401


def test_get_other_dept_forbidden(client):
    fid = _publish(client, ALICE).json()["file_id"]
    assert client.get(_doc_url("研发部", fid), headers=BOB).status_code == 200
    assert client.get(_doc_url("研发部", fid), headers=CAROL).status_code == 403
    assert client.get(_doc_url("研发部", fid), headers=ADMIN).status_code == 200


def test_republish_updates_same_row_and_bumps_updated_at(client):
    first = _publish(client, ALICE).json()
    fid = first["file_id"]
    second = _publish(client, ALICE, file_id=fid, body="正文 v2").json()
    assert second["file_id"] == fid
    assert second["updated_at"] >= first["updated_at"]

    docs = client.get("/wiki/documents", headers=BOB).json()["documents"]
    assert len(docs) == 1
    doc = client.get(_doc_url("研发部", fid), headers=BOB).json()
    assert doc["body_md"] == "正文 v2"
    assert doc["published_by"] == "alice@x"
    assert doc["updated_at"] == second["updated_at"]


def test_republish_by_someone_else_forbidden(client):
    fid = _publish(client, ALICE).json()["file_id"]
    r = _publish(client, BOB, file_id=fid, body="篡改")
    assert r.status_code == 403
    doc = client.get(_doc_url("研发部", fid), headers=BOB).json()
    assert doc["body_md"] == "正文 v1"


def test_unpublish_marks_stale_and_keeps_metadata(client):
    fid = _publish(client, ALICE).json()["file_id"]
    url = _doc_url("研发部", fid) + "/unpublish"

    assert client.post(url, json={}, headers=BOB).status_code == 403
    r = client.post(url, json={"reason": "写错了"}, headers=ALICE)
    assert r.status_code == 200, r.text

    doc = client.get(_doc_url("研发部", fid), headers=BOB).json()
    assert doc["stale_after_unpublish"] is True
    assert doc["body_md"] == ""
    assert doc["unpublished_reason"] == "写错了"
    listed = client.get("/wiki/documents", headers=BOB).json()["documents"]
    assert [d["stale_after_unpublish"] for d in listed] == [True]
    fresh = client.get("/wiki/documents?include_stale=false", headers=BOB).json()
    assert fresh["count"] == 0

    assert client.post(url, json={}, headers=ALICE).status_code == 400


def test_bad_dept_in_path_rejected(client):
    assert client.get("/wiki/documents/dept/..%2Fx/abc123", headers=ADMIN).status_code in (400, 404)
    assert client.get(_doc_url("a:b", "abc123"), headers=ADMIN).status_code == 400


@pytest.mark.parametrize("dept,ok", [
    ("研发部", True),
    ("finance", True),
    ("", False),
    ("a/b", False),
    ("..", False),
    (".hidden", False),
    ("a:b", False),
    (" x", False),
    ("x" * 65, False),
])
def test_validate_dept(dept, ok):
    from catfish_wiki_hub.namespaces import validate_dept

    assert (validate_dept(dept) is None) is ok


# ── 销售漏斗摘要 (10/12, 销售跟进阶段 3) ──

def _mgr(sub: str, dept: str, managed: list[str]) -> dict:
    h = _h(sub, dept, "manager")
    h["X-Catfish-User-Managed-Depts"] = ",".join(urllib.parse.quote(d, safe="") for d in managed)
    return h


SALES_A = _h("sales-a@x", "销售部")
SALES_B = _h("sales-b@x", "销售部")
SALES_MGR = _mgr("boss@x", "总经办", ["销售部"])
OTHER_MGR = _mgr("rd-boss@x", "研发部", ["研发部"])


def _publish_summary(client, headers, funnel, customers, file_id=None):
    import json as _json

    summary = {"owner": headers["X-Catfish-User-Sub"], "funnel": funnel, "customers": customers}
    payload = {
        "filename": "catfish/sales-summary.md", "title": "销售漏斗", "kind": "query",
        "frontmatter_yaml": "title: 销售漏斗\ncatfish_doc: sales_summary\nsales_summary: "
                            + _json.dumps(summary, ensure_ascii=False),
        "body_md": "| 客户 | 阶段 |",
    }
    if file_id:
        payload["file_id"] = file_id
    r = client.post("/wiki/documents", json=payload, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["file_id"]


def test_sales_summary_hidden_from_colleagues(client):
    fid = _publish_summary(client, SALES_A, [{"stage": "线索", "count": 2, "deal_total": 0}], [{"name": "南岭建设"}])
    _publish(client, SALES_B)  # 普通 wiki 照常互相可见
    seen_by_b = [d["file_id"] for d in client.get("/wiki/documents", headers=SALES_B).json()["documents"]]
    assert fid not in seen_by_b and len(seen_by_b) == 1
    assert client.get(_doc_url("销售部", fid), headers=SALES_B).status_code == 403
    assert client.get(_doc_url("销售部", fid), headers=SALES_A).status_code == 200  # 本人
    assert client.get(_doc_url("销售部", fid), headers=ADMIN).status_code == 200


def test_sales_funnel_for_managers_only(client):
    fid = _publish_summary(client, SALES_A, [{"stage": "线索", "count": 2, "deal_total": 0},
                                             {"stage": "已报价", "count": 1, "deal_total": 120000}], [{"name": "南岭建设"}])
    _publish_summary(client, SALES_A, [{"stage": "线索", "count": 3, "deal_total": 0},
                                       {"stage": "已报价", "count": 1, "deal_total": 120000}], [{"name": "南岭建设"}], file_id=fid)
    _publish_summary(client, SALES_B, [{"stage": "已报价", "count": 2, "deal_total": 80000},
                                       {"stage": "成交", "count": 1, "deal_total": 50000}], [{"name": "恒远检测"}])
    url = f"/wiki/sales/dept/{urllib.parse.quote('销售部')}"
    r = client.get(url, headers=SALES_MGR)
    assert r.status_code == 200, r.text
    data = r.json()
    assert sorted(m["published_by"] for m in data["members"]) == ["sales-a@x", "sales-b@x"]
    funnel = {f["stage"]: f for f in data["funnel"]}
    assert [f["stage"] for f in data["funnel"]] == ["线索", "已报价", "成交"]
    assert funnel["线索"]["count"] == 3 and funnel["已报价"]["count"] == 3 and funnel["已报价"]["deal_total"] == 200000
    assert client.get(url, headers=ADMIN).status_code == 200
    for who in (SALES_A, OTHER_MGR):
        assert client.get(url, headers=who).status_code == 403
    # 主管能在部门知识库列表里看到摘要 (管的部门), 也能打开
    assert client.get(_doc_url("销售部", fid), headers=SALES_MGR).status_code == 200
