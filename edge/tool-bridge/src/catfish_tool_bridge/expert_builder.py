"""一句话搭专家 (11/08, docs/EXPERT-PACK-PLAN.md §5)。

鸿波: 「一句话同时生成工作流和专家」。员工说一句 (「帮我搭个资质专员: 每周一整理上周的资质邮件附件建任务,
证书快到期提前提醒, 平时回答资质政策问题」), 小鲶起草一张总览卡; 员工看过 / 改过, 点一下全部落地。

不新造生成能力, 是把已有的三段串起来:
  1. 拆活 (调一次模型): 一句话 → 一个或几个专家, 每个专家 2-6 件「活」, 每件活标明怎么做:
       reuse      复用员工已有的工作流 / 技能 (只能按编号挑, 优先复用)
       new        用积木新搭一个工作流
       knowledge  查知识库回答, 不用新建东西
       teach      还不会 (要在网页上操作, 积木做不了): 员工先演示一遍
     要不要定时、员工通常怎么说, 也一起标出来。
  2. 每件 new 的活起草工作流 (同 catfish_workflow draft 那套格式和校验), 校验不过把问题交回模型改, 最多两轮;
     还不过的在卡上标红。存成工作流草稿 (界面上能看步骤、试跑)。
  3. 员工点「全部保存并生效」(只有界面能点): 保存新工作流 → 存专家定义 → Companion 建专家分身 (Rust) → 挂定时。
     中间哪一步失败, 已经做了的撤回 (新存的工作流退回草稿、专家定义删掉), 不留半成品。
「还不会」的那件写进专家的「还不会的事」; 员工点「现在教」→ 教学对话 (技能名事先定好) → 技能凝固成功时
attach_taught() 按名字找到这件活, 接进专家的选路, 返回要重新生效的专家 —— Companion 收到就自动重新生效。

规矩照旧: 模型只能挑积木、按格式填, 不写代码; 对外步骤照样停下等员工确认; 新建的都放员工个人名下;
员工点保存才落地。总览卡存 ~/.catfish/expert_builds/<id>.json (本机)。
"""
from __future__ import annotations

import json
import re
import secrets
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from . import expert_local, wiki_files
from . import workflow_catalog as catalog
from . import workflow_lint as lint
from . import workflow_store as store
from . import workflow_tool, workflow_xiaonian

MAX_EXPERTS = 4
MAX_JOBS = 6
FIX_ROUNDS = 2
KINDS = ("reuse", "new", "knowledge", "teach")
_CRON = re.compile(r"^\s*(\S+\s+){4}\S+\s*$")
_BID = re.compile(r"^b[0-9a-f]{8}$")

Llm = Callable[[str], str]


def _llm(prompt: str) -> str:
    try:
        return workflow_xiaonian._call_llm(prompt, source="plugin:toolbridge-expert-builder")   # 外发记录: 专家 · 一句话搭专家
    except RuntimeError as e:   # 连不上模型 / 没登录: 界面上说清楚
        raise ValueError(str(e)) from None


def builds_dir() -> Path:
    d = wiki_files._catfish_home() / "expert_builds"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(bid: str) -> Path:
    if not _BID.match(str(bid or "")):
        raise ValueError(f"总览卡编号不对: {bid!r}")
    return builds_dir() / f"{bid}.json"


def load(bid: str) -> dict[str, Any]:
    p = _path(bid)
    if not p.exists():
        raise LookupError("这张总览卡不在了 (可能已经丢掉)")
    return json.loads(p.read_text(encoding="utf-8"))


def _save(b: dict[str, Any]) -> dict[str, Any]:
    b["updated_at"] = store.now()
    _path(b["build_id"]).write_text(json.dumps(b, ensure_ascii=False, indent=2), encoding="utf-8")
    return b


# ── 1. 拆活 ──────────────────────────────────────────────────────

