"""同事群聊 · 群里一起改文档 (colleague_rooms_revise.py): 发起 → 下载修改 → 交回 (只给发起人) → 合并 → 发出合并版。
假邮筒, 全部虚构, 邮箱都是 example.com, 文档是现场生成的虚构方案。"""
from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import docx
import pytest

from catfish_tool_bridge import colleague_rooms as cr
from catfish_tool_bridge import colleague_rooms_bigfiles as big
from catfish_tool_bridge import colleague_rooms_outbox as outbox
from catfish_tool_bridge import colleague_rooms_revise as rv
from catfish_tool_bridge import colleague_rooms_revise_ai as rai
from catfish_tool_bridge import colleague_rooms_revise_docs as rvd
from catfish_tool_bridge import forms_tool
from catfish_tool_bridge import forms_net
from catfish_tool_bridge.doc_merge import marker

BOSS, ZHANG, LI, WANG = "zhou@example.com", "zhang@example.com", "li@example.com", "wang@example.com"


class World:
    def __init__(self, tmp_path, monkeypatch):
        self.tmp, self.mp = tmp_path, monkeypatch
        self.box: list[dict] = []
        self.who = ""
        monkeypatch.setattr(forms_net, "me", lambda: self.who)
        monkeypatch.setattr(forms_net, "post", self._post)
        monkeypatch.setattr(forms_net, "take", self._take)
        monkeypatch.setattr(outbox, "drain_in_background", lambda: [outbox.retry_due(500) for _ in range(3)])
        monkeypatch.setattr(Path, "home", lambda: self.tmp / (self.who.split("@")[0] + "-home"))

    def be(self, email):
        self.who = email
        self.mp.setenv("CATFISH_HOME", str(self.tmp / email.split("@")[0]))
        return self

    def _post(self, to, kind, env, ttl, source=None):
        self.box.append({"from": self.who, "to": to, "kind": kind, "env": json.loads(json.dumps(env, ensure_ascii=False))})
        return len(self.box)

    def _take(self, kinds=forms_net.FORM_KINDS):
        mine = [m for m in self.box if m["to"] == self.who and m["kind"] in kinds]
        for m in mine:
            self.box.remove(m)
        return [{"from": m["from"], "kind": m["kind"], "env": m["env"], "created_at": None} for m in mine]

    def sync(self, who):
        self.be(who)
        return cr.sync()

    def sync_all(self):
        for _ in range(2):
            for x in (BOSS, ZHANG, LI):
                self.sync(x)


