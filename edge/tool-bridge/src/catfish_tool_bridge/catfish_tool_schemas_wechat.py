"""微信历史记录只读工具 schema。模型分析沿用当前会话 Picker。"""
from __future__ import annotations

from typing import Any, Dict, List


_TIME_PROPERTY = {
    "type": "string",
    "description": "ISO 8601, 必须带日期; 单次跨度 ≤31 天",
}

# 全部只读, 结果不上传中央, 由当前聊天 Picker 模型分析; Picker 变化后授权失效
# (wechat_archive 会返回 picker 相关错误, 请员工在 Companion 重新确认)。
WECHAT_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "catfish_wechat_sessions",
        "description": (
            "列出员工已授权的微信会话 (群 / 私聊) 及首末消息时间, 用于确定 "
            "catfish_wechat_history / catfish_wechat_search 的时间范围。只读。"
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
            "读取一个微信会话在指定时间范围内的文本记录, 不含附件文件; "
            "attachment_present=false 表示该附件未随导出带出。"
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
            "在已授权的微信聊天记录中按关键词 + 时间范围搜索, 可用 session_id 限定单个会话。"
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