_PLAN_PROMPT = """员工想在鲶鱼里搭「专家」—— 只管某一类事的小鲶分身。根据员工的一句话, 拆出要搭哪几个专家、每个专家接哪几件活。

员工的话: <<<{sentence}>>>

员工已有的 (编号 · 中文名 · 说明), 能复用就复用:
{existing}

鲶鱼能直接用的积木 (新搭工作流时用; 名字 · 中文 · 级别):
{actions}

规则:
- 一句话里说的是一类事就一个专家; 明显是几类不相干的事才拆成几个 (最多 {max_experts} 个)
- 每个专家 2-{max_jobs} 件活 (jobs), 每件活:
  - when: 员工平时会怎么说这件事 (一句)
  - title: 4-12 字的短名
  - kind: reuse (上面已有的就能做, use 写编号 w1 / s2) / new (用积木新搭工作流, goal 写清楚要做成什么、交出什么) /
          knowledge (查知识库 / 文档回答就行) / teach (要在某个网页系统里点点点, 积木做不了, what 写要教什么)
  - schedule: 要不要定时, 要的话写 5 段 cron (例 每周一 9 点 "0 9 * * 1"), 不要就空; schedule_text 写中文 (例 每周一 9:00)
- 员工没提到的活不要加
- persona: 两三句人设 (我是谁、说话风格、不碰什么); quick_prompts: 2-3 句快捷问法; decline: 哪些相近的事不归它管 (一句, 没有就空)
- tool_groups 从这些里挑 (可多选): {groups}
只交回 JSON:
{{"experts": [{{"name": "...", "description": "一句话", "persona": "...", "quick_prompts": ["..."], "tool_groups": ["knowledge"],
  "decline": "...", "jobs": [{{"when": "...", "title": "...", "kind": "new", "use": "", "goal": "...", "what": "",
  "schedule": "", "schedule_text": ""}}]}}]}}
"""

_WF_PROMPT = """用鲶鱼的积木搭一个工作流 (一串步骤), 完成这件事:
要做成: <<<{goal}>>>
员工平时这么说: {when}

积木 (action): 名字 · 中文 · 级别 · 参数 (* 必填) · 输出字段
{actions}

员工已有、能当一步用的技能 (skill): 路径 · 中文名 · 参数
{skills}

格式 (只能这样写, 不能写代码或表达式):
- 每步 {{"id": 小写字母开头, 三选一: "action": 积木名 + "params" | "skill": 技能路径 + "params" |
  "xiaonian": 让小鲶判断什么 + "input": "${{steps.某步.字段}}" + "returns": {{字段: text|number|list|bool|date}}}}
- 取前面步骤的结果: "${{steps.<id>.<输出字段>}}"; 可变的值做成参数 "${{params.<名>}}" (在 params 里声明 {{"type": text|number|date|bool|list, "default": ...}})
- 对一个列表每项做一次: "for_each": "${{steps.<id>.<列表字段>}}", 里面用 "${{item}}"
- 有条件才做: "when": {{"of": "${{...}}", "is": not_empty|empty|true|false|eq|ne|gt|ge|lt|le|contains, "to": 值}}; 分支 = 两步写相反的条件
- 一步至少要有结果才算成: "expect": {{"count_min": 1}}
- 级别 outward 的积木会停下等员工确认, 照常用
- 小鲶那步 (xiaonian) 只能看交进来的数据写字: 不能调工具、跑代码、生成文件、上网、发东西。
  要 Word 用 export_docx (body 填前面那步交出的 markdown); 要存知识库用 wiki_create;
  网上的资料、链接、版权信息拿不到, 只用本机文件 / 知识库 / 邮件里查得到的, 不要让小鲶编
- 越简单越好, 最多 {max_steps} 步: 对一串东西做同一件事, 先让一步交出列表, 再 for_each 一步做完, 不要抄好几遍
- 每个参数写 label (员工看得懂的中文名, 例 {{"type": "text", "label": "主题"}}), 员工没说的值给合理的 default
{current}{fix}
只交回 JSON: {{"title": "4-12 字中文名", "description": "一句话: 什么时候用", "triggers": ["员工的说法"],
  "workflow": {{"kind": "workflow", "version": "1.0.0", "params": {{}}, "steps": [...]}}}}
"""


