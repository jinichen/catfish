"""对话里跑通的代码 → 技能程序 (11/19)。

鸿波: 「在工作台里把 Excel / Word 拖进对话, 告诉小鲶做什么, 验证可以了固化成技能」。
11/19 真走一遍 (应收账龄): 对话里小鲶写代码算账、出 Excel, 数全对; 说「固化成技能」后两条路都断:
  - hermes 自带 skill_manage: 模型把 1.5 万字的程序**重打一遍**, 一轮 2–5 分钟, 最后参数名写错没存上,
    技能里的程序是个空壳
  - catfish_propose_skill → install_proposal: 程序按模板生成, 只有步骤注释, 一跑就报「未实现」
都不是「刚才跑通、员工核对过的那段代码」。

这里做的: catfish_propose_skill 带着当前会话号 (hermes 插件的 pre_tool_call 钩子注入 `_session_id`)
来时, 从 ~/.hermes/state.db 找这个会话里**最后一次成功、并且存了文件**的 execute_code, 原样存进技能;
每次要换的东西 (输入文件的绝对路径、截至日期) 识别出来变成参数。技能运行 = 换参数 + 在子进程里
跑同一段代码, 不经过模型, 同样的输入出同样的结果。

只认纯 Python 的代码: 里面调了 hermes_tools (代码里再调小鲶的工具) 的, 子进程里跑不了, 不固化,
如实告诉员工。代码先过 execute_code 同一套安全检查, 过不了不固化。
"""
from __future__ import annotations

import ast
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

_SAVE_MARKERS = (".save(", "to_excel(", "to_csv(", "savefig(", ".write_text(", ".write_bytes(")
_ISO = re.compile(r"(?<!\d)(20\d\d)-(\d\d)-(\d\d)(?!\d)")
RUN_TIMEOUT = 300


def _state_db() -> Path:
    from .hermes_paths import hermes_home  # noqa: PLC0415 — Windows 上在 %LOCALAPPDATA%\hermes
    return hermes_home() / "state.db"


def session_runs(session_id: str) -> list[dict[str, Any]]:
    """这个会话里所有 execute_code 调用 (代码 + 是否成功 + 输出前 2000 字), 按时间先后。"""
    db_path = _state_db()
    if not session_id or not db_path.is_file():
        return []
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    try:
        rows = db.execute("SELECT role, content, tool_calls, tool_call_id FROM messages WHERE session_id=? ORDER BY rowid",
                          (session_id,)).fetchall()
    finally:
        db.close()
    calls: dict[str, str] = {}
    runs = []
    for role, content, tool_calls, tool_call_id in rows:
        if tool_calls:
            try:
                for t in json.loads(tool_calls):
                    fn = t.get("function") or {}
                    if fn.get("name") == "execute_code":
                        calls[t.get("id")] = (json.loads(fn.get("arguments") or "{}") or {}).get("code", "")
            except (ValueError, TypeError):
                continue
        elif role == "tool" and tool_call_id in calls:
            try:
                res = json.loads(content or "{}")
            except ValueError:
                res = {}
            ok = res.get("status") == "success" and not res.get("error")
            runs.append({"code": calls[tool_call_id], "ok": ok, "output": str(res.get("output") or "")[:2000]})
    return runs


