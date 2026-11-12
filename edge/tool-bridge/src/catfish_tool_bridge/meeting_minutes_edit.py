"""会议纪要: 改 / 导出 Word / 准备发到协同群 (11/12 鸿波「是不是应该有个分享或者邮件; 内容是不是还应该可以修正」)。

纪要是模型按录音整理的, 人名听错、结论记反都有; 原来只能整份重新生成。现在:
  · meeting/minutes_save   员工改过的纪要存回去。标准格式: 摘要 / 决议 / 待办 / 待定问题 / 上次遗留 逐项改,
                           minutes.md 按改后的内容重新排 (末尾写「已人工核对修改」); 按文字模版写的那份直接改全文。
                           minutes.json 记 edited_at —— 界面上「重新生成」会先提醒会覆盖。
  · meeting/minutes_docx   存成 Word (会议目录里「<标题> 会议纪要.docx」, 每次覆盖); 用 Word / Excel 模版
                           生成过的, 直接用那份填好的文件。邮件附件、「导出 Word」都用它。
  · meeting/minutes_stage  发到协同群: 把上面那个文件放进群聊的暂存区, 返回 stage_id, 界面再走群聊原来的「发送」
                           (群消息照旧只在员工点了之后才发, docs/CENTRAL-EDGE-DATA-BOUNDARY.md 第 4 条)。
全部只动本机文件, 不调大模型。
"""
from __future__ import annotations

import json
import re
import secrets
import shutil
import time
from pathlib import Path
from typing import Any

from .meeting_minutes import load_inputs, meeting_date, render_markdown, resolve_meeting_dir

MAX_TEXT = 4000
MAX_ITEMS = 50
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_BAD_NAME = re.compile(r'[\\/:*?"<>|\s]+')


def _load_doc(d: Path) -> dict[str, Any]:
    p = d / "minutes.json"
    if not p.exists():
        raise ValueError("还没生成纪要")
    return json.loads(p.read_text(encoding="utf-8"))


def _strs(v: Any) -> list[str]:
    if not isinstance(v, list):
        return []
    return [str(x).strip()[:MAX_TEXT] for x in v if str(x).strip()][:MAX_ITEMS]


def _items(v: Any) -> list[dict[str, str]]:
    out = []
    for it in v if isinstance(v, list) else []:
        if not isinstance(it, dict) or not str(it.get("task") or "").strip():
            continue
        due = str(it.get("due") or "").strip()
        out.append({
            "owner": str(it.get("owner") or "").strip()[:40],
            "task": str(it["task"]).strip()[:MAX_TEXT],
            "due": due if _DATE_RE.match(due) else "",
        })
    return out[:MAX_ITEMS]


def _refs_note(doc: dict[str, Any]) -> str:
    from .meeting_context import references_note  # noqa: PLC0415
    refs = doc.get("references") or {}
    return references_note({"wiki": refs.get("wiki") or [], "previous": refs.get("previous_meeting")})


def handle_save(params: dict) -> dict:
    d = resolve_meeting_dir(params)
    doc = _load_doc(d)
    fields = params.get("fields")
    markdown = params.get("markdown")
    if fields is None and markdown is None:
        raise ValueError("没有要保存的内容")
    if isinstance(fields, dict):
        if "summary" in fields:
            doc["summary"] = str(fields.get("summary") or "").strip()[:MAX_TEXT * 2]
        for key in ("decisions", "open_questions", "carried_over"):
            if key in fields:
                doc[key] = _strs(fields.get(key))
        if "action_items" in fields:
            doc["action_items"] = _items(fields.get("action_items"))
    doc["edited_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    if isinstance(markdown, str):
        # 按文字模版写的那份: 员工直接改全文
        md = markdown.strip()
        if not md:
            raise ValueError("纪要不能是空的")
        md += "\n"
    elif doc.get("template") and not doc.get("output_file"):
        md = (d / "minutes.md").read_text(encoding="utf-8")   # 模版版只改了待办, 正文不动
    else:
        meta, _, names = load_inputs(d)
        md = render_markdown(meta, doc, names, _refs_note(doc), edited=True)
    (d / "minutes.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    (d / "minutes.md").write_text(md, encoding="utf-8")
    return {"ok": True, "edited_at": doc["edited_at"]}


def _docx_path(d: Path) -> Path:
    """有 Word / Excel 模版填好的文件就用它; 否则按 minutes.md 排一份 Word。"""
    doc = _load_doc(d)
    out = doc.get("output_file")
    if out and Path(out).is_file() and Path(out).resolve().parent == d.resolve():
        return Path(out)
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    title = str(meta.get("title") or "会议").strip() or "会议"
    md = (d / "minutes.md").read_text(encoding="utf-8")
    # 第一行「# 标题」由 Word 的大标题代替, 不重复
    body = re.sub(r"\A#\s+[^\n]*\n+", "", md)
    from .docx_export import render  # noqa: PLC0415
    document = render(f"{title} 会议纪要", body)
    safe = _BAD_NAME.sub(" ", title).strip()[:40] or "会议"
    p = d / f"{safe} 会议纪要 {meeting_date(meta)}.docx".replace("  ", " ")
    document.save(str(p))
    return p


def handle_docx(params: dict) -> dict:
    d = resolve_meeting_dir(params)
    try:
        p = _docx_path(d)
    except ImportError:
        raise RuntimeError("这台电脑上缺 Word 组件 (python-docx), 重装一次 Companion 再试") from None
    return {"path": str(p), "name": p.name, "size": p.stat().st_size}


def handle_stage(params: dict) -> dict:
    """纪要文件放进群聊暂存区 → {stage_id, name}; 界面拿去走群聊的「发送」(带 files)。"""
    from .colleague_rooms_bigfiles import MAX_FILE, _dir  # noqa: PLC0415
    d = resolve_meeting_dir(params)
    p = _docx_path(d)
    if p.stat().st_size > MAX_FILE:
        raise ValueError("纪要文件太大, 发不到群里")
    sid = f"s_{secrets.token_hex(8)}"
    shutil.copyfile(p, _dir("staging") / sid)
    return {"stage_id": sid, "name": p.name}
