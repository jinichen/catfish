"""蒸馏节流 —— 24h cooldown 和"上次跑到哪了"。

8/15 从 catfish_memory_helpers.py 搬出来。

# 为什么要 cooldown

蒸馏是把散落的 journal / wiki 材料压成长期记忆, 一次要跑好几轮 LLM。没有
24h 闸的话, 同一次会话里连着触发几次, 既烧 token 又会把同样的内容反复写进
记忆。这个数跟老 gateway 的 memory_distill 保持一致 —— 两边不一致的话,
迁移期间会出现"同一件事在新旧两套里各记一遍"。
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from .catfish_memory_base import _DISTILL_COOLDOWN_SECONDS, logger
    from .catfish_memory_distill_reconcile import head_needs_reconcile
except ImportError:  # 独立脚本模式 (无父包)
    from catfish_memory_base import _DISTILL_COOLDOWN_SECONDS, logger
    from catfish_memory_distill_reconcile import head_needs_reconcile



def _should_run_distill(catfish_home: Path) -> bool:
    """24h 内跑过 → False (不再跑). 没跑过 / 已超 24h → True."""
    state_path = catfish_home / "memory_distill_state.json"
    if not state_path.exists():
        return True
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        last = state.get("last_run_ts", 0)
        return (time.time() - last) >= _DISTILL_COOLDOWN_SECONDS
    except (OSError, ValueError, json.JSONDecodeError):
        return True


#: 游标校验只看 journal 开头这么多字符的 sha1 —— 文件被整个换掉 (迁移 / 手清)
#: 时开头必变, 游标作废走全量; 正常 append 不动开头。
_JOURNAL_HEAD_CHARS = 2000


def _journal_head_sha(journal_text: str, consumed: int) -> str:
    """只 hash 已消费前缀的开头 (≤ _JOURNAL_HEAD_CHARS) —— journal 还很短时 append
    会改变"前 2000 字符", 按消费长度截才不会把正常追加误判成换文件。"""
    n = min(_JOURNAL_HEAD_CHARS, max(0, consumed))
    return hashlib.sha1(journal_text[:n].encode("utf-8")).hexdigest()


def _distill_cursor(catfish_home: Path, journal_text: str) -> Tuple[int, List[Tuple[int, int]]]:
    """(上次蒸馏消费到 journal 的第几个字符, 游标之前还没蒸成的缺口区间列表)。
    对不上 (没记过 / 文件换过 / 变短了) → (0, []) 全量。

    10/7 增量蒸馏: journal 820K 字 = 103 段, 每天全量重蒸要调 100+ 次 LLM
    (10/7 实测 88 次、1h40m, 大半超时)。append-only 的文件只蒸新增那几段就够了。
    缺口 (10/7 晚): 上游 504 两轮都没过的段, 成功段照常保留, 失败区间下次单独补。
    """
    state_path = catfish_home / "memory_distill_state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return 0, []
    offset = int(state.get("journal_consumed_chars") or 0)
    if offset <= 0 or offset > len(journal_text):
        return 0, []
    if state.get("journal_head_sha") != _journal_head_sha(journal_text, offset):
        return 0, []
    gaps: List[Tuple[int, int]] = []
    for g in state.get("journal_gaps") or []:
        try:
            a, b = int(g[0]), int(g[1])
        except (TypeError, ValueError, IndexError):
            continue
        if 0 <= a < b <= offset:
            gaps.append((a, b))
    return offset, gaps


#: 10/7 深夜: 上次"当前状态"合并没成 (头部兜底) 时, 不等 24h, 每隔这么久就在
#: session_end / 定时蒸馏时只重跑一次合并 (1–6 次 LLM 调用, 便宜)。公网 upstream
#: 晚高峰 180s 超时成片, 过一两个小时往往就好了, 等 24h 等于一整天都顶着兜底头。
_RECONCILE_RETRY_SECONDS = 3600


def _read_state_json(catfish_home: Path) -> Dict[str, Any]:
    try:
        return json.loads((catfish_home / "memory_distill_state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def _should_retry_reconcile(catfish_home: Path) -> bool:
    """头部是兜底 且 距上次合并尝试 ≥ _RECONCILE_RETRY_SECONDS → 该只重跑一次合并。"""
    try:
        distilled = (catfish_home / "distilled_facts.md").read_text(encoding="utf-8")
    except OSError:
        return False
    if not head_needs_reconcile(distilled):
        return False
    last = float(_read_state_json(catfish_home).get("last_reconcile_attempt_ts") or 0)
    return (time.time() - last) >= _RECONCILE_RETRY_SECONDS


def _mark_distill_run(
    catfish_home: Path,
    *,
    journal_consumed_chars: Optional[int] = None,
    journal_text: Optional[str] = None,
    journal_gaps: Optional[List[Tuple[int, int]]] = None,
    reconcile_attempt_only: bool = False,
) -> None:
    """写 memory_distill_state.json 记录这次跑过 (+ 10/7 增量游标和缺口, 给了才写).

    reconcile_attempt_only: 只重跑了合并 —— 不动 last_run_ts (24h cooldown 照旧),
    只记 last_reconcile_attempt_ts 给 _should_retry_reconcile 节流。
    """
    state_path = catfish_home / "memory_distill_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    prev = _read_state_json(catfish_home)
    payload: Dict[str, Any] = {
        "last_run_ts": prev.get("last_run_ts", now) if reconcile_attempt_only else now,
        "last_run_iso": prev.get("last_run_iso", time.strftime("%Y-%m-%dT%H:%M:%S")) if reconcile_attempt_only else time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": "catfish-memory-plugin",
    }
    if reconcile_attempt_only:
        payload["last_reconcile_attempt_ts"] = now
    if journal_consumed_chars is not None and journal_text is not None:
        payload["journal_consumed_chars"] = int(journal_consumed_chars)
        payload["journal_head_sha"] = _journal_head_sha(journal_text, int(journal_consumed_chars))
        payload["journal_gaps"] = [[int(a), int(b)] for a, b in (journal_gaps or [])]
    try:
        state_path.write_text(json.dumps(payload), encoding="utf-8")
    except OSError as e:
        logger.warning("写 memory_distill_state.json 失败: %s", e)


def _write_distilled(catfish_home: Path, text: str) -> None:
    """覆盖写 distilled_facts.md (跟老 gateway memory_distill.write_distilled_facts 等价)."""
    path = catfish_home / "distilled_facts.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        f"<!-- Generated by catfish-memory plugin at "
        f"{time.strftime('%Y-%m-%d %H:%M:%S')} -->\n"
        f"<!-- on_session_end 触发, 来源 catfish-memory plugin -->\n\n"
    )
    try:
        path.write_text(header + text.strip() + "\n", encoding="utf-8")
    except OSError as e:
        logger.warning("写 distilled_facts.md 失败: %s", e)

