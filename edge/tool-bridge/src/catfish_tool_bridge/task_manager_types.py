"""Task 数据类与几个上限常量 —— 其余几块共同的地基。

BL-TASKMGR-SPLIT 8/15: 从 task_manager.py 抽出来 (1133 行超限)。纯搬迁, 逻辑一行未改。

单独一层是为了让箭头单向: jsonl / notify / recovery 都要 Task, 而 task_manager
末尾要 re-export 它们。不抽出来就是循环 —— metrics.py 那次就这么炸过, 而且是
"换个 import 顺序才炸"。
"""
from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field
from typing import Any

# 任务输出最大字符数 (防 LLM 拿到天量结果)
_MAX_RESULT_CHARS = 100_000

# 老任务自动清理时间 (秒). 完成 24h 后从 store 移除.
_TASK_TTL_SECONDS = 24 * 3600

# P3.5.39 (6/18): latest_output tail buffer 上限 (字符). LLM 调 catfish_task_status
# 时返这一段, 让长 task 看得到中间进度. 4KB 够覆盖 print() 跑 1-2 个 page tail,
# 短于 _MAX_RESULT_CHARS (那是 final result 的). 不能太大 — runner 每行都更新,
# 每次 substring 拷贝过大会拖累.
_LATEST_OUTPUT_TAIL = 4096


@dataclass
class Task:
    """单个 background task 的状态记录."""
    task_id: str
    kind: str
    label: str  # 给员工看的人类可读描述
    status: str = "pending"  # pending / running / completed / failed / interrupted
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    result: Any = None
    error: str | None = None
    # BL-LONG-RUNNING-V1-PHASE-C (6/1): payload 跨重启保留, retry 时拿来重启同样
    # input. 不暴露给 LLM (status_dict 不带), jsonl 持久化 (本机, 跟 task metadata
    # 同密级).
    payload: dict = field(default_factory=dict, repr=False)
    # P3.5.33 (6/18 鸿波 catch '端后不再重试缺评估机制'): retry 评估 + 启动 supervisor.
    # 4 字段串联起评估 gate + 重试链路追溯, 防 quota 炸 / 死循环.
    retry_count: int = 0  # 当前 task 已经是第几次重试 (0 = 初次)
    max_retries: int = 3  # 重试次数上限, 达此值后 auto/manual retry 都拒
    last_error_type: str = "unknown"  # transient / permanent / unknown (_classify_error_type 填)
    parent_task_id: str | None = None  # 上一次失败的 task_id, retry 链路追溯
    # P3.5.39 (6/18 鸿波 audit daytona 后催 'PTY streaming'): 长 task 实时进度.
    # tail buffer (~_LATEST_OUTPUT_TAIL 字符), runner 用 progress_cb 滚动写入.
    # LLM 调 catfish_task_status / catfish_task_result 时看 latest_output 拿中间进度.
    latest_output: str = ""
    # 写 latest_output 的锁 (reader thread 跟 LLM 查询 thread 防 race)
    _output_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    # 内部 asyncio task 引用, 不序列化给 LLM
    _async_task: asyncio.Task | None = field(default=None, repr=False)
