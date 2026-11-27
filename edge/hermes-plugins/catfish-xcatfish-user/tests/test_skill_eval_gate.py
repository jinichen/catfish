"""skill_eval_gate + skill_eval_learn (11/27): 改技能过验证题 / 矛盾检查 / 删并检查, 员工原话出题改题。

模型换成假的: 按系统提示分辨是作答、判分、出题、查矛盾还是查删并, 各按测试里定的规则回。
"""
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("seg_under_test", Path(__file__).parents[1] / "skill_eval_gate.py")
seg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(seg)
L = seg._learn()

RULE = "换证不算新增"


class Fake:
    def __init__(self):
        self.calls = []
        self.learn = {"add": [], "update": []}
        self.conflict_word = None
        self.judge_ok = False

    def chat_with(self, model):
        def chat(messages, max_tokens):
            sys_, user = messages[0]["content"], messages[-1]["content"]
            kind = ("answer" if "# 技能" in sys_ else "judge" if "判分员" in sys_ else "learn" if "维护技能验证题" in sys_
                    else "confirm" if "一定会违反" in sys_ else "conflict" if "直接矛盾" in sys_ else "missing")
            self.calls.append(kind)
            if kind == "answer":
                text = sys_
                if "换证" in user:
                    return "只有全新申请的才算, 换证不算" if RULE in text else ("换证也算新增" if "换证也算" in text else "都算新增")
                return "仿宋_GB2312" if "仿宋" in text else "宋体"
            if kind == "judge":
                return json.dumps({"ok": self.judge_ok, "why": "假判分"})
            if kind == "learn":
                return json.dumps(self.learn, ensure_ascii=False)
            if kind == "confirm":
                return json.dumps({"contradict": bool(self.conflict_word and self.conflict_word in user), "why": "复核"})
            if kind == "conflict":
                added = user.split("【小鲶新加的内容】")[-1].strip()
                hit = self.conflict_word and self.conflict_word in added
                return json.dumps({"conflicts": [{"rule": RULE, "added": added.splitlines()[0], "why": "说反了"}] if hit else []},
                                  ensure_ascii=False)
            target = user.split("【目标技能全文】")[-1]
            return json.dumps({"missing": [] if RULE in target else [RULE]}, ensure_ascii=False)
        return chat


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path / "catfish"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    d = tmp_path / "hermes" / "skills" / "productivity" / "zizhi-tongbao"
    (d / "references").mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: zizhi-tongbao\n---\n# 通报\n- 员工 9/1 定: 只有全新申请的才算新增, {RULE}\n", encoding="utf-8")
    (d / "references" / "a.md").write_text("参考", encoding="utf-8")
    (tmp_path / "catfish").mkdir()
    (tmp_path / "catfish" / "picker_state.json").write_text('{"chat_model": "m-flash"}', encoding="utf-8")
    seg.evals_dir().mkdir(parents=True)
    seg.save_evals("zizhi-tongbao", {"skill": "zizhi-tongbao", "questions": [
        {"id": "tb1", "q": "换证算不算新增?", "gold": "不算", "must": [["全新申请"], ["不算"]]},
        {"id": "tb4", "q": "表头字体?", "gold": "仿宋", "must": [["仿宋"]]}]})
    db = sqlite3.connect(tmp_path / "hermes" / "state.db")
    db.execute("create table sessions (id text primary key, source text)")
    db.execute("create table messages (id integer primary key autoincrement, session_id text, role text, content text, display_kind text)")
    db.commit()
    db.close()
    fake = Fake()
    monkeypatch.setattr(seg, "chat_with", fake.chat_with)
    return d, fake, tmp_path / "hermes" / "state.db"


def _say(state_db, text, session="s1", source="api_server", display_kind=None):
    db = sqlite3.connect(state_db)
    db.execute("insert or ignore into sessions values (?, ?)", (session, source))
    db.execute("insert into messages (session_id, role, content, display_kind) values (?, 'user', ?, ?)",
               (session, text, display_kind))
    db.commit()
    db.close()