def _existing() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cat = expert_local.catalog()
    return cat["workflows"][:40], cat["skills"][:60]


def _actions_text(full: bool = False) -> str:
    rows = []
    for a in catalog.describe():
        if full:
            params = ", ".join(f"{p}{'*' if p in a['required'] else ''}" for p in a["params"])
            rows.append(f"{a['name']} · {a['label']} · {a['level']} · 参数: {params or '无'} · 输出: {', '.join(a['outputs']) or '无'}")
        else:
            rows.append(f"{a['name']} · {a['label']} · {a['level']}")
    return "\n".join(rows)


def _skills_text() -> str:
    rows = []
    for s in workflow_tool.runnable_skills()[:60]:
        params = ", ".join(f"{p['name']}{'*' if p.get('required') else ''}" for p in s.get("params") or [])
        rows.append(f"{s['skill_path']} · {s.get('title') or s.get('description') or ''} · 参数: {params or '无'}"[:200])
    return "\n".join(rows) or "(还没有)"


def _new_bid() -> str:
    while True:
        bid = "b" + secrets.token_hex(4)
        if not (builds_dir() / f"{bid}.json").exists():
            return bid


def _slug(bid: str, ek: int, jk: int) -> str:
    return f"{bid[1:7]}-e{ek}-j{jk}"


def draft_workflow(job: dict[str, Any], name: str, llm: Llm, *, current: dict[str, Any] | None = None,
                   request: str = "") -> dict[str, Any]:
    """起草一件 new 的活的工作流, 校验不过把问题交回去改, 最多 FIX_ROUNDS 轮。存成草稿; 返回 {draft, problems}。
    current + request: 在现有草稿上按员工的一句话改 (卡上「改一改」/「让小鲶修好」)。
    步数太多、同一块积木抄好几遍这类 (lint.build_hints) 也交回去改, 但改不动不算问题、不拦保存。"""
    fix = ""
    problems: list[str] = []
    got: dict[str, Any] = {}
    cur = ""
    if current:
        cur = ("现在的工作流 (在它上面改, 员工没让改的地方别动):\n"
               + json.dumps(current.get("workflow") or {}, ensure_ascii=False)[:6000]
               + f"\n员工要这样改: <<<{request[:500]}>>>\n")
    for _round in range(FIX_ROUNDS + 1):
        raw = llm(_WF_PROMPT.format(goal=job.get("goal") or job["title"], when=job["when"], actions=_actions_text(True),
                                    skills=_skills_text(), max_steps=lint.BUILD_MAX_STEPS, current=cur, fix=fix))
        try:
            got = workflow_xiaonian.parse_json(raw)
            wf = got.get("workflow")
            if not isinstance(wf, dict):
                raise ValueError("没交回 workflow")
            wf.setdefault("kind", "workflow")
            wf.setdefault("version", "1.0.0")
            problems = lint.check(wf, skill_lookup=workflow_tool.skill_lookup)
            hints = lint.build_hints(wf)
        except ValueError as e:
            problems, hints = [f"交回的不是能用的 JSON: {e}"], []
        if not problems and not hints:
            break
        fix = "上一次写的有这些问题, 改好再交:\n" + "\n".join(f"- {p}" for p in (problems + hints)[:10])
    if not isinstance(got.get("workflow"), dict):
        return {"draft": None, "problems": problems}
    keep = current or {}
    d = store.save_draft({"name": name, "title": str(got.get("title") or keep.get("title") or job["title"])[:30],
                          "namespace": "personal",
                          "description": str(got.get("description") or keep.get("description") or job["when"])[:300],
                          "triggers": [str(t)[:60] for t in got.get("triggers") or keep.get("triggers") or [job["when"]]][:12],
                          "workflow": got["workflow"], "from_build": name.split("-")[0]})
    return {"draft": d["name"], "problems": problems}


