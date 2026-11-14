"""分享到部门: 来源 / 关系同事那边能不能看到 (11/09, wiki_share.py)。数据全是虚构的。

钉住:
1. 发出去那份: 本机路径 (raw/sources/…) 和对话编号换成人话; 原件一起分享了的换成 [[原件 · 名称]]; 本机文件不动
2. 原件能当单独一页发 (标题「原件 · 名称」, 原件自己的头部不发), 扫描照样做
3. 预检清单: 每条来源能不能一起分享、每条关系同事能不能看到 (已在部门 / 本机有 / 都没有 / 客户档案不能发)
4. 同事装一页时, 同一个人分享的关联页一起装上; 别人分享的同名页不算
"""
from __future__ import annotations

import json
import urllib.parse

import httpx
import pytest

from catfish_tool_bridge import wiki_install, wiki_publish, wiki_share

ENTRY = """---
type: entity
title: 市场部
entity_type: department
aliases: []
related: [{name: "中电福富信息科技有限公司", rel: "隶属"}, {name: "销售单元", rel: "下辖"}, {name: "北部分公司", rel: "下辖"}, {name: "某客户", rel: "服务"}]
sources: ["raw/sources/1783325598-福富组织架构20260423", "journal:2026-08-15"]
---

# 市场部

福富市场经营体系一级部。另见 [[营销手册]]。

## 关联

- **来源**: raw/sources/1783325598-福富组织架构20260423
- **出处**: session:20261101_091002_aaa111
"""


@pytest.fixture()
def home(tmp_path, monkeypatch):
    root = tmp_path / ".catfish"
    monkeypatch.setenv("CATFISH_HOME", str(root))
    monkeypatch.setattr(wiki_publish, "_sensitive_terms_path", lambda: tmp_path / "none")
    ent = root / "wiki" / "entities"
    ent.mkdir(parents=True)
    (root / "wiki" / "concepts").mkdir()
    (ent / "市场部.md").write_text(ENTRY, encoding="utf-8")
    (ent / "中电福富.md").write_text("---\ntitle: 中电福富信息科技有限公司\n---\n\n公司\n", encoding="utf-8")
    (ent / "销售单元.md").write_text("---\ntitle: 销售单元\n---\n\n单元\n", encoding="utf-8")
    (ent / "某客户.md").write_text("---\ntitle: 某客户\nentity_type: customer\n---\n\n客户原话\n", encoding="utf-8")
    raw = root / "wiki" / "raw" / "sources"
    raw.mkdir(parents=True)
    (raw / "1783325598-福富组织架构20260423.md").write_text(
        "---\nimported: 2026-04-23\n---\n\n# 福富组织架构\n市场经营体系: 市场部 …\n", encoding="utf-8")
    return root


