"""「整理记忆」的 move 建议: 长的做事口径挪进知识库, 记忆里只留一句指针 (11/06, 见 memory_compact.py)。

hermes 的待批写入 (write_approval) 只管记忆本身 (把那条换成指针)。要写进知识库的那一页 (标题 + 原文)
另存在这里, 按建议 id 一一对应:

    ~/.catfish/memory_compact_moves/<建议 id>.json   {"title": ..., "body": 原文}

员工点同意 (memory_pending.resolve): 先写知识库 (``apply``), 写成了再改记忆; 点放弃 / 重新整理: ``drop``。
知识库里已经有同名页: 把原文追加到那页末尾 (一条规则页可能攒好几条相关的口径), 不另建重复页。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import wiki_files

_ID_RE = re.compile(r"^[0-9a-f]{6,32}$")


def _dir() -> Path:
    d = wiki_files._catfish_home() / "memory_compact_moves"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(pid: str) -> Path | None:
    return _dir() / f"{pid}.json" if _ID_RE.match(pid or "") else None


def save(pid: str, wiki: dict[str, Any]) -> None:
    p = _path(pid)
    if p is not None:
        p.write_text(json.dumps({"title": wiki["title"], "body": wiki["body"]}, ensure_ascii=False), encoding="utf-8")


def load(pid: str) -> dict[str, Any] | None:
    p = _path(pid)
    if p is None or not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return data if isinstance(data, dict) and data.get("title") and data.get("body") else None


def drop(pid: str) -> None:
    p = _path(pid)
    if p is not None:
        p.unlink(missing_ok=True)


def apply(pid: str) -> dict[str, Any]:
    """把这条建议要挪的原文写进知识库。没有挪的 (不是 move 建议) 返回 {"ok": True, "wiki": None}。"""
    move = load(pid)
    if move is None:
        return {"ok": True, "wiki": None}
    title, body = str(move["title"]), str(move["body"])
    created = wiki_files.create_wiki_entry("concept", title, body, subtype="rule", tags=["做事口径", "记忆整理"])
    if created.get("ok"):
        return {"ok": True, "wiki": created.get("rel_path") or title, "created": created.get("rel_path")}
    rel = created.get("rel_path")
    if not rel:
        return {"ok": False, "error": f"写知识库「{title}」失败: {created.get('error')}"}
    # 11/08: 名字撞上的只能是「做事口径」类的概念页才追加 —— 「福富」是公司页的别名, 口径原文不能糊进公司页
    if not rel.startswith("wiki/concepts/"):
        return {"ok": False, "error": f"「{title}」已经是 {rel} 这一页的名字 (不是做事口径), 不能把口径追加进去: "
                                      "把这条建议的标题改一下再同意, 或者放弃这条"}
    # 同名概念页已有: 原文追加到末尾 (已经包含这段就不重复追加)
    try:
        current = (wiki_files._catfish_home() / rel).read_text(encoding="utf-8")
    except OSError as e:
        return {"ok": False, "error": f"读知识库「{title}」失败: {e}"}
    if body.strip() in current:
        return {"ok": True, "wiki": rel}
    updated = wiki_files.update_wiki_file(rel, current.rstrip() + "\n\n" + body.strip() + "\n")
    if not updated.get("ok", True):
        return {"ok": False, "error": f"补进知识库「{title}」失败: {updated.get('error')}"}
    return {"ok": True, "wiki": rel, "appended": rel, "before": current}


def undo(moved: dict[str, Any]) -> None:
    """记忆那一步没改成: 刚建的页删掉 / 刚追加的还原, 不留孤儿页 (11/08)。"""
    home = wiki_files._catfish_home()
    try:
        if moved.get("created"):
            (home / str(moved["created"])).unlink(missing_ok=True)
        elif moved.get("appended") and moved.get("before") is not None:
            (home / str(moved["appended"])).write_text(str(moved["before"]), encoding="utf-8")
    except OSError:
        pass
