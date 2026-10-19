"""catfish_workflow 工具 + Companion 的 pim/workflow_* (docs/WORKFLOW-SKILL-PLAN.md §4)。

小鲶能做: 看积木 / 看能用的 skill / 存草稿 (带校验) / 试跑草稿 / 员工同意后保存 / 看运行 / 从失败那步接着跑。
**确认对外步骤、改「对外步骤不用每次确认」这两件只有界面能做** (pim/*), 工具里没有。
跑一个已保存的工作流照旧用 catfish_run_skill。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

import yaml

from . import skill_workflow as engine
from . import workflow_catalog as catalog
from . import workflow_store as store


def _roots() -> list[Path]:
    from .catfish_tools_skill_ops import _skill_search_roots  # noqa: PLC0415
    return _skill_search_roots()


def skill_lookup(skill_path: str) -> Path | None:
    from .catfish_tools_skill_ops import _find_skill_dir  # noqa: PLC0415
    return _find_skill_dir(skill_path)


def _install_root() -> Path:
    from .catfish_tools_skill_ops import _local_skills_root  # noqa: PLC0415
    return _local_skills_root()


def _render_params(script: Path) -> list[dict[str, Any]]:
    """不 import (import 就可能动浏览器), 用 AST 读 render_* 的参数。"""
    try:
        tree = ast.parse(script.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("render_"):
            args = node.args.args
            defaults = [None] * (len(args) - len(node.args.defaults)) + list(node.args.defaults)
            return [{"name": a.arg, "required": d is None,
                     "default": ast.literal_eval(d) if isinstance(d, ast.Constant) else None}
                    for a, d in zip(args, defaults)]
    return []


def _frontmatter(md: Path) -> dict[str, Any]:
    try:
        text = md.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    try:
        return yaml.safe_load(text.split("---", 2)[1]) or {}
    except yaml.YAMLError:
        return {}


def runnable_skills() -> list[dict[str, Any]]:
    """能当技能积木的: 有 script.py 的凝固 skill (过程型 / 工作流不行)。"""
    out, seen = [], set()
    for root in _roots():
        for script in sorted(root.glob("*/*/script.py")):
            d = script.parent
            path = f"{d.parent.name}/{d.name}"
            if path in seen:
                continue
            seen.add(path)
            fm = _frontmatter(d / "SKILL.md")
            out.append({"skill_path": path, "description": str(fm.get("description") or "")[:200],
                        "version": str(fm.get("version") or ""), "params": _render_params(script)})
    return out


def chain(wf: dict[str, Any]) -> list[dict[str, Any]]:
    """步骤链 (界面画成「搜邮件 → 读正文 → … → 🔔推送微信」): 每步中文名 + 是不是对外 (会停下等确认)。"""
    return [{"id": s.get("id"), "label": engine.step_label(s), "block": engine.block_of(s),
             "outward": engine.is_outward(s) or bool(s.get("confirm")), "each": "for_each" in s}
            for s in wf.get("steps") or [] if isinstance(s, dict)]


def param_specs(wf: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for k, spec in (wf.get("params") or {}).items():
        spec = spec if isinstance(spec, dict) else {}
        out.append({"key": k, "label": spec.get("label") or k, "type": spec.get("type", "text"),
                    "default": spec.get("default"), "required": bool(spec.get("required"))})
    return out


def workflows() -> list[dict[str, Any]]:
    from . import skill_names  # noqa: PLC0415
    out = []
    for root in _roots():
        for y in sorted(root.glob("*/*/workflow.yaml")):
            d = y.parent
            fm = _frontmatter(d / "SKILL.md")
            try:
                wf = engine.load(d)
            except (OSError, ValueError):
                continue
            path = f"{d.parent.name}/{d.name}"
            out.append({"skill_path": path, **skill_names.skill_display(d),
                        "description": fm.get("description") or "", "version": wf.get("version"),
                        "steps": len(wf.get("steps") or []), "chain": chain(wf), "params": param_specs(wf),
                        "auto_confirm_outward": store.auto_confirm_outward(path),
                        "has_outward": any(engine.is_outward(s) or s.get("confirm") for s in wf.get("steps") or [])})
    return out


def _draft_from(args: dict[str, Any]) -> dict[str, Any]:
    wf = args.get("workflow")
    if isinstance(wf, str):
        wf = engine.parse(wf)
    if not isinstance(wf, dict):
        raise ValueError("workflow 要给步骤 (对象或 yaml 文本)")
    wf.setdefault("kind", "workflow")
    wf.setdefault("version", "1.0.0")
    triggers = [str(t).strip() for t in (args.get("triggers") or []) if str(t).strip()][:12]
    desc = str(args.get("description") or "").strip()
    if not desc:
        raise ValueError("要写一句描述: 什么时候用这个工作流")
    title = re.sub(r"\s+", " ", str(args.get("title") or "")).strip()[:30]
    return {"name": args.get("name"), "title": title, "namespace": args.get("namespace") or "personal",
            "description": desc[:300], "triggers": triggers, "workflow": wf}


def _with_outward_marks(wf: dict[str, Any]) -> dict[str, Any]:
    return {**wf, "steps": [{**s, "_outward": engine.is_outward(s) or bool(s.get("confirm"))} for s in wf.get("steps") or []]}


def save_draft(args: dict[str, Any]) -> dict[str, Any]:
    d = store.save_draft(_draft_from(args))
    problems = engine.validate(d["workflow"], skill_lookup=skill_lookup)
    steps = [{"id": s.get("id"), "block": engine.block_of(s),
              "what": s.get("skill") or s.get("action") or str(s.get("xiaonian") or "")[:60],
              "outward": engine.is_outward(s) or bool(s.get("confirm"))} for s in d["workflow"].get("steps") or []]
    return {"ok": not problems, "draft": d["name"], "namespace": d["namespace"], "steps": steps, "problems": problems,
            "note": ("草稿存好了。把步骤念给员工听 (哪步会停下等他确认), 他同意后可以 action=trial 试跑, 再 action=save 保存。"
                     if not problems else "草稿有问题, 改好再存: 见 problems。缺积木的那步请员工教一遍。")}


def trial(name: str, params: dict[str, Any]) -> dict[str, Any]:
    d = store.load_draft(name)
    return engine.start(f"draft:{d['name']}", d["workflow"], params or {},
                        title=f"{d.get('title') or d['name']} (试跑)", skill_lookup=skill_lookup, trigger="trial")


def save(name: str) -> dict[str, Any]:
    d = store.load_draft(name)
    problems = engine.validate(d["workflow"], skill_lookup=skill_lookup)
    if problems:
        return {"ok": False, "error": "还有问题, 不能保存: " + "; ".join(problems[:6]), "problems": problems}
    path = store.install(d["name"], d["namespace"], d["description"], d["triggers"],
                         _with_outward_marks(d["workflow"]), _install_root(), title=d.get("title") or "")
    store.delete_draft(d["name"])
    return {"ok": True, "skill_path": f"{d['namespace']}/{d['name']}", "path": str(path),
            "summary": f"工作流 {d['namespace']}/{d['name']} 保存好了, 以后说一句就能跑 (catfish_run_skill)。"}


def update_installed(skill_path: str, wf: Any) -> dict[str, Any]:
    """界面上改了已保存的工作流: 校验过了才写回。"""
    d = skill_lookup(skill_path)
    if d is None or not (d / "workflow.yaml").exists():
        raise LookupError(f"没有这个工作流: {skill_path}")
    wf = engine.parse(wf) if isinstance(wf, str) else wf
    problems = engine.validate(wf, skill_lookup=skill_lookup)
    if problems:
        return {"ok": False, "problems": problems}
    fm = _frontmatter(d / "SKILL.md")
    store.install(d.name, d.parent.name, str(fm.get("description") or ""), list(fm.get("triggers") or []),
                  _with_outward_marks(wf), d.parent.parent, title=str(fm.get("title") or ""))
    return {"ok": True}


def run_installed(skill_dir: Path, skill_path: str, params: dict[str, Any], trigger: str = "chat") -> dict[str, Any]:
    """catfish_run_skill 遇到 workflow.yaml 交到这里 (界面「现在跑一次」也走这里, trigger=ui)。"""
    from . import skill_names  # noqa: PLC0415
    try:
        wf = engine.load(skill_dir)
    except (OSError, ValueError) as e:
        return {"ok": False, "error": str(e), "files": [], "summary": ""}
    return engine.start(skill_path, wf, params or {}, title=skill_names.skill_title(skill_dir),
                        skill_lookup=skill_lookup, trigger=trigger)


def dispatch(name: str, args: dict[str, Any]) -> dict[str, Any]:
    action = str(args.get("action") or "").strip()
    try:
        if action == "catalog":
            return {"ok": True, "actions": catalog.describe(), "skills": runnable_skills(),
                    "note": "level=outward 的积木跑到之前会停下等员工在界面里确认。没有积木能做的步骤, 请员工教一个 skill。"}
        if action == "list":
            return {"ok": True, "workflows": workflows(), "drafts": store.list_drafts()}
        if action == "draft":
            return save_draft(args)
        if action == "trial":
            return trial(str(args.get("name") or ""), args.get("params") or {})
        if action == "save":
            if not args.get("confirmed"):
                d = store.load_draft(str(args.get("name") or ""))
                return {"ok": True, "needs_confirmation": True,
                        "proposal": f"保存工作流 {d['namespace']}/{d['name']}: {d['description']} "
                                    f"({len(d['workflow'].get('steps') or [])} 步)",
                        "note": "念给员工听, 员工同意后带 confirmed=true 再调"}
            return save(str(args.get("name") or ""))
        if action == "runs":
            return {"ok": True, "runs": store.list_runs(int(args.get("limit") or 20), args.get("status"))}
        if action == "status":
            run = store.load_run(str(args.get("run_id") or ""))
            return {"ok": True, **{k: run.get(k) for k in ("run_id", "skill_path", "status", "pending", "error",
                                                            "failed_step", "params", "created_at", "updated_at")},
                    "steps": [{k: s.get(k) for k in ("id", "status", "error", "ms")} for s in run["steps"]]}
        if action == "resume":
            return engine.resume(str(args.get("run_id") or ""))
        raise ValueError("action 只能是 catalog / list / draft / trial / save / runs / status / resume")
    except (ValueError, LookupError, OSError) as e:
        return {"ok": False, "error": str(e)}


def pim_handlers(params: dict[str, Any]) -> dict[str, Any]:
    """Companion 界面。确认对外步骤、设「对外步骤不用每次确认」只有这里有。"""
    rid = str(params.get("run_id") or "")
    sp = str(params.get("skill_path") or "")
    return {
        "pim/workflow_catalog": lambda: dispatch("catfish_workflow", {"action": "catalog"}),
        "pim/workflow_list": lambda: dispatch("catfish_workflow", {"action": "list"}),
        "pim/workflow_get": lambda: _get(sp),
        "pim/workflow_update": lambda: update_installed(sp, params.get("workflow")),
        "pim/workflow_draft_get": lambda: store.load_draft(str(params.get("name") or "")),
        "pim/workflow_draft_save": lambda: save_draft(params),
        "pim/workflow_draft_delete": lambda: (store.delete_draft(str(params.get("name") or "")), {"ok": True})[1],
        "pim/workflow_trial": lambda: trial(str(params.get("name") or ""), params.get("params") or {}),
        "pim/workflow_save": lambda: save(str(params.get("name") or "")),
        "pim/workflow_run": lambda: _run(sp, params.get("params") or {}),
        "pim/workflow_runs": lambda: {"ok": True, "runs": runs_for_ui(int(params.get("limit") or 30), params.get("status"))},
        "pim/workflow_run_get": lambda: store.load_run(rid),
        "pim/workflow_pending": lambda: {"ok": True, "runs": store.list_runs(50, "waiting_confirm")},
        "pim/workflow_confirm": lambda: engine.confirm(rid, bool(params.get("approve"))),
        "pim/workflow_resume": lambda: engine.resume(rid),
        "pim/workflow_run_delete": lambda: store.delete_runs(params.get("run_ids") or [],
                                                             all_finished=bool(params.get("all_finished"))),
        "pim/workflow_set_auto_confirm": lambda: store.set_auto_confirm_outward(sp, bool(params.get("on"))),
    }


def runs_for_ui(limit: int, status: str | None) -> list[dict[str, Any]]:
    """运行记录给界面: 10/19 之前失败的没存人话, 按原始报错补一句 (只在内存里补, 不改记录文件)。"""
    out = store.list_runs(limit, status)
    # 运行记录里存的是当时的名字 (老记录是目录名 weekly-xxx); 显示现在的中文名
    names = {w["skill_path"].split("/", 1)[1]: w["title"] for w in workflows() if w.get("title")}
    names.update({f"{w['skill_path']}": w["title"] for w in workflows() if w.get("title")})
    for r in out:
        sp = str(r.get("skill_path") or "")
        cur = names.get(sp) or names.get(sp.removeprefix("draft:"))
        if cur:
            r["title"] = cur + (" (试跑)" if sp.startswith("draft:") or str(r.get("title") or "").endswith("(试跑)") else "")
        if r.get("status") == "failed" and not r.get("error_human"):
            try:
                got = engine.humanize_legacy(store.load_run(r["run_id"]))
            except (OSError, ValueError, LookupError):
                got = None
            if got:
                r["error_human"], r["error_fix"] = got
        if r.get("status") == "done" and not r.get("outcome"):
            try:
                r["outcome"] = engine.outcome(store.load_run(r["run_id"]))
            except (OSError, ValueError, LookupError):
                pass
    return out


def _get(skill_path: str) -> dict[str, Any]:
    d = skill_lookup(skill_path)
    if d is None or not (d / "workflow.yaml").exists():
        raise LookupError(f"没有这个工作流: {skill_path}")
    fm = _frontmatter(d / "SKILL.md")
    from . import skill_names  # noqa: PLC0415
    return {"ok": True, "skill_path": skill_path, **skill_names.skill_display(d), "description": fm.get("description") or "",
            "triggers": fm.get("triggers") or [], "workflow": engine.load(d),
            "auto_confirm_outward": store.auto_confirm_outward(skill_path)}


def _run(skill_path: str, params: dict[str, Any]) -> dict[str, Any]:
    d = skill_lookup(skill_path)
    if d is None or not (d / "workflow.yaml").exists():
        raise LookupError(f"没有这个工作流: {skill_path}")
    return run_installed(d, skill_path, params, trigger="ui")