def _job(raw: dict[str, Any], wf: list[dict[str, Any]], sk: list[dict[str, Any]]) -> dict[str, Any]:
    kind = raw.get("kind") if raw.get("kind") in KINDS else "new"
    job = {"when": str(raw.get("when") or "").strip()[:120], "title": str(raw.get("title") or "").strip()[:30],
           "kind": kind, "include": True, "problems": []}
    if not job["when"]:
        job["when"] = job["title"]
    if kind == "reuse":
        ref = str(raw.get("use") or "").strip()
        try:
            n = int(ref[1:])
            item = (wf if ref.startswith("w") else sk)[n - 1] if n >= 1 else None
        except (ValueError, IndexError):
            item = None
        if not item:   # 编号对不上: 当成要新搭
            job.update(kind="new", goal=str(raw.get("goal") or job["when"]))
        else:
            job.update(use=("workflow:" if ref.startswith("w") else "skill:") + item["skill_path"],
                       use_title=item.get("title") or item["skill_path"])
    if job["kind"] == "new":
        job["goal"] = str(raw.get("goal") or job["when"]).strip()[:300]
    if kind == "teach":
        job["what"] = str(raw.get("what") or job["when"]).strip()[:200]
    cron = str(raw.get("schedule") or "").strip()
    if cron and _CRON.match(cron) and job["kind"] in ("new", "reuse") and str(job.get("use", "workflow:")).startswith("workflow:"):
        job["schedule"] = {"cron": cron, "text": str(raw.get("schedule_text") or cron)[:30]}
    return job


def plan(sentence: str, *, llm: Llm | None = None) -> dict[str, Any]:
    """一句话 → 总览卡 (还没落地)。"""
    llm = llm or _llm
    sentence = str(sentence or "").strip()
    if len(sentence) < 6:
        raise ValueError("多说一点: 要它管哪类事、具体做哪几件 (例「帮我搭个资质专员: 每周一整理资质邮件附件建任务…」)")
    wf, sk = _existing()
    existing = [f"w{i} · {w['title']} · {w.get('summary') or w['description']}"[:160] for i, w in enumerate(wf, 1)]
    existing += [f"s{i} · {s['title']} · {s.get('summary') or s['description']}"[:160] for i, s in enumerate(sk, 1)]
    groups = ", ".join(f"{k} ({v['label']})" for k, v in expert_local.TOOL_GROUPS.items())
    got = workflow_xiaonian.parse_json(llm(_PLAN_PROMPT.format(
        sentence=sentence[:1000], existing="\n".join(existing) or "(还没有)", actions=_actions_text(),
        groups=groups, max_experts=MAX_EXPERTS, max_jobs=MAX_JOBS)))
    bid = _new_bid()
    experts = []
    for ek, e in enumerate((got.get("experts") or [])[:MAX_EXPERTS], 1):
        if not isinstance(e, dict) or not str(e.get("name") or "").strip():
            continue
        jobs = [_job(j, wf, sk) for j in (e.get("jobs") or [])[:MAX_JOBS] if isinstance(j, dict)]
        if not jobs:
            continue
        experts.append({
            "key": ek, "include": True, "name": str(e["name"]).strip()[:30],
            "description": str(e.get("description") or "").strip()[:200],
            "persona": str(e.get("persona") or "").strip()[:2000],
            "quick_prompts": [str(q).strip()[:80] for q in e.get("quick_prompts") or [] if str(q).strip()][:4],
            "tool_groups": [g for g in dict.fromkeys(e.get("tool_groups") or []) if g in expert_local.TOOL_GROUPS],
            "decline": str(e.get("decline") or "").strip()[:120], "jobs": jobs})
    if not experts:
        raise ValueError("小鲶没拆出能做的活 —— 把要它做的几件事说具体些再试")
    # 2. 新搭的工作流并排起草 (每件一两次模型调用)
    todo = [(e, jk, j) for e in experts for jk, j in enumerate(e["jobs"], 1) if j["kind"] == "new"]
    with ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(lambda t: draft_workflow(t[2], _slug(bid, t[0]["key"], t[1]), llm), todo))
    for (_e, _jk, j), r in zip(todo, results):
        j["draft"], j["problems"] = r["draft"], r["problems"]
        _check_job(j)
    for e in experts:
        for jk, j in enumerate(e["jobs"], 1):
            if j["kind"] == "teach":
                j["teach_name"] = f"t{bid[1:7]}-e{e['key']}-j{jk}"
            if j["kind"] == "knowledge" and "knowledge" not in e["tool_groups"]:
                e["tool_groups"].append("knowledge")
    return _save({"build_id": bid, "sentence": sentence, "status": "draft", "created_at": store.now(), "experts": experts})