@pytest.fixture()
def w(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


def _doc() -> bytes:
    d = docx.Document()
    d.add_heading("实施方案", 1)
    for t in ("第一阶段: 需求调研, 2 周。", "第二阶段: 系统开发, 8 周。", "实施周期 3 个月, 总价 48 万。"):
        d.add_paragraph(t)
    b = io.BytesIO()
    d.save(b)
    return b.getvalue()


def _room(w):
    w.be(BOSS)
    rid = cr.create({"name": "方案群", "members": [{"email": m, "name": n} for m, n in ((ZHANG, "张三"), (LI, "李四"))]})["room"]["room_id"]
    for m in (ZHANG, LI):
        w.sync(m)
        cr.join(rid)
    w.sync(BOSS)
    return rid


def _start(w, rid, data=None, name="实施方案.docx", **kw):
    w.be(BOSS)
    sid = big.stage({"offset": 0, "data": base64.b64encode(data or _doc()).decode()})["stage_id"]
    return rv.start(rid, {"files": [{"name": name, "stage_id": sid}], "deadline": "2026-11-20", "note": "重点看报价", **kw})


def _edit(path: str, old: str, new: str) -> None:
    d = docx.Document(path)
    p = next(p for p in d.paragraphs if p.text == old)
    p.runs[0].text = new
    for r in p.runs[1:]:
        r.text = ""
    d.save(path)


def test_完整一轮_交回只给发起人_合并_发出(w):
    rid = _room(w)
    st = _start(w, rid)
    rev = st["rev_id"]
    card = st["message"]["revise"]
    assert card["editors"] == [ZHANG, LI] and card["version"] == 1 and card["fmt"] == "docx"

    ev = w.sync(ZHANG)["events"]
    assert any(e["kind"] == "mention" and e["msg_kind"] == "revise" for e in ev)
    got = rv.open_copy(rid, rev)
    assert "(v1·待我修改).docx" in got["path"] and marker.read(Path(got["path"]).read_bytes()) == f"{rev}:v1"
    assert rv.open_copy(rid, rev)["path"] == got["path"]   # 再点一次还是那份
    _edit(got["path"], "第一阶段: 需求调研, 2 周。", "第一阶段: 需求调研, 3 周。")
    rv.give_back(rid, rev)
    to = {m["to"] for m in w.box if m["kind"] == "room_msg" and "revise_return" in m["env"]}
    assert to == {BOSS}                                       # 交回稿只发给发起人
    assert any("revise_status" in m["env"] and m["to"] == LI and m["env"]["revise_status"]["state"] == "returned" for m in w.box)

    w.sync(LI)
    p = rv.open_copy(rid, rev)["path"]
    _edit(p, "实施周期 3 个月, 总价 48 万。", "实施周期 3 个月, 总价 52 万。")
    rv.give_back(rid, rev, note="价格按新报价")
    assert not any(m["kind"] == "revise_return" for m in cr.messages(rid) if m.get("from") == ZHANG)   # 李四看不到张三的交回稿

    w.sync_all()
    w.be(BOSS)
    pr = rv.progress(rid, rev)
    assert pr["owner"] and pr["all_in"] and pr["can_merge"]
    assert {p["email"]: p["state"] for p in pr["people"]} == {ZHANG: "returned", LI: "returned"}
    an = rv.merge(rid, rev)
    assert an["conflicts"] == [] and an["changes"] == {ZHANG: 1, LI: 1}
    pub = rv.publish(rid, rev)
    assert pub["version"] == 2
    names = [f["name"] for f in pub["message"]["files"]]
    assert names == ["实施方案 v2 修订版.docx", "实施方案 v2.docx"]
    assert "合并了 张三 1 处、李四 1 处" in pub["message"]["text"]

    w.sync_all()
    w.be(ZHANG)
    vs = rvd.versions(rid, card["doc_id"])["versions"]
    assert [(v["version"], v["kind"]) for v in vs] == [(2, "merged"), (1, "start")]
    clean = next(f for f in vs[0]["files"] if "修订版" not in f["name"])
    texts = [p.text for p in docx.Document(io.BytesIO(rv.file_bytes(cr.load(rid)["room_id"], clean["id"]))).paragraphs]
    assert "第一阶段: 需求调研, 3 周。" in texts and "实施周期 3 个月, 总价 52 万。" in texts
    assert marker.read(rv.file_bytes(cr.load(rid)["room_id"], clean["id"])) is None   # 发出去的不带记号
    d = rvd.diff(rid, card["doc_id"], 1, 2)
    assert d["path"].endswith("v1→v2 对比.docx") and Path(d["path"]).exists()
    assert rv.progress(rid, rev)["merged"]["version"] == 2


def test_冲突_定了才能发_没改的先问(w):
    rid = _room(w)
    rev = _start(w, rid)["rev_id"]
    for who, new in ((ZHANG, "实施周期 4 个月, 总价 48 万。"), (LI, "实施周期 3 个月, 总价 52 万。")):
        w.sync(who)
        _edit(rv.open_copy(rid, rev)["path"], "实施周期 3 个月, 总价 48 万。", new)
        rv.give_back(rid, rev)
    w.sync_all()
    w.be(BOSS)
    an = rv.merge(rid, rev)
    assert len(an["conflicts"]) == 1
    c = an["conflicts"][0]
    assert c["combined"] == "实施周期 4 个月, 总价 52 万。" and [o["names"] for o in c["options"]] == [["张三"], ["李四"]]
    with pytest.raises(ValueError, match="冲突没定"):
        rv.publish(rid, rev)
    rv.decide(rid, rev, c["cid"], "text", c["combined"])
    assert rv.merge(rid, rev)["decisions"][c["cid"]]["pick"] == "text"
    assert "冲突 1 处已定" in rv.publish(rid, rev)["message"]["text"]


def test_一点没改_要确认_记成看过没改(w):
    rid = _room(w)
    rev = _start(w, rid)["rev_id"]
    w.sync(ZHANG)
    rv.open_copy(rid, rev)
    assert rv.give_back(rid, rev) == {"ok": False, "need_confirm": "unchanged"}
    rv.give_back(rid, rev, confirm_unchanged=True)
    w.sync_all()
    w.be(BOSS)
    assert {p["email"]: p["state"] for p in rv.progress(rid, rev)["people"]}[ZHANG] == "unchanged"
    with pytest.raises(ValueError, match="还没有人交回"):
        rv.merge(rid, rev)


def test_交错文件_类型不对_都拦(w, tmp_path):
    rid = _room(w)
    rev1 = _start(w, rid)["rev_id"]
    rev2 = _start(w, rid)["rev_id"]
    w.sync(ZHANG)
    p1 = rv.open_copy(rid, rev1)["path"]
    with pytest.raises(ValueError, match="另一轮"):
        rv.give_back(rid, rev2, path=p1)
    other = tmp_path / "x.txt"
    other.write_text("hi")
    with pytest.raises(ValueError, match="类型"):
        rv.give_back(rid, rev1, path=str(other))


def test_老版本直接发回群里的文件_带记号也算交回(w):
    rid = _room(w)
    rev = _start(w, rid)["rev_id"]
    w.sync(ZHANG)
    p = rv.open_copy(rid, rev)["path"]
    _edit(p, "第二阶段: 系统开发, 8 周。", "第二阶段: 系统开发, 10 周。")
    data = Path(p).read_bytes()
    cr.send(rid, "改好了", files=[{"name": "实施方案(张三).docx", "data": base64.b64encode(data).decode()}])
    w.sync_all()
    w.be(BOSS)
    pr = rv.progress(rid, rev)
    z = next(x for x in pr["people"] if x["email"] == ZHANG)
    assert z["state"] == "returned" and z.get("plain")
    assert rv.merge(rid, rev)["changes"] == {ZHANG: 1}


def test_防冒名_非发起人发合并_不是请的人交回_都不收(w):
    rid = _room(w)
    st = _start(w, rid, editors=[ZHANG])
    rev, card = st["rev_id"], st["message"]["revise"]
    w.sync(LI)
    w.box.append({"from": LI, "to": ZHANG, "kind": "room_msg", "env": {
        "v": 1, "room_id": rid, "msg_id": "m_" + "b" * 20, "at": "2026-11-12T10:00:00+08:00", "text": "假合并",
        "revise_status": {"rev_id": rev, "state": "merged", "version": 9}}})
    w.box.append({"from": LI, "to": BOSS, "kind": "room_msg", "env": {
        "v": 1, "room_id": rid, "msg_id": "m_" + "c" * 20, "at": "2026-11-12T10:00:00+08:00", "text": "我也交",
        "revise_return": {"rev_id": rev, "doc_id": card["doc_id"], "version": 1}}})
    w.sync(ZHANG)
    assert not any(m.get("revise_status") for m in cr.messages(rid))
    w.sync(BOSS)
    assert not any(m.get("revise_return") for m in cr.messages(rid))
    w.be(LI)
    with pytest.raises(ValueError, match="没请你改"):
        rv.open_copy(rid, rev)


def test_迟到的交回_跟上一版合并稿再合一次(w):
    rid = _room(w)
    rev = _start(w, rid)["rev_id"]
    w.sync(ZHANG)
    _edit(rv.open_copy(rid, rev)["path"], "第一阶段: 需求调研, 2 周。", "第一阶段: 需求调研, 3 周。")
    rv.give_back(rid, rev)
    w.sync(LI)
    lp = rv.open_copy(rid, rev)["path"]
    w.sync_all()
    w.be(BOSS)
    assert rv.publish(rid, rev)["version"] == 2
    w.sync(LI)
    _edit(lp, "第二阶段: 系统开发, 8 周。", "第二阶段: 系统开发, 9 周。")
    rv.give_back(rid, rev)
    w.sync_all()
    w.be(BOSS)
    an = rv.merge(rid, rev)
    assert an["late"] and an["conflicts"] == [] and an["changes"].get(LI) == 1
    pub = rv.publish(rid, rev)
    assert pub["version"] == 3 and "迟到" in pub["message"]["text"]
    clean = rv._blob(pub["message"]["revise_status"]["doc_id"], "v3")
    texts = [p.text for p in docx.Document(io.BytesIO(clean)).paragraphs]
    assert "第一阶段: 需求调研, 3 周。" in texts and "第二阶段: 系统开发, 9 周。" in texts


def test_不能合并的格式_整份选用(w):
    rid = _room(w)
    st = _start(w, rid, data=b"%PDF-1.4 fake", name="图纸.pdf")
    assert st["hint"] and st["message"]["revise"]["fmt"] == "whole"
    rev = st["rev_id"]
    w.sync(ZHANG)
    p = Path(rv.open_copy(rid, rev)["path"])
    p.write_bytes(b"%PDF-1.4 fake changed")
    rv.give_back(rid, rev)
    w.sync_all()
    w.be(BOSS)
    an = rv.merge(rid, rev)
    assert [c["cid"] for c in an["conflicts"]] == ["whole"]
    rv.decide(rid, rev, "whole", ZHANG)
    pub = rv.publish(rid, rev)
    assert [f["name"] for f in pub["message"]["files"]] == ["图纸 v2.pdf"]


def test_建议稿里的数字必须有来处():
    assert rai.check_suggestion("周期 4 个月, 总价 52 万", "周期 3 个月, 总价 48 万", ["周期 4 个月, 总价 48 万", "周期 3 个月, 总价 52 万"]) is None
    assert "60" in rai.check_suggestion("周期 4 个月, 总价 60 万", "周期 3 个月, 总价 48 万", ["周期 4 个月"])


def test_删群时一起删(w):
    rid = _room(w)
    st = _start(w, rid)
    w.be(BOSS)
    assert rv._get(st["rev_id"]) is not None
    cr.disband(rid)
    cr.delete_local(rid)
    assert rv._get(st["rev_id"]) is None and rv._blob(st["message"]["revise"]["doc_id"], "v1") is None


def test_拖进来的文件认得出_用暂存的文件交回(w):
    rid = _room(w)
    rev = _start(w, rid)["rev_id"]
    w.sync(ZHANG)
    p = rv.open_copy(rid, rev)["path"]
    _edit(p, "第二阶段: 系统开发, 8 周。", "第二阶段: 系统开发, 10 周。")
    sid = big.stage({"offset": 0, "data": base64.b64encode(Path(p).read_bytes()).decode()})["stage_id"]
    got = rv.detect(rid, sid, "实施方案 改.docx")
    assert got["rev_id"] == rev and got["owner_name"] and got["version"] == 1
    assert rv.detect(rid, sid, "x.pdf")["rev_id"] is None
    rv.give_back(rid, rev, stage_id=sid, name="实施方案 改.docx")
    w.sync_all()
    w.be(BOSS)
    assert rv.merge(rid, rev)["changes"] == {ZHANG: 1}


def test_结束之后不能再交(w):
    rid = _room(w)
    rev = _start(w, rid)["rev_id"]
    w.be(BOSS)
    rv.close(rid, rev)
    w.sync_all()
    w.be(ZHANG)
    with pytest.raises(ValueError, match="已经结束"):
        rv.open_copy(rid, rev)
    assert rv.progress(rid, rev)["closed"]


def test_小鲶_发起要先念_同意才发_能看进度和合并(w, tmp_path):
    rid = _room(w)
    w.be(BOSS)
    f = tmp_path / "实施方案.docx"
    f.write_bytes(_doc())
    p = forms_tool.dispatch("catfish_form", {"action": "revise_start", "room_id": rid, "path": str(f), "recipients": [ZHANG]})
    assert p["needs_confirmation"] and "张三" in p["proposal"] and not any(m["kind"] == "room_msg" for m in w.box)
    got = forms_tool.dispatch("catfish_form", {"action": "revise_start", "room_id": rid, "path": str(f), "recipients": [ZHANG],
                                               "deadline": "2026-11-20", "confirmed": True})
    rev = got["rev_id"]
    w.sync(ZHANG)
    _edit(rv.open_copy(rid, rev)["path"], "第一阶段: 需求调研, 2 周。", "第一阶段: 需求调研, 3 周。")
    rv.give_back(rid, rev)
    w.sync_all()
    w.be(BOSS)
    lst = forms_tool.dispatch("catfish_form", {"action": "revise_list"})["rounds"]
    assert lst[0]["rev_id"] == rev and lst[0]["people"] == {"张三": "returned"} and lst[0]["mine"]
    assert forms_tool.dispatch("catfish_form", {"action": "revise_merge", "rev_id": rev})["changes"] == {ZHANG: 1}
    p = forms_tool.dispatch("catfish_form", {"action": "revise_publish", "rev_id": rev})
    assert p["needs_confirmation"] and "合并版" in p["proposal"]
    assert forms_tool.dispatch("catfish_form", {"action": "revise_publish", "rev_id": rev, "confirmed": True})["version"] == 2
    assert forms_tool.dispatch("catfish_form", {"action": "revise_status", "rev_id": "r_20260101_ffffffff"})["ok"] is False


def test_文件还在_Word_里开着_先问一句(w):
    rid = _room(w)
    rev = _start(w, rid)["rev_id"]
    w.sync(ZHANG)
    p = Path(rv.open_copy(rid, rev)["path"])
    _edit(str(p), "第一阶段: 需求调研, 2 周。", "第一阶段: 需求调研, 3 周。")
    lock = p.parent / ("~$" + p.name[2:])          # Word 名字长时把开头两个字换成 ~$
    lock.write_bytes(b"x")
    assert rv.give_back(rid, rev) == {"ok": False, "need_confirm": "open"}
    lock.unlink()
    lo = p.parent / f".~lock.{p.name}#"            # LibreOffice
    lo.write_bytes(b"x")
    assert rv.give_back(rid, rev)["need_confirm"] == "open"
    assert rv.give_back(rid, rev, confirm_open=True)["ok"]   # 锁文件是上次崩溃留下的: 员工确认存好了照样能交


def test_发起人我也改_跟大家的一起合(w):
    rid = _room(w)
    rev = _start(w, rid)["rev_id"]
    w.be(BOSS)
    p = rv.open_copy(rid, rev)["path"]
    assert not any(m["kind"] == "room_msg" and "revise_status" in m["env"] for m in w.box)   # 发起人在改不往群里报
    _edit(p, "实施周期 3 个月, 总价 48 万。", "实施周期 3 个月, 总价 50 万。")
    assert rv.give_back(rid, rev)["self"] is True
    w.sync(ZHANG)
    _edit(rv.open_copy(rid, rev)["path"], "第一阶段: 需求调研, 2 周。", "第一阶段: 需求调研, 3 周。")
    rv.give_back(rid, rev)
    w.sync_all()
    w.be(BOSS)
    pr = rv.progress(rid, rev)
    me = next(x for x in pr["people"] if x.get("self"))
    assert me["state"] == "returned" and me["name"] == "我" and not pr["all_in"]   # 李四还没交; 我不算进「都交了」
    an = rv.merge(rid, rev)
    assert an["changes"] == {ZHANG: 1, BOSS: 1} and an["self_file"]
    # 合并面板里: 去掉我那份 → 只合张三的; 再选一份我改的文件 (暂存的) 加回来
    rv.self_clear(rev)
    an = rv.merge(rid, rev)
    assert an["changes"] == {ZHANG: 1} and an["self_file"] is None
    sid = big.stage({"offset": 0, "data": base64.b64encode(Path(p).read_bytes()).decode()})["stage_id"]
    assert rv.give_back(rid, rev, stage_id=sid, name="我改的方案.docx")["self"] is True
    assert rv.merge(rid, rev)["self_file"] == "我改的方案.docx"
    pub = rv.publish(rid, rev)
    clean = rv._blob(pub["message"]["revise_status"]["doc_id"], "v2")
    texts = [x.text for x in docx.Document(io.BytesIO(clean)).paragraphs]
    assert "实施周期 3 个月, 总价 50 万。" in texts and "第一阶段: 需求调研, 3 周。" in texts
    r = rv.remind(rid, rev)   # 只催李四, 不催自己
    assert "李四" in r["message"]["text"] and "我" not in r["message"]["text"].split("还等")[1]


def test_待办_请我改还没交的_截止早的在前(w):
    rid = _room(w)
    a = _start(w, rid)["rev_id"]
    b = _start(w, rid, deadline="2026-11-12")["rev_id"]
    c = _start(w, rid, editors=[LI])["rev_id"]
    w.sync(ZHANG)
    items = rv.pending()["items"]
    assert [x["rev_id"] for x in items] == [b, a] and items[0]["owner_name"]   # c 没请张三
    _edit(rv.open_copy(rid, a)["path"], "第一阶段: 需求调研, 2 周。", "第一阶段: 需求调研, 3 周。")
    assert next(x for x in rv.pending()["items"] if x["rev_id"] == a)["state"] == "editing"
    rv.give_back(rid, a)
    rv.decline(rid, b, "这周出差")
    assert rv.pending()["items"] == []
    w.be(BOSS)
    assert rv.pending()["items"] == [] and c   # 发起人自己没有待办


def test_群里普通文件的记号读过就记住(w, monkeypatch):
    rid = _room(w)
    rev = _start(w, rid)["rev_id"]
    w.sync(ZHANG)
    p = rv.open_copy(rid, rev)["path"]
    cr.send(rid, "改好了", files=[{"name": "方案.docx", "data": base64.b64encode(Path(p).read_bytes()).decode()}])
    w.sync_all()
    w.be(BOSS)
    calls = []
    real = rv.marker.read
    monkeypatch.setattr(rv.marker, "read", lambda d: calls.append(1) or real(d))
    for _ in range(3):
        rv.progress(rid, rev)
    assert len(calls) == 1


def test_用苹果自带的_Keynote_Pages_改的_存成了别的格式_交回时说清楚(w):
    rid = _room(w)
    rev = _start(w, rid)["rev_id"]
    w.sync(ZHANG)
    p = Path(rv.open_copy(rid, rev)["path"])
    assert rv.give_back(rid, rev) == {"ok": False, "need_confirm": "unchanged"}   # 真没改: 照旧只问一句
    # 用 Pages 打开、改完存: 存成了下载目录里同名的 .pages, 下载的那份 .docx 没动
    key = Path.home() / "Downloads" / (p.stem + ".pages")
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_bytes(b"pages")
    got = rv.give_back(rid, rev)
    assert got["need_confirm"] == "unchanged"
    assert got["saved_elsewhere"] == {"app": "Pages", "office": "Word", "saved": str(key), "target": str(p)}


def test_没人交回_发起人只合自己改的那份(w, tmp_path):
    rid = _room(w)
    rev = _start(w, rid)["rev_id"]
    w.be(BOSS)
    with pytest.raises(ValueError, match="还没有人交回"):
        rv.merge(rid, rev)
    mine = tmp_path / "我改的.docx"
    mine.write_bytes(_doc())
    _edit(str(mine), "第二阶段: 系统开发, 8 周。", "第二阶段: 系统开发, 12 周。")
    sid = big.stage({"offset": 0, "data": base64.b64encode(mine.read_bytes()).decode()})["stage_id"]
    assert rv.give_back(rid, rev, stage_id=sid, name="我改的.docx")["self"] is True
    an = rv.merge(rid, rev)
    assert an["changes"] == {BOSS: 1} and an["conflicts"] == [] and an["self_file"] == "我改的.docx"
    pub = rv.publish(rid, rev)
    assert pub["version"] == 2
    texts = [x.text for x in docx.Document(io.BytesIO(rv._blob(pub["message"]["revise_status"]["doc_id"], "v2"))).paragraphs]
    assert "第二阶段: 系统开发, 12 周。" in texts
