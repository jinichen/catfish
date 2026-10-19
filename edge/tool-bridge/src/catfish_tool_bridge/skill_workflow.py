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
    """一步没做成。kind / data 给 humanize() 翻成员工看得懂的话 (10/19)。"""

    def __init__(self, msg: str, kind: str = "other", **data: Any):
        super().__init__(msg)
        self.kind = kind
        self.data = data


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
            raise StepFailure(f"${{{path}}} 取不到: 没有「{key}」", "missing_ref", path=path, key=key)
        cur = cur[key]
        for n in re.findall(r"\[(\d+)\]", idx):
            if not isinstance(cur, list) or int(n) >= len(cur):
                raise StepFailure(f"${{{path}}} 取不到: 第 {n} 项不存在", "missing_ref", path=path, key=f"第 {n} 项")
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
            raise StepFailure(r.get("error") or "skill 没跑成", "block")
        res = r.get("result")
        out = dict(res) if isinstance(res, dict) else {"value": res}
        out["files"] = r.get("files") or []
        return out
    if kind == "action":
        a = catalog.get(str(st["action"]))
        res = a.run(args) if a and a.run else {"ok": False, "error": "积木不可用"}
        err = _failed(res)
        if err:
            raise StepFailure(err, "block")
        out = dict(res) if isinstance(res, dict) else {"value": res}
        out.setdefault("files", _files_of(res))
        return out
    data = resolve(st.get("input"), ctx) if "input" in st else {k: v for k, v in ctx["steps"].items()}
    instruction = resolve(str(st["xiaonian"]), ctx)   # 说明里也能写 ${params.x} / ${steps.x.y}
    try:
        return workflow_xiaonian.run(str(instruction), data, st["returns"])
    except (ValueError, RuntimeError) as e:
        raise StepFailure(str(e), "xiaonian") from None


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
        raise StepFailure(f"结果只有 {_count(out)} 项, 要求至少 {exp['count_min']} 项", "count_min",
                          got=_count(out), need=int(exp["count_min"]))
    if "files_min" in exp:
        real = [f for f in out.get("files") or [] if Path(str(f)).expanduser().is_file()]
        if len(real) < int(exp["files_min"]):
            raise StepFailure(f"只拿到 {len(real)} 个文件 (磁盘上找得到的), 要求至少 {exp['files_min']} 个", "files_min",
                              got=len(real), need=int(exp["files_min"]))
    if "text_contains" in exp and str(exp["text_contains"]) not in json.dumps(out, ensure_ascii=False, default=str):
        raise StepFailure(f"结果里没有「{exp['text_contains']}」", "text", want=str(exp["text_contains"]))
    if "page_contains" in exp:
        from .catfish_tools import dispatch_native  # noqa: PLC0415
        page = dispatch_native("catfish_browser_evaluate", {"expression": "document.body.innerText"})
        if str(exp["page_contains"]) not in json.dumps(page, ensure_ascii=False, default=str):
            raise StepFailure(f"页面上没有「{exp['page_contains']}」", "text", want=str(exp["page_contains"]))


def _audit(run: dict[str, Any]) -> None:
    from .catfish_tools_skill_ops import _write_skill_audit  # noqa: PLC0415
    _write_skill_audit({"ts": store.now(), "kind": "workflow", "skill_path": run["skill_path"],
                        "skill_version": run.get("version"), "run_id": run["run_id"], "status": run["status"],
                        "param_keys": sorted(run.get("params") or {}),
                        "steps": [{"id": s["id"], "block": s.get("block"), "ok": s.get("status") == "done",
                                   "ms": s.get("ms")} for s in run["steps"]]})


# ── 给员工看的说法 (10/19, 鸿波「失败原因员工看不懂」) ─────────────────

def step_label(st: dict[str, Any]) -> str:
    """一步的中文名: 积木用目录里的名字, 技能用它的中文名, 小鲶那步用说明的开头。"""
    kind = block_of(st)
    if kind == "action":
        a = catalog.get(str(st.get("action") or ""))
        return a.label if a else str(st.get("action"))
    if kind == "skill":
        from . import skill_names  # noqa: PLC0415
        from .catfish_tools_skill_ops import _find_skill_dir  # noqa: PLC0415
        d = _find_skill_dir(str(st["skill"]).split("@")[0])
        return skill_names.skill_title(d) if d else str(st["skill"])
    text = re.split(r"[，,。；;：:\n]", str(st.get("xiaonian") or ""), maxsplit=1)[0].strip()
    text = _REF.sub("", text)
    return "小鲶：" + (text[:12] + ("…" if len(text) > 12 else "") if text else "整理判断")


def _step_by_id(wf: dict[str, Any], sid: str) -> tuple[int, dict[str, Any] | None]:
    for j, s in enumerate(wf.get("steps") or []):
        if s.get("id") == sid:
            return j, s
    return -1, None