@pytest.fixture()
def gateway(monkeypatch):
    """routes[(method, path)] = fn(req) -> Response; 记下所有请求"""
    state: dict = {"reqs": [], "routes": {}}

    def handler(req: httpx.Request) -> httpx.Response:
        state["reqs"].append(req)
        fn = state["routes"].get((req.method, urllib.parse.unquote(req.url.path)))
        return fn(req) if fn else httpx.Response(404, json={"detail": "nope"})

    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda *a, **kw: real(*a, transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(wiki_publish.skill_publish, "_read_id_token", lambda: "tok")
    return state


def _hub_list(*docs: dict) -> callable:
    return lambda req: httpx.Response(200, json={"documents": list(docs), "count": len(docs)})


# ── 1. 改写 ───────────────────────────────────────────────────


def test_rewrite_turns_local_paths_into_words_and_attached_sources_into_links():
    fm, body = wiki_publish._parse_frontmatter(ENTRY)
    new_fm, new_body = wiki_share.rewrite_for_share(fm, body, set())
    assert 'sources: ["原件「福富组织架构20260423」(在发布人那里, 没随这页分享)", "发布人的对话记录 (没随这页分享)"]' in new_fm
    assert "raw/sources" not in new_fm + new_body
    assert "session:" not in new_body and "journal:" not in new_fm
    assert "- **出处**: 发布人的对话记录" in new_body
    # 其余行一字不动
    assert 'related: [{name: "中电福富信息科技有限公司", rel: "隶属"}' in new_fm and "entity_type: department" in new_fm

    _, linked = wiki_share.rewrite_for_share(fm, body, {"raw/sources/1783325598-福富组织架构20260423"})
    assert "- **来源**: [[原件 · 福富组织架构20260423]]" in linked


def test_rewrite_handles_block_list_sources_and_manual():
    fm = 'title: x\nsources:\n  - manual\n  - "raw/sources/1700000000-规范.md"\ntags: [a]'
    new_fm, _ = wiki_share.rewrite_for_share(fm, "", {"raw/sources/1700000000-规范"})
    assert new_fm == 'title: x\nsources: ["manual", "原件 · 规范"]\ntags: [a]'


# ── 2. 发布: 发出去的是改写后的, 原件能当一页发 ─────────────────


def _capture_post(state):
    def post(req):
        state.setdefault("payloads", []).append(json.loads(req.content))
        n = len(state["payloads"])
        return httpx.Response(200, json={"ok": True, "namespace": "dept/研发部", "file_id": f"fid{n:06d}",
                                         "published_at": "2026-11-09T00:00:00Z", "updated_at": "2026-11-09T00:00:00Z"})
    return post


def test_publish_sends_rewritten_copy_and_leaves_local_file_alone(home, gateway):
    gateway["routes"][("POST", "/v1/wiki/documents")] = _capture_post(gateway)
    r = wiki_publish.wiki_publish({"wiki_rel_path": "wiki/entities/市场部.md", "_from_ui": True,
                                   "attached_sources": ["raw/sources/1783325598-福富组织架构20260423"]})
    assert r["ok"], r
    sent = gateway["payloads"][0]
    assert "[[原件 · 福富组织架构20260423]]" in sent["body_md"]
    assert "raw/sources" not in sent["body_md"] + sent["frontmatter_yaml"]
    assert (home / "wiki" / "entities" / "市场部.md").read_text(encoding="utf-8") == ENTRY


def test_publish_source_original_as_its_own_page(home, gateway):
    gateway["routes"][("POST", "/v1/wiki/documents")] = _capture_post(gateway)
    rel = wiki_share.source_publish_path("raw/sources/1783325598-福富组织架构20260423")
    assert rel == "raw/sources/1783325598-福富组织架构20260423.md"
    r = wiki_publish.wiki_publish({"wiki_rel_path": rel, "_from_ui": True})
    assert r["ok"], r
    sent = gateway["payloads"][0]
    assert sent["title"] == "原件 · 福富组织架构20260423" and sent["kind"] == "concept"
    assert "concept_type: source_original" in sent["frontmatter_yaml"]
    assert sent["body_md"].startswith("# 福富组织架构") and "imported:" not in sent["body_md"]
    # 再发是更新同一页
    r2 = wiki_publish.wiki_publish({"wiki_rel_path": rel, "_from_ui": True})
    assert gateway["payloads"][1]["file_id"] == "fid000001" and r2["ok"]


def test_source_original_with_credentials_is_refused(home, gateway):
    raw = home / "wiki" / "raw" / "sources" / "1700000001-账号.md"
    raw.write_text("数据库 password = Sup3rS3cretPassw0rd!\n", encoding="utf-8")
    r = wiki_publish.wiki_publish({"wiki_rel_path": "raw/sources/1700000001-账号.md", "_from_ui": True})
    assert not r["ok"] and r["scan_phase"] == "credentials"
    assert gateway["reqs"] == []


def test_source_path_stays_inside_raw_sources(home):
    assert wiki_share.source_path("raw/sources/../../entities/市场部") is None
    assert wiki_share.source_path("raw/sources/不存在") is None


# ── 3. 预检清单 ───────────────────────────────────────────────


def test_plan_lists_sources_and_relations_with_what_colleagues_can_see(home, gateway, monkeypatch):
    # 「销售单元」我分享过; 「北部分公司」别人分享过 (中央上有); 「中电福富」本机有、没分享; 「营销手册」哪都没有
    monkeypatch.setattr(wiki_publish, "_load_publish_state",
                        lambda: {"wiki/entities/销售单元.md": {"file_id": "x", "namespace": "dept/研发部"}})
    gateway["routes"][("GET", "/v1/wiki/documents")] = _hub_list(
        {"namespace": "dept/研发部", "file_id": "f1", "title": "北部分公司", "published_by": "bob@x.cn"})
    p = wiki_share.plan({"wiki_rel_path": "wiki/entities/市场部.md",
                         "attach_sources": ["raw/sources/1783325598-福富组织架构20260423"]})
    assert p["ok"] and p["title"] == "市场部" and p["hub_known"]
    rel = {r["name"]: r for r in p["relations"]}
    assert rel["中电福富信息科技有限公司"]["status"] == "local"
    assert rel["中电福富信息科技有限公司"]["rel_path"] == "wiki/entities/中电福富.md"
    assert rel["中电福富信息科技有限公司"]["scan"] == {"blocked": None, "warnings": []}
    assert rel["销售单元"]["status"] == "shared"
    assert rel["北部分公司"]["status"] == "shared"
    assert rel["某客户"]["status"] == "private"
    assert rel["营销手册"]["status"] == "missing"
    src = {s["kind"]: s for s in p["sources"]}
    assert src["raw"]["attachable"] and src["raw"]["title"] == "原件 · 福富组织架构20260423" and not src["raw"]["shared"]
    assert src["conversation"]["label"] == "发布人的对话记录 (没随这页分享)"
    assert "[[原件 · 福富组织架构20260423]]" in p["preview"]["body_md"]
    # 预览用的头部字段: 跟知识页同一套解析, 来源是改写后的说法
    f = p["preview"]["fields"]
    assert f["title"] == "市场部" and f["entity_type"] == "department"
    assert f["related"][0] == {"name": "中电福富信息科技有限公司", "rel": "隶属"}
    assert f["sources"] == ["原件 · 福富组织架构20260423", "发布人的对话记录 (没随这页分享)"]
    assert f["evidence"] == []


def test_publish_scope_company_is_sent_and_remembered(home, gateway, monkeypatch):
    # 11/09 全公司: scope 跟着发; 下次不传 scope 沿用上次的
    state: dict = {}
    monkeypatch.setattr(wiki_publish, "_load_publish_state", lambda: state)
    monkeypatch.setattr(wiki_publish, "_save_publish_state", lambda rel, e: state.__setitem__(rel, e))
    gateway["routes"][("POST", "/v1/wiki/documents")] = _capture_post(gateway)
    r = wiki_publish.wiki_publish({"wiki_rel_path": "wiki/entities/市场部.md", "_from_ui": True, "scope": "company"})
    assert r["ok"] and r["scope"] == "company" and "管理员审过" in r["summary"]
    assert gateway["payloads"][0]["scope"] == "company"
    wiki_publish.wiki_publish({"wiki_rel_path": "wiki/entities/市场部.md", "_from_ui": True})
    assert gateway["payloads"][1]["scope"] == "company" and gateway["payloads"][1]["file_id"] == "fid000001"
    wiki_publish.wiki_publish({"wiki_rel_path": "wiki/entities/市场部.md", "_from_ui": True, "scope": "department"})
    assert gateway["payloads"][2]["scope"] == "department"
    bad = wiki_publish.wiki_publish({"wiki_rel_path": "wiki/entities/市场部.md", "_from_ui": True, "scope": "all"})
    assert not bad["ok"] and len(gateway["payloads"]) == 3


def test_plan_marks_what_other_departments_can_open(home, gateway, monkeypatch):
    # 「销售单元」我发过, 只在部门里; 「北部分公司」别人发的、全公司可见; 上次这页发到全公司, 等管理员审
    monkeypatch.setattr(wiki_publish, "_load_publish_state", lambda: {
        "wiki/entities/销售单元.md": {"file_id": "s1", "namespace": "dept/研发部"},
        "wiki/entities/市场部.md": {"file_id": "m1", "namespace": "dept/研发部", "scope": "company"}})
    gateway["routes"][("GET", "/v1/wiki/documents")] = _hub_list(
        {"namespace": "dept/研发部", "file_id": "s1", "title": "销售单元", "visible_to": "department"},
        {"namespace": "dept/销售部", "file_id": "f1", "title": "北部分公司", "visible_to": "company", "scope": "company",
         "company_status": "approved"},
        {"namespace": "dept/研发部", "file_id": "m1", "title": "市场部", "visible_to": "department", "scope": "company",
         "company_status": "rejected", "company_note": "有经营数字"})
    p = wiki_share.plan({"wiki_rel_path": "wiki/entities/市场部.md"})
    rel = {r["name"]: r for r in p["relations"]}
    assert rel["北部分公司"]["status"] == "shared" and rel["北部分公司"]["company"] is True
    sales = rel["销售单元"]
    assert sales["status"] == "shared" and sales["company"] is False and sales["mine"] is True
    assert sales["scan"] == {"blocked": None, "warnings": []}, "我发过的部门页, 发全公司时可以一起重发, 要先扫"
    assert p["scope_before"] == "company"
    assert p["hub_status"] == {"scope": "company", "company_status": "rejected", "company_note": "有经营数字",
                               "visible_to": "department"}
    assert p["hub_ref"] == {"namespace": "dept/研发部", "file_id": "m1"}, "11/14 对话框「历史版本」按它查"
    assert wiki_share.plan({"wiki_rel_path": "wiki/entities/销售单元.md"})["hub_ref"]["file_id"] == "s1"
    assert p["sources"][0]["shared_company"] is False and p["sources"][0]["mine"] is False


def test_plan_without_hub_still_works(home, gateway):
    gateway["routes"][("GET", "/v1/wiki/documents")] = lambda req: httpx.Response(502)
    p = wiki_share.plan({"wiki_rel_path": "wiki/entities/市场部.md"})
    assert p["ok"] and not p["hub_known"]
    assert {r["name"]: r["status"] for r in p["relations"]}["北部分公司"] == "missing"


def test_plan_refuses_customer_page(home, gateway):
    p = wiki_share.plan({"wiki_rel_path": "wiki/entities/某客户.md"})
    assert not p["ok"] and "客户档案" in p["error"]


# ── 4. 安装: 同一个人分享的关联页一起装 ───────────────────────


def _doc(fid: str, title: str, by: str, body: str = "正文", fm: str = "") -> dict:
    return {"namespace": "dept/研发部", "file_id": fid, "title": title, "kind": "entity", "published_by": by,
            "frontmatter_yaml": fm or f"title: {title}", "body_md": body, "updated_at": "2026-11-09T00:00:00Z"}


def test_install_brings_linked_pages_from_same_publisher(home, gateway):
    main = _doc("main0001", "市场部", "alice@x.cn", body="见 [[原件 · 福富组织架构20260423]]",
                fm='title: 市场部\nrelated: [{name: "中电福富信息科技有限公司", rel: "隶属"}, {name: "销售单元", rel: "下辖"}]')
    docs = {
        "main0001": main,
        "src00001": _doc("src00001", "原件 · 福富组织架构20260423", "alice@x.cn"),
        "comp0001": _doc("comp0001", "中电福富信息科技有限公司", "alice@x.cn"),
        "sale0001": _doc("sale0001", "销售单元", "bob@x.cn"),   # 别人分享的同名页: 不装
    }
    for fid, d in docs.items():
        gateway["routes"][("GET", f"/v1/wiki/documents/dept/研发部/{fid}")] = (lambda d: lambda req: httpx.Response(200, json=d))(d)
    gateway["routes"][("GET", "/v1/wiki/documents")] = _hub_list(*docs.values())
    r = wiki_install.wiki_install({"hub_namespace": "dept/研发部", "hub_file_id": "main0001"})
    assert r["ok"], r
    assert sorted(r["also_installed"]) == sorted(["原件 · 福富组织架构20260423", "中电福富信息科技有限公司"])
    shared = home / "wiki-shared" / "dept" / "研发部"
    assert sorted(p.name for p in shared.glob("*.md")) == ["comp0001.md", "main0001.md", "src00001.md"]
    assert "也一起装上了" in r["summary"]
    # 只装一层: 关联页自己的关联不再往外拉 (列表只拉一次)
    assert sum(1 for q in gateway["reqs"] if q.url.path == "/v1/wiki/documents") == 1


# ── 11/12 发出去那份不带本机路径 (原件页开头小鲶写的 SOURCE_PATH / METHOD, 正文里的绝对路径) ──

def test_scrub_local_去掉说明行和本机路径_留文件名():
    body = ("# Upload: 中电福富工作汇报（栾总） -v1.65.pptx\n\n"
            "SOURCE_PATH: /Users/someone/Downloads/材料库/2024/中电福富工作汇报（栾总） -v1.65.pptx\n"
            "METHOD: python-pptx full extract\n\n"
            "## 第 1 页\n详见 /Users/someone/Downloads/材料库/附件一.docx。另见 C:\\Users\\zhang\\Desktop\\方案.pdf 和 ~/Documents/对标.xlsx\n"
            "目录 /Users/someone/Downloads/材料库 里有; 网址 https://example.com/a/b.html 和相对路径 docs/a.md 不动")
    out = wiki_share.scrub_local(body)
    assert out.startswith("## 第 1 页")
    assert "/Users/" not in out and "C:\\" not in out and "~/" not in out and "SOURCE_PATH" not in out and "METHOD" not in out
    assert "「附件一.docx」" in out and "「方案.pdf」 和 「对标.xlsx」" in out and "目录 「材料库」 里有" in out
    assert "https://example.com/a/b.html" in out and "docs/a.md" in out


def test_rewrite_for_share_也去本机路径():
    _, body = wiki_share.rewrite_for_share("title: x", "见 /Users/someone/Desktop/规范.pdf 第二节。")
    assert body == "见 「规范.pdf」 第二节。"
