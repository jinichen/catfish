"""catfish-memory 插件的基础层 —— logger / 预算表 / 常量 / 安全读文件。

8/15 从 catfish_memory_helpers.py 抽出来 (949 行, 过了 CLAUDE.md §1 的 800 红线)。

# 为什么必须先有这一层

catfish_memory_helpers.py 是一个**刻意造出来的循环枢纽**: 5 个兄弟模块
(prompts / fm / wiki / llm / merge) 都 `from catfish_memory_helpers import logger`,
而 helpers 又在**文件末尾**回指它们的符号。能跑靠的是顺序 —— helpers 执行到
末尾那个 re-export 块时, logger 早就定义好了。

这个结构本身脆, 但它在跑, 而且有 tests/test_loader_fidelity.py 钉着。
8/15 这次要再抽四个模块出去, 如果新模块也去 helpers 拿 logger, 就是**往环里
再塞四条边**。

所以先沉一个真正的底: 本文件只依赖标准库, 谁都不 import。新抽的四个模块
从这里拿 logger 和常量。helpers 照旧 re-export 一切, 老调用方一个字不用改。

原来那 5 个兄弟仍然从 helpers 拿 logger —— 那条老环没动, 但也没变糟。
真要拆那个环, 是把它们也改成从本文件拿, 那是另一件事的排期。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("catfish.memory")

#: 默认 catfish 数据目录 (员工本机). env CATFISH_HOME 覆盖.
#: 5/28 鸿波: helper 之前 reference 这个 constant 但**没定义**, 导致 is_available()
#: NameError, 整个 plugin 永远报 unavailable → hermes 报 "no provider instance found".
#: 这是 plugin 5/19 装好但 9 天一直没真注册的根因之二 (第一个根因是 __init__.py
#: import 链失败, 第二个就是这里).
_DEFAULT_CATFISH_HOME: Path = Path.home() / ".catfish"


def _catfish_home() -> Path:
    """`~/.catfish/` 或 env CATFISH_HOME 指定的目录."""
    env = os.environ.get("CATFISH_HOME")
    return Path(env).expanduser() if env else _DEFAULT_CATFISH_HOME


#: 每个数据源单独 budget (字节), 跟老 gateway provider 对齐.
#: 超出部分尾部截 (留前段更重要内容). 总 cap ~30KB, system prompt 容得下.
#: BL-STRATEGIC-DOC-SYNC (6/7): strategic_docs 子段 budget — 战略/设计 doc
#:   (manifesto / patent / moat 类). query 空走 8KB (cap 5 份 × ~1.5KB), query
#:   触发 top-K 时 _render_strategic_docs 内部 cap 到 5KB.
_BUDGETS: Dict[str, int] = {
    "employee_journal": 5000,
    # P3.5.5 (6/16 鸿波): skills_catalog 20K → 5K. 真因: 鸿波 advisor 流程 Qwen 内网
    #   prompt 44K 跑 100-200s. 真大头是 catfish-memory plugin prefetch 38.5KB 全量注入,
    #   单 skills_catalog 占 20K. 实测 chat 用 5K 够 (top-K 5 个 skill, 每 skill ~1K),
    #   员工 chat 时常用 skill 就那几个, 全列没必要. 砍 15K, advisor 提速 60%+.
    "skills_catalog": 5000,
    # P3.5.5 (6/16 鸿波): strategic_docs 8K → 3K. 战略 doc 是 manifesto / moat 类,
    #   员工 chat 时偶尔参考, 不需要全注入. 3K 够留 top-K 2 段.
    "strategic_docs": 3000,
    "feedback": 2000,
    "session_meta": 500,
    "skill_guard": 3000,
}


def _read_text_safe(path: Path, max_bytes: int) -> str:
    """读文件返字符串, 不存在 / IO 错 → 空字符串. 超 max_bytes 尾部截.

    永不抛, 让 prefetch 整体不挂.
    """
    try:
        if not path.exists() or not path.is_file():
            return ""
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text.encode("utf-8")) <= max_bytes:
            return text
        # 字节 cap — 简单按字符 truncate (UTF-8 可能切半字符, 凑合; 真要严谨
        # 用 incremental decoder, POC 不必).
        return text[: max_bytes // 3] + "\n...[truncated]"
    except OSError as e:
        logger.debug("catfish-memory: 读 %s 失败 %s, 跳过", path, e)
        return ""


def _read_jsonl_tail(path: Path, max_lines: int = 20, max_bytes: int = 2000) -> List[Dict[str, Any]]:
    """读 jsonl 最后 max_lines 条, 不存在 / 解析失败 → 空 list."""
    try:
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        recent = lines[-max_lines:]
        out: List[Dict[str, Any]] = []
        budget = max_bytes
        for raw in recent:
            try:
                obj = json.loads(raw)
                if isinstance(obj, dict):
                    out.append(obj)
                    budget -= len(raw)
                    if budget <= 0:
                        break
            except json.JSONDecodeError:
                continue
        return out
    except OSError as e:
        logger.debug("catfish-memory: 读 jsonl %s 失败 %s", path, e)
        return []


# ── 写路径 helpers (Week 2 — 替代 gateway session_summarizer + memory_distill) ──
#
# 这些 helper 都是**纯函数 + 显式参数**, 方便单测 mock 注入. 不读 module-level
# 全局状态 (除 env), 不依赖 CatfishMemoryProvider 实例.

#: gateway 旧 caller 跟我们这条 plugin 路径**双写**期间, plugin 写之前看 journal
#: mtime, < 这个秒数视为 gateway 刚写过, plugin skip (Step B-Step C 过渡期保护).
#: Step C env gate 关 gateway 后这层保护自然失效 (因为只有 plugin 自己在写).
_SUMMARIZE_DEDUP_SECONDS = 300  # 5 分钟

#: 蒸馏 24h cooldown (跟 gateway memory_distill 原 24h 一致, 防同次 chat 反复触发).
_DISTILL_COOLDOWN_SECONDS = 24 * 3600

#: sync_turn 节流默认: 每 N 轮触发一次 summary. env CATFISH_PLUGIN_SUMMARIZE_EVERY_N_TURNS 覆盖.
#: 跟 5/20 Step D 失败原因相关 — on_session_end 不在 per-chat trigger (run_agent.py:16078
#: 注释 "Memory provider on_session_end NOT called per turn"), 必须用 sync_turn + 节流.
_DEFAULT_TURNS_BETWEEN_SUMMARY = 5

#: sync_turn 节流默认: 距上次 summary 最小间隔 (秒). 跟 N 轮规则取 "或" — 任一满足都触发.
#: env CATFISH_PLUGIN_SUMMARIZE_MIN_INTERVAL_SECONDS 覆盖.
#: 30min 是经验值: 员工连续 chat 30min 算一段思路完成, 该总结了.
_DEFAULT_MIN_SUMMARY_INTERVAL_SECONDS = 1800

#: gateway loopback URL (env 覆盖, 默认 8999).
_DEFAULT_GATEWAY_URL = "http://127.0.0.1:8999/v1/chat/completions"

#: HTTP 超时 (LLM 总结+蒸馏不应该超 60s; 真超就 cooldown 等下次).
_LLM_HTTP_TIMEOUT = 60.0

#: P1.1.1 wiki Step 2 Generation 单独超时 — 生 ~4000 tokens 长 response,
#: 60s 不够 (6/4 12:55 实测 ReadTimeout). 180s 给 LLM 慢慢生.
_GENERATION_HTTP_TIMEOUT = 180.0

#: 每个 session 取最多 N 条消息进 prompt (防长 session 撑爆 LLM context).
_MAX_MESSAGES_PER_SUMMARY = 60

#: 蒸馏 chunk 大小 (字符), 跟 gateway memory_distill 原 DISTILL_CHUNK_CHARS 对齐.
_DISTILL_CHUNK_CHARS = 8000
