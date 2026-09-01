"""微信历史记录只读工具 schema。模型分析沿用当前会话 Picker。"""
from __future__ import annotations

from typing import Any, Dict, List


_TIME_PROPERTY = {
    "type": "string",
    "description": "ISO 8601 时间，必须带日期；单次查询跨度最多 31 天。",
}

WECHAT_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "catfish_wechat_sessions",
        "description": (
            "列出员工已显式授权的微信聊天导出文件中的会话摘要。只读，不上传中央；"
            "返回结果由当前聊天 Picker 模型继续分析。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 200,
                    "default": 50,
                }
            },
            "additionalProperties": False,
        },
        "available": True,
        "x_catfish_runtime": {"platforms": ["darwin", "windows"]},
    },
    {
        "name": "catfish_wechat_history",
        "description": (
            "读取一个聊天会话在明确时间范围内的微信导出文本记录。只在员工授权的当前 Picker"
            "未改变时可用；不返回附件二进制。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "minLength": 1},
                "start_time": _TIME_PROPERTY,
                "end_time": _TIME_PROPERTY,
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 200,
                    "default": 100,
                },
            },
            "required": ["session_id", "start_time", "end_time"],
            "additionalProperties": False,
        },
        "available": True,
        "x_catfish_runtime": {"platforms": ["darwin", "windows"]},
    },
    {
        "name": "catfish_wechat_search",
        "description": (
            "在员工授权的微信聊天导出文件中按关键词和明确时间范围搜索。可限定单个会话；"
            "结果由当前聊天 Picker 模型继续分析。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 200},
                "session_id": {"type": "string"},
                "start_time": _TIME_PROPERTY,
                "end_time": _TIME_PROPERTY,
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 200,
                    "default": 100,
                },
            },
            "required": ["query", "start_time", "end_time"],
            "additionalProperties": False,
        },
        "available": True,
        "x_catfish_runtime": {"platforms": ["darwin", "windows"]},
    },
]
