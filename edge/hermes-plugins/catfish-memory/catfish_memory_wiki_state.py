"""wiki 摄取状态 —— 哪些 wiki/queries/ 和 wiki/raw/sources/ 已经吃过了。

8/15 从 catfish_memory_helpers.py 搬出来 (949 行, 过了 CLAUDE.md §1 红线)。

# 它防的是重复摄取

员工往 wiki/queries/ 丢一个问题、往 wiki/raw/sources/ 丢一份材料, 蒸馏会把它
读进记忆。没有这层状态的话, 每次蒸馏都会把同一份材料再吃一遍 —— 表现不是报错,
是**记忆里同一件事被写好几遍**, 越滚越大, 而且从外面看不出来是重复。

状态写在 `<catfish_home>/wiki_ingested_state.json`, 记 {文件名: 摄取时间戳}。

# 路径都是参数传进来的

`catfish_home: Path` 是参数, 不自己去猜位置 —— 所以这一组能独立测, 也不会
因为 CATFISH_HOME 的解析规则变了而受影响。仓内依赖只有 logger 一个。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, List, Tuple

try:
    from .catfish_memory_base import logger
except ImportError:  # 独立脚本模式 (无父包)
    from catfish_memory_base import logger



# ── BL-CATFISH-WIKI-MODE P1.2.3 (6/4) — wiki/queries/ 触发 partial ingest ──

def _wiki_ingested_state_path(catfish_home: Path) -> Path:
    """记 哪些 wiki/queries/*.md 已被 ingest, 避免重复处理."""
    return catfish_home / "wiki_ingested_state.json"


def _read_wiki_ingested_state(catfish_home: Path) -> Dict[str, float]:
    """返 {file_basename: ingested_ts} dict."""
    p = _wiki_ingested_state_path(catfish_home)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def _mark_wiki_queries_ingested(catfish_home: Path, file_names: List[str]) -> None:
    """append 已 ingest 真 queries file 名 + ts 到 wiki_ingested_state.json."""
    if not file_names:
        return
    state = _read_wiki_ingested_state(catfish_home)
    now = time.time()
    for name in file_names:
        state[name] = now
    p = _wiki_ingested_state_path(catfish_home)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        logger.warning("写 wiki_ingested_state.json 失败: %s", e)


def _list_pending_queries(catfish_home: Path) -> List[Path]:
    """列 wiki/queries/ 真未 ingest 真 *.md file (按 mtime 排, 最旧先)."""
    queries_dir = catfish_home / "wiki" / "queries"
    if not queries_dir.is_dir():
        return []
    state = _read_wiki_ingested_state(catfish_home)
    pending = []
    try:
        for f in queries_dir.glob("*.md"):
            if f.name not in state:
                pending.append(f)
    except OSError:
        return []
    # 按 mtime 排 (旧 → 新)
    try:
        pending.sort(key=lambda p: p.stat().st_mtime)
    except OSError:
        pass
    return pending


def _read_queries_concat(query_files: List[Path], max_chars: int = 12000) -> str:
    """读所有 queries file 拼一段 text 给 Analysis. 总 cap max_chars 防爆.

    格式: 每 file 加 `### query: <filename>` 头. content 整 file (含
    frontmatter — Analysis LLM 能看 metadata).
    """
    if not query_files:
        return ""
    parts = []
    used = 0
    for f in query_files:
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        block = f"### query: {f.name}\n\n{text.strip()}\n"
        if used + len(block) > max_chars:
            break
        parts.append(block)
        used += len(block)
    return "\n\n".join(parts)


# ── P16 (6/5 鸿波) — wiki/raw/sources/ 触发 partial ingest ─────────────────
# 对话上传文件 → ~/.catfish/wiki/raw/sources/<ts>-<slug>.md (含 frontmatter
# + 全文 body, Companion wiki_ingest_source `Tauri command 写真). 这
# 一组 helper 跟 P1.2.3 queries hook 同结构, 复用 wiki_ingested_state.json
# (key 加 `source:` 前缀防与 queries 冲突).
# sync_turn 3b 会把 sources + queries 一起 merge 进 Analysis input → LLM
# 抽 entity/concept → wiki/entities/ + wiki/concepts/.


def _list_pending_sources(catfish_home: Path) -> List[Path]:
    """列 wiki/raw/sources/ 真未 ingest 真 *.md file (按 mtime 排, 最旧先)."""
    sources_dir = catfish_home / "wiki" / "raw" / "sources"
    if not sources_dir.is_dir():
        return []
    state = _read_wiki_ingested_state(catfish_home)
    pending = []
    try:
        for f in sources_dir.glob("*.md"):
            key = f"source:{f.name}"
            if key not in state:
                pending.append(f)
    except OSError:
        return []
    try:
        pending.sort(key=lambda p: p.stat().st_mtime)
    except OSError:
        pass
    return pending


def _read_sources_concat(source_files: List[Path], max_chars: int = 24000) -> str:
    """读所有 sources file 拼一段 text 给 Analysis. 总 cap max_chars 防爆.

    Sources 全文体积比 queries 大 (PDF/Word 转出来), max_chars 默认 24K
    (queries 12K 真 2 倍). 还是会被 cap, 单文件超 24K 会 break.

    格式: 每 file 加 `### source: <filename>` 头. content 整 file 含
    frontmatter (Analysis LLM 能看 filename / kind / uploaded date).
    """
    if not source_files:
        return ""
    parts = []
    used = 0
    for f in source_files:
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        block = f"### source: {f.name}\n\n{text.strip()}\n"
        if used + len(block) > max_chars:
            # 超 cap 但还想塞点 — 截前 (max_chars - used) 字进去 + 标记截断
            remain = max_chars - used
            if remain > 500:
                parts.append(block[:remain] + "\n\n[... source 内容截断, 剩余下次 ingest]")
                used = max_chars
            break
        parts.append(block)
        used += len(block)
    return "\n\n".join(parts)


def _mark_wiki_sources_ingested(catfish_home: Path, file_names: List[str]) -> None:
    """append 已 ingest 真 sources file 名 + ts 到 wiki_ingested_state.json.
    key 加 `source:` 前缀防与 queries 冲突 (queries 用裸 file_name).
    """
    if not file_names:
        return
    state = _read_wiki_ingested_state(catfish_home)
    now = time.time()
    for name in file_names:
        state[f"source:{name}"] = now
    p = _wiki_ingested_state_path(catfish_home)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        logger.warning("写 wiki_ingested_state.json (sources) 失败: %s", e)
