"""catfish_practice 工具入口 —— 一个工具多个 action (只占工具名额一格), 逻辑在 practice.py。"""
from __future__ import annotations

from typing import Any

from . import practice


def dispatch(name: str, args: dict[str, Any]) -> dict[str, Any]:
    action = str(args.get("action") or "").strip()
    try:
        con = practice.connect()
        try:
            if action == "add":
                items = args.get("questions") or []
                if not isinstance(items, list) or not items:
                    return {"error": "questions 要是题目数组"}
                return practice.add_questions(con, items)
            if action == "import":
                return practice.add_questions(con, practice.read_table(str(args.get("path") or "")))
            if action == "next":
                qs = practice.next_questions(con, args.get("n") or 5, str(args.get("mode") or "review"),
                                             str(args.get("tag") or ""))
                return {"questions": qs, "stats": practice.stats(con)}
            if action == "answer":
                return practice.answer(con, int(args.get("id")), args.get("user_answer"),
                                       str(args.get("rating") or ""))
            if action == "stats":
                return practice.stats(con)
            if action == "master":
                return practice.set_mastered(con, int(args.get("id")), bool(args.get("mastered", True)))
            if action == "delete":
                return practice.delete(con, int(args.get("id")))
            return {"error": f"不认识的 action: {action}"}
        finally:
            con.close()
    except (ValueError, TypeError) as e:
        return {"error": str(e)}