def humanize(wf: dict[str, Any], i: int, st: dict[str, Any], e: Exception) -> tuple[str, str]:
    """(一句员工看得懂的话, 怎么办的类型)。类型: conditions 放宽条件 / workflow 要改工作流 / retry 再跑 / login 先登录。"""
    n, label = i + 1, step_label(st)
    kind = getattr(e, "kind", "other")
    data = getattr(e, "data", {})
    msg = str(e)
    if kind == "count_min":
        return (f"第 {n} 步「{label}」只拿到 {data.get('got')} 条，要求至少 {data.get('need')} 条。"
                "可能这次确实没有符合条件的内容 —— 可以放宽条件 (关键词、天数) 再跑。", "conditions")
    if kind == "missing_ref":
        ref = str(data.get("path") or "").split(".")
        j, ref_st = _step_by_id(wf, ref[1]) if len(ref) > 2 and ref[0] == "steps" else (-1, None)
        src = f"第 {j + 1} 步「{step_label(ref_st)}」" if ref_st else "前面那一步"
        return (f"第 {n} 步要用{src}的「{data.get('key')}」，但那一步的结果里没有它。"
                "这是工作流本身写得跟实际结果对不上，需要改一下工作流 (可以让小鲶改)。", "workflow")
    if kind == "files_min":
        return (f"第 {n} 步「{label}」应该产出至少 {data.get('need')} 个文件，实际只找到 {data.get('got')} 个。"
                "从这一步重跑试试。", "retry")
    if kind == "text":
        return f"第 {n} 步「{label}」做完了，但结果里没看到「{data.get('want')}」，没达到预期。从这一步重跑试试。", "retry"
    if kind == "for_each":
        return f"第 {n} 步要逐条处理前面给出的列表，但拿到的不是列表。需要改一下工作流。", "workflow"
    if kind == "too_many":
        return f"第 {n} 步要逐条处理 {data.get('got')} 条，一次最多 {MAX_FOR_EACH} 条。缩小范围 (比如少看几天) 再跑。", "conditions"
    if re.search(r"登录|token|401|未授权|unauthor", msg, re.I):
        return f"第 {n} 步「{label}」没做成：需要先登录 ({msg[:80]})。登录后从这一步重跑。", "login"
    if kind == "xiaonian":
        if "连不上模型" in msg or "模型返回" in msg:
            return f"第 {n} 步「{label}」连不上模型，可能是网络问题。稍后从这一步重跑。", "retry"
        return (f"第 {n} 步「{label}」小鲶交回的结果不符合要求 ({msg[:80]})。"
                "再跑一次通常就好；反复出现就把这一步的说明写清楚些。", "retry")
    if kind == "block":
        return f"第 {n} 步「{label}」没做成：{msg[:150]}。从这一步重跑试试。", "retry"
    return f"第 {n} 步「{label}」出了程序错误 ({msg[:100]})。从这一步重跑试试；反复出现请发给 IT。", "retry"


_LEGACY = [
    (re.compile(r"结果只有 (\d+) 项, 要求至少 (\d+) 项"), lambda m: ("count_min", {"got": int(m[1]), "need": int(m[2])})),
    (re.compile(r"\$\{([^}]+)\} 取不到: 没有「([^」]+)」"), lambda m: ("missing_ref", {"path": m[1], "key": m[2]})),
    (re.compile(r"只拿到 (\d+) 个文件.*至少 (\d+) 个"), lambda m: ("files_min", {"got": int(m[1]), "need": int(m[2])})),
]


def humanize_legacy(run: dict[str, Any]) -> tuple[str, str] | None:
    """10/19 之前的失败记录没存人话: 按原始报错认出是哪种, 补一句 (界面列运行记录时用, 不改文件)。"""
    wf, i = run.get("workflow") or {}, run.get("failed_index")
    steps = wf.get("steps") or []
    if run.get("status") != "failed" or not isinstance(i, int) or not 0 <= i < len(steps):
        return None
    err = str(run.get("error") or "")
    kind, data = "block", {}
    for pat, conv in _LEGACY:
        m = pat.search(err)
        if m:
            kind, data = conv(m)
            break
    return humanize(wf, i, steps[i], StepFailure(err, kind, **data))


