"""学习第 3 步 (11/18, learning_graph.py): 要点 + 推理链 —— 提炼 / 认同一条 / 合并 / 化简 / 删出处 / 挂知识库 / 检索。大模型用假的。"""
from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest

from catfish_tool_bridge import gateway_llm, learning, learning_graph as G, learning_store, local_lookup


def _llm(monkeypatch, replies: list):
    seen: list = []

    async def fake(messages, **kw):
        seen.append(messages)
        r = replies.pop(0)
        return r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)
    monkeypatch.setattr(gateway_llm, "call_llm", fake)
    return seen


KW = {"gateway_url": "http://gw", "auth_token": "t", "source": "x"}
A = {"points": [{"id": "p1", "name": "二级项目数", "statement": "CCRC 应急处理二级要求近三年完成至少 3 个应急处理项目。", "aliases": []},
                {"id": "p2", "name": "项目要过程记录", "statement": "每个应急处理项目都要有完整的过程记录。", "aliases": []},
                {"id": "p3", "name": "二级材料齐", "statement": "二级申报材料要包含 3 个有完整过程记录的项目。", "aliases": []}],
     "chains": [{"premises": ["p1", "p2"], "conclusion": "p3"}]}


def test_tokens_and_search(tmp_path):
    assert G.tokens("CCRC 二级") == ["二级", "ccrc"]
    con = G.connect(tmp_path)
    G.merge(con, "s1", A, {})
    hits = G.search(con, "应急处理二级要几个项目")
    assert hits[0]["name"] == "二级项目数"
    assert G.search(con, "差旅报销") == []


def test_ingest_extract_audit_merge_and_same_point_from_two_sources(tmp_path, monkeypatch):
    seen = _llm(monkeypatch, [A])                                              # 图空: 不调认同
    r = asyncio.run(G.ingest(str(tmp_path), "learn:l1", "学习: CCRC", "learn", "二级要三个项目, 每个要过程记录。", KW))
    assert r == {"added": 3, "linked": 0, "chains": 1, "dropped": 0, "parts": 1} and len(seen) == 1
    con = G.connect(tmp_path)
    p1 = con.execute("SELECT id FROM points WHERE name = '二级项目数'").fetchone()[0]
    # 第二份资料说的是同一条 (换了说法) → 认同后并进去, 出处两份
    B = {"points": [{"id": "p1", "name": "二级三个项目", "statement": "应急处理二级: 近三年至少三个项目。", "aliases": ["二级项目要求"]}],
         "chains": []}
    seen = _llm(monkeypatch, [B, {"links": [{"id": "p1", "link_to": p1}]}])
    r = asyncio.run(G.ingest(str(tmp_path), "file:abc", "资料: 实施规则.pdf", "file", "近三年至少三个项目", KW))
    assert r["added"] == 0 and r["linked"] == 1
    cand = json.loads(seen[1][1]["content"].split("\n", 1)[1])
    assert cand["new"][0]["candidates"][0] == p1 and "同一主题" in seen[1][0]["content"]
    srcs = [x[0] for x in con.execute("SELECT source FROM point_src WHERE point_id = ?", (p1,))]
    assert sorted(srcs) == ["file:abc", "learn:l1"]
    al = json.loads(con.execute("SELECT aliases FROM points WHERE id = ?", (p1,)).fetchone()[0])
    assert "二级项目要求" in al and "应急处理二级: 近三年至少三个项目。" in al
    # 删一份: 另一份也提到的留着, 只有它提到的删掉
    G.delete_source(str(tmp_path), "learn:l1")
    left = {x[0] for x in con.execute("SELECT name FROM points")}
    assert left == {"二级项目数"} and con.execute("SELECT count(*) FROM chains").fetchone()[0] == 0


def test_extract_validation_sent_back(tmp_path, monkeypatch):
    seen = _llm(monkeypatch, [{"points": [{"id": "p1", "name": "x"}], "chains": [{"premises": ["p9"], "conclusion": "p1"}]}, A])
    asyncio.run(G.ingest(str(tmp_path), "s", "l", "file", "t", KW))
    msg = seen[1][-1]["content"]
    assert "id / name / statement" in msg and "不存在的 id: p9" in msg


def test_audit_link_must_be_a_given_candidate(tmp_path, monkeypatch):
    con = G.connect(tmp_path)
    G.merge(con, "s1", A, {})
    pts = [{"id": "p1", "name": "二级项目数", "statement": "二级三个项目", "aliases": []}]
    seen = _llm(monkeypatch, [{"links": [{"id": "p1", "link_to": "pt_ffffffff"}]}, {"links": []}])
    assert asyncio.run(G.audit(con, pts, KW)) == {}
    assert "只能连到给它的候选里" in seen[1][-1]["content"]


def test_simplify_drops_redundant_chains(tmp_path):
    con = G.connect(tmp_path)
    found = {"points": [{"id": f"p{i}", "name": f"n{i}", "statement": f"s{i}", "aliases": []} for i in range(1, 6)],
             "chains": [{"premises": ["p1"], "conclusion": "p3"},
                        {"premises": ["p1", "p2"], "conclusion": "p3"},          # p1 已经够 → 多余
                        {"premises": ["p3"], "conclusion": "p4"},
                        {"premises": ["p1"], "conclusion": "p4"}]}               # 先保留前提少的; 同为 1 个按先后
    r = G.merge(con, "s", found, {})
    keys = {row[0] for row in con.execute("SELECT key FROM chains")}
    assert r["dropped"] >= 1 and not any(k.count("+") and k.endswith(">" + k.split(">")[1]) and "p" in k for k in [])
    names = {r2["id"]: r2["name"] for r2 in con.execute("SELECT id, name FROM points")}
    readable = {"+".join(sorted(names[x] for x in k.split(">")[0].split("+"))) + ">" + names[k.split(">")[1]] for k in keys}
    assert "n1>n3" in readable and "n1+n2>n3" not in readable


