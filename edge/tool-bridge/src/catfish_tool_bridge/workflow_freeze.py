"""教学 trace → 工作流草稿; 以及两条凝固路共用的「按值认出数据流」(docs/WORKFLOW-SKILL-PLAN.md §4.2)。

跟验证码那一招同一个思路, 不让 LLM 猜: 后一步某个入参的值 = 前面某一步结果里某个字段的值 →
它就是从那一步来的, 凝固成引用, 不焊死教学那天的值。

教学 trace 分三种:
  workflow  没有浏览器动作, 只有 catfish_run_skill / 能力积木 → 出工作流草稿 (员工确认才保存)
  script    有浏览器动作 → 照旧凝固成 script.py; 里面顺手查到的只读能力 (搜一下知识库之类) 当成只给小鲶看的, 跳过
  mixed     浏览器动作 + 会改东西 / 对外的能力混在一起 → 不凝固, 请员工把浏览器那段单独教成 skill, 再用工作流串
"""
from __future__ import annotations

from datetime import date
from typing import Any, Iterator

from . import workflow_catalog as catalog

_BROWSER_PREFIX = "catfish_browser_"
_LLM_ONLY_BROWSER = {"catfish_browser_snapshot", "catfish_browser_screenshot", "catfish_browser_find_by_text",
                     "catfish_browser_locate", "catfish_recognize_captcha"}


def _action_of(tool: str):
    name = catalog.NATIVE_TO_ACTION.get(tool)
    return catalog.get(name) if name else None


def classify(trace: list[dict[str, Any]]) -> str:
    tools = [s.get("tool") or "" for s in trace]
    browser = any(t.startswith(_BROWSER_PREFIX) or t == "catfish_recognize_captcha" for t in tools)
    writes = [t for t in tools if (a := _action_of(t)) and a.level != catalog.READ]
    if browser:
        return "mixed" if writes else "script"
    if any(t == "catfish_run_skill" or _action_of(t) for t in tools):
        return "workflow"
    return "script"


def drop_read_actions(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """浏览器教学里顺手调的只读能力 = 给小鲶看的信息, 跟 snapshot 一样不进脚本。"""
    return [s for s in trace if not ((a := _action_of(s.get("tool") or "")) and a.level == catalog.READ)]


def _flatten(obj: Any, path: tuple = (), depth: int = 0) -> Iterator[tuple[tuple, str]]:
    if depth > 5:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _flatten(v, (*path, k), depth + 1)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:50]):
            yield from _flatten(v, (*path, i), depth + 1)
    elif isinstance(obj, str) and len(obj.strip()) >= 2:
        yield path, obj


def find_source(value: Any, earlier: list[tuple[Any, Any]]) -> tuple[Any, tuple] | None:
    """value 在前面哪一步的结果里出现过 (精确相等, 最近的优先)。earlier = [(步骤标识, 结果), ...] 按时间先后。"""
    if not isinstance(value, str) or len(value.strip()) < 2:
        return None
    for key, out in reversed(earlier):
        for path, v in _flatten(out):
            if v == value:
                return key, path
    return None


def _ref(step_id: str, path: tuple) -> str:
    s = "steps." + step_id
    for p in path:
        s += f"[{p}]" if isinstance(p, int) else f".{p}"
    return "${" + s + "}"


def _workflow_output(step: dict[str, Any]) -> Any:
    """运行器里这一步的输出长什么样 (跟 skill_workflow.run_block 一致): skill 是 result + files。"""
    r = step.get("result") or {}
    if step.get("tool") == "catfish_run_skill" and isinstance(r, dict):
        inner = r.get("result")
        out = dict(inner) if isinstance(inner, dict) else {"value": inner}
        out["files"] = r.get("files") or []
        return out
    return r


def trace_to_workflow(trace: list[dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
    """返回 (workflow, 要问员工的问题)。"""
    steps: list[dict[str, Any]] = []
    earlier: list[tuple[str, Any]] = []
    params: dict[str, Any] = {}
    questions: list[str] = []
    today = date.today().isoformat()
    used: set[str] = set()
    for s in trace:
        tool = s.get("tool") or ""
        if s.get("_dup_of_prev"):
            continue
        if tool == "catfish_run_skill":
            args = (s.get("args") or {})
            target, given = str(args.get("skill_path") or ""), dict(args.get("params") or {})
            base = target.split("/")[-1].replace("-", "_")
            step: dict[str, Any] = {"skill": target}
        elif (a := _action_of(tool)):
            given = {k: v for k, v in (s.get("args") or {}).items() if k in a.params}
            base = a.name
            step = {"action": a.name}
        else:
            continue
        sid, n = base[:24] or "step", 2
        while sid in used:
            sid, n = f"{base[:20]}_{n}", n + 1
        used.add(sid)
        wired: dict[str, Any] = {}
        for k, v in given.items():
            src = find_source(v, earlier)
            if src:
                wired[k] = _ref(src[0], src[1])
            elif isinstance(v, str) and v == today:
                params.setdefault("date", {"type": "date", "default": "today", "label": "日期"})
                wired[k] = "${params.date}"
            else:
                wired[k] = v
                if isinstance(v, str) and v.strip():
                    questions.append(f"「{sid}」这一步的 {k} = 「{v[:40]}」: 是每次都固定这样, 还是每次要换 (换的话做成参数)?")
        if wired:
            step["params"] = wired
        steps.append({"id": sid, **step})
        earlier.append((sid, _workflow_output(s)))
    return {"kind": "workflow", "version": "1.0.0", "params": params, "steps": steps}, questions


def freeze_to_draft(trace: list[dict[str, Any]], name: str, namespace: str, description: str) -> dict[str, Any]:
    from . import workflow_tool  # noqa: PLC0415
    wf, questions = trace_to_workflow(trace)
    if not wf["steps"]:
        return {"ok": False, "error": "教学记录里没有能串成工作流的步骤 (没调 skill, 也没用到能力积木)"}
    ns = namespace if namespace in ("personal", "department") else "personal"
    res = workflow_tool.save_draft({"name": name, "namespace": ns, "description": description, "triggers": [],
                                    "workflow": wf})
    res.update(kind="workflow", questions=questions,
               note=("教学记录里没有浏览器操作, 凝固成了**工作流草稿** (还没保存)。先把 questions 问员工: 固定的留着, "
                     "要换的改成参数 (catfish_workflow action=draft 带改好的 workflow 再存一次); 要加「小鲶判断」的步骤也在这时加。"
                     "然后 action=trial 试跑, 员工满意再 action=save。"))
    return res


def link_script_dataflow(trace: list[dict[str, Any]]) -> int:
    """script.py 那条路: 嵌套 skill 的参数如果等于前面下载 / 嵌套 skill 的结果 → 标成运行时取值。

    给源头那步打 `_keep_output`, 给用到的那步打 `_param_refs = {参数: (源头 seq, 路径)}`; 模板据此发代码。
    返回连上了几处。
    """
    earlier: list[tuple[int, Any]] = []
    linked = 0
    for s in trace:
        tool = s.get("tool") or ""
        if tool == "catfish_run_skill" and not s.get("_dup_of_prev"):
            refs = {}
            for k, v in ((s.get("args") or {}).get("params") or {}).items():
                src = find_source(v, earlier)
                if src:
                    refs[k] = src
            if refs:
                s["_param_refs"] = refs
                linked += len(refs)
                for seq, _ in refs.values():
                    for e in trace:
                        if e.get("seq") == seq:
                            e["_keep_output"] = True
        if tool in ("catfish_run_skill", "catfish_browser_download"):
            earlier.append((s.get("seq"), s.get("result") or {}))
    return linked
