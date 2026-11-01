"""catfish_practice —— 题库与错题复习 (11/02, 借 DeepTutor Practice)。实现在 practice.py / practice_tool.py。"""
from __future__ import annotations

from typing import Any, Dict, List

PRACTICE_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "catfish_practice",
        "description": (
            "题库与错题复习 (员工本机)。出题: 从知识库/资料里出, 每题带答案和解析, add 入库; Excel/CSV 用 import。"
            "考: next 拿题 (review=今天要复习的错题, new=没做过的新题), 一次只问一题, 别先说答案; "
            "员工答完 answer: 客观题这里判对错, 主观题先给参考答案, 问他自评 again/hard/good/easy 再 answer 一次。"
            "答错自动进错题按间隔复习。stats 看数量; master 标已掌握。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["add", "import", "next", "answer", "stats", "master", "delete"]},
                "questions": {
                    "type": "array",
                    "description": "add: 题目",
                    "items": {"type": "object", "properties": {
                        "type": {"type": "string", "enum": ["single_choice", "multi_choice", "true_false",
                                                            "fill_blank", "short_answer"]},
                        "question": {"type": "string"},
                        "options": {"type": "object", "description": "选择题 {A: .., B: ..}"},
                        "answer": {"type": "string", "description": "单选 A; 多选 A,C; 判断 true/false; 其他写参考答案"},
                        "explanation": {"type": "string"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "source": {"type": "string", "description": "出自哪 (知识库条目 / 文件名)"},
                    }, "required": ["question", "answer"]},
                },
                "path": {"type": "string", "description": "import: .xlsx/.csv/.json 路径"},
                "mode": {"type": "string", "enum": ["review", "new"]},
                "n": {"type": "integer"},
                "tag": {"type": "string"},
                "id": {"type": "integer"},
                "user_answer": {"type": "string"},
                "rating": {"type": "string", "enum": ["again", "hard", "good", "easy"]},
                "mastered": {"type": "boolean"},
            },
            "required": ["action"],
        },
    },
]
