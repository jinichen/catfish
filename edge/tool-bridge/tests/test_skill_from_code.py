"""对话里跑通的代码 → 技能程序 (skill_from_code.py, 11/19)。

鸿波「验证可以了固化成技能」: 真走一遍时 catfish_propose_skill → install_proposal 生成的程序只有步骤注释,
一跑就「未实现」; 这里钉住: 从会话里取出最后跑通、存了文件的代码, 文件路径 / 截至日期变参数, 换参数直接跑。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from catfish_tool_bridge import catfish_tools_propose as propose
from catfish_tool_bridge import skill_from_code as sfc


@pytest.fixture
def hermes(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    db = sqlite3.connect(home / "state.db")
    db.execute("CREATE TABLE messages (session_id TEXT, role TEXT, content TEXT, tool_calls TEXT, tool_call_id TEXT)")
    db.commit()
    db.close()
    return home


def add_run(home: Path, sid: str, code: str, ok: bool = True, n=[0]):
    n[0] += 1
    cid = f"call_{n[0]}"
    db = sqlite3.connect(home / "state.db")
    db.execute("INSERT INTO messages VALUES (?,?,?,?,?)", (sid, "assistant", "", json.dumps(
        [{"id": cid, "function": {"name": "execute_code", "arguments": json.dumps({"code": code})}}]), None))
    res = {"status": "success", "output": "done"} if ok else {"status": "error", "error": "BLOCKED"}
    db.execute("INSERT INTO messages VALUES (?,?,?,?,?)", (sid, "tool", json.dumps(res), None, cid))
    db.commit()
    db.close()


def ledger(path: Path, rows):
    wb = Workbook()
    ws = wb.active
    ws.append(["客户", "金额", "日期"])
    for r in rows:
        ws.append(r)
    wb.save(path)
    return path


CODE = '''import openpyxl, datetime as dt, os
src = "{src}"
OUT = "{out}"; os.makedirs(OUT, exist_ok=True)
ASOF = dt.date(2026, 11, 19)
ws = openpyxl.load_workbook(src).active
total = sum(r[1] for r in ws.iter_rows(min_row=2, values_only=True))
wb = openpyxl.Workbook(); s = wb.active
s["A1"] = "截至 2026-11-19 合计"; s["B1"] = total; s["C1"] = ASOF.isoformat()
p = os.path.join(OUT, "合计-截至2026-11-19.xlsx"); wb.save(p)
print("SAVED", p, total)
'''


def test_取最后跑通并存了文件的那段_参数认出输入文件和日期(hermes, tmp_path):
    out = Path.home() / ".catfish" / "outputs" / "2026-11-19"
    src = ledger(tmp_path / "11月.xlsx", [["甲", 100, "2026-11-01"], ["乙", 50, "2026-11-02"]])
    add_run(hermes, "s1", "print(1)")                                   # 探路: 没存文件
    add_run(hermes, "s1", CODE.format(src=src, out=out), ok=False)      # 被拦了: 不算
    add_run(hermes, "s1", CODE.format(src=src, out=out))                # 出成品的
    add_run(hermes, "s1", "import openpyxl; print('核对')")              # 之后的核对: 没存文件
    add_run(hermes, "other", "x=1; open('a','w').write('b')")           # 别的会话
    r = sfc.from_session("s1")
    assert r["ok"] and r["runs_seen"] == 4 and "SAVED" in r["code"]
    kinds = {s["param"]: s for s in r["subs"]}
    assert kinds["input_1"]["original"] == str(src) and kinds["asof"]["original"] == "2026-11-19"
    assert kinds["_outdir"]["kind"] == "outdir"


def test_换参数直接跑_日期各种写法一起换_产出进运行当天目录(hermes, tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path / "catfish"))
    out = Path.home() / ".catfish" / "outputs" / "2026-11-19"
    src11 = ledger(tmp_path / "11月.xlsx", [["甲", 100, "x"], ["乙", 50, "x"]])
    src12 = ledger(tmp_path / "12月.xlsx", [["甲", 70, "x"], ["丙", 30, "x"], ["丁", 5, "x"]])
    code = CODE.format(src=src11, out=out)
    subs = sfc.parameterize(code)["subs"]
    res = sfc.run_frozen(code, subs, {"input_1": str(src12), "asof": "2026-12-19"})
    assert res["ok"], res
    assert len(res["files"]) == 1
    f = Path(res["files"][0])
    assert f.name == "合计-截至2026-12-19.xlsx"
    assert str(tmp_path / "catfish" / "outputs") in str(f), "产出进运行当天的目录, 不是写死的 2026-11-19 那个"
    ws = load_workbook(f).active
    assert ws["B1"].value == 105 and ws["C1"].value == "2026-12-19" and "2026-12-19" in ws["A1"].value
    assert not (Path.home() / ".catfish" / "outputs" / "2026-12-19" / f.name).exists()


def test_参数不对不跑_调了小鲶工具的不固化_没跑通的不固化(hermes, tmp_path):
    src = ledger(tmp_path / "a.xlsx", [["甲", 1, "x"]])
    code = CODE.format(src=src, out=tmp_path / "o")
    subs = sfc.parameterize(code)["subs"]
    r = sfc.run_frozen(code, subs, {"input_1": str(tmp_path / "不存在.xlsx")})
    assert r["ok"] is False and "不存在" in r["error"]
    assert r == r  # 没跑
    assert "hermes_tools" in (sfc.check_code("from hermes_tools import x\nx()") or "")
    add_run(hermes, "s2", "print('只看了看')")
    assert sfc.from_session("s2")["ok"] is False and "存了文件" in sfc.from_session("s2")["reason"]
    assert sfc.from_session("nope")["ok"] is False


def test_help_只列员工要给的参数(tmp_path):
    src = ledger(tmp_path / "a.xlsx", [["甲", 1, "x"]])
    code = CODE.format(src=src, out=Path.home() / ".catfish" / "outputs" / "2026-11-19")
    subs = sfc.parameterize(code)["subs"]
    names = [p["name"] for p in sfc.run_frozen(code, subs, {"_help": True})["params"]]
    assert names == ["input_1", "asof"]
    assert "_outdir" not in sfc.params_doc(subs)


def test_提议到安装_技能程序就是那段代码_能直接跑(hermes, tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path / "catfish"))
    monkeypatch.setattr(propose, "SKILL_PROPOSALS_PATH", tmp_path / "skill_proposals.jsonl")
    src = ledger(tmp_path / "11月.xlsx", [["甲", 100, "x"]])
    add_run(hermes, "s3", CODE.format(src=src, out=Path.home() / ".catfish" / "outputs" / "2026-11-19"))
    r = propose.propose_skill({"name": "test-monthly-total", "reason": "员工在对话里核对过月度合计的算法, 要求固化成技能", "action_steps": "- 读员工给的明细表\n- 按客户合计金额\n- 出一份合计 Excel 并报总数",
                               "evidence_count": 1, "triggered_by": "user_request", "triggers": ["合计", "月度合计", "出合计"],
                               "_session_id": "s3"})
    assert r["type"] == "ok", r
    assert "带上" in r["code"] and "input_1" in r["code"]
    inst = propose.install_proposal({"proposal_id": r["proposal_id"], "skills_root": str(tmp_path / "skills"),
                                     "sync_to_hermes": False})
    assert inst["ok"], inst
    skill_dir = Path(inst["skill_dir"])
    md = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert "## 参数" in md and "input_1" in md
    import importlib.util
    spec = importlib.util.spec_from_file_location("frozen_skill", skill_dir / "script.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fn = next(getattr(mod, n) for n in dir(mod) if n.startswith("render_"))
    src12 = ledger(tmp_path / "12月.xlsx", [["甲", 7, "x"], ["乙", 8, "x"]])
    out = fn({"input_1": str(src12), "asof": "2026-12-19"})
    assert out["ok"], out
    assert load_workbook(out["files"][0]).active["B1"].value == 15
