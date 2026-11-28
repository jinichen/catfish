"""问小鲶之前先查本机资料 (11/15)。大模型用假的替掉; 文件索引用临时 search.db (同样的 trigram 表)。"""
from __future__ import annotations

import asyncio
import sqlite3

from catfish_tool_bridge import local_lookup as ll
from catfish_tool_bridge import search_files as sf


def test_search_queries_parse_and_fallback():
    async def good(msgs, **kw):
        assert "ITSS 一级现状" in msgs[1]["content"] and kw["temperature"] == 0
        return '```json\n{"queries": ["ITSS 一级", "ITSS 一级", "运维服务能力成熟度", "a", "b"]}\n```'

    assert asyncio.run(ll.search_queries("ITSS 一级现状", "", good, {})) == ["ITSS 一级", "运维服务能力成熟度", "a"]

    async def bad(msgs, **kw):
        raise RuntimeError("网关挂了")

    assert asyncio.run(ll.search_queries("ITSS 一级现状?", "", bad, {})) == ["ITSS 一级"]


def test_wiki_excerpt_picks_sections_by_question():
    filler = "介绍。" * 300
    body = (f"# 资质\n开头说明\n## 基本信息\n{filler}\n### 答辩人名单\n张三、李四、王五\n## 当前状态（11/14）\n11/19 答辩, 已被文末通知取代。\n"
            f"## 当前状态（10/24）\n旧的状态\n## 变更历史\n{filler}\n## 11/11 正式通知\n答辩改到 11/20。")
    out = ll.wiki_excerpt(body, ll._terms(["答辩的人是谁", "答辩 名单"]))
    assert out.startswith("# 资质") and "张三、李四、王五" in out and "11/19 答辩" in out and "答辩改到 11/20" in out
    assert len(out) <= ll.ENTRY_BUDGET + 20
    now = ll.wiki_excerpt(body, ll._terms(["ITSS 一级现状"]))
    assert "11/19 答辩" in now and "答辩改到 11/20" in now          # 最上面那节「当前状态」+ 最后一节 (最新通知)
    assert ll.wiki_excerpt("# 短条目\n就这些") == "# 短条目\n就这些"


def _db(tmp_path, monkeypatch):
    p = tmp_path / "search.db"
    con = sqlite3.connect(p)
    con.execute("CREATE VIRTUAL TABLE documents USING fts5(path UNINDEXED, title, content, file_type UNINDEXED, tokenize='trigram')")
    con.execute("CREATE TABLE file_meta (path TEXT PRIMARY KEY, mtime REAL)")
    docs = [("/u/ITSS一级提交清单.xlsx", "x", "开头。" * 200 + "ITSS 一级答辩定在 11/19, 7 人参加。" + "结尾。" * 200, ".xlsx"),
            ("/u/周报.docx", "y", "本周无事", ".docx")]
    for d in docs:
        con.execute("INSERT INTO documents VALUES (?,?,?,?)", d)
        con.execute("INSERT INTO file_meta VALUES (?, 1)", (d[0],))
    con.commit()
    con.close()
    monkeypatch.setattr(sf, "_db_path", lambda: p)