# ── 卡片上的改动 ─────────────────────────────────────────────────

def _expert(b: dict[str, Any], ek: int) -> dict[str, Any]:
    e = next((x for x in b["experts"] if x["key"] == ek), None)
    if e is None:
        raise LookupError(f"没有第 {ek} 个专家")
    return e


def update(bid: str, ek: int, patch: dict[str, Any]) -> dict[str, Any]:
    """员工在卡上改: 专家勾不勾 / 名字 / 人设 / 简介; 某件活勾不勾 / 定时。"""
    b = load(bid)
    if b["status"] != "draft":
        raise ValueError("这张卡已经保存生效了, 要改去专家页改")
    e = _expert(b, int(ek))
    for k in ("name", "persona", "description"):
        if k in patch:
            e[k] = str(patch[k] or "").strip()[:2000 if k == "persona" else 200]
    if "include" in patch:
        e["include"] = bool(patch["include"])
    for jp in patch.get("jobs") or []:
        idx = int(jp.get("index", -1))
        if not 0 <= idx < len(e["jobs"]):
            continue
        j = e["jobs"][idx]
        if "include" in jp:
            j["include"] = bool(jp["include"])
        if "schedule" in jp:
            cron = str(jp["schedule"] or "").strip()
            if cron and not _CRON.match(cron):
                raise ValueError(f"定时要写 5 段 cron: {cron!r}")
            j["schedule"] = {"cron": cron, "text": str(jp.get("schedule_text") or cron)[:30]} if cron else None
    return _save(b)


def _check_job(j: dict[str, Any]) -> None:
    """重新查一件 new 的活: 卡上的问题 + 步骤链 (卡上直接画「① 搜邮件 → ② 小鲶：整理 → ③ 导出 Word 文件」)。"""
    if j["kind"] != "new" or not j.get("draft"):
        return
    try:
        wf = store.load_draft(j["draft"])["workflow"]
    except LookupError:
        j["problems"], j["chain"] = ["工作流草稿不见了, 点「让小鲶修好」重新搭这一件"], []
        return
    j["problems"] = lint.check(wf, skill_lookup=workflow_tool.skill_lookup)
    j["chain"] = workflow_tool.chain(wf)


def refresh_problems(bid: str) -> dict[str, Any]:
    """员工在工作流编辑器里 / 小鲶在对话里改过草稿之后: 重新校验, 卡上的红字跟着变。"""
    b = load(bid)
    if b["status"] == "draft":
        for e in b["experts"]:
            for j in e["jobs"]:
                _check_job(j)
    return _save(b)


def refresh_for_draft(draft: str) -> None:
    """对话里改了某件活的草稿 (workflow_tool.save_draft): 找到它所在的总览卡, 卡上的问题跟着刷新。"""
    tag = draft.split("-")[0]
    for p in builds_dir().glob(f"b{tag}*.json"):
        try:
            refresh_problems(p.stem)
        except (LookupError, ValueError, OSError):
            continue


def revise(bid: str, ek: int, index: int, request: str) -> dict[str, Any]:
    """卡上那件活旁边说一句「要怎么改」/ 点「让小鲶修好」: 在现有草稿上改, 校验不过再改, 卡跟着刷新。
    草稿丢了就按这件活的目标重新搭。"""
    b = load(bid)
    if b["status"] != "draft":
        raise ValueError("这张卡已经保存生效了, 去工作流列表里改")
    jobs = _expert(b, ek)["jobs"]
    if not 0 <= index < len(jobs) or jobs[index]["kind"] != "new":
        raise ValueError("这件活不是新搭的工作流, 没有步骤可改")
    j = jobs[index]
    try:
        current = store.load_draft(j["draft"]) if j.get("draft") else None
    except LookupError:
        current = None
    request = str(request or "").strip()
    if not request:
        problems = [lint.plain(p) for p in j.get("problems") or []]
        request = "把这些问题修好, 别的不动: " + "; ".join(problems) if problems else "再检查一遍, 能简化就简化"
    r = draft_workflow(j, j.get("draft") or _slug(bid, ek, index + 1), _llm, current=current, request=request)
    j["draft"] = r["draft"]
    _check_job(j)
    return _save(b)


