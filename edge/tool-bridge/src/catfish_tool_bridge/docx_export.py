"""工作流积木「导出 Word 文件」(11/08): 把一段 Markdown 存成 .docx, 放进「我的产出」(~/.catfish/outputs/<今天>/)。

之前要 Word 只能让小鲶那步「用 execute_code 调 python-docx」—— 小鲶那步不给工具, 根本出不了文件。
这块积木把常见的 Markdown 写法排成 Word 的样子: # 标题、- / 1. 列表、| 表格 |、**加粗**、> 引用、--- 分隔线;
别的原样当正文。同名文件不覆盖, 加 (2)、(3)。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .advisor_io import today_outputs_dir

_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')
_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_TABLE_SEP = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$")


def _filename(name: str, title: str) -> str:
    base = _BAD.sub("_", (name or title or "文档").strip()).strip(" ._") or "文档"
    base = re.sub(r"\.docx$", "", base, flags=re.I)[:80]
    return base + ".docx"


def _unique(d: Path, fname: str) -> Path:
    p = d / fname
    k = 2
    while p.exists():
        p = d / f"{fname[:-5]} ({k}).docx"
        k += 1
    return p


def _runs(par: Any, text: str) -> None:
    """一行里的 **加粗** 拆成几段 run, 其余原样。"""
    pos = 0
    for m in _BOLD.finditer(text):
        if m.start() > pos:
            par.add_run(text[pos:m.start()])
        par.add_run(m.group(1) or m.group(2)).bold = True
        pos = m.end()
    if pos < len(text):
        par.add_run(text[pos:])


def _cells(line: str) -> list[str]:
    s = line.strip()
    s = s[1:] if s.startswith("|") else s
    s = s[:-1] if s.endswith("|") else s
    return [c.strip() for c in s.split("|")]


def _table(doc: Any, rows: list[list[str]]) -> None:
    width = max(len(r) for r in rows)
    t = doc.add_table(rows=len(rows), cols=width)
    t.style = "Table Grid"
    for i, r in enumerate(rows):
        for j in range(width):
            cell = t.cell(i, j)
            cell.text = ""
            _runs(cell.paragraphs[0], r[j] if j < len(r) else "")
            if i == 0:
                for run in cell.paragraphs[0].runs:
                    run.bold = True


def render(title: str, body: str) -> Any:
    from docx import Document  # noqa: PLC0415 —— hermes venv 里有 (pyproject execute 段)

    doc = Document()
    if title.strip():
        doc.add_heading(title.strip(), level=0)
    lines = str(body or "").replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        s = line.strip()
        if s.startswith("|") and i + 1 < len(lines) and _TABLE_SEP.match(lines[i + 1].strip()):
            rows = [_cells(s)]
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(_cells(lines[i]))
                i += 1
            _table(doc, rows)
            continue
        i += 1
        if not s:
            continue
        if s.startswith("```"):
            continue   # 代码块的围栏不要, 里面的行当正文
        if m := re.match(r"^(#{1,6})\s+(.*)$", s):
            if m.group(2).strip() != title.strip():   # 正文第一行常常重复标题
                doc.add_heading(m.group(2).strip(), level=min(len(m.group(1)), 4))
        elif re.match(r"^(-{3,}|\*{3,}|_{3,})$", s):
            doc.add_paragraph("")
        elif m := re.match(r"^[-*+]\s+(.*)$", s):
            _runs(doc.add_paragraph(style="List Bullet"), m.group(1))
        elif m := re.match(r"^\d+[.)、]\s*(.*)$", s):
            _runs(doc.add_paragraph(style="List Number"), m.group(1))
        elif s.startswith(">"):
            _runs(doc.add_paragraph(style="Quote"), s.lstrip("> ").strip())
        else:
            _runs(doc.add_paragraph(), s)
    return doc


def export(args: dict[str, Any]) -> dict[str, Any]:
    title = str(args.get("title") or "").strip()
    body = args.get("body")
    if isinstance(body, (list, dict)):
        import json  # noqa: PLC0415
        body = json.dumps(body, ensure_ascii=False, indent=2)
    body = str(body or "").strip()
    if not body:
        return {"ok": False, "error": "要导出的内容是空的 (前面那步没交出正文)"}
    try:
        doc = render(title, body)
    except ImportError:
        return {"ok": False, "error": "这台电脑上缺 Word 组件 (python-docx), 重装一次鲶鱼"}
    path = _unique(today_outputs_dir(), _filename(str(args.get("filename") or ""), title))
    doc.save(str(path))
    return {"ok": True, "path": str(path), "files": [str(path)], "summary": f"已存成 Word: {path.name} (在「我的产出」里)"}
