"""工具说明的体积闸 (10/22)。

catfish 工具的 name/description/input_schema 每次聊天请求都整份发给模型。10/22 量过: 84 个工具
约 3.7 万 tokens (说明文字 5.2 万字, 大半是踩坑史 / BL 编号 / 人名 / 实现细节), 加上系统提示和对话,
每轮首个回复要等 15–30 秒 (模型先把 7 万多 tokens 读一遍)。精简后约 1.9 万 tokens,
用 64 条真实请求对拍过选工具的准确率没有变差。

这里钉住: 名字不重复 (原来 catfish_expert 被拼了两遍)、总量不回涨。踩坑史写进 Python 注释, 别写进 description。
"""
from __future__ import annotations

import json

from catfish_tool_bridge.catfish_tool_schemas import CATFISH_NATIVE_TOOLS

#: 10/22 精简后约 6 万字 (含 input_schema)。留一点余量给新工具; 要突破先想想能不能写短。
TOTAL_CHAR_BUDGET = 66_000
#: 单个工具说明上限 (字符, 含 ASCII)。最复杂的几个 (workflow / customer / user_profile_propose) 在 470 以内。
PER_TOOL_DESCRIPTION_LIMIT = 520


def test_tool_names_unique():
    names = [t["name"] for t in CATFISH_NATIVE_TOOLS]
    dup = sorted({n for n in names if names.count(n) > 1})
    assert not dup, f"工具重复注册, 每次请求白发一遍: {dup}"


def test_total_schema_size_stays_small():
    size = len(json.dumps(CATFISH_NATIVE_TOOLS, ensure_ascii=False))
    assert size <= TOTAL_CHAR_BUDGET, (
        f"catfish 工具定义 {size} 字, 超过 {TOTAL_CHAR_BUDGET} —— 每次请求都要发, 直接拖慢首个回复")


def test_each_description_is_short():
    long = [(t["name"], len(t["description"])) for t in CATFISH_NATIVE_TOOLS
            if len(t.get("description") or "") > PER_TOOL_DESCRIPTION_LIMIT]
    assert not long, f"这些工具说明太长 (历史 / 细节放 Python 注释): {long}"