def discard(bid: str) -> dict[str, Any]:
    b = load(bid)
    if b["status"] == "draft":
        for e in b["experts"]:
            for j in e["jobs"]:
                if j.get("draft"):
                    store.delete_draft(j["draft"])
    _path(bid).unlink(missing_ok=True)
    return {"ok": True}


def list_open() -> list[dict[str, Any]]:
    out = []
    for p in sorted(builds_dir().glob("b*.json"), reverse=True):
        try:
            b = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        teach_left = sum(1 for e in b["experts"] if e.get("include") for j in e["jobs"]
                         if j.get("include") and j["kind"] == "teach" and not j.get("attached"))
        if b["status"] == "draft" or teach_left:
            out.append(b)
    return out


# ── 3. 落地 / 撤回 ───────────────────────────────────────────────

def _uninstall(skill_path: str) -> None:
    d = workflow_tool.skill_lookup(skill_path)
    if d is not None and (d / "workflow.yaml").exists() and not (d / "script.py").exists():
        shutil.rmtree(d, ignore_errors=True)


def _definition(e: dict[str, Any]) -> dict[str, Any]:
    routes, workflows, skills, todo = [], [], [], []
    for j in e["jobs"]:
        if not j.get("include"):
            continue
        if j["kind"] in ("reuse", "new") and j.get("use"):
            kind, _, path = j["use"].partition(":")
            (workflows if kind == "workflow" else skills).append(path)
            routes.append({"when": j["when"], "use": j["use"], "ask": []})
        elif j["kind"] == "knowledge":
            routes.append({"when": j["when"], "use": "knowledge", "ask": []})
        elif j["kind"] == "teach":
            if j.get("attached"):
                skills.append(j["attached"])
                routes.append({"when": j["when"], "use": f"skill:{j['attached']}", "ask": []})
            else:
                todo.append({"when": j["when"], "what": j["what"], "teach_name": j["teach_name"]})
    if e.get("decline"):
        routes.append({"when": e["decline"], "use": "decline", "ask": []})
    return {"id": "", "name": e["name"], "description": e["description"], "persona": e["persona"],
            "quick_prompts": e["quick_prompts"], "workflows": list(dict.fromkeys(workflows)),
            "skills": list(dict.fromkeys(skills)), "routes": routes, "todo": todo, "tool_groups": e["tool_groups"],
            "model": "inherit", "enabled": True}