def _patch(d, old, new, call_id="c1", session="s1"):
    args = {"action": "patch", "name": "zizhi-tongbao", "old_string": old, "new_string": new}
    assert seg.pre_tool_call_hook("skill_manage", args, tool_call_id=call_id, session_id=session) is None
    p = d / "SKILL.md"
    p.write_text(p.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
    return seg.transform_tool_result_hook("skill_manage", args, '{"success": true}', tool_call_id=call_id, session_id=session)


# ── 判分 ──

def test_要点判分_正则和千分位():
    q = {"must": [["148500"], ["re:第二批.{0,20}ISO ?20000"]], "must_not": ["一封全发"]}
    assert seg.keys_grade(q, "**第二批**第一次: ISO 20000 咨询, 148,500 元")[0]
    assert not seg.keys_grade(q, "第二批: ISO 20000, 148,000 元")[0]
    assert not seg.keys_grade({"must": [["不写"]]}, "技能里没有这个口径, 建议不写")[0]


def test_要点没命中再让模型判_没要点的题直接让模型判(env):
    _, fake, _ = env
    q = {"q": "x", "gold": "第二批第一次", "must": [["第二批第一次"]]}
    fake.judge_ok = True
    assert seg.grade(q, "第二批第1次", fake.chat_with("m"))[0] and fake.calls == ["judge"]
    assert seg.grade({"q": "x", "gold": "y"}, "随便", fake.chat_with("m"))[0]
    fake.judge_ok = False
    assert not seg.grade(q, "第三批", fake.chat_with("m"))[0]


# ── 验证题 ──

def test_改动没碰口径_照常生效_基线更新(env):
    d, _, _ = env
    assert _patch(d, "# 通报", "# 月度通报\n- 表头仿宋_GB2312 15pt") is None
    assert sorted(seg.load_evals("zizhi-tongbao")["baseline"]["passed"]) == ["tb1", "tb4"]


def test_改丢了员工的口径_撤回并说清哪题(env):
    d, _, _ = env
    before = (d / "SKILL.md").read_text(encoding="utf-8")
    r = json.loads(_patch(d, RULE, "复评换证也计入新增"))
    assert r["success"] is False and "已撤回" in r["error"] and "换证算不算新增" in r["error"]
    assert (d / "SKILL.md").read_text(encoding="utf-8") == before
    assert '"reverted": true' in (seg.evals_dir() / "gate_log.jsonl").read_text(encoding="utf-8")


def test_待确认的题不拦(env):
    d, _, _ = env
    data = seg.load_evals("zizhi-tongbao")
    for q in data["questions"]:
        q["status"] = "pending"
    seg.save_evals("zizhi-tongbao", data)
    assert _patch(d, RULE, "复评换证也计入新增") is None


def test_撤回时新加的文件也删_依赖目录不进快照(env):
    d, _, _ = env
    (d / "node_modules").mkdir()
    (d / "node_modules" / "big.js").write_text("x", encoding="utf-8")
    args = {"action": "patch", "name": "zizhi-tongbao", "old_string": RULE, "new_string": "换证也算"}
    seg.pre_tool_call_hook("skill_manage", args, tool_call_id="c9")
    assert not list((seg.evals_dir() / ".snap").rglob("big.js"))
    (d / "SKILL.md").write_text("改坏了", encoding="utf-8")
    (d / "references" / "new.md").write_text("新", encoding="utf-8")
    seg.transform_tool_result_hook("skill_manage", args, '{"success": true}', tool_call_id="c9")
    assert RULE in (d / "SKILL.md").read_text(encoding="utf-8")
    assert not (d / "references" / "new.md").exists() and (d / "node_modules" / "big.js").exists()
    assert not (seg.evals_dir() / ".snap" / "c9").exists()


def test_只改参考文件不跑题(env):
    d, fake, _ = env
    args = {"action": "write_file", "name": "zizhi-tongbao", "file_path": "references/a.md", "file_content": "新"}
    seg.pre_tool_call_hook("skill_manage", args, tool_call_id="c2")
    (d / "references" / "a.md").write_text("新", encoding="utf-8")
    assert seg.transform_tool_result_hook("skill_manage", args, '{"success": true}', tool_call_id="c2") is None
    assert fake.calls == []


def test_文件工具直接改SKILL_md也管(env):
    d, _, _ = env
    args = {"path": str(d / "SKILL.md"), "content": "清空"}
    seg.pre_tool_call_hook("write_file", args, tool_call_id="c3")
    (d / "SKILL.md").write_text("清空", encoding="utf-8")
    out = seg.transform_tool_result_hook("write_file", args, '{"success": true}', tool_call_id="c3")
    assert "已撤回" in json.loads(out)["error"] and RULE in (d / "SKILL.md").read_text(encoding="utf-8")


def test_原来对的题答错一次再答对_不算退步(env, monkeypatch):
    d, fake, _ = env
    seq = iter(["都算", "只有全新申请的才算, 换证不算"])
    real = fake.chat_with

    def flaky(model):
        inner = real(model)

        def chat(messages, n):
            if "# 技能" in messages[0]["content"] and "换证" in messages[-1]["content"] and "15pt" in messages[0]["content"]:
                return next(seq)
            return inner(messages, n)
        return chat
    monkeypatch.setattr(seg, "chat_with", flaky)
    assert _patch(d, "# 通报", "# 通报 15pt") is None


def test_没选模型_改动照留但明说没验证(env):
    d, fake, _ = env
    (seg._catfish_home() / "picker_state.json").unlink()
    out = _patch(d, RULE, "换证也算")
    assert "没验证" in json.loads(out)["skill_eval_note"] and fake.calls == []


def test_工具本身失败就不跑(env):
    d, fake, _ = env
    args = {"action": "patch", "name": "zizhi-tongbao", "old_string": "nope", "new_string": "y"}
    seg.pre_tool_call_hook("skill_manage", args, tool_call_id="c8")
    assert seg.transform_tool_result_hook("skill_manage", args, '{"success": false, "error": "no match"}',
                                          tool_call_id="c8") is None
    assert fake.calls == []


# ── 不用题的检查 (换个员工、一道题都没有也管用) ──

def test_没有题的技能_新加内容跟员工定的规则矛盾_撤回(env, tmp_path):
    _, fake, _ = env
    o = tmp_path / "hermes" / "skills" / "weekly"
    o.mkdir()
    (o / "SKILL.md").write_text("# 周报\n- 员工 9/8 定: 已结项的不要再放入周报\n", encoding="utf-8")
    fake.conflict_word = "已结项的也保留"
    args = {"action": "patch", "name": "weekly", "old_string": "# 周报", "new_string": "# 周报\n- 已结项的也保留一行"}
    seg.pre_tool_call_hook("skill_manage", args, tool_call_id="k1")
    (o / "SKILL.md").write_text((o / "SKILL.md").read_text(encoding="utf-8").replace("# 周报", args["new_string"]), encoding="utf-8")
    r = json.loads(seg.transform_tool_result_hook("skill_manage", args, '{"success": true}', tool_call_id="k1"))
    assert "矛盾" in r["error"] and "已结项的也保留" not in (o / "SKILL.md").read_text(encoding="utf-8")
    assert "conflict" in fake.calls and "answer" not in fake.calls


def test_技能里没有员工规则_不查矛盾(env, tmp_path):
    _, fake, _ = env
    o = tmp_path / "hermes" / "skills" / "plain"
    o.mkdir()
    (o / "SKILL.md").write_text("# 步骤\n1. 打开网页\n", encoding="utf-8")
    args = {"action": "patch", "name": "plain", "old_string": "1.", "new_string": "1. 先登录\n2."}
    seg.pre_tool_call_hook("skill_manage", args, tool_call_id="k2")
    (o / "SKILL.md").write_text("# 步骤\n1. 先登录\n2. 打开网页\n", encoding="utf-8")
    assert seg.transform_tool_result_hook("skill_manage", args, '{"success": true}', tool_call_id="k2") is None
    assert fake.calls == []


def test_删有员工口径的技能_没说并进哪_员工也没说删_拦(env):
    msg = seg.pre_tool_call_hook("skill_manage", {"action": "delete", "name": "zizhi-tongbao"}, tool_call_id="c4", session_id="s1")
    assert msg["action"] == "block" and "员工定的口径" in msg["message"]


def test_员工这一轮说了删_放行(env):
    _, _, db = env
    _say(db, "这个通报技能不要了, 删掉")
    assert seg.pre_tool_call_hook("skill_manage", {"action": "delete", "name": "zizhi-tongbao"},
                                  tool_call_id="c4", session_id="s1") is None


def test_并进去的技能没收下口径_拦_收下了放行(env, tmp_path):
    t = tmp_path / "hermes" / "skills" / "productivity" / "zizhi-all"
    t.mkdir()
    (t / "SKILL.md").write_text("# 合集\n只写了仿宋", encoding="utf-8")
    args = {"action": "delete", "name": "zizhi-tongbao", "absorbed_into": "zizhi-all"}
    msg = seg.pre_tool_call_hook("skill_manage", args, tool_call_id="c5")
    assert msg["action"] == "block" and RULE in msg["message"]
    (t / "SKILL.md").write_text(f"# 合集\n仿宋; {RULE}", encoding="utf-8")
    assert seg.pre_tool_call_hook("skill_manage", args, tool_call_id="c6") is None


# ── 员工原话出题 / 改题 ──

def test_员工说了口径_照原话出待确认的题_编的原话进不来(env):
    d, fake, db = env
    _say(db, "记住, 结算单都用 Excel 交, 不出 docx")
    fake.learn = {"add": [{"q": "结算单交什么格式?", "gold": "Excel, 不出 docx", "kind": "S", "quote": "结算单都用 Excel 交"},
                          {"q": "编的", "gold": "x", "kind": "S", "quote": "员工从没说过这句话"}], "update": []}
    out = _patch(d, "# 通报", "# 通报\n- 结算单用 Excel")
    qs = seg.load_evals("zizhi-tongbao")["questions"]
    new = [q for q in qs if q.get("origin") == "auto"]
    assert [q["q"] for q in new] == ["结算单交什么格式?"] and new[0]["status"] == "pending"
    assert "待员工在首页确认" in json.loads(out)["skill_eval_note"]


def test_员工改了口径_旧题照新原话更新_改动不被拦(env):
    d, fake, db = env
    _say(db, "以后复评换证也算新增, 口径改了")
    fake.learn = {"add": [], "update": [{"id": "tb1", "gold": "复评换证也算新增", "quote": "以后复评换证也算新增"}]}
    fake.judge_ok = True  # 新答案没有要点, 交给模型判
    out = _patch(d, RULE, "换证也算 (员工 11/27 改口径)")
    q = next(q for q in seg.load_evals("zizhi-tongbao")["questions"] if q["id"] == "tb1")
    assert q["gold"] == "复评换证也算新增" and q["history"][0]["gold"] == "不算" and "must" not in q
    assert "已撤回" not in (out or "") and "换证也算" in (d / "SKILL.md").read_text(encoding="utf-8")


def test_定时任务里没有员工的话_不出题不改题(env):
    d, fake, db = env
    _say(db, "以后复评换证也算新增", session="cron1", source="cron")
    r = json.loads(_patch(d, RULE, "复评换证也计入新增", session="cron1"))
    assert "已撤回" in r["error"] and "learn" not in fake.calls


def test_员工的话_去掉机器拼进来的部分():
    assert L.employee_words("这是周报  === 附件: a.xlsx (Excel) === 内容") == "这是周报"
    assert L.employee_words("[CONTEXT COMPACTION] xx") == ""
    assert L.employee_words(' json:[{"type": "text", "text": "赛宝也有"}, {"type": "image_url"}]') == "赛宝也有"


def test_从历史挖题(env):
    _, fake, db = env
    con = sqlite3.connect(db)
    con.execute("insert into sessions values ('h1', 'api_server')")
    con.execute("insert into messages (session_id, role, content) values ('h1', 'user', '记住, 是有新申请的才算新增, 换证的也不能算为新增')")
    con.execute("alter table messages add column tool_calls text")
    call = {"function": {"name": "skill_manage", "arguments": json.dumps(
        {"action": "patch", "name": "zizhi-tongbao", "old_string": "a", "new_string": "只有新申请才算新增"}, ensure_ascii=False)}}
    con.execute("insert into messages (session_id, role, content, tool_calls) values ('h1', 'assistant', '', ?)",
                (json.dumps([call], ensure_ascii=False),))
    con.commit()
    con.close()
    fake.learn = {"add": [{"q": "新增怎么算?", "gold": "只有新申请才算, 换证不算", "kind": "S", "quote": "是有新申请的才算新增"}], "update": []}
    got = seg.mine_history()
    assert got == {"zizhi-tongbao": 1}
    assert any(q.get("origin") == "mined" for q in seg.load_evals("zizhi-tongbao")["questions"])


# ── 中间件 (实际挂的是它; 钩子限时 30 秒) ──

def test_中间件_改坏了撤回_删技能拦下不执行(env):
    d, _, _ = env
    ran = []

    def apply(args):
        ran.append(args["action"])
        if args["action"] == "patch":
            p = d / "SKILL.md"
            p.write_text(p.read_text(encoding="utf-8").replace(args["old_string"], args["new_string"]), encoding="utf-8")
        return '{"success": true}'
    out = seg.tool_execution_middleware("skill_manage", {"action": "patch", "name": "zizhi-tongbao", "old_string": RULE,
                                        "new_string": "换证也算"}, apply, tool_call_id="m1", session_id="s1")
    assert "已撤回" in json.loads(out)["error"] and RULE in (d / "SKILL.md").read_text(encoding="utf-8")
    out = seg.tool_execution_middleware("skill_manage", {"action": "delete", "name": "zizhi-tongbao"}, apply,
                                        tool_call_id="m2", session_id="s1")
    assert json.loads(out)["success"] is False and ran == ["patch"]


def test_中间件_别的工具直接放过(env):
    assert seg.tool_execution_middleware("terminal", {"command": "ls"}, lambda a: "ok", tool_call_id="m3") == "ok"


def test_矛盾第一道找到_第二道复核说不矛盾_不算():
    def chat(messages, n):
        if "一定会违反" in messages[0]["content"]:
            return '{"contradict": false, "why": "说的不是一件事"}'
        return json.dumps({"conflicts": [{"rule": "专利不填", "added": "多口径要并列写清", "why": "?"}]}, ensure_ascii=False)
    assert L.conflicts(chat, ["专利不填"], "- 多口径要并列写清", []) == []


def test_规则行只认写明员工的():
    text = "---\nname: x\n---\n- 必须用 statistics 页取市值\n- 员工 10/19 定: 专利不填\n- 原话「备注留空」\n"
    assert L.rule_lines(text, []) == ["- 员工 10/19 定: 专利不填", "- 原话「备注留空」"]


def test_Companion按钮发的任务说明不算员工的话():
    assert L.employee_words("对知识库里的《民用无人机》做「官网核查」。 这条的属性：证书编号: Z1") == ""
    assert L.employee_words('参数不清楚先传 params={"_help": true}') == ""
    assert L.employee_words("以后查询后就直接更新数据") == "以后查询后就直接更新数据"
