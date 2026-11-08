"""11/08 知识库写入把关: 改前备份 + updated 换今天 / base_mtime 对不上不覆写 / 相似名查重 / 关系按类型定义校验 /
引用不存在的页点名 / 发布到部门的警告只认界面确认 / 记忆口径挪进知识库不糊进实体页、失败回滚。全部虚构。"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from catfish_tool_bridge import catfish_tools, memory_compact_moves, wiki_files, wiki_guard, wiki_publish, wiki_types
from catfish_tool_bridge import wiki_write_tools as wt


@pytest.fixture()
def home(tmp_path, monkeypatch):
    h = tmp_path / ".catfish"
    for sub in ("wiki/entities", "wiki/concepts", "wiki/queries"):
        (h / sub).mkdir(parents=True)
    monkeypatch.setenv("CATFISH_HOME", str(h))
    return h


def _page(h: Path, rel: str, title: str, body: str = "正文。", **fm):
    lines = [f"title: {title}"] + [f"{k}: {v}" for k, v in fm.items()]
    (h / rel).write_text("---\n" + "\n".join(lines) + "\n---\n\n" + body + "\n", encoding="utf-8")


# ── 备份 + updated ────────────────────────────────────────────────

def test_整页覆写_先备份_updated换成今天_最多留二十份(home):
    _page(home, "wiki/entities/a.md", "甲", type="entity", entity_type="org", created="2026-01-01", updated="2026-01-01")
    for i in range(23):
        r = wiki_files.update_wiki_file("wiki/entities/a.md", f"---\ntitle: 甲\ntype: entity\nentity_type: org\ncreated: 2026-01-01\n"
                                                              f"updated: 2026-01-01\n---\n\n第 {i} 版\n")
        assert r["ok"]
    fm, body = wiki_files._split_frontmatter((home / "wiki/entities/a.md").read_text())
    assert wiki_files._parse_field(fm, "updated") == time.strftime("%Y-%m-%d") and "第 22 版" in body
    hist = wiki_guard.history(home, "wiki/entities/a.md")
    assert len(hist) == wiki_guard.HISTORY_KEEP and "第 21 版" in Path(hist[0]["path"]).read_text()
    assert wt.tool_wiki_history({"rel_path": "wiki/entities/a.md", "limit": 3})["count"] == wiki_guard.HISTORY_KEEP


def test_没有updated这一行的_补上_没frontmatter的不动():
    out = wiki_guard.touch_updated("---\ntitle: x\ncreated: 2026-01-01\ntags: []\n---\n\n正文\n", today="2026-11-08")
    assert out.startswith("---\ntitle: x\ncreated: 2026-01-01\nupdated: 2026-11-08\ntags: []\n---")
    assert wiki_guard.touch_updated("没有文件头的\n") == "没有文件头的\n"


def test_改属性也备份_也刷新updated(home):
    _page(home, "wiki/entities/c.md", "证书", type="entity", entity_type="cert", updated="2026-01-01")
    r = wiki_types.set_props_file("wiki/entities/c.md", {"valid_until": "2030-01-01"})
    assert r["ok"] and r["changed"]
    txt = (home / "wiki/entities/c.md").read_text()
    assert f"updated: {time.strftime('%Y-%m-%d')}" in txt and "valid_until: " in txt
    assert len(wiki_guard.history(home, "wiki/entities/c.md")) == 1


# ── base_mtime ───────────────────────────────────────────────────

def test_读过之后别人改过_不覆写_把现在的内容交回去(home):
    _page(home, "wiki/entities/a.md", "甲", type="entity", entity_type="org")
    got = wiki_files.read_wiki_file("wiki/entities/a.md")
    time.sleep(0.01)
    (home / "wiki/entities/a.md").write_text(got["content"].replace("正文。", "界面上刚改的。"))
    os.utime(home / "wiki/entities/a.md", (time.time() + 5, time.time() + 5))
    r = wt.tool_wiki_update({"rel_path": "wiki/entities/a.md", "content": got["content"].replace("正文。", "小鲶的改法。"),
                             "base_mtime": got["mtime"]})
    assert not r["ok"] and r["stale"] and "界面上刚改的" in r["content"] and "没有覆写" in r["error"]
    assert "界面上刚改的" in (home / "wiki/entities/a.md").read_text()
    r2 = wt.tool_wiki_update({"rel_path": "wiki/entities/a.md", "content": r["content"].replace("界面上刚改的", "合并后"),
                              "base_mtime": r["mtime"]})
    assert r2["ok"] and "合并后" in (home / "wiki/entities/a.md").read_text()


# ── 相似名 ───────────────────────────────────────────────────────

def test_相似名归一():
    n = wiki_guard.normalize_name
    assert n("中电福富信息科技有限公司") == n("中电福富") == n("中电福富（信息科技）")
    assert n("云堤公司") == n("云堤") and n("公司董事会") != n("董事会")
    assert n("ISO 27001") == n("iso27001")


def test_像不像_人力部与人力资源部算像_北京分公司与长名字不算():
    alike = wiki_guard.names_alike
    assert alike("人力部", "人力资源部") and alike("云堤", "云堤公司") and alike("董事会", "公司董事会")
    assert not alike("北京分公司", "北京福富软件技术股份有限公司福州分公司")
    assert not alike("中国电信", "中国电信海南公司") and not alike("CCRC 信息安全服务资质", "CCRC 信息安全服务资质软件安全开发一级")
    assert alike("揭榜挂帅", "揭榜挂帅机制") and alike("墨攻平台", "墨攻安全运营平台")
    assert not alike("星辰", "星辰平台"), "两个字的名字不比, 太容易误中"
    assert not alike("财务部", "人力资源部") and not alike("ab", "abc")


def test_建页_跟已有的很像就拦_确认不同才建(home):
    _page(home, "wiki/entities/yundi.md", "云堤", type="entity", entity_type="org")
    r = wt.tool_wiki_create({"kind": "entity", "title": "云堤公司", "subtype": "org", "body": "x"})
    assert not r["ok"] and "很像" in r["error"] and r["similar"][0]["title"] == "云堤" and r["rel_path"] == "wiki/entities/yundi.md"
    r = wt.tool_wiki_create({"kind": "entity", "title": "云堤公司", "subtype": "org", "body": "x", "allow_similar": True})
    assert r["ok"]
    assert wt.tool_wiki_create({"kind": "entity", "title": "云堤", "subtype": "org", "body": "x"})["error"].startswith("「云堤」已是")


# ── 关系按类型定义 ────────────────────────────────────────────────

def test_关系类型不对的拦下_说能用什么(home):
    _page(home, "wiki/entities/cert.md", "ISO 9001 证书", type="entity", entity_type="cert")
    _page(home, "wiki/entities/zhang.md", "张三", type="entity", entity_type="person")
    bad, missing = wiki_guard.relation_problems("person", [{"name": "ISO 9001 证书", "rel": "包含"}])
    assert len(bad) == 1 and "不能用「包含」" in bad[0] and "负责" in bad[0] and missing == []
    bad, missing = wiki_guard.relation_problems("person", [{"name": "ISO 9001 证书", "rel": "负责"}, {"name": "不存在的页", "rel": "参与"}])
    assert bad == [] and missing == ["不存在的页"]
    r = wt.tool_wiki_create({"kind": "entity", "title": "李四", "subtype": "person", "body": "x",
                             "related": [{"name": "ISO 9001 证书", "rel": "包含"}]})
    assert not r["ok"] and "关系用得不对" in r["error"]
    r = wt.tool_wiki_create({"kind": "entity", "title": "李四", "subtype": "person", "body": "x",
                             "related": [{"name": "ISO 9001 证书", "rel": "负责"}, {"name": "不存在的页", "rel": "参与"}]})
    assert r["ok"] and r["missing_relations"] == ["不存在的页"] and "「不存在的页」, 知识库里没有这一页" in r["warning"]
    assert "unresolved_relation" not in r["warning"] and "引用的页找不到" in r["warning"]


def test_没类型定义的对象_词表外和自定义的关系_不拦(home):
    _page(home, "wiki/entities/ev.md", "某活动", type="entity", entity_type="event")   # event 没有类型定义
    _page(home, "wiki/entities/p.md", "王五", type="entity", entity_type="person")
    _page(home, "wiki/entities/q.md", "赵六", type="entity", entity_type="person")
    assert wiki_guard.relation_problems("person", [{"name": "某活动", "rel": "依据"}]) == ([], [])
    assert wiki_guard.relation_problems("", [{"name": "王五", "rel": "协作"}]) == ([], [])
    assert wiki_guard.relation_problems("person", [{"name": "赵六", "rel": "关联"}]) == ([], []), "兜底的「关联」不算"
    assert wiki_guard.relation_problems("person", [{"name": "赵六", "rel": "师徒"}]) == ([], []), "词表外 / 自定义的关系不归定义管"


# ── 发布到部门: 警告只认界面确认 ───────────────────────────────────

def test_小鲶自己加确认不算_界面点的才算(home, monkeypatch):
    _page(home, "wiki/entities/k.md", "客户机房", "机房 IP 10.0.0.8, 联系人手机 13800000000。", type="entity", entity_type="org")
    monkeypatch.setattr(wiki_publish, "_read_id_token", lambda: "tok")
    sent = []

    class _Resp:
        status_code = 200

        def json(self):
            return {"file_id": "f1", "namespace": "dept/研发部", "published_at": "2026-11-08T00:00:00Z"}

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, **k):
            sent.append(json.loads(k["content"]))
            return _Resp()
    import httpx
    monkeypatch.setattr(httpx, "Client", _Client)
    r = wiki_publish.wiki_publish({"wiki_rel_path": "wiki/entities/k.md"})
    assert not r["ok"] and r["scan_phase"] == "warnings" and r["confirm_in_ui"]["wiki_rel_path"] == "wiki/entities/k.md"
    r = wiki_publish.wiki_publish({"wiki_rel_path": "wiki/entities/k.md", "acknowledge_warnings": True})
    assert not r["ok"] and "你不能替员工确认" in r["error"] and sent == []
    r = wiki_publish.wiki_publish({"wiki_rel_path": "wiki/entities/k.md", "acknowledge_warnings": True, "_from_ui": True})
    assert r["ok"] and len(sent) == 1


# ── 记忆口径挪进知识库 ───────────────────────────────────────────

def test_口径不糊进实体页_失败回滚(home):
    _page(home, "wiki/entities/fufu.md", "中电福富", type="entity", entity_type="org", aliases='["福富"]')
    memory_compact_moves.save("abc123", {"title": "福富", "body": "报销走 OA。"})
    r = memory_compact_moves.apply("abc123")
    assert not r["ok"] and "不是做事口径" in r["error"]
    assert "报销走 OA" not in (home / "wiki/entities/fufu.md").read_text()
    memory_compact_moves.save("abc124", {"title": "报销口径", "body": "报销走 OA。"})
    r = memory_compact_moves.apply("abc124")
    assert r["ok"] and r["created"] == "wiki/concepts/报销口径.md"
    memory_compact_moves.undo(r)
    assert not (home / "wiki/concepts/报销口径.md").exists()
    _page(home, "wiki/concepts/bx.md", "报销口径", "原来的。", type="concept", concept_type="rule")
    r = memory_compact_moves.apply("abc124")
    assert r["ok"] and r["appended"] == "wiki/concepts/bx.md" and "报销走 OA" in (home / "wiki/concepts/bx.md").read_text()
    memory_compact_moves.undo(r)
    assert "报销走 OA" not in (home / "wiki/concepts/bx.md").read_text()


def test_工具入口_create走把关_update走把关(home):
    _page(home, "wiki/entities/yundi.md", "云堤", type="entity", entity_type="org")
    r = catfish_tools.dispatch_native("catfish_wiki_create", {"kind": "entity", "title": "云堤公司", "subtype": "org", "body": "x"})
    assert not r["ok"] and "很像" in r["error"]
    got = wiki_files.read_wiki_file("wiki/entities/yundi.md")
    r = wiki_types.tool_dispatch("catfish_wiki_update", {"rel_path": "wiki/entities/yundi.md", "content": got["content"],
                                                         "base_mtime": got["mtime"]})
    assert r["ok"]


def test_界面保存前的校验_入口(home):
    _page(home, "wiki/entities/cert.md", "ISO 9001 证书", type="entity", entity_type="cert")
    _page(home, "wiki/entities/yundi.md", "云堤", type="entity", entity_type="org")
    r = wiki_guard.check({"title": "云堤公司", "subtype": "org", "new": True,
                          "related": [{"name": "ISO 9001 证书", "rel": "包含"}, {"name": "没有的页", "rel": "持有"}]})
    assert not r["ok"] and r["bad"][0]["rel"] == "包含" and r["missing"] == ["没有的页"] and r["similar"][0]["title"] == "云堤"
    assert "很像" in r["similar_message"] and "没有这一页" in r["missing_message"]
    r = wiki_guard.check({"title": "云堤", "subtype": "org", "rel_path": "wiki/entities/yundi.md",
                          "related": [{"name": "ISO 9001 证书", "rel": "持有"}]})
    assert r["ok"] and r["similar"] == [] and r["bad"] == []