def apply(bid: str) -> dict[str, Any]:
    """员工点「全部保存并生效」的前半段: 保存新工作流 + 存专家定义。专家分身由 Companion 建 (Rust), 失败时调 rollback。"""
    b = refresh_problems(bid)   # 草稿可能刚在编辑器 / 对话里改过, 按现在的样子查
    if b["status"] != "draft":
        raise ValueError("这张卡已经保存过了")
    chosen = [e for e in b["experts"] if e.get("include") and any(j.get("include") for j in e["jobs"])]
    if not chosen:
        raise ValueError("一个专家都没勾")
    bad = [f"「{e['name']}」的「{j['title']}」: {lint.plain(j['problems'][0])}" for e in chosen for j in e["jobs"]
           if j.get("include") and j["kind"] == "new" and (j.get("problems") or not j.get("draft"))]
    if bad:
        raise ValueError("还有工作流没搭好, 先改好或者不勾那件活: " + "; ".join(bad[:4]))
    made_wf: list[tuple[dict[str, Any], str]] = []
    made_ex: list[str] = []
    try:
        for e in chosen:
            for j in e["jobs"]:
                if j.get("include") and j["kind"] == "new":
                    try:
                        draft = store.load_draft(j["draft"])
                    except LookupError:   # 员工在工作流卡片里已经单独保存了这份草稿: 直接用装好的
                        if workflow_tool.skill_lookup(f"personal/{j['draft']}") is None:
                            raise ValueError(f"「{j['title']}」的工作流草稿不见了, 重新生成这张卡") from None
                        j["use"] = f"workflow:personal/{j['draft']}"
                        continue
                    r = workflow_tool.save(j["draft"])
                    if not r.get("ok"):
                        raise ValueError(f"「{j['title']}」保存不了: {r.get('error')}")
                    j["use"], j["draft_copy"] = f"workflow:{r['skill_path']}", draft
                    made_wf.append((j, r["skill_path"]))
            d = expert_local.save(_definition(e))
            e["expert_id"] = d["id"]
            made_ex.append(d["id"])
    except Exception:
        _undo(made_wf, made_ex)
        for e in chosen:
            e.pop("expert_id", None)
        _save(b)
        raise
    b["status"] = "saved"
    b["saved_at"] = store.now()
    _save(b)
    schedules = [{"skill_path": j["use"].split(":", 1)[1], "title": j["title"], "cron": j["schedule"]["cron"]}
                 for e in chosen for j in e["jobs"]
                 if j.get("include") and j.get("schedule") and str(j.get("use", "")).startswith("workflow:")]
    return {"ok": True, "experts": [{"id": e["expert_id"], "name": e["name"]} for e in chosen], "schedules": schedules,
            "build": b}


def _undo(made_wf: list[tuple[dict[str, Any], str]], made_ex: list[str]) -> None:
    for eid in made_ex:
        try:
            expert_local.delete(eid)
        except (OSError, LookupError, ValueError):
            pass
    for j, path in made_wf:
        _uninstall(path)
        copy = j.pop("draft_copy", None)
        if copy:
            store.save_draft(copy)
        j.pop("use", None)


def rollback(bid: str) -> dict[str, Any]:
    """Companion 建专家分身失败: 撤回这张卡落地的东西 (新存的工作流退回草稿、专家定义删掉), 卡回到没保存的样子。"""
    b = load(bid)
    if b["status"] != "saved":
        return {"ok": True, "build": b}
    made_wf = [(j, j["use"].split(":", 1)[1]) for e in b["experts"] for j in e["jobs"]
               if j.get("draft_copy") and str(j.get("use", "")).startswith("workflow:")]
    made_ex = [e["expert_id"] for e in b["experts"] if e.get("expert_id")]
    _undo(made_wf, made_ex)
    for e in b["experts"]:
        e.pop("expert_id", None)
    b["status"] = "draft"
    return {"ok": True, "build": _save(b)}


# ── 「还不会」的那件: 教完自动接上 ────────────────────────────────

def teach_prompt(bid: str, ek: int, index: int) -> dict[str, Any]:
    """点「现在教」: 给对话的第一句 (技能名事先定好, 凝固时用它, 教完才接得上)。"""
    b = load(bid)
    e = _expert(b, int(ek))
    j = e["jobs"][int(index)]
    if j["kind"] != "teach":
        raise ValueError("这件活不用教")
    prompt = "\n".join([
        f"我要教你做一件事, 教会后它会接进专家「{e['name']}」: {j['what']}。",
        f"先调 catfish_teach_start (name={j['teach_name']}, description 写上面这件事) 开始录, 然后我一步步指挥你做,",
        "只做我说的步骤, 不确定先问我。我说教完了, 你调 catfish_teach_end, 再调 catfish_freeze_skill",
        f"(name={j['teach_name']}, namespace=personal, title 用「{j['title']}」)。凝固好它会自动接进专家, 不用你另外做。",
    ])
    return {"ok": True, "prompt": prompt, "chat_key": f"expert-teach:{j['teach_name']}"}


