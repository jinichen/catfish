"""在**真 hermes 的 venv** 里跑: 技能验证题门槛挂在真的 tool_execution 中间件上, 走真的 skill_manage。

由 tests/test_skill_eval_gate_on_real_hermes.py 起子进程调用, HERMES_HOME / CATFISH_HOME 指向临时目录。
模型换成假的 (技能里写着口径就答得出); 其余都是真 hermes: model_tools.handle_function_call → 中间件 → registry →
skill_manager_tool.skill_manage 真改文件。有一步假模型故意拖 35 秒 (过 hermes 钩子的 30 秒限时), 看改技能这一步
是不是等验完才返回。结果按行打 JSON: {"check", "ok", "detail"}。
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from pathlib import Path

HERMES_ROOT = Path(sys.argv[1]).resolve()
PLUGIN_DIR = Path(sys.argv[2]).resolve()
sys.path[:0] = [str(HERMES_ROOT)]
RULE = "换证不算新增"


def report(check: str, ok: bool, detail: str = "") -> None:
    print(json.dumps({"check": check, "ok": bool(ok), "detail": detail}, ensure_ascii=False), flush=True)


def main() -> None:
    home = Path(os.environ["HERMES_HOME"])
    os.environ["CATFISH_HOME"] = str(home.parent / ".catfish")
    d = home / "skills" / "productivity" / "zizhi-tongbao"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: zizhi-tongbao\ndescription: 资质通报\n---\n# 通报\n- 员工 9/1 定: 只有全新申请的才算新增, {RULE}\n",
                                encoding="utf-8")
    cat = Path(os.environ["CATFISH_HOME"])
    (cat / "skill_evals").mkdir(parents=True)
    (cat / "picker_state.json").write_text('{"chat_model": "m-flash"}', encoding="utf-8")
    (cat / "skill_evals" / "zizhi-tongbao.json").write_text(json.dumps({"skill": "zizhi-tongbao", "questions": [
        {"id": "tb1", "q": "换证算不算新增?", "gold": "不算, 只有全新申请才算", "must": [["全新申请"], ["不算"]]}]},
        ensure_ascii=False), encoding="utf-8")

    spec = importlib.util.spec_from_file_location("_seg_real", PLUGIN_DIR / "skill_eval_gate.py")
    seg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seg)
    slow = {"on": False}

    def chat_with(model):
        def chat(messages, n):
            sys_, user = messages[0]["content"], messages[-1]["content"]
            if "# 技能" in sys_:
                if slow["on"]:
                    time.sleep(35)
                return "只有全新申请的才算, 换证不算" if RULE in sys_ else "换证也算新增"
            if "判分员" in sys_:
                return '{"ok": false, "why": "假判分"}'
            if "维护技能验证题" in sys_:
                return '{"add": [], "update": []}'
            if "直接矛盾" in sys_ or "是不是跟员工定的一条规则直接矛盾" in sys_:
                return '{"conflicts": [], "contradict": false}'
            return '{"missing": []}' if RULE in user.split("【目标技能全文】")[-1] else json.dumps({"missing": [RULE]}, ensure_ascii=False)
        return chat
    seg.chat_with = chat_with

    from hermes_cli.plugins import _delivery_manager
    mgr = _delivery_manager()
    mgr._middleware.setdefault("tool_execution", []).append(seg.tool_execution_middleware)
    import model_tools

    def call(args, tid):
        return model_tools.handle_function_call("skill_manage", args, tool_call_id=tid, session_id="s1")

    before = (d / "SKILL.md").read_text(encoding="utf-8")
    out = call({"action": "patch", "name": "zizhi-tongbao", "old_string": RULE, "new_string": "复评换证也计入新增"}, "t1")
    report("真 skill_manage 改丢口径: 工具结果是「已撤回」", "已撤回" in out and "换证算不算新增" in out, out[:300])
    report("真 skill_manage 改丢口径: 技能恢复成改之前", (d / "SKILL.md").read_text(encoding="utf-8") == before)

    out = call({"action": "patch", "name": "zizhi-tongbao", "old_string": "# 通报", "new_string": "# 月度资质通报"}, "t2")
    report("没碰口径的改动照常生效", "月度资质通报" in (d / "SKILL.md").read_text(encoding="utf-8") and "已撤回" not in out, out[:200])

    slow["on"] = True
    t0 = time.time()
    out = call({"action": "patch", "name": "zizhi-tongbao", "old_string": RULE, "new_string": "换证也算"}, "t3")
    took = time.time() - t0
    slow["on"] = False
    report("作答拖 35 秒 (过钩子 30 秒限时): 工具等验完才返回, 结果是撤回", took >= 35 and "已撤回" in out
           and RULE in (d / "SKILL.md").read_text(encoding="utf-8"), f"{took:.0f}s {out[:120]}")

    out = call({"action": "delete", "name": "zizhi-tongbao"}, "t4")
    report("删有员工口径的技能 (没写并进哪): 拦下, 技能还在", (d / "SKILL.md").exists() and "员工定的口径" in out, out[:200])

    log = (cat / "skill_evals" / "gate_log.jsonl").read_text(encoding="utf-8")
    report("门槛日志记了撤回和拦删", '"reverted": true' in log and '"blocked": true' in log)
    report("驱动跑完", True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        import traceback
        report("驱动跑完", False, traceback.format_exc()[-1500:])
