"""员工装的共享内容 (技能 / 专家包 / 部门知识页): 定时查「有没有新版」「被撤回了没」(11/14).

原来只有员工自己打开对应卡片才看得到更新, 撤回的技能 / 专家包装过的人一点提示都没有, 还继续在用。
这里由 Companion 后台定时调 (pim/shared_check, Rust services/shared_check.rs, 启动后几分钟一次、之后每 6 小时),
结果落 ~/.catfish/shared_check.json, 早安简报那一条读它。

撤回的处理 (公理 4: 不删员工本机的东西, 只停用):
- 技能: 结果里给出 disable_skill_names, Rust 把它们加进 hermes 的 skills.disabled (新对话里模型看不到);
  catfish_run_skill 也查这里的 auto_disabled, 不再执行。以后装上了能用的版本 → 自动恢复。
- 专家包: expert.yaml 里 enabled 改 false + source.withdrawn 记谁什么时候撤的, Rust 再 apply 一次 (「找谁」里不再出现)。
  以后装上了能用的版本 → 只恢复我们自己停掉的那些。
- 知识页: 跟「拉更新」一样只标「已撤回」(wiki_install._mark_local_withdrawn), 搜索默认不再列。

「hidden」= 这个人现在看不到了 (换部门 / 作者把全公司收回到本部门): 只列出来, 不停用。
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("catfish.tool_bridge.shared_check")

WITHDRAWN = ("withdrawn", "rejected", "gone")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _home() -> Path:
    from .wiki_files import _catfish_home  # noqa: PLC0415
    return _catfish_home()


def state_path() -> Path:
    return _home() / "shared_check.json"


def load_state() -> dict[str, Any]:
    try:
        return json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(st: dict[str, Any]) -> None:
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def ver_key(v: str) -> tuple:
    """跟 skills-hub storage.version_key 同一条规则: 数字段按数值比。"""
    return tuple((1, int(p), "") if p.isdigit() else (0, 0, p) for p in re.split(r"[.\-+]", str(v or "")))


def _newer(latest: str | None, installed: str) -> bool:
    return bool(latest) and ver_key(str(latest)) > ver_key(installed)


# ── 本机装了什么 ──────────────────────────────────────────────────


def _frontmatter_name(skill_md: Path) -> str:
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError:
        return ""
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    for line in text[3:end if end > 0 else 0].split("\n"):
        if line.startswith("name:"):
            return line.partition(":")[2].strip().strip("'\"")
    return ""


def installed_skills() -> list[dict[str, Any]]:
    """从中央装的技能。安装时只在 ~/.catfish/skill_audit.jsonl 记了来源和版本, 按装到哪个目录取最后一次成功安装:
    最后一次是从中央装的、目录还在、不是试用装的 (试用由 skill_trial 管) 才算。"""
    from .catfish_tools_skill_ops import _skill_audit_path  # noqa: PLC0415
    last: dict[str, dict] = {}
    try:
        with _skill_audit_path().open(encoding="utf-8") as f:
            for line in f:
                if '"install"' not in line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("event_type") == "install" and ev.get("ok") and ev.get("installed_path"):
                    last[str(ev["installed_path"])] = ev
    except OSError:
        return []
    out = []
    for path, ev in last.items():
        meta = ev.get("hub_meta") or {}
        d = Path(path)
        if ev.get("install_source") != "hub" or ev.get("trial") or not meta.get("hub_version"):
            continue
        if not (d / "SKILL.md").exists() or (d / "trial.json").exists():
            continue
        out.append({"namespace": meta.get("hub_namespace"), "name": meta.get("hub_name"),
                    "version": str(meta["hub_version"]), "path": path,
                    "skill_name": _frontmatter_name(d / "SKILL.md") or str(meta.get("hub_name"))})
    return out


def installed_experts() -> list[dict[str, Any]]:
    import yaml  # noqa: PLC0415
    from .expert_local import experts_dir  # noqa: PLC0415
    out = []
    for p in sorted(experts_dir().glob("*/expert.yaml")):
        try:
            d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        src = d.get("source") or {}
        if src.get("kind") != "pack" or src.get("origin") != "hub" or src.get("trial") or not src.get("version"):
            continue
        out.append({"id": d.get("id") or p.parent.name, "version": str(src["version"]), "title": d.get("name") or d.get("id"),
                    "enabled": d.get("enabled", True) is not False})
    return out


def installed_wiki() -> list[dict[str, Any]]:
    from .wiki_install import _shared_root  # noqa: PLC0415
    out = []
    for p in sorted(_shared_root().glob("dept/*/*.meta.json")):
        try:
            m = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if m.get("file_id") and m.get("namespace"):
            out.append(m)
    return out


# ── 停用 / 恢复 ──────────────────────────────────────────────────


def _set_expert_enabled(eid: str, enabled: bool, withdrawn: dict | None) -> None:
    """专家包装来的专家 expert_local.save() 不让改, 这里只动 enabled 和 source.withdrawn 两个字段。"""
    import yaml  # noqa: PLC0415
    from .expert_local import _dir  # noqa: PLC0415
    p = _dir(eid) / "expert.yaml"
    d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    d["enabled"] = enabled
    src = dict(d.get("source") or {})
    if withdrawn:
        src["withdrawn"] = withdrawn
    else:
        src.pop("withdrawn", None)
    d["source"] = src
    tmp = p.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(d, allow_unicode=True, sort_keys=False), encoding="utf-8")
    tmp.replace(p)


def is_skill_withdrawn(skill_dir: Path) -> dict[str, Any] | None:
    """catfish_run_skill 用: 这个目录的技能是不是被我们因撤回停用了。"""
    want = str(skill_dir)
    for s in (load_state().get("auto_disabled") or {}).get("skills") or []:
        if s.get("path") == want:
            return s
    return None


# ── 主流程 ───────────────────────────────────────────────────────


def _wiki_docs() -> tuple[dict[tuple[str, str], dict], bool]:
    import httpx  # noqa: PLC0415
    from .wiki_install import GATEWAY_URL, _read_id_token  # noqa: PLC0415
    token = _read_id_token()
    if not token:
        raise ValueError("还没登录公司账号")
    try:
        with httpx.Client(timeout=20.0) as c:
            r = c.get(f"{GATEWAY_URL}/v1/wiki/documents", headers={"Authorization": f"Bearer {token}"})
    except httpx.RequestError as e:
        raise ValueError(f"连不上中央: {e}") from None
    if r.status_code >= 400:
        raise ValueError(f"中央返回 {r.status_code}")
    return {(d.get("namespace"), d.get("file_id")): d for d in r.json().get("documents") or []}, True


def _wiki_single(namespace: str, file_id: str) -> int:
    """列表里没有的页, 单独问一次: 返状态码 (403/404 = 看不到了 / 没了)。"""
    import httpx  # noqa: PLC0415
    from .wiki_install import _read_id_token  # noqa: PLC0415
    from .wiki_publish import wiki_doc_url  # noqa: PLC0415
    try:
        with httpx.Client(timeout=15.0) as c:
            return c.get(wiki_doc_url(namespace, file_id), headers={"Authorization": f"Bearer {_read_id_token()}"}).status_code
    except httpx.RequestError:
        return 0


def check() -> dict[str, Any]:
    from . import skill_trial  # noqa: PLC0415
    prev = load_state()
    auto = prev.get("auto_disabled") or {}
    prev_skills = {s["path"]: s for s in auto.get("skills") or [] if s.get("path")}
    prev_experts = set(auto.get("experts") or [])

    skills, experts, pages = installed_skills(), installed_experts(), installed_wiki()
    updates: list[dict] = []
    withdrawn: list[dict] = []
    hidden: list[dict] = []
    errors: list[str] = []
    disable_names: list[str] = []
    enable_names: list[str] = []
    apply_experts: list[str] = []
    new_skills: dict[str, dict] = {}
    new_experts: set[str] = set()

    # 技能 + 专家包: 一次问完
    status: dict[str, Any] = {"skills": [], "experts": []}
    if skills or experts:
        try:
            status = skill_trial._hub("POST", "/installed/status", json={
                "skills": [{k: s[k] for k in ("namespace", "name", "version")} for s in skills],
                "experts": [{"id": e["id"], "version": e["version"]} for e in experts]}).json()
        except ValueError as e:
            errors.append(f"技能 / 专家包: {e}")
    s_stat = {(x["namespace"], x["name"], x["version"]): x for x in status.get("skills") or []}
    e_stat = {(x["id"], x["version"]): x for x in status.get("experts") or []}

    for s in skills:
        st = s_stat.get((s["namespace"], s["name"], s["version"]))
        if st is None:
            if s["path"] in prev_skills:
                new_skills[s["path"]] = prev_skills[s["path"]]   # 这次没问到: 维持原状
            continue
        item = {"kind": "skill", "key": f"{s['namespace']}/{s['name']}", "title": s["skill_name"] or s["name"],
                "namespace": s["namespace"], "name": s["name"], "installed_version": s["version"],
                "latest_version": st.get("latest_version"), "status": st["status"],
                "withdrawn_by": st.get("withdrawn_by"), "withdrawn_at": st.get("withdrawn_at"), "note": st.get("note")}
        if st["status"] in WITHDRAWN:
            withdrawn.append(item)
            new_skills[s["path"]] = {**{k: s[k] for k in ("path", "skill_name", "namespace", "name", "version")},
                                     "status": st["status"], "since": prev_skills.get(s["path"], {}).get("since") or _now()}
            if s["path"] not in prev_skills:
                disable_names.append(s["skill_name"])
            continue
        if s["path"] in prev_skills:   # 装上了能用的版本 → 恢复
            enable_names.append(prev_skills[s["path"]].get("skill_name") or s["skill_name"])
        if st["status"] == "hidden":
            hidden.append(item)
        elif _newer(st.get("latest_version"), s["version"]):
            updates.append(item)

    for e in experts:
        st = e_stat.get((e["id"], e["version"]))
        if st is None:
            if e["id"] in prev_experts:
                new_experts.add(e["id"])
            continue
        item = {"kind": "expert", "key": e["id"], "title": e["title"], "id": e["id"], "installed_version": e["version"],
                "latest_version": st.get("latest_version"), "status": st["status"],
                "withdrawn_by": st.get("withdrawn_by"), "withdrawn_at": st.get("withdrawn_at"), "note": st.get("note")}
        if st["status"] in WITHDRAWN:
            withdrawn.append(item)
            if e["enabled"] or e["id"] in prev_experts:
                if e["enabled"]:
                    try:
                        _set_expert_enabled(e["id"], False, {"status": st["status"], "by": st.get("withdrawn_by"),
                                                             "at": st.get("withdrawn_at") or _now()})
                        apply_experts.append(e["id"])
                    except (OSError, ValueError, LookupError) as err:
                        errors.append(f"停用专家 {e['title']}: {err}")
                        continue
                new_experts.add(e["id"])   # 只记我们自己停掉的; 员工原来就关着的不算
            continue
        if e["id"] in prev_experts:
            try:
                _set_expert_enabled(e["id"], True, None)
                apply_experts.append(e["id"])
            except (OSError, ValueError, LookupError) as err:
                errors.append(f"恢复专家 {e['title']}: {err}")
                new_experts.add(e["id"])
        if st["status"] == "hidden":
            hidden.append(item)
        elif _newer(st.get("latest_version"), e["version"]):
            updates.append(item)

    # 知识页: 列表里有的直接比; 没有的单独问一次
    if pages:
        try:
            docs, _ = _wiki_docs()
        except ValueError as e:
            errors.append(f"知识页: {e}")
            docs = None
        if docs is not None:
            from .wiki_install import _mark_local_withdrawn, _shared_root  # noqa: PLC0415
            for m in pages:
                ns, fid = m["namespace"], m["file_id"]
                item = {"kind": "wiki", "key": f"{ns}/{fid}", "title": m.get("title") or fid, "namespace": ns, "file_id": fid,
                        "installed_at": m.get("hub_updated_at") or m.get("installed_at")}
                d = docs.get((ns, fid))
                if d is None:
                    code = _wiki_single(ns, fid)
                    if code in (403, 404):
                        hidden.append({**item, "status": "hidden" if code == 403 else "gone"})
                    continue
                if d.get("stale_after_unpublish") or d.get("review_status") == "withdrawn":
                    withdrawn.append({**item, "status": "withdrawn", "withdrawn_by": d.get("withdrawn_by"),
                                      "withdrawn_at": d.get("withdrawn_at") or d.get("unpublished_at"),
                                      "note": d.get("review_note") or d.get("unpublished_reason")})
                    if m.get("review_status") != "withdrawn":
                        try:
                            _mark_local_withdrawn(_shared_root() / ns, fid, {**d, "namespace": ns})
                        except OSError as err:
                            errors.append(f"标记已撤回 {item['title']}: {err}")
                    continue
                hub_t = str(d.get("updated_at") or d.get("published_at") or "")
                mine_t = str(m.get("hub_updated_at") or m.get("installed_at") or "")
                if hub_t and mine_t and _ts(hub_t) > _ts(mine_t):
                    updates.append({**item, "status": "update", "latest_at": hub_t})

    state = {
        "checked_at": _now(), "ok": not errors, "errors": errors,
        "counts": {"skills": len(skills), "experts": len(experts), "wiki": len(pages)},
        "updates": updates, "withdrawn": withdrawn, "hidden": hidden,
        "auto_disabled": {"skills": list(new_skills.values()), "experts": sorted(new_experts)},
    }
    _save_state(state)
    return {**state, "disable_skill_names": sorted(set(disable_names)), "enable_skill_names": sorted(set(enable_names)),
            "apply_experts": apply_experts}


def _ts(s: str) -> float:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def pim_handlers(params: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
    return {
        "pim/shared_check": check,
        "pim/shared_check_last": lambda: {"ok": True, **load_state()},
    }