def outcome(run: dict[str, Any]) -> str:
    """跑完了做成了什么 (一句话): 对外 / 写本机的积木各说一句, 再加产出文件数。"""
    wf = run.get("workflow") or {}
    bits: list[str] = []
    for rec in run.get("steps") or []:
        if rec.get("status") != "done" or rec.get("block") != "action":
            continue
        _j, st = _step_by_id(wf, rec["id"])
        a = catalog.get(str((st or {}).get("action") or ""))
        if not a or a.level == catalog.READ:
            continue
        res = rec.get("result") or {}
        name = (res.get("title") or res.get("name") or res.get("subject")) if isinstance(res, dict) else None
        bits.append(f"{a.label}《{str(name)[:30]}》" if name else a.label)
    files = {f for rec in run.get("steps") or [] for f in (rec.get("result") or {}).get("files") or [] if isinstance(f, str)}
    if files:
        bits.append(f"产出 {len(files)} 个文件")
    return "、".join(dict.fromkeys(bits)) or f"{len(run.get('steps') or [])} 步都做完了"


# ── 运行 ─────────────────────────────────────────────────────────

def _summary(run: dict[str, Any]) -> dict[str, Any]:
    out = {"ok": run["status"] in ("done", "waiting_confirm"), "run_id": run["run_id"], "status": run["status"],
           "skill_path": run["skill_path"], "steps": [{"id": s["id"], "status": s["status"]} for s in run["steps"]]}
    files = [f for s in run["steps"] for f in (s.get("result") or {}).get("files") or [] if isinstance(f, str)]
    out["files"] = list(dict.fromkeys(files))
    if run["status"] == "done":
        out["summary"] = f"工作流「{run.get('title') or run['skill_path']}」跑完了: {run.get('outcome') or outcome(run)}"
    elif run["status"] == "waiting_confirm":
        p = run["pending"]
        out["pending"] = p
        out["summary"] = (f"停在第 {p['index'] + 1} 步「{p['label']}」, 这一步会对外发送, 要员工在鲶鱼界面里点确认才继续 "
                          "(你不能替员工确认, 告诉他去确认)。")
    else:
        out["ok"] = False
        out["error"] = run.get("error")
        out["error_human"] = run.get("error_human")
        out["summary"] = ((run.get("error_human") or
                           f"第 {run.get('failed_index', 0) + 1} 步 ({run.get('failed_step')}) 没做成: {run.get('error')}。")
                          + " 修好后可以从这一步接着跑。")
    return out


def start(skill_path: str, wf: dict[str, Any], params: dict[str, Any], *, title: str | None = None,
          skill_lookup: Callable[[str], Path | None], trigger: str = "chat") -> dict[str, Any]:
    """trigger: trial 试跑 / ui 界面上点的 / chat 小鲶跑的 (对话里或定时任务)。"""
    errs = validate(wf, skill_lookup=skill_lookup)
    if errs:
        return {"ok": False, "status": "invalid", "error": "开跑前检查没过: " + "; ".join(errs[:8]), "problems": errs}
    try:
        p = resolve_params(wf, params or {})
    except ValueError as e:
        return {"ok": False, "status": "invalid", "error": str(e)}
    run = {"run_id": store.new_run_id(), "skill_path": skill_path, "title": title or skill_path,
           "version": wf.get("version"), "workflow": wf, "params": p, "status": "running",
           "created_at": store.now(), "steps": [], "approved": [], "trigger": trigger}
    return _advance(run, 0)


def _advance(run: dict[str, Any], start_index: int) -> dict[str, Any]:
    wf = run["workflow"]
    steps = wf["steps"]
    run["status"], run["pending"], run["error"] = "running", None, None
    run["error_human"] = run["error_fix"] = run["outcome"] = None
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
                run["pending"] = {"index": i, "step": sid, "label": step_label(st) if (a or "skill" in st) else sid,
                                  "preview": store.trim(preview), "for_each_count": len(each) if isinstance(each, list) else None}
                break
            t0 = time.time()
            if "for_each" in st:
                items = resolve(st["for_each"], ctx)
                if not isinstance(items, list):
                    raise StepFailure("for_each 取到的不是列表", "for_each")
                if len(items) > MAX_FOR_EACH:
                    raise StepFailure(f"列表有 {len(items)} 项, 一次最多处理 {MAX_FOR_EACH} 项", "too_many", got=len(items))
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
            human, fix = humanize(wf, i, st, e)
            run["steps"].append({"id": sid, "block": block_of(st), "status": "failed", "error": str(e)})
            run.update(status="failed", failed_step=sid, failed_index=i, error=str(e), error_human=human, error_fix=fix)
            break
        except Exception as e:  # noqa: BLE001 —— 积木内部炸了也要停在这一步、记下来, 不让整个运行器崩
            human, fix = humanize(wf, i, st, e)
            run["steps"].append({"id": sid, "block": block_of(st), "status": "failed", "error": f"{type(e).__name__}: {e}"})
            run.update(status="failed", failed_step=sid, failed_index=i, error=f"{type(e).__name__}: {e}"[:300],
                       error_human=human, error_fix=fix)
            break
    else:
        run["status"] = "done"
        run["outcome"] = outcome(run)
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