def test_link_wiki_by_title_in_text(tmp_path):
    db = sqlite3.connect(tmp_path / "wiki_graph.db")
    db.execute("CREATE TABLE wiki_graph_node (rel_path TEXT PRIMARY KEY, title TEXT, slug TEXT, kind TEXT, aliases TEXT, mtime REAL)")
    db.execute("INSERT INTO wiki_graph_node VALUES ('wiki/c.md', 'CCRC 应急处理二级', 'c', 'entity', '[]', 0)")
    db.execute("INSERT INTO wiki_graph_node VALUES ('wiki/x.md', '二级', 'x', 'entity', '[\"应急\"]', 0)")
    db.commit()
    db.close()
    con = G.connect(tmp_path)
    G.merge(con, "s", A, {})
    assert G.link_wiki(con, str(tmp_path)) == 1
    row = con.execute("SELECT wiki, wiki_title FROM points WHERE name = '二级项目数'").fetchone()
    assert tuple(row) == ("wiki/c.md", "CCRC 应急处理二级")


def test_view_relevant_and_lookup(tmp_path, monkeypatch):
    con = G.connect(tmp_path)
    G._register(con, "learn:l1", "学习: CCRC", "learn")
    G.merge(con, "learn:l1", A, {})
    v = G.view(str(tmp_path))
    assert v["points"] == 3 and v["chains"] == 1 and v["sources"][0]["label"] == "学习: CCRC"
    p3 = next(p for p in v["sources"][0]["points"] if p["name"] == "二级材料齐")
    assert p3["chains"] == ["二级项目数 + 项目要过程记录 ⇒ 二级材料齐"]
    rel = G.relevant(str(tmp_path), "二级申报材料要什么")
    assert rel[0]["name"] == "二级材料齐" and rel[0]["chains"]
    assert G.relevant(str(tmp_path / "nope"), "x") == []
    monkeypatch.setattr(local_lookup, "_catfish_home", lambda: tmp_path)
    found = local_lookup._learned("二级申报材料")
    assert found[0]["kind"] == "学过的要点" and "推理: 二级项目数 + 项目要过程记录 ⇒ 二级材料齐" in found[0]["text"]


def test_chunks_keep_lines():
    text = "\n".join(f"第{i}行" + "字" * 100 for i in range(200))
    parts = G.chunks(text, 3000)
    assert len(parts) > 1 and all(len(p) <= 3000 for p in parts) and "".join(parts) == text


def test_session_ingest_and_delete_session_keeps_shared(tmp_path, monkeypatch):
    learning._RUNNING.clear()
    meta = learning_store.create(tmp_path, "准备 CCRC 答辩", "quick")
    d = learning_store.session_dir(tmp_path, meta["id"])
    learning_store.append(d, {"role": "tutor", "kind": "say", "text": "二级要近三年 3 个项目, 每个要过程记录。"})
    _llm(monkeypatch, [A])
    v = asyncio.run(learning.handle("learn/ingest", {"catfish_home": str(tmp_path), "auth_token": "t", "gateway_url": "http://gw",
                                                    "model": "m", "id": meta["id"]}))
    asyncio.run(asyncio.sleep(0))

    async def wait():
        await asyncio.gather(*list(learning._TASKS))
    asyncio.run(wait())
    evs = learning_store.events(d)
    assert any(e["kind"] == "graph" and e["added"] == 3 for e in evs), v
    con = G.connect(tmp_path)
    p1 = con.execute("SELECT id FROM points WHERE name = '二级项目数'").fetchone()[0]
    con.execute("INSERT INTO point_src VALUES (?, 'file:x', 0)", (p1,))
    con.commit()
    asyncio.run(learning.handle("learn/delete", {"catfish_home": str(tmp_path), "id": meta["id"]}))
    assert {r[0] for r in con.execute("SELECT name FROM points")} == {"二级项目数"}


def test_graph_rpc_import_and_delete(tmp_path, monkeypatch):
    from catfish_tool_bridge import colleague_rooms_bigfiles, learning_graph_rpc as R
    f = tmp_path / "stage.bin"
    f.write_text("CCRC 应急处理二级要求近三年至少 3 个项目。\n每个项目要有过程记录。", encoding="utf-8")
    monkeypatch.setattr(colleague_rooms_bigfiles, "staged", lambda sid: f)
    _llm(monkeypatch, [A])
    p = {"catfish_home": str(tmp_path), "auth_token": "t", "gateway_url": "http://gw", "model": "m"}
    with pytest.raises(ValueError, match="只收"):
        asyncio.run(learning.handle("learn/graph_import", {**p, "stage_id": "x", "name": "a.exe"}))

    async def go():
        r = await learning.handle("learn/graph_import", {**p, "stage_id": "x", "name": "实施规则.txt"})
        assert r["parts"] == 1 and r["chars"] > 10   # 假模型秒回, 返回时可能已经跑完
        await asyncio.gather(*list(R._TASKS))
        return await learning.handle("learn/graph_view", p)
    v = asyncio.run(go())
    assert v["running"] is None and v["last"]["added"] == 3 and v["sources"][0]["label"] == "资料: 实施规则.txt"
    src = v["sources"][0]["source"]
    with pytest.raises(ValueError, match="跟着那次学习走"):
        asyncio.run(learning.handle("learn/graph_delete", {**p, "source": "learn:x"}))
    v = asyncio.run(learning.handle("learn/graph_delete", {**p, "source": src}))
    assert v["points"] == 0 and v["last"]["deleted"]["points"] == 3