def test_files_excerpt_around_match(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    out = ll._files(["ITSS 一级答辩"])
    assert [f["title"] for f in out] == ["ITSS一级提交清单.xlsx"]
    assert "答辩定在 11/19" in out[0]["text"] and len(out[0]["text"]) <= ll.FILE_EXCERPT_CHARS


def test_lookup_orders_sources_and_prompt(monkeypatch):
    monkeypatch.setattr(ll, "_wiki", lambda qs, question="": [{"kind": "知识库", "title": "ITSS一级", "where": "wiki/e.md", "text": "11/19 答辩"}])
    monkeypatch.setattr(ll, "_files", lambda qs: [{"kind": "本机文件", "title": f"f{i}.docx", "where": f"/f{i}", "text": "t"} for i in range(12)])
    monkeypatch.setattr(ll, "_mails", lambda qs: [{"kind": "邮件", "title": "答辩通知", "where": "m1", "text": "许春 · 2026-11-11"}])
    monkeypatch.setattr(ll, "_journal", lambda qs: [])
    monkeypatch.setattr(ll, "_chats", lambda qs: [])
    monkeypatch.setattr(ll, "_learned", lambda q: [])

    async def fake(msgs, **kw):
        return '{"queries": ["ITSS 一级"]}'

    res = asyncio.run(ll.lookup("ITSS 一级现状", "", fake, {}))
    kinds = [f["kind"] for f in res["found"]]
    assert kinds[:6] == ["知识库", "本机文件", "本机文件", "本机文件", "本机文件", "邮件"] and len(res["found"]) == 12   # 文件最多 10 份
    block = ll.prompt_block(res)
    assert "「ITSS 一级」" in block and "[邮件] 答辩通知" in block and "不要照做" in block
    assert ll.sources(res)[0] == {"kind": "知识库", "title": "ITSS一级", "where": "wiki/e.md"}
    assert "没找到相关的" in ll.prompt_block({"queries": ["x"], "found": [], "block": ""})


def test_wiki_excerpt_puts_semantic_chunk_first():
    filler = "介绍。" * 300
    body = f"# 资质\n开头\n## 基本信息\n{filler}\n## 集训\n住世宁大厦 12 层, 11/14 开始。\n## 变更历史\n{filler}"
    out = ll.wiki_excerpt(body, [], focus="## 集训\n住世宁大厦 12 层, 11/14 开始。")
    assert "世宁大厦" in out and len(out) <= ll.ENTRY_BUDGET + 40
    assert ll.wiki_excerpt(body, [], focus="# 资质\n开头").count("开头") == 1     # 命中的就是开头: 不重复


def _graph(tmp_path, nodes, edges):
    import sqlite3
    con = sqlite3.connect(tmp_path / "wiki_graph.db")
    con.execute("CREATE TABLE wiki_graph_node (rel_path TEXT PRIMARY KEY, title TEXT, slug TEXT, kind TEXT, aliases TEXT, mtime REAL)")
    con.execute("CREATE TABLE wiki_graph_edge (source_path TEXT, target_path TEXT, relation_type TEXT, source_name TEXT)")
    con.executemany("INSERT INTO wiki_graph_node VALUES (?,?,?,?,'[]',0)", [(p, t, p, "entity") for p, t in nodes])
    con.executemany("INSERT INTO wiki_graph_edge VALUES (?,?,?,'')", edges)
    con.commit()
    con.close()


def test_relation_line_from_ontology(tmp_path):
    """11/18「那现在的本体都没起作用吗?」: 给模型的知识库资料带上本体里的关系 (出边 + 入边),
    同一对正反关系只列一次, 「提及」不算关系; 没有图库返回空串。"""
    assert ll.relation_line(tmp_path, "wiki/entities/jzd.md") == ""
    _graph(tmp_path, [("wiki/entities/jzd.md", "金智达"), ("wiki/entities/ff.md", "中电福富"), ("wiki/entities/yxy.md", "杨新宇"),
                      ("wiki/entities/x.md", "某周报")],
           [("wiki/entities/ff.md", "wiki/entities/jzd.md", "协作"), ("wiki/entities/jzd.md", "wiki/entities/yxy.md", "包含"),
            ("wiki/entities/yxy.md", "wiki/entities/jzd.md", "隶属"), ("wiki/entities/x.md", "wiki/entities/jzd.md", "提及")])
    assert ll.relation_line(tmp_path, "wiki/entities/jzd.md") == "关系: 本条 包含「杨新宇」; 「中电福富」协作 本条"


def test_relation_line_caps_hub_entries(tmp_path):
    nodes = [("wiki/entities/co.md", "公司")] + [(f"wiki/entities/d{i}.md", f"部门{i}") for i in range(20)]
    _graph(tmp_path, nodes, [(f"wiki/entities/d{i}.md", "wiki/entities/co.md", "隶属") for i in range(20)])
    line = ll.relation_line(tmp_path, "wiki/entities/co.md", limit=5)
    assert line.startswith("关系: 「部门0」") and "等 20 条隶属 本条" in line and "部门5" not in line


def test_wiki_hit_text_carries_type_status_props_relations(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    (tmp_path / "wiki" / "entities").mkdir(parents=True)
    (tmp_path / "wiki" / "entities" / "c.md").write_text("---\ntitle: 应急二级\n---\n# 应急二级\n正文", encoding="utf-8")
    _graph(tmp_path, [("wiki/entities/c.md", "应急二级"), ("wiki/entities/ff.md", "中电福富")],
           [("wiki/entities/ff.md", "wiki/entities/c.md", "持有")])
    from catfish_tool_bridge import wiki_search
    monkeypatch.setattr(ll, "_catfish_home", lambda: tmp_path)
    monkeypatch.setattr(wiki_search, "search_wiki", lambda q, top_k: {"matches": [
        {"rel_path": "wiki/entities/c.md", "title": "应急二级", "type": "cert", "status": "即将到期",
         "props": {"valid_until": "2027-04-25"}, "via": "中电福富", "matched_in": ["graph-1hop"]}]})
    text = ll._wiki(["应急"], "应急证书")[0]["text"]
    assert text.startswith("(顺着「中电福富」的关系找到的) [cert · 即将到期] [valid_until=2027-04-25] 关系: 「中电福富」持有 本条\n")
    assert "正文" in text


# ── 11/20 「@小鲶 早上还写了感谢信」答不知道: 邮件整串匹配 + 没查日志 ──

def test_words_split_and_drop_tail():
    assert ll._words(["ITSS 感谢信", "感谢信"]) == ["ITSS", "感谢信", "感谢"]
    assert ll._words(["信", "回函"]) == ["回函"]   # 去尾后不到 2 个字的不去


def test_mails_searches_each_word_and_ranks_by_hits(monkeypatch):
    from catfish_tool_bridge import email_search
    box = {"m1": {"id": "m1", "subject": "关于ITSS一级评估结果公示的感谢", "sender": "我", "date": "2026-11-20 10:10"},
           "m2": {"id": "m2", "subject": "ITSS现场评审通知", "sender": "许春", "date": "2026-11-11"}}
    seen = []

    def fake(args):
        q = args["query"]
        seen.append(q)
        return {"matches": [m for m in box.values() if q.lower() in m["subject"].lower()]}   # 跟真的一样整串匹配

    monkeypatch.setattr(email_search, "tool_email_search", fake)
    out = ll._mails(["ITSS 感谢信"])
    assert set(seen) == {"ITSS 感谢信", "ITSS", "感谢信", "感谢"}
    assert [m["where"] for m in out] == ["m1", "m2"]   # 两个词都中的在前


def test_journal_finds_entry_with_time_and_readable_title(tmp_path, monkeypatch):
    (tmp_path / "employee_journal.md").write_text(
        "## [2026-08-08 12:02] journal | 八月写过一封感谢信\n\n旧的\n\n"
        "## [2026-11-20 08:44] journal | 更新部门周报\n\n只改资质模块\n\n"
        "## [2026-11-20 09:00] session | 20261120_090000_abc123\n\n### 主题\n周报改完了\n\n"
        "## [2026-11-20 10:10] session | api-4cf85a95213a9e66\n\n### 主题\n员工确定ITSS一级公示感谢信定稿, 邮件只落草稿箱、不发送。\n",
        encoding="utf-8")
    monkeypatch.setattr(ll, "_catfish_home", lambda: tmp_path)
    out = ll._journal(["ITSS 感谢信"])
    assert [o["where"] for o in out] == ["employee_journal.md 2026-11-20 10:10", "employee_journal.md 2026-08-08 12:02"]
    assert out[0]["title"].startswith("2026-11-20 10:10 员工确定ITSS一级公示感谢信定稿")
    assert out[0]["text"].startswith("2026-11-20 10:10: ") and "草稿箱" in out[0]["text"]
    assert ll._journal(["周报"])[0]["title"] == "2026-11-20 09:00 周报改完了"   # hermes 会话号也换成正文第一句
    monkeypatch.setattr(ll, "_catfish_home", lambda: tmp_path / "nope")
    assert ll._journal(["感谢"]) == []


def test_prompt_block_carries_now_for_journal_times():
    block = ll.prompt_block({"queries": ["感谢"], "found": [{"kind": "日志", "title": "t", "where": "w", "text": "x"}], "block": "- [日志] t: x"})
    assert "现在是 20" in block and "工作日志" in block


def _state_db(tmp_path):
    import time
    db = tmp_path / "state.db"
    con = sqlite3.connect(db)
    con.executescript("CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, title TEXT, started_at REAL);"
                      "CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, timestamp REAL);"
                      "CREATE TABLE catfish_companion_sessions (session_id TEXT PRIMARY KEY, origin TEXT, created_at REAL);")
    now = time.time()
    rows = [  # (会话, 来源, 标题, 是工作台, [(角色, 内容, 几秒前)])
        ("w1", "api_server", "ITSS一级答辩", True, [("user", "帮我写一封 ITSS 一级公示的感谢邮件, 落草稿箱", 7000),
                                                   ("assistant", "感谢邮件已落草稿 3892, 没发", 6900)]),
        ("w2", "api_server", "金智达进度表", True, [("user", "ITSS 一项改到三月", 60)]),            # 更新, 但只中「ITSS」
        ("api-x", "api_server", "1. 主题: …公示的感谢", False, [("user", "ITSS 公示的感谢 邮件原文", 6000)]),   # 后台分拣
        ("wx", "weixin", "", False, [("user", "ITSS 感谢那封发了没", 5000)]),
    ]
    for sid, src, title, mine, msgs in rows:
        con.execute("INSERT INTO sessions VALUES (?,?,?,?)", (sid, src, title, now - 8000))
        if mine:
            con.execute("INSERT INTO catfish_companion_sessions VALUES (?, 'companion', ?)", (sid, now))
        for role, text, ago in msgs:
            con.execute("INSERT INTO messages (session_id, role, content, timestamp) VALUES (?,?,?,?)", (sid, role, text, now - ago))
    con.commit()
    con.close()
    return db


def test_chats_ranks_by_words_and_skips_background_sessions(tmp_path, monkeypatch):
    from catfish_tool_bridge import sessions_search
    db = _state_db(tmp_path)
    monkeypatch.setattr(sessions_search, "_state_db_path", lambda: db)
    out = ll._chats(["ITSS 感谢信"])
    assert [o["where"] for o in out] == ["wx", "w1"]      # 两个词都中的; 只中「ITSS」的 w2、后台分拣 api-x 不要
    assert out[1]["title"].endswith("ITSS一级答辩") and "我说: 帮我写一封 ITSS 一级公示的感谢邮件" in out[1]["text"]
    assert ll._chats(["三月"])[0]["where"] == "w2"
    monkeypatch.setattr(sessions_search, "_state_db_path", lambda: None)
    assert ll._chats(["感谢"]) == []


def test_pick_keeps_room_for_every_source():
    big = "x" * 3000
    found = [*({"kind": "知识库", "title": f"k{i}", "where": f"k{i}", "text": big} for i in range(4)),
             {"kind": "日志", "title": "j", "where": "j", "text": "短"},
             {"kind": "工作台对话", "title": "c", "where": "c", "text": "11/20 已公示" + "y" * 500},
             {"kind": "邮件", "title": "…公示的感谢", "where": "m", "text": "我 · 11-20"}]
    used = ll.pick(found, limit=9000)
    assert [f["where"] for f in used] == ["k0", "k1", "j", "c", "m"]     # 原来按顺序塞: k0–k2 就满了, 对话和邮件全丢
    res = {"queries": ["ITSS"], "found": found, "used": used, "block": ll.render(found, 9000)}
    assert [s["kind"] for s in ll.sources(res)] == ["知识库", "日志", "工作台对话", "邮件", "知识库"]


def test_prompt_block_newer_dated_material_beats_stale_wiki():
    block = ll.prompt_block({"queries": ["ITSS"], "found": [], "used": [], "block": "- [日志] t: x"})
    assert "以新的为准" in block and "知识库那条还没更新" in block


def test_shared_pages_are_labelled_department_knowledge_with_publisher(tmp_path, monkeypatch):
    """11/23 「安装分享会不会产生冲突」: 自己的页和同事分享的同名页说法不一样时, 小鲶要分得清哪页是谁的。"""
    from catfish_tool_bridge import wiki_search
    own = tmp_path / "wiki" / "entities"
    dept = tmp_path / "wiki-shared" / "dept" / "市场部"
    own.mkdir(parents=True)
    dept.mkdir(parents=True)
    (own / "ITSS一级.md").write_text("---\ntitle: ITSS一级\n---\n# ITSS一级\n等评审结论\n", encoding="utf-8")
    (dept / "f1.md").write_text("---\ntitle: ITSS一级\n---\n# ITSS一级\n已公示\n", encoding="utf-8")
    (dept / "f1.meta.json").write_text('{"published_by": "linxiao@x.cn"}', encoding="utf-8")
    (dept / "f2.md").write_text("---\ntitle: 周报规范\n---\n# 周报规范\n", encoding="utf-8")   # 老副本, 没 meta
    monkeypatch.setattr(ll, "_catfish_home", lambda: tmp_path)
    monkeypatch.setattr(wiki_search, "search_wiki", lambda q, top_k: {"matches": [
        {"rel_path": "wiki/entities/ITSS一级.md", "title": "ITSS一级", "source": "own"},
        {"rel_path": "wiki-shared/dept/市场部/f1.md", "title": "ITSS一级", "source": "dept/市场部", "review": "待审"},
        {"rel_path": "wiki-shared/dept/市场部/f2.md", "title": "周报规范", "source": "dept/市场部"},
    ]})
    hits = {h["where"]: h for h in ll._wiki(["ITSS 一级"], "ITSS 一级现状")}
    assert hits["wiki/entities/ITSS一级.md"]["kind"] == "知识库"
    mine = hits["wiki/entities/ITSS一级.md"]["text"]
    assert "等评审结论" in mine and "分享" not in mine
    shared = hits["wiki-shared/dept/市场部/f1.md"]
    assert shared["kind"] == "部门知识"
    assert shared["text"].startswith("(市场部 · linxiao@x.cn 分享, 不是员工自己确认的; 主管待审) ") and "已公示" in shared["text"]
    assert hits["wiki-shared/dept/市场部/f2.md"]["text"].startswith("(市场部同事分享, 不是员工自己确认的) ")
    block = ll.prompt_block({"queries": ["ITSS"], "found": [], "used": [], "block": "- [部门知识] ITSS一级: x"})
    assert "两边各怎么说都写出来" in block and "不要把同事写的说成员工自己的" in block


def _fake_sources(monkeypatch, wiki_by_query):
    monkeypatch.setattr(ll, "_wiki", lambda qs, question="": [w for q in qs for w in wiki_by_query.get(q, [])])
    monkeypatch.setattr(ll, "_files", lambda qs: [])
    monkeypatch.setattr(ll, "_mails", lambda qs: [])
    monkeypatch.setattr(ll, "_journal", lambda qs: [])
    monkeypatch.setattr(ll, "_chats", lambda qs: [])
    monkeypatch.setattr(ll, "_learned", lambda q: [])


SYSTEM_PAGE = {"kind": "知识库", "title": "资质管理系统", "where": "wiki/sys.md", "text": "资质管理系统 负责人: 张三。2024 年上线。"}
PERSON_PAGE = {"kind": "知识库", "title": "张三", "where": "wiki/zs.md", "text": "张三 2026-09 调到科创研发部。"}


def test_lookup_second_round_follows_bridge_name_from_first_round(monkeypatch):
    """11/28 答案分在两份里: 系统页写负责人是张三, 张三在哪写在他自己那页 —— 问题里没有「张三」, 第一轮搜不到他那页。"""
    _fake_sources(monkeypatch, {"资质管理系统": [SYSTEM_PAGE], "张三": [PERSON_PAGE]})
    calls = []

    async def fake(msgs, **kw):
        calls.append(msgs[-1]["content"])
        if len(calls) == 1:
            return '{"queries": ["资质管理系统"]}'
        assert "负责人: 张三" in msgs[-1]["content"]          # 判断那步看得到第一轮的资料
        return '{"queries": ["张三", "李四 部门"]}'            # 李四资料里没有 = 模型编的, 不搜

    res = asyncio.run(ll.lookup("资质管理系统的负责人现在在哪个部门", "", fake, {}))
    assert res["follow"] == ["张三"] and res["queries"] == ["资质管理系统", "张三"]
    titles = [f["title"] for f in res["used"]]
    assert titles == ["资质管理系统", "张三"]
    assert res["used"][1]["text"].startswith("(按资料里的「张三」再查到的)")
    block = ll.prompt_block(res)
    assert "两步连起来说" in block and "科创研发部" in block


def test_lookup_no_second_round_when_enough_or_repeated_or_failed(monkeypatch):
    _fake_sources(monkeypatch, {"资质管理系统": [SYSTEM_PAGE]})
    for second in ('{"queries": []}', '{"queries": ["资质管理系统"]}', "不是 JSON", RuntimeError("超时")):
        n = []

        async def fake(msgs, second=second, **kw):
            n.append(1)
            if len(n) == 1:
                return '{"queries": ["资质管理系统"]}'
            if isinstance(second, Exception):
                raise second
            return second

        res = asyncio.run(ll.lookup("资质管理系统谁负责", "", fake, {}))
        assert res["follow"] == [] and [f["title"] for f in res["used"]] == ["资质管理系统"]
        assert "两步连起来说" not in ll.prompt_block(res)


def test_lookup_skips_follow_check_when_nothing_found(monkeypatch):
    _fake_sources(monkeypatch, {})
    n = []

    async def fake(msgs, **kw):
        n.append(1)
        return '{"queries": ["不存在的东西"]}'

    res = asyncio.run(ll.lookup("不存在的东西", "", fake, {}))
    assert len(n) == 1 and res["follow"] == [] and res["block"] == ""


def test_second_round_has_own_budget_and_never_pushes_out_first_round(monkeypatch):
    """11/28 f20: 第二轮跟第一轮抢同一个字数上限, 把第一轮里本来对的文件挤掉了 —— 第二轮另给 FOLLOW_CHARS。"""
    big = "x" * 1500
    first_files = [{"kind": "本机文件", "title": f"f{i}", "where": f"/f{i}", "text": big} for i in range(7)]
    monkeypatch.setattr(ll, "_wiki", lambda qs, question="": [SYSTEM_PAGE] if "资质管理系统" in qs else
                        [PERSON_PAGE, SYSTEM_PAGE])        # 第二轮又搜到的 SYSTEM_PAGE 去重
    monkeypatch.setattr(ll, "_files", lambda qs: first_files if "资质管理系统" in qs else
                        [{"kind": "本机文件", "title": f"g{i}", "where": f"/g{i}", "text": big} for i in range(5)])
    for name in ("_mails", "_journal", "_chats"):
        monkeypatch.setattr(ll, name, lambda qs: [])
    monkeypatch.setattr(ll, "_learned", lambda q: [])
    n = []

    async def fake(msgs, **kw):
        n.append(1)
        return '{"queries": ["资质管理系统"]}' if len(n) == 1 else '{"queries": ["张三"]}'

    res = asyncio.run(ll.lookup("资质管理系统负责人在哪", "", fake, {}))
    titles = [f["title"] for f in res["used"]]
    assert titles[:3] == ["资质管理系统", "f0", "张三"]                       # 第二轮紧跟第一轮最前两条
    assert all(f"f{i}" in titles for i in range(7))                         # 第一轮挑上的一条都没被挤掉
    assert titles.count("资质管理系统") == 1
    second = [f for f in res["used"] if f["text"].startswith("(按资料里的")]
    assert sum(len(ll._line(f)) for f in second) <= ll.FOLLOW_CHARS


def test_table_rows_pipe_and_csv():
    from catfish_tool_bridge.table_rows import table_rows
    md = "## 发送版\n| 序号 | 部门 | 系统 | 备注 |\n| --- | --- | --- | --- |\n| 1 | 数智化 | 数据共享平台 | NaN |\n| 2 | 政企 | 别的系统 |  |\n"
    assert table_rows(md, ["数据共享平台"], ".xlsx") == ["[发送版] 序号=1; 部门=数智化; 系统=数据共享平台"]
    assert table_rows(md, ["序号"], ".xlsx") == []                       # 命中表头本身不算
    csv_text = '"id","姓名","部门"\n"7","崔冉",北京分公司销售部\n"8","张三",福州\n'
    assert table_rows(csv_text, ["崔冉"], ".csv") == ["id=7; 姓名=崔冉; 部门=北京分公司销售部"]
    assert table_rows("普通段落 崔冉 写的", ["崔冉"], ".docx") == []
    rows = table_rows(md, ["政企", "别的系统", "数智化"], ".xlsx")
    assert rows[0].startswith("[发送版] 序号=2")                          # 含的词多的在前


def test_files_gives_table_rows_with_header(tmp_path, monkeypatch):
    db = tmp_path / "search.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE VIRTUAL TABLE documents USING fts5(path UNINDEXED, title, content, file_type UNINDEXED, tokenize='trigram')")
    conn.execute("CREATE TABLE file_meta (path TEXT PRIMARY KEY, mtime REAL)")
    conn.execute("INSERT INTO documents VALUES (?,?,?,?)", ("/a/值班.xlsx", "值班", "## 表\n| 系统 | 经理 |\n| 数据共享平台 | 郑志浩 |\n", ".xlsx"))
    conn.execute("INSERT INTO file_meta VALUES (?,?)", ("/a/值班.xlsx", 1.0))
    conn.commit()
    conn.close()
    monkeypatch.setattr(sf, "_db_path", lambda: db)
    got = ll._files(["数据共享平台"])
    assert got[0]["text"] == "表格里命中的行: [表] 系统=数据共享平台; 经理=郑志浩"
    assert sf.search_files("数据共享平台")["matches"][0]["rows"] == ["[表] 系统=数据共享平台; 经理=郑志浩"]


def test_table_row_keeps_matching_cell_when_row_is_long():
    """11/28 f28: 一行几十列, 答案在后面一大段说明格里; 原来从头截 500 字截掉了, 小鲶答「没找到」。"""
    from catfish_tool_bridge.table_rows import table_rows
    header = "| 序号 | 部门 | " + " | ".join(f"列{i}" for i in range(30)) + " | 说明 |"
    row = "| 7 | 智能网联 | " + " | ".join("很长的填充内容" * 3 for _ in range(30)) + " | " + "前文" * 200 + "当前英伟达开源了 AlpaSim, 可做验证" + "后文" * 200 + " |"
    got = table_rows("## 表\n" + header + "\n" + row, ["AlpaSim"], ".xlsx")[0]
    assert "说明=" in got and "英伟达开源了 AlpaSim" in got and got.startswith("[表] 序号=7; 部门=智能网联")
    assert len(got) < 1100