def attach_taught(skill_name: str, skill_path: str) -> dict[str, Any]:
    """技能凝固成功后调: 名字对上某张卡里「还不会」的那件 → 接进专家选路。返回要重新生效的专家 id。"""
    reapply: list[str] = []
    attached: list[str] = []
    for p in sorted(builds_dir().glob("b*.json")):
        try:
            b = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        hit = False
        for e in b["experts"]:
            for j in e["jobs"]:
                if j["kind"] == "teach" and j.get("teach_name") == skill_name and not j.get("attached"):
                    j["attached"] = skill_path
                    hit = True
                    attached.append(f"{e['name']} · {j['title']}")
                    if b["status"] == "saved" and e.get("expert_id"):
                        # 只往专家现在的定义里添这一条 (员工后来在专家页改过的不动)
                        try:
                            d = expert_local.load(e["expert_id"])
                        except (OSError, LookupError, ValueError):
                            continue
                        d["skills"] = list(dict.fromkeys([*(d.get("skills") or []), skill_path]))
                        d["routes"] = [r for r in d.get("routes") or [] if r.get("use") != "decline"] + \
                            [{"when": j["when"], "use": f"skill:{skill_path}", "ask": []}] + \
                            [r for r in d.get("routes") or [] if r.get("use") == "decline"]
                        d["todo"] = [t for t in d.get("todo") or [] if t.get("teach_name") != skill_name]
                        expert_local.save(d)
                        reapply.append(e["expert_id"])
        if hit:
            _save(b)
    return {"reapply_experts": list(dict.fromkeys(reapply)), "attached": attached}


def after_freeze(args: dict[str, Any], r: Any) -> Any:
    """catfish_freeze_skill 凝固成功后: 名字对上总览卡里「还不会」的那件 → 接进专家;
    结果里带上 reapply_experts / attached, Companion 收到就自动重新生效 (ChatExpertBuild.TaughtReapply)。"""
    if not (isinstance(r, dict) and r.get("ok") and r.get("skill_path")):
        return r
    try:
        got = attach_taught(str(r.get("name") or args.get("name") or ""), r["skill_path"])
    except (OSError, ValueError, LookupError) as e:
        return {**r, "attach_error": f"技能存好了, 但没接进专家: {e}"}
    if not got["attached"]:
        return r
    return {**r, **got, "summary": f"{r.get('summary') or ''} 已接进专家: {'、'.join(got['attached'])}".strip()}


# ── 给工具 / 界面 ────────────────────────────────────────────────

def summary_text(b: dict[str, Any]) -> str:
    lines = []
    for e in b["experts"]:
        lines.append(f"专家「{e['name']}」: {e['description']}")
        for j in e["jobs"]:
            how = {"reuse": f"用已有的「{j.get('use_title') or j.get('use')}」",
                   "new": "新搭工作流" + (f" (还没搭好: {lint.plain(j['problems'][0])})" if j.get("problems") else ""),
                   "knowledge": "查知识库回答", "teach": f"还不会, 要你先教一遍: {j.get('what')}"}[j["kind"]]
            sched = f", {j['schedule']['text']}" if j.get("schedule") else ""
            lines.append(f"  - {j['title']}: {how}{sched}")
    return "\n".join(lines)


def pim_handlers(params: dict[str, Any]) -> dict[str, Any]:
    bid = str(params.get("build_id") or "")
    return {
        "pim/expert_build_plan": lambda: {"ok": True, "build": plan(str(params.get("sentence") or ""))},
        "pim/expert_build_list": lambda: {"ok": True, "builds": list_open()},
        "pim/expert_build_get": lambda: {"ok": True, "build": refresh_problems(bid)},
        "pim/expert_build_update": lambda: {"ok": True, "build": update(bid, int(params.get("expert") or 0), params.get("patch") or {})},
        "pim/expert_build_apply": lambda: apply(bid),
        "pim/expert_build_rollback": lambda: rollback(bid),
        "pim/expert_build_discard": lambda: discard(bid),
        "pim/expert_build_revise": lambda: {"ok": True, "build": revise(bid, int(params.get("expert") or 0),
                                                                       int(params.get("index") or 0),
                                                                       str(params.get("request") or ""))},
        "pim/expert_build_teach": lambda: teach_prompt(bid, int(params.get("expert") or 0), int(params.get("index") or 0)),
    }
