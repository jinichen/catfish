"""学习会话的本机存储 (11/18, docs/LEARNING-PLAN.md §4)。

    ~/.catfish/learning/sessions/<id>/
        session.json   目标 / 模式 / 阶段 / 路线图 / 摸底评估 / 资料 / 当前要答的题 / 上次出错
        events.jsonl   对话记录, 只追加: 导师讲解 / 出题 / 员工回答 / 员工说的话 / 评分 / 路线图 / 评估

规矩 (借 LearnHarness 的运行机制):
  · 先存后算: 员工的回答 / 说的话先追加进 events.jsonl, 再调模型; 模型挂了回答不丢, 可以「重试」。
  · 题目的标准答案在员工答之前就写进记录 (出题那一刻), 答完不许改; 给界面的那份把没答的题的答案剥掉。
  · session.json 原子写 (先写 .tmp 再 replace)。
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any

PHASES = ("probe", "plan_review", "teach", "done", "practice")
MODES = ("quick", "deep")
MAX_GOAL = 500


def root(home: str | Path) -> Path:
    return Path(home) / "learning" / "sessions"


def new_id() -> str:
    return f"lrn_{datetime.now().strftime('%Y%m%d-%H%M%S')}_{secrets.token_hex(2)}"


def session_dir(home: str | Path, sid: str) -> Path:
    if not sid.startswith("lrn_") or "/" in sid or "\\" in sid or ".." in sid:
        raise ValueError("学习编号不对")
    d = root(home) / sid
    if not (d / "session.json").is_file():
        raise ValueError("这个学习不存在 (可能已经删了)")
    return d


def _write_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def create(home: str | Path, goal: str, mode: str) -> dict[str, Any]:
    goal = goal.strip()[:MAX_GOAL]
    if not goal:
        raise ValueError("想学会什么? 写一句目标")
    if mode not in MODES:
        mode = "quick"
    sid = new_id()
    d = root(home) / sid
    d.mkdir(parents=True)
    now = time.time()
    meta = {"id": sid, "goal": goal, "mode": mode, "phase": "probe", "created": now, "updated": now,
            "plan": None, "assessment": None, "pending": None, "last_error": None, "materials": None, "node_materials": {}}
    _write_json(d / "session.json", meta)
    (d / "events.jsonl").write_text("", encoding="utf-8")
    append(d, {"role": "learner", "kind": "say", "text": goal})
    return meta


def load(d: Path) -> dict[str, Any]:
    return json.loads((d / "session.json").read_text(encoding="utf-8"))


def save(d: Path, meta: dict[str, Any]) -> None:
    meta["updated"] = time.time()
    _write_json(d / "session.json", meta)


def events(d: Path) -> list[dict[str, Any]]:
    out = []
    for line in (d / "events.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue   # 写到一半断电的那一行不要了
        if isinstance(ev, dict):
            out.append(ev)
    return out


def append(d: Path, ev: dict[str, Any]) -> dict[str, Any]:
    ev = {"id": ev.get("id") or f"e_{secrets.token_hex(5)}", "at": time.time(), **ev}
    with (d / "events.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    return ev


def list_all(home: str | Path) -> list[dict[str, Any]]:
    r = root(home)
    if not r.is_dir():
        return []
    out = []
    for d in r.iterdir():
        try:
            meta = load(d)
        except (OSError, ValueError):
            continue
        plan = meta.get("plan") or {}
        out.append({"id": meta["id"], "goal": meta["goal"], "mode": meta.get("mode"), "phase": meta.get("phase"),
                    "created": meta.get("created"), "updated": meta.get("updated"),
                    "nodes": len(plan.get("nodes") or []), "visited": len(meta.get("visited") or [])})
    return sorted(out, key=lambda m: -(m.get("updated") or 0))


def delete(home: str | Path, sid: str) -> None:
    shutil.rmtree(session_dir(home, sid))


def public_view(meta: dict[str, Any], evs: list[dict[str, Any]], running: bool) -> dict[str, Any]:
    """给界面的: 没答的题剥掉标准答案和解析; 资料只给出处列表。"""
    pending = (meta.get("pending") or {}).get("qid")
    problem = (meta.get("problem") or {}).get("pid")
    shown = []
    for ev in evs:
        if ev.get("kind") == "question" and ev.get("qid") == pending:
            q = {k: v for k, v in (ev.get("q") or {}).items() if k not in ("answer", "explanation")}
            ev = {**ev, "q": q}
        if ev.get("kind") == "problem":
            from .learning_practice import _ok  # noqa: PLC0415
            if not _ok(ev.get("p")):
                continue   # 残的一行不给界面 (界面按完整的题画)
            if ev.get("pid") == problem:   # 练习题做完 (结束) 前不给看标准解法
                ev = {**ev, "p": {k: v for k, v in ev["p"].items() if k != "solution"}}
        shown.append(ev)
    m = {k: v for k, v in meta.items() if k not in ("materials", "node_materials")}
    m["sources"] = (meta.get("materials") or {}).get("sources") or []
    last = evs[-1] if evs else None
    # 没在跑、没出错、最后一条是员工的 → 上次那一步没接上 (tool-bridge 重启过之类), 界面给「重试」
    from .learning_practice import summary  # noqa: PLC0415
    m["practice"] = summary(evs)
    m["stalled"] = bool(not running and not meta.get("last_error") and last and last.get("role") == "learner"
                        and meta.get("phase") != "done")
    return {"session": m, "events": shown, "running": running}
