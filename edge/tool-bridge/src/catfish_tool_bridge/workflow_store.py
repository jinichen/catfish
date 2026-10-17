"""工作流的本机存放 (docs/WORKFLOW-SKILL-PLAN.md): 运行记录 / 员工设置 / 草稿 / 装进 skills。

全在员工本机 (~/.catfish/), 不出本机:
  workflow_runs/<run_id>.json   每次运行: 每步结果 (含从邮件 / 文件取到的内容)、停在哪、等不等确认
  workflow_prefs.json           员工自己在界面上设的 (哪个工作流的对外步骤不用每次确认) —— 只有 pim/* 写
  workflow_drafts/<name>.json   小鲶提的草稿, 员工点保存才装进 skills
"""
from __future__ import annotations

import json
import re
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from . import wiki_files

MAX_RESULT_CHARS = 200_000   # 单步结果在运行记录里最多存这么多 (再大的截断, 标出来)
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,48}$")
_NAMESPACES = ("personal", "department")


def _home() -> Path:
    return wiki_files._catfish_home()


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def _read(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ── 运行记录 ─────────────────────────────────────────────────────

def runs_dir() -> Path:
    return _home() / "workflow_runs"


def new_run_id() -> str:
    return f"r_{datetime.now():%Y%m%d%H%M%S}_{secrets.token_hex(3)}"


def _run_path(run_id: str) -> Path:
    if not re.fullmatch(r"r_\d{14}_[0-9a-f]{6}", str(run_id or "")):
        raise ValueError(f"运行编号不对: {run_id!r}")
    return runs_dir() / f"{run_id}.json"


def trim(value: Any) -> Any:
    text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= MAX_RESULT_CHARS:
        return value
    return {"_truncated": True, "_chars": len(text), "preview": text[:MAX_RESULT_CHARS]}


def save_run(run: dict[str, Any]) -> None:
    run["updated_at"] = now()
    _write(_run_path(run["run_id"]), run)


def load_run(run_id: str) -> dict[str, Any]:
    run = _read(_run_path(run_id))
    if not run:
        raise LookupError(f"没有这次运行: {run_id}")
    return run


def list_runs(limit: int = 30, status: str | None = None) -> list[dict[str, Any]]:
    out = []
    for p in sorted(runs_dir().glob("r_*.json"), reverse=True):
        r = _read(p)
        if not r or (status and r.get("status") != status):
            continue
        out.append({k: r.get(k) for k in ("run_id", "skill_path", "title", "status", "created_at", "updated_at",
                                          "failed_step", "error")} | {"pending": r.get("pending")})
        if len(out) >= limit:
            break
    return out


# ── 员工设置 (只有界面写) ─────────────────────────────────────────

def _prefs_path() -> Path:
    return _home() / "workflow_prefs.json"


def prefs() -> dict[str, Any]:
    return _read(_prefs_path()) or {}


def auto_confirm_outward(skill_path: str) -> bool:
    return bool((prefs().get(skill_path) or {}).get("auto_confirm_outward"))


def set_auto_confirm_outward(skill_path: str, on: bool) -> dict[str, Any]:
    p = prefs()
    p.setdefault(skill_path, {})["auto_confirm_outward"] = bool(on)
    p[skill_path]["set_at"] = now()
    _write(_prefs_path(), p)
    return p[skill_path]


# ── 草稿 / 装进 skills ───────────────────────────────────────────

def drafts_dir() -> Path:
    return _home() / "workflow_drafts"


def check_name(name: str, namespace: str) -> tuple[str, str]:
    name = str(name or "").strip().lower()
    namespace = str(namespace or "personal").strip().lower()
    if not _NAME.match(name):
        raise ValueError("名字只能用小写字母、数字和横线, 2-49 个字符 (例 weekly-qual-mail)")
    if namespace not in _NAMESPACES:
        raise ValueError("namespace 只能是 personal / department")
    return name, namespace


def save_draft(draft: dict[str, Any]) -> dict[str, Any]:
    name, ns = check_name(draft.get("name"), draft.get("namespace"))
    draft = {**draft, "name": name, "namespace": ns, "saved_at": now()}
    _write(drafts_dir() / f"{name}.json", draft)
    return draft


def load_draft(name: str) -> dict[str, Any]:
    name, _ = check_name(name, "personal")
    d = _read(drafts_dir() / f"{name}.json")
    if not d:
        raise LookupError(f"没有这个草稿: {name}")
    return d


def list_drafts() -> list[dict[str, Any]]:
    out = []
    for p in sorted(drafts_dir().glob("*.json")):
        d = _read(p)
        if d:
            out.append({k: d.get(k) for k in ("name", "namespace", "description", "saved_at")}
                       | {"steps": len((d.get("workflow") or {}).get("steps") or [])})
    return out


def delete_draft(name: str) -> None:
    name, _ = check_name(name, "personal")
    (drafts_dir() / f"{name}.json").unlink(missing_ok=True)


def skill_md(name: str, ns: str, description: str, triggers: list[str], wf: dict[str, Any]) -> str:
    """工作流的 SKILL.md: 小鲶靠 description / triggers 知道什么时候用, 正文告诉它怎么调。"""
    params = wf.get("params") or {}
    fm = {"name": name, "version": wf.get("version") or "1.0.0", "kind": "workflow", "deprecated": False,
          "namespace": ns, "author": "鲶鱼 工作流", "created": now()[:10], "updated": now()[:10],
          "description": description, "triggers": triggers}
    lines = ["---", yaml.safe_dump(fm, allow_unicode=True, sort_keys=False).strip(), "---", "",
             f"# {name}", "", description, "",
             "这是一个**工作流**: 步骤在同目录 workflow.yaml 里, 由运行器按顺序跑, 不要自己一步步去做。", "",
             "## 怎么调", "",
             f"`catfish_run_skill(skill_path=\"{ns}/{name}\", params={{...}})`", ""]
    if params:
        lines += ["参数 (不给就用默认值):", ""]
        for k, spec in params.items():
            spec = spec if isinstance(spec, dict) else {}
            lines.append(f"- `{k}` ({spec.get('type', 'text')}) {spec.get('label') or ''}"
                         f"{' — 默认 ' + str(spec['default']) if 'default' in spec else ''}"
                         f"{' — 必填' if spec.get('required') else ''}")
        lines.append("")
    lines += ["## 步骤", ""]
    for i, st in enumerate(wf.get("steps") or [], 1):
        kind = "skill" if "skill" in st else "action" if "action" in st else "xiaonian"
        what = st.get(kind) if kind != "xiaonian" else "小鲶判断: " + str(st.get("xiaonian"))[:80]
        lines.append(f"{i}. {what}{' (对外, 跑到这一步会停下等员工在鲶鱼里确认)' if st.get('_outward') else ''}")
    lines += ["", "## 结果", "",
              "- `status=done`: 跑完了, 把 summary 告诉员工。",
              "- `status=waiting_confirm`: 停在对外步骤, **员工要在鲶鱼界面里点确认**, 你不能替他确认; 告诉员工去确认。",
              "- `status=failed`: 告诉员工第几步、为什么; 修好后可以 `catfish_workflow(action=resume, run_id=...)` 从那一步接着跑。", ""]
    return "\n".join(lines)


def install(name: str, ns: str, description: str, triggers: list[str], wf: dict[str, Any], root: Path) -> Path:
    """写进 skills 目录 (~/.catfish/skills/<ns>/<name>/), hermes 通过 external_dirs 看得到。"""
    d = root / ns / name
    if (d / "script.py").exists():
        raise ValueError(f"{ns}/{name} 已经是一个教出来的 skill, 换个名字")
    d.mkdir(parents=True, exist_ok=True)
    marked = {**wf, "steps": [{k: v for k, v in s.items() if not k.startswith("_")} for s in wf.get("steps") or []]}
    (d / "workflow.yaml").write_text(yaml.safe_dump(marked, allow_unicode=True, sort_keys=False), encoding="utf-8")
    (d / "SKILL.md").write_text(skill_md(name, ns, description, triggers, wf), encoding="utf-8")
    return d
