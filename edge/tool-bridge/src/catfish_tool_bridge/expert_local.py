"""本机专家 (docs/EXPERT-PACK-PLAN.md 第一阶段)。

专家 = 一个 hermes profile (专家 Bot 的壳) + 这里管的「定义」: 人设、用哪些工作流 / 技能、选路、交付前核对、能用哪几组能力。
定义存 `~/.catfish/experts/<id>/expert.yaml`; `compile()` 把它算成 profile 要写的东西, 由 Companion (Rust) 落到 profile:

  soul           SOUL.md = 人设 + 编进去的「选路规则 / 交付前核对 / 规矩」
  tools_include  catfish-tools MCP 只露这些工具 (算出来的, 员工不手填)
  skills_dir     专家只看这个目录: 里面是指向选中技能 / 工作流的软链 (hermes 扫 skill 跟软链)
  disabled_toolsets  hermes 自带的工具集默认关掉的 (终端 / 写文件 / 自带浏览器 …)
  disabled_skills    profile 自己 skills/ 里克隆来的那些, 全部禁用 —— 专家只用员工选的

本机专家可以用员工自己的个人 skill (10/18 定); 打包发给别人那一步 (第二阶段) 才禁止。
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any

import yaml

from . import wiki_files, workflow_store

PROFILE_PREFIX = "expert-"
_ID = re.compile(r"^[a-z][a-z0-9-]{1,30}$")
MAX_ROUTES = 20

#: 专家永远有的: 跑技能 / 工作流, 看工作流运行记录、从失败那步接着跑
BASE_TOOLS = ("catfish_run_skill", "catfish_workflow")

#: 员工勾的能力组 → 露给专家的 catfish 工具
TOOL_GROUPS: dict[str, dict[str, Any]] = {
    "knowledge": {"label": "查知识库 / 文档", "tools": ("catfish_wiki_search", "catfish_wiki_read", "catfish_search_docs",
                                                    "catfish_search_files")},
    "email": {"label": "读邮件、存草稿 (不发)", "tools": ("catfish_email_search", "catfish_email_read", "catfish_email_attachment",
                                                        "catfish_email_create_draft", "catfish_draft_email_reply")},
    "tasks": {"label": "任务 / 提醒 / 日程", "tools": ("catfish_list_tasks", "catfish_create_task", "catfish_list_reminders",
                                                     "catfish_create_reminder", "catfish_list_calendars",
                                                     "catfish_create_calendar_event")},
    "browser": {"label": "操作网页 (说明型技能要自己点网页时才需要)",
                "tools": ("catfish_browser_goto", "catfish_browser_click", "catfish_browser_fill", "catfish_browser_snapshot",
                          "catfish_browser_screenshot", "catfish_browser_find_by_text", "catfish_browser_locate",
                          "catfish_browser_evaluate", "catfish_browser_console", "catfish_browser_download",
                          "catfish_recognize_captcha")},
    "forms": {"label": "一起填表 (发 / 催会停下等你确认)", "tools": ("catfish_form",)},
    "wechat_records": {"label": "查微信聊天记录", "tools": ("catfish_wechat_search", "catfish_wechat_history")},
}

#: hermes 自带的工具集, 专家默认关 —— 专家只做选中的那几件事
DEFAULT_DISABLED_TOOLSETS = ("terminal", "code_execution", "coding", "debugging", "computer_use", "delegation", "file",
                             "browser", "connections", "kanban", "image_gen", "video_gen", "video", "tts", "x_search",
                             "desktop_ui", "project", "web", "search")
CODE_TOOLSETS = ("terminal", "code_execution", "file")
WEB_TOOLSETS = ("web", "search")


def experts_dir() -> Path:
    d = wiki_files._catfish_home() / "experts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _dir(expert_id: str) -> Path:
    if not _ID.match(str(expert_id or "")):
        raise ValueError("专家 id 只能用小写字母、数字、横线, 字母开头, 2-31 个字符 (例 qual-specialist)")
    return experts_dir() / expert_id


def profile_id(expert_id: str) -> str:
    return PROFILE_PREFIX + expert_id


def _skill_lookup(path: str) -> Path | None:
    from .catfish_tools_skill_ops import _find_skill_dir  # noqa: PLC0415
    return _find_skill_dir(path)


def _frontmatter(md: Path) -> dict[str, Any]:
    try:
        text = md.read_text(encoding="utf-8")
        return (yaml.safe_load(text.split("---", 2)[1]) or {}) if text.startswith("---") else {}
    except (OSError, yaml.YAMLError, IndexError):
        return {}


# ── 定义 ─────────────────────────────────────────────────────────

def normalize(raw: dict[str, Any]) -> dict[str, Any]:
    """规整 + 校验一份定义。问题直接抛 ValueError (一次说全)。"""
    problems: list[str] = []
    eid = str(raw.get("id") or "").strip()
    if not _ID.match(eid):
        problems.append("专家 id 只能用小写字母、数字、横线, 字母开头 (例 qual-specialist)")
    name = str(raw.get("name") or "").strip()
    if not name:
        problems.append("要有个名字 (例 资质专员)")
    workflows = [str(x).strip() for x in raw.get("workflows") or [] if str(x).strip()]
    skills = [str(x).strip() for x in raw.get("skills") or [] if str(x).strip()]
    for p in workflows:
        d = _skill_lookup(p)
        if d is None or not (d / "workflow.yaml").exists():
            problems.append(f"工作流 {p} 不存在")
    for p in skills:
        d = _skill_lookup(p)
        if d is None or not (d / "SKILL.md").exists():
            problems.append(f"技能 {p} 不存在")
        elif (d / "workflow.yaml").exists():
            problems.append(f"{p} 是工作流, 放进「工作流」那一栏")
    usable = {f"workflow:{p}" for p in workflows} | {f"skill:{p}" for p in skills} | {"knowledge", "decline"}
    routes = []
    for i, r in enumerate(raw.get("routes") or [], 1):
        if not isinstance(r, dict):
            continue
        when, use = str(r.get("when") or "").strip(), str(r.get("use") or "").strip()
        if not when or not use:
            problems.append(f"第 {i} 条选路要写「什么事」和「走哪条路」")
            continue
        if use not in usable:
            problems.append(f"第 {i} 条选路用的「{use}」不在这个专家选中的工作流 / 技能里")
        routes.append({"when": when[:120], "use": use,
                       "ask": [str(a).strip()[:80] for a in r.get("ask") or [] if str(a).strip()][:5]})
    if not routes:
        problems.append("至少写一条选路: 什么事走哪个工作流 (或查知识库)")
    if len(routes) > MAX_ROUTES:
        problems.append(f"选路最多 {MAX_ROUTES} 条")
    if any(r["use"] == "knowledge" for r in routes) and "knowledge" not in (raw.get("tool_groups") or []):
        raw = {**raw, "tool_groups": [*(raw.get("tool_groups") or []), "knowledge"]}
    groups = [g for g in dict.fromkeys(raw.get("tool_groups") or []) if g]
    bad = [g for g in groups if g not in TOOL_GROUPS]
    if bad:
        problems.append(f"不认识的能力组: {', '.join(bad)}")
    if problems:
        raise ValueError("; ".join(problems))
    return {
        "id": eid, "name": name[:30], "description": str(raw.get("description") or "").strip()[:200],
        "persona": str(raw.get("persona") or "").strip()[:20000],
        "quick_prompts": [str(q).strip()[:80] for q in raw.get("quick_prompts") or [] if str(q).strip()][:6],
        "workflows": workflows, "skills": skills, "routes": routes,
        "checks": [{"after": str(c.get("after") or "").strip(), "verify": str(c.get("verify") or "").strip()[:200]}
                   for c in raw.get("checks") or [] if isinstance(c, dict) and str(c.get("verify") or "").strip()][:10],
        "tool_groups": groups, "allow_web": bool(raw.get("allow_web")), "allow_code": bool(raw.get("allow_code")),
        "model": str(raw.get("model") or "inherit").strip() or "inherit",
        "enabled": raw.get("enabled", True) is not False,
    }


def _new_id() -> str:
    """新建时自动给 id (内部用的目录名 / profile 名, 员工不用管): 字母开头 + 6 位随机。"""
    import secrets  # noqa: PLC0415
    while True:
        eid = "e" + secrets.token_hex(3)
        if not (experts_dir() / eid).exists():
            return eid


def save(raw: dict[str, Any]) -> dict[str, Any]:
    if not str(raw.get("id") or "").strip():
        raw = {**raw, "id": _new_id()}
    d = normalize(raw)
    old = load(d["id"]) if (_dir(d["id"]) / "expert.yaml").exists() else {}
    if (old.get("source") or {}).get("kind") == "pack":
        raise ValueError("这是从专家包装来的专家, 不能直接改 (要改请找原作者发新版本)")
    if old.get("pack"):
        d["pack"] = old["pack"]
    d["created_at"] = old.get("created_at") or workflow_store.now()
    d["updated_at"] = workflow_store.now()
    path = _dir(d["id"]) / "expert.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(d, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return d


def load(expert_id: str) -> dict[str, Any]:
    p = _dir(expert_id) / "expert.yaml"
    if not p.exists():
        raise LookupError(f"没有这个专家: {expert_id}")
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def list_all() -> list[dict[str, Any]]:
    out = []
    for p in sorted(experts_dir().glob("*/expert.yaml")):
        try:
            d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        src = d.get("source") or {}
        out.append({k: d.get(k) for k in ("id", "name", "description", "quick_prompts", "enabled", "updated_at")}
                   | {"profile_id": profile_id(d.get("id") or ""), "routes": len(d.get("routes") or []),
                      "workflows": len(d.get("workflows") or []), "skills": len(d.get("skills") or []),
                      "pack": {"version": src.get("version"), "origin": src.get("origin"),
                               "signed": (src.get("signature") or {}).get("status") == "signed",
                               "reviewed_by": (src.get("signature") or {}).get("reviewed_by")}
                      if src.get("kind") == "pack" else None,
                      "packed_version": (d.get("pack") or {}).get("version")})
    return out


def delete(expert_id: str) -> None:
    """删定义; 从专家包装的连包里的技能 / 工作流一起删 (运行记录留着)。"""
    d = _dir(expert_id)
    try:
        is_pack = (load(expert_id).get("source") or {}).get("kind") == "pack"
    except (LookupError, OSError, yaml.YAMLError):
        is_pack = False
    if is_pack:
        from .expert_pack import pack_ns, packs_root  # noqa: PLC0415
        shutil.rmtree(packs_root() / pack_ns(expert_id), ignore_errors=True)
    if d.exists():
        shutil.rmtree(d)


# ── 编译成 profile 要写的东西 ─────────────────────────────────────

def _use_text(use: str) -> str:
    if use == "knowledge":
        return "不跑流程, 用 catfish_wiki_search / catfish_wiki_read / catfish_search_docs 查资料回答, 说清出处"
    if use == "decline":
        return "这不归你管: 直说「这件事不归我管, 请在对话框上方「找谁」切回小鲶」, 不要自己去做"
    kind, path = use.split(":", 1)
    if kind == "workflow":
        return (f"跑工作流 `catfish_run_skill(skill_path=\"{path}\", params={{...}})` —— 不要自己一步步去做; "
                "返回 waiting_confirm 就告诉员工去鲶鱼页面顶上确认, failed 就说清第几步、为什么")
    return f"用技能 `{path}`: 有脚本的用 catfish_run_skill 跑, 说明型的照它 SKILL.md 里写的步骤做"


def build_soul(d: dict[str, Any]) -> str:
    persona = d.get("persona") or f"你是「{d['name']}」, {d.get('description') or '一个只做特定几件事的助手'}。"
    lines = [persona.strip(), "", "---", "", f"## 你能做的事 (选路规则, 照这个来)", ""]
    for i, r in enumerate(d["routes"], 1):
        lines.append(f"{i}. 员工要「{r['when']}」 → {_use_text(r['use'])}")
        if r.get("ask"):
            lines.append(f"   开始前先确认 (员工没说就问): {'; '.join(r['ask'])}")
    lines += ["", "不在上面任何一条里的事: 说「这件事不归我管, 请在对话框上方「找谁」切回小鲶」, 不要硬做。", ""]
    if d.get("checks"):
        lines += ["## 交付前核对 (告诉员工结果之前逐条自查, 没做到就补上或者明说)", ""]
        for c in d["checks"]:
            lines.append(f"- {('跑完 ' + c['after'] + ' 之后: ') if c.get('after') else ''}{c['verify']}")
        lines.append("")
    lines += ["## 规矩", "",
              "- 只用你看得到的工具和技能; 做不了的直说, 不绕路、不编结果。",
              "- 对外的步骤 (发收集表、推送、发布) 跑到时会停下, 只有员工能在鲶鱼界面里确认, 你没有确认的办法。",
              "- 数字和结论照工具 / 工作流返回的说, 不编。", ""]
    return "\n".join(lines)


def compile(expert_id: str) -> dict[str, Any]:
    """算出 profile 要写的东西, 顺手建好专家的 skills 目录 (软链)。"""
    from . import hermes_paths  # noqa: PLC0415

    d = normalize(load(expert_id))
    tools = list(BASE_TOOLS)
    for g in d["tool_groups"]:
        tools += [t for t in TOOL_GROUPS[g]["tools"] if t not in tools]
    disabled = [t for t in DEFAULT_DISABLED_TOOLSETS
                if not (d["allow_code"] and t in CODE_TOOLSETS) and not (d["allow_web"] and t in WEB_TOOLSETS)]

    links = _dir(expert_id) / "skills"
    if links.exists():
        shutil.rmtree(links)
    links.mkdir(parents=True)
    for p in d["workflows"] + d["skills"]:
        src = _skill_lookup(p)
        if src is not None:
            os.symlink(src, links / p.replace("/", "__"), target_is_directory=True)

    pid = profile_id(expert_id)
    local = hermes_paths.hermes_home() / "profiles" / pid / "skills"
    disabled_skills = sorted({_frontmatter(md).get("name") or md.parent.name
                              for md in local.rglob("SKILL.md")} - {"hermes-agent"}) if local.is_dir() else []
    return {"ok": True, "expert": d, "profile_id": pid, "soul": build_soul(d), "tools_include": tools,
            "skills_dir": str(links), "disabled_toolsets": disabled, "disabled_skills": disabled_skills,
            "model": d["model"], "display_name": d["name"], "description": d["description"] or d["name"]}


def catalog() -> dict[str, Any]:
    """界面上搭专家时能选的: 我的工作流、我的技能 (有脚本的和说明型的都行)、能力组。"""
    from .catfish_tools_skill_ops import _skill_search_roots  # noqa: PLC0415
    workflows, skills, seen = [], [], set()
    for root in _skill_search_roots():
        for md in sorted(root.glob("*/*/SKILL.md")):
            d = md.parent
            path = f"{d.parent.name}/{d.name}"
            if path in seen or d.parent.name not in ("personal", "department", "external"):
                continue
            seen.add(path)
            fm = _frontmatter(md)
            item = {"skill_path": path, "description": str(fm.get("description") or "")[:160]}
            if (d / "workflow.yaml").exists():
                workflows.append(item)
            else:
                skills.append({**item, "runnable": (d / "script.py").exists()})
    return {"ok": True, "workflows": workflows, "skills": skills,
            "tool_groups": [{"id": k, "label": v["label"]} for k, v in TOOL_GROUPS.items()]}


def pim_handlers(params: dict[str, Any]) -> dict[str, Any]:
    from . import expert_pack  # noqa: PLC0415
    eid = str(params.get("id") or "")
    return {
        "pim/expert_catalog": catalog,
        "pim/expert_list": lambda: {"ok": True, "experts": list_all()},
        "pim/expert_get": lambda: load(eid),
        "pim/expert_save": lambda: {"ok": True, "expert": save(params.get("expert") or {})},
        "pim/expert_compile": lambda: compile(eid),
        "pim/expert_delete": lambda: (delete(eid), {"ok": True})[1],
        **expert_pack.pim_handlers(params),
    }