def pick_final(runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """最后一次成功、并且存了文件的那次 (出成品的那段); 没有存文件的就不算能固化。"""
    for r in reversed(runs):
        if r["ok"] and any(m in r["code"] for m in _SAVE_MARKERS):
            return r
    return None


def _string_literals(code: str) -> list[str]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    return [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def parameterize(code: str, outputs_root: Path | None = None) -> dict[str, Any]:
    """找出每次要换的东西: 输入文件 (代码里写死的、存在的绝对路径, 不在产出目录下) 和截至日期。

    返回 {"subs": [{param, kind, original}], "default_date": "YYYY-MM-DD" | None}。
    运行时按 original 原文替换 —— 路径、ISO 日期字符串、date(2026, 11, 19) 这种写法都换。
    """
    outputs_root = (outputs_root or Path.home() / ".catfish" / "outputs").resolve()
    subs: list[dict[str, str]] = []
    seen: set[str] = set()
    for s in _string_literals(code):
        p = Path(s)
        if s in seen or not p.is_absolute() or not p.is_file():
            continue
        try:
            p.resolve().relative_to(outputs_root)
            continue  # 产出目录里的是上次的结果, 不是输入
        except ValueError:
            pass
        seen.add(s)
        subs.append({"param": f"input_{len(subs) + 1}", "kind": "file", "original": s})
    # 产出目录 (…/outputs/<日期>) 每次都换成运行当天的, 不跟着截至日期走
    for s in _string_literals(code):
        try:
            rel = Path(s).resolve().relative_to(outputs_root)
        except (ValueError, OSError):
            continue
        if rel.parts and _ISO.fullmatch(rel.parts[0]):
            day_dir = str(outputs_root / rel.parts[0])
            if day_dir not in seen and s.startswith(day_dir):
                seen.add(day_dir)
                subs.insert(0, {"param": "_outdir", "kind": "outdir", "original": day_dir})
    dates = [m.group(0) for m in _ISO.finditer(code)]
    default_date = max(set(dates), key=dates.count) if dates else None
    if default_date:
        subs.append({"param": "asof", "kind": "date", "original": default_date})
    return {"subs": subs, "default_date": default_date}


def _date_forms(iso: str) -> list[str]:
    y, m, d = (int(x) for x in iso.split("-"))
    return [iso, f"{y},{m},{d}", f"{y}, {m}, {d}", f"{y},{m:02d},{d:02d}", f"{y}, {m:02d}, {d:02d}",
            f"{y}年{m}月{d}日", f"{y}年{m:02d}月{d:02d}日", f"{y}{m:02d}{d:02d}"]


def fill(template: str, subs: list[dict[str, str]], params: dict[str, Any]) -> str:
    """按参数替换原文。日期把常见写法 (2026-11-19 / date(2026,11,19) / 2026年11月19日 / 20261119) 一起换。"""
    code = template
    for s in subs:
        val = params.get(s["param"])
        if s["kind"] == "outdir":
            code = code.replace(s["original"], "\x00OUTDIR\x00")
            continue
        if s["kind"] == "date":
            new = str(val or date.today().isoformat())[:10]
            date.fromisoformat(new)  # 不合法直接抛
            for old, rep in zip(_date_forms(s["original"]), _date_forms(new)):
                code = code.replace(old, rep)
        elif val:
            p = Path(str(val)).expanduser()
            if not p.is_file():
                raise FileNotFoundError(f"参数 {s['param']} 的文件不存在: {p}")
            code = code.replace(s["original"], str(p))
    if "\x00OUTDIR\x00" in code:  # 最后换回来, 免得运行当天的日期被当成截至日期又换一遍
        from . import advisor_io  # noqa: PLC0415
        code = code.replace("\x00OUTDIR\x00", str(advisor_io.today_outputs_dir()))
    return code


def check_code(code: str) -> str | None:
    """能不能固化 / 运行: 返回不能的原因, 能就 None。"""
    if "hermes_tools" in code:
        return "这段代码在里面又调了小鲶的工具 (hermes_tools), 单独跑不了, 没法固化成程序"
    try:
        ast.parse(code)
    except SyntaxError as e:
        return f"代码解析不了 ({e.msg})"
    from .adapter_security import _check_execute_code_security  # noqa: PLC0415
    msg = _check_execute_code_security("execute_code", {"code": code})
    return f"没过安全检查: {msg}" if msg else None


def from_session(session_id: str) -> dict[str, Any]:
    """会话 → 可固化的代码 + 参数; 不行就带原因。"""
    runs = session_runs(session_id)
    final = pick_final(runs)
    if final is None:
        return {"ok": False, "reason": ("这次对话里没有找到成功运行、并且存了文件的代码 (先在对话里把活做通、出了文件, 再固化)"
                                        if runs else "这次对话里没有运行过代码")}
    why = check_code(final["code"])
    if why:
        return {"ok": False, "reason": why}
    p = parameterize(final["code"])
    return {"ok": True, "code": final["code"], "subs": p["subs"], "default_date": p["default_date"],
            "runs_seen": len(runs), "lines": final["code"].count("\n") + 1}


def run_frozen(template: str, subs: list[dict[str, str]], params: dict[str, Any], *, timeout: int = RUN_TIMEOUT) -> dict[str, Any]:
    """技能运行: 换参数 → 安全检查 → 子进程跑 → 收产出文件。"""
    if params.get("_help"):
        return {"ok": True, "params": [{"name": s["param"], "kind": s["kind"], "example": s["original"]}
                                       for s in subs if not s["param"].startswith("_")]}
    try:
        code = fill(template, subs, params)
    except (FileNotFoundError, ValueError) as e:
        return {"ok": False, "error": str(e), "summary": "参数不对, 没有运行"}
    why = check_code(code)
    if why:
        return {"ok": False, "error": why, "summary": "没有运行"}
    from . import advisor_io  # noqa: PLC0415
    out_dir = advisor_io.today_outputs_dir()
    started = time.time()
    try:
        proc = subprocess.run([sys.executable, "-c", code], cwd=str(out_dir), capture_output=True, text=True,
                              timeout=timeout, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"运行超过 {timeout} 秒, 已停止", "summary": "超时"}
    root = out_dir.parent
    files = sorted(str(p) for p in root.rglob("*") if p.is_file() and p.stat().st_mtime >= started - 1)
    printed = [m for m in re.findall(r"(/[^\s'\"]+\.(?:xlsx|xls|docx|csv|pdf|pptx|png))", proc.stdout) if Path(m).is_file()]
    files = sorted(set(files) | set(printed))
    if proc.returncode != 0:
        return {"ok": False, "error": (proc.stderr or "")[-1500:], "summary": "程序出错了", "files": files}
    return {"ok": True, "summary": (proc.stdout or "")[-3000:], "files": files}


def render_script(fn_name: str, skill_name: str, code: str, subs: list[dict[str, str]]) -> str:
    """技能的 script.py: 存原样代码 + 参数表, 运行交给 run_frozen (以后修运行逻辑, 老技能一起受益)。"""
    return "\n".join([
        f'"""{skill_name}: 对话里跑通的代码固化而成 (skill_from_code.py)。',
        "",
        "TEMPLATE 是员工当时核对过的那段代码, 原样保存; SUBS 是每次要换的东西 (输入文件 / 截至日期)。",
        "运行 = 按参数替换 → 安全检查 → 子进程跑同一段代码。要改算法: 在对话里改好跑通, 再固化一次。",
        '"""',
        "",
        f"SUBS = {json.dumps(subs, ensure_ascii=False, indent=1)}",
        "",
        f"TEMPLATE = {json.dumps(code, ensure_ascii=False)}",
        "",
        "",
        f"def {fn_name}(params):",
        "    from catfish_tool_bridge.skill_from_code import run_frozen",
        "    return run_frozen(TEMPLATE, SUBS, params or {})",
        "",
    ])


def params_doc(subs: list[dict[str, str]]) -> str:
    """SKILL.md 里的参数说明。"""
    lines = ["## 参数 (catfish_run_skill 的 params)", ""]
    for s in subs:
        if s["param"].startswith("_"):
            continue
        what = "要处理的文件 (绝对路径)" if s["kind"] == "file" else "截至日期 YYYY-MM-DD (不给 = 今天)"
        lines.append(f"- `{s['param']}`: {what}。固化时用的是 `{s['original']}`")
    lines += ["", "运行就是换上这些参数, 跑固化时那段核对过的代码, 不需要再写代码。结果文件在返回的 files 里。",
              "口径要改: 先在对话里改好、跑通、核对, 再说「重新固化」。"]
    return "\n".join(lines)
