"""工作流 skill 的校验和运行 (docs/WORKFLOW-SKILL-PLAN.md)。

workflow.yaml = 一串步骤, 每步是一块积木:
  skill:    员工 / 部门已有的凝固 skill (script.py)
  action:   能力积木目录里的鲶鱼原生能力 (workflow_catalog.py)
  xiaonian: 交给小鲶判断, 必须写 returns (交回哪些字段)

取值只有 `${params.x}` / `${steps.<id>.<字段>[n]...}` / `${item...}` (for_each 里) 三种, 没有表达式。
整个值就是一个 `${...}` 时原样取 (列表还是列表); 夹在文字里时换成文字。

运行: 开跑前检查 → 逐步跑 → 每步查 expect → 对外步骤前停下 (等员工在界面里确认, 小鲶不能替他确认)
→ 失败 / 停下时记住走到哪, 之后从那一步接着跑。运行记录只在本机 (workflow_store)。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable

import yaml

from . import workflow_catalog as catalog
from . import workflow_store as store
from . import workflow_xiaonian

MAX_STEPS = 30
MAX_FOR_EACH = 50
EXPECT_KEYS = ("ok", "count_min", "files_min", "text_contains", "page_contains")
PARAM_TYPES = ("text", "number", "date", "bool", "list")
_ID = re.compile(r"^[a-z][a-z0-9_]{0,30}$")
_REF = re.compile(r"\$\{([^}]+)\}")
_SKILL = re.compile(r"^(personal|department|external|pack-[a-z][a-z0-9-]{1,30})/[a-z0-9][a-z0-9-]*(@\^\d+)?$")
_SEG = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)((?:\[\d+\])*)$")


class StepFailure(Exception):
    pass


def parse(text: str) -> dict[str, Any]:
    try:
        wf = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ValueError(f"workflow.yaml 格式不对: {e}") from None
    if not isinstance(wf, dict):
        raise ValueError("workflow.yaml 应该是一个对象")
    return wf


def load(skill_dir: Path) -> dict[str, Any]:
    return parse((skill_dir / "workflow.yaml").read_text(encoding="utf-8"))


def block_of(step: dict[str, Any]) -> str:
    kinds = [k for k in ("skill", "action", "xiaonian") if k in step]
    return kinds[0] if len(kinds) == 1 else ""


def is_outward(step: dict[str, Any]) -> bool:
    a = catalog.get(step.get("action") or "") if "action" in step else None
    return bool(a and a.level == catalog.OUTWARD)


# ── 校验 ─────────────────────────────────────────────────────────

def _refs(value: Any) -> list[str]:
    if isinstance(value, str):
        return [m.strip() for m in _REF.findall(value)]
    if isinstance(value, dict):
        return [r for v in value.values() for r in _refs(v)]
    if isinstance(value, list):
        return [r for v in value for r in _refs(v)]
    return []


def validate(wf: dict[str, Any], *, skill_lookup: Callable[[str], Path | None]) -> list[str]:
    """不跑就能查出来的问题。skill_lookup(skill_path) → skill 目录或 None。"""
    errs: list[str] = []
    if wf.get("kind") != "workflow":
        errs.append("kind 要写 workflow")
    params = wf.get("params") or {}
    if not isinstance(params, dict):
        errs.append("params 应该是 名字 → 说明")
        params = {}
    for k, spec in params.items():
        if not _ID.match(str(k)):
            errs.append(f"参数名「{k}」只能用小写字母、数字、下划线")
        if isinstance(spec, dict) and spec.get("type", "text") not in PARAM_TYPES:
            errs.append(f"参数「{k}」的类型只能是 {' / '.join(PARAM_TYPES)}")
    steps = wf.get("steps")
    if not isinstance(steps, list) or not steps:
        return errs + ["至少要有一步"]
    if len(steps) > MAX_STEPS:
        errs.append(f"步骤太多 ({len(steps)}), 最多 {MAX_STEPS} 步")
    seen: list[str] = []
    for i, st in enumerate(steps, 1):
        where = f"第 {i} 步"
        if not isinstance(st, dict):
            errs.append(f"{where} 格式不对")
            continue
        sid = str(st.get("id") or "")
        if not _ID.match(sid):
            errs.append(f"{where} 要有 id (小写字母开头, 例 mails)")
        elif sid in seen:
            errs.append(f"{where} 的 id「{sid}」重复了")
        where = f"第 {i} 步 ({sid})"
        kind = block_of(st)
        if not kind:
            errs.append(f"{where} 要在 skill / action / xiaonian 里选且只选一个")
        elif kind == "skill":
            sp = str(st["skill"])
            if not _SKILL.match(sp):
                errs.append(f"{where} skill 写法不对 (例 department/eis-login 或 department/eis-login@^1)")
            else:
                d = skill_lookup(sp.split("@")[0])
                if d is None:
                    errs.append(f"{where} 找不到 skill {sp.split('@')[0]} (这台电脑上没有, 去资源市场装或先教一个)")
                elif (d / "workflow.yaml").exists():
                    errs.append(f"{where} 不能在工作流里再套工作流 ({sp})")
                elif not (d / "script.py").exists():
                    errs.append(f"{where} {sp} 是说明型 skill (没有可直接跑的脚本), 不能当一步用")
        elif kind == "action":
            a = catalog.get(str(st["action"]))
            if a is None:
                errs.append(f"{where} 没有「{st['action']}」这块积木 (用 catfish_workflow action=catalog 看有哪些)")
            else:
                given = st.get("params") or {}
                extra = [k for k in given if k not in a.params]
                if extra:
                    errs.append(f"{where} {a.name} 不认这些参数: {', '.join(extra)}")
                missing = [k for k in a.required if k not in given]
                if missing:
                    errs.append(f"{where} {a.name} 缺参数: {', '.join(missing)}")
        else:
            ret = st.get("returns")
            if not isinstance(ret, dict) or not ret:
                errs.append(f"{where} 小鲶这一步要写 returns (交回哪些字段, 例 summary: text)")
            else:
                bad = [k for k, t in ret.items() if t not in workflow_xiaonian.RETURN_TYPES]
                if bad:
                    errs.append(f"{where} returns 的类型只能是 {' / '.join(workflow_xiaonian.RETURN_TYPES)}: {', '.join(bad)}")
            if not str(st.get("xiaonian") or "").strip():
                errs.append(f"{where} 要写清楚让小鲶做什么")
        exp = st.get("expect") or {}
        if not isinstance(exp, dict) or any(k not in EXPECT_KEYS for k in exp):
            errs.append(f"{where} expect 只能用 {' / '.join(EXPECT_KEYS)}")
        has_each = "for_each" in st
        for ref in _refs({k: st.get(k) for k in ("params", "input", "for_each", "expect", "xiaonian")}):
            head = ref.split(".")[0].split("[")[0]
            if head == "params":
                name = ref.split(".")[1] if "." in ref else ""
                if name.split("[")[0] not in params:
                    errs.append(f"{where} 用了没声明的参数 ${{{ref}}}")
            elif head == "steps":
                target = ref.split(".")[1].split("[")[0] if "." in ref else ""
                if target not in seen:
                    errs.append(f"{where} ${{{ref}}} 只能引用前面的步骤")
            elif head == "item":
                if not has_each:
                    errs.append(f"{where} ${{item}} 只能用在带 for_each 的步骤里")
            else:
                errs.append(f"{where} 看不懂 ${{{ref}}} (只能用 params / steps / item)")
        if has_each and not (isinstance(st["for_each"], str) and _REF.fullmatch(st["for_each"].strip())):
            errs.append(f"{where} for_each 要写成 ${{steps.某步.某个列表}}")
        if sid:
            seen.append(sid)
    return errs


# ── 取值 ─────────────────────────────────────────────────────────

def _lookup(path: str, ctx: dict[str, Any]) -> Any:
    cur: Any = ctx
    for seg in path.split("."):
        m = _SEG.match(seg.strip())
        if not m:
            raise StepFailure(f"看不懂 ${{{path}}}")
        key, idx = m.group(1), m.group(2)
        if not isinstance(cur, dict) or key not in cur:
            raise StepFailure(f"${{{path}}} 取不到: 没有「{key}」")
        cur = cur[key]
        for n in re.findall(r"\[(\d+)\]", idx):
            if not isinstance(cur, list) or int(n) >= len(cur):
                raise StepFailure(f"${{{path}}} 取不到: 第 {n} 项不存在")
            cur = cur[int(n)]
    return cur


def resolve(value: Any, ctx: dict[str, Any]) -> Any:
    if isinstance(value, str):
        whole = _REF.fullmatch(value.strip())
        if whole:
            return _lookup(whole.group(1).strip(), ctx)

        def sub(m: re.Match) -> str:
            v = _lookup(m.group(1).strip(), ctx)
            return "、".join(map(str, v)) if isinstance(v, list) else ("" if v is None else str(v))
        return _REF.sub(sub, value)
    if isinstance(value, dict):
        return {k: resolve(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, ctx) for v in value]
    return value


def resolve_params(wf: dict[str, Any], given: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, spec in (wf.get("params") or {}).items():
        spec = spec if isinstance(spec, dict) else {}
        if k in given and given[k] not in (None, ""):
            out[k] = given[k]
        elif "default" in spec:
            d = spec["default"]
            out[k] = time.strftime("%Y-%m-%d") if (spec.get("type") == "date" and d == "today") else d
        elif spec.get("required"):
            raise ValueError(f"缺参数「{spec.get('label') or k}」({k})")
    return out


# ── 每一块积木怎么跑 ──────────────────────────────────────────────

def _files_of(v: Any) -> list[str]:
    from .catfish_tools_skill_ops import _extract_file_paths  # noqa: PLC0415
    return _extract_file_paths(v)


def _failed(result: Any) -> str | None:
    if isinstance(result, dict):
        if result.get("type") == "error" or ("ok" in result and not result["ok"]):
            return str(result.get("error") or result.get("summary") or "这一步没做成")[:300]
    return None


def run_block(st: dict[str, Any], args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    kind = block_of(st)
    if kind == "skill":
        from .catfish_tools_skill_ops import run_skill  # noqa: PLC0415
        r = run_skill({"skill_path": str(st["skill"]).split("@")[0], "params": args})
        if not r.get("ok"):
            raise StepFailure(r.get("error") or "skill 没跑成")
        res = r.get("result")
        out = dict(res) if isinstance(res, dict) else {"value": res}
        out["files"] = r.get("files") or []
        return out
    if kind == "action":
        a = catalog.get(str(st["action"]))
        res = a.run(args) if a and a.run else {"ok": False, "error": "积木不可用"}
        err = _failed(res)
        if err:
            raise StepFailure(err)
        out = dict(res) if isinstance(res, dict) else {"value": res}
        out.setdefault("files", _files_of(res))
        return out
    data = resolve(st.get("input"), ctx) if "input" in st else {k: v for k, v in ctx["steps"].items()}
    instruction = resolve(str(st["xiaonian"]), ctx)   # 说明里也能写 ${params.x} / ${steps.x.y}
    try:
        return workflow_xiaonian.run(str(instruction), data, st["returns"])
    except (ValueError, RuntimeError) as e:
        raise StepFailure(str(e)) from None


def _count(out: dict[str, Any]) -> int:
    if isinstance(out.get("count"), int):
        return out["count"]
    for k in ("items", "matches", "results", "tasks", "reminders", "files"):
        if isinstance(out.get(k), list):
            return len(out[k])
    return 0


def check_expect(exp: dict[str, Any], out: dict[str, Any]) -> None:
    if not exp:
        return
    if exp.get("ok") and out.get("ok") is not True:
        raise StepFailure("结果里没有明确说成功 (ok)")
    if "count_min" in exp and _count(out) < int(exp["count_min"]):
        raise StepFailure(f"结果只有 {_count(out)} 项, 要求至少 {exp['count_min']} 项")
    if "files_min" in exp:
        real = [f for f in out.get("files") or [] if Path(str(f)).expanduser().is_file()]
        if len(real) < int(exp["files_min"]):
            raise StepFailure(f"只拿到 {len(real)} 个文件 (磁盘上找得到的), 要求至少 {exp['files_min']} 个")
    if "text_contains" in exp and str(exp["text_contains"]) not in json.dumps(out, ensure_ascii=False, default=str):
        raise StepFailure(f"结果里没有「{exp['text_contains']}」")
    if "page_contains" in exp:
        from .catfish_tools import dispatch_native  # noqa: PLC0415
        page = dispatch_native("catfish_browser_evaluate", {"expression": "document.body.innerText"})
        if str(exp["page_contains"]) not in json.dumps(page, ensure_ascii=False, default=str):
            raise StepFailure(f"页面上没有「{exp['page_contains']}」")


def _audit(run: dict[str, Any]) -> None:
    from .catfish_tools_skill_ops import _write_skill_audit  # noqa: PLC0415
    _write_skill_audit({"ts": store.now(), "kind": "workflow", "skill_path": run["skill_path"],
                        "skill_version": run.get("version"), "run_id": run["run_id"], "status": run["status"],
                        "param_keys": sorted(run.get("params") or {}),
                        "steps": [{"id": s["id"], "block": s.get("block"), "ok": s.get("status") == "done",
                                   "ms": s.get("ms")} for s in run["steps"]]})


# ── 运行 ─────────────────────────────────────────────────────────

def _summary(run: dict[str, Any]) -> dict[str, Any]:
    out = {"ok": run["status"] in ("done", "waiting_confirm"), "run_id": run["run_id"], "status": run["status"],
           "skill_path": run["skill_path"], "steps": [{"id": s["id"], "status": s["status"]} for s in run["steps"]]}
    files = [f for s in run["steps"] for f in (s.get("result") or {}).get("files") or [] if isinstance(f, str)]
    out["files"] = list(dict.fromkeys(files))
    if run["status"] == "done":
        out["summary"] = f"工作流 {run['skill_path']} 跑完了 ({len(run['steps'])} 步)" + (
            f", 产出 {len(out['files'])} 个文件" if out["files"] else "")
    elif run["status"] == "waiting_confirm":
        p = run["pending"]
        out["pending"] = p
        out["summary"] = (f"停在第 {p['index'] + 1} 步「{p['label']}」, 这一步会对外发送, 要员工在鲶鱼界面里点确认才继续 "
                          "(你不能替员工确认, 告诉他去确认)。")
    else:
        out["ok"] = False
        out["error"] = run.get("error")
        out["summary"] = (f"第 {run.get('failed_index', 0) + 1} 步 ({run.get('failed_step')}) 没做成: {run.get('error')}。"
                          "修好后可以从这一步接着跑。")
    return out


def start(skill_path: str, wf: dict[str, Any], params: dict[str, Any], *, title: str | None = None,
          skill_lookup: Callable[[str], Path | None]) -> dict[str, Any]:
    errs = validate(wf, skill_lookup=skill_lookup)
    if errs:
        return {"ok": False, "status": "invalid", "error": "开跑前检查没过: " + "; ".join(errs[:8]), "problems": errs}
    try:
        p = resolve_params(wf, params or {})
    except ValueError as e:
        return {"ok": False, "status": "invalid", "error": str(e)}
    run = {"run_id": store.new_run_id(), "skill_path": skill_path, "title": title or skill_path,
           "version": wf.get("version"), "workflow": wf, "params": p, "status": "running",
           "created_at": store.now(), "steps": [], "approved": []}
    return _advance(run, 0)


def _advance(run: dict[str, Any], start_index: int) -> dict[str, Any]:
    wf = run["workflow"]
    steps = wf["steps"]
    run["status"], run["pending"], run["error"] = "running", None, None
    run["steps"] = run["steps"][:start_index]
    ctx: dict[str, Any] = {"params": run["params"], "steps": {s["id"]: s.get("result") or {} for s in run["steps"]}}
    auto = store.auto_confirm_outward(run["skill_path"])
    for i in range(start_index, len(steps)):
        st = steps[i]
        sid = st["id"]
        needs_ok = (is_outward(st) and not auto) or bool(st.get("confirm"))
        try:
            if needs_ok and sid not in run["approved"]:
                preview = (resolve(st.get("params") or {}, ctx))
                each = resolve(st["for_each"], ctx) if "for_each" in st else None
                a = catalog.get(st.get("action") or "")
                run["status"] = "waiting_confirm"
                run["pending"] = {"index": i, "step": sid, "label": a.label if a else (st.get("skill") or sid),
                                  "preview": store.trim(preview), "for_each_count": len(each) if isinstance(each, list) else None}
                break
            t0 = time.time()
            if "for_each" in st:
                items = resolve(st["for_each"], ctx)
                if not isinstance(items, list):
                    raise StepFailure("for_each 取到的不是列表")
                if len(items) > MAX_FOR_EACH:
                    raise StepFailure(f"列表有 {len(items)} 项, 一次最多处理 {MAX_FOR_EACH} 项")
                outs = []
                for item in items:
                    c = {**ctx, "item": item}
                    outs.append(run_block(st, resolve(st.get("params") or {}, c), c))
                out = {"ok": True, "items": outs, "count": len(outs),
                       "files": [f for o in outs for f in o.get("files") or []]}
            else:
                out = run_block(st, resolve(st.get("params") or {}, ctx), ctx)
            check_expect(st.get("expect") or {}, out)
            run["steps"].append({"id": sid, "block": block_of(st), "status": "done", "ms": int((time.time() - t0) * 1000),
                                 "result": store.trim(out)})
            ctx["steps"][sid] = out
        except StepFailure as e:
            run["steps"].append({"id": sid, "block": block_of(st), "status": "failed", "error": str(e)})
            run.update(status="failed", failed_step=sid, failed_index=i, error=str(e))
            break
        except Exception as e:  # noqa: BLE001 —— 积木内部炸了也要停在这一步、记下来, 不让整个运行器崩
            run["steps"].append({"id": sid, "block": block_of(st), "status": "failed", "error": f"{type(e).__name__}: {e}"})
            run.update(status="failed", failed_step=sid, failed_index=i, error=f"{type(e).__name__}: {e}"[:300])
            break
    else:
        run["status"] = "done"
    store.save_run(run)
    if run["status"] != "waiting_confirm":
        _audit(run)
    return _summary(run)


def resume(run_id: str) -> dict[str, Any]:
    """从失败的那一步接着跑 (小鲶和界面都能调)。停在确认步骤的要等员工确认, 这里不放行。"""
    run = store.load_run(run_id)
    if run["status"] == "waiting_confirm":
        return _summary(run)
    if run["status"] == "done":
        return {**_summary(run), "note": "这次已经跑完了"}
    if run["status"] != "failed":
        return {"ok": False, "error": f"这次运行的状态是 {run['status']}, 不能接着跑"}
    return _advance(run, int(run.get("failed_index", 0)))


def confirm(run_id: str, approve: bool) -> dict[str, Any]:
    """员工在界面里点「确认 / 不发」(只走 pim/*, 模型工具里没有这个动作)。"""
    run = store.load_run(run_id)
    if run["status"] != "waiting_confirm" or not run.get("pending"):
        return {"ok": False, "error": "这次运行没有在等确认"}
    p = run["pending"]
    if not approve:
        run["steps"].append({"id": p["step"], "status": "declined"})
        run.update(status="declined", pending=None, error="员工没同意发送")
        store.save_run(run)
        _audit(run)
        return {"ok": True, "status": "declined", "run_id": run_id, "summary": f"「{p['label']}」没发, 这次运行到此结束"}
    run["approved"] = list(dict.fromkeys([*run.get("approved", []), p["step"]]))
    return _advance(run, int(p["index"]))
