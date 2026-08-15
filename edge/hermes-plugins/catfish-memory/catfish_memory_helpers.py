"""catfish-memory helpers — 抽自 catfish_memory.py (5/21 拆分).

包含: 文件 IO helper / journal 操作 / 蒸馏判定 / buffer 跟 state 持久化 /
plugin config 加载 / gateway URL 解析 / dev_token 获取 helpers.

主入口 CatfishMemoryProvider class 在 catfish_memory.py 内部 import 这些.
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

#: 总结 LLM prompt — 跟老 gateway 风格一致, 让员工 journal 风格连续.


def _wiki_enabled() -> bool:
    """P1.1 wiki two-step 开关 — 优先 yaml, env 兜底, 默认 off (LLM 调用贵).

    P3.5.12 (6/16 鸿波): 加 yaml 守门, 优先级 yaml > env > default False.

    真因: 6/16 早 entities/concepts 真被自动写入 19+34 条, 鸿波反馈"对话自动入
    知识库会很乱". audit 证实: 当时 shell `CATFISH_WIKI_ENABLE=1` 被 hermes
    继承 → 此函数返 True → wiki two-step 跑. 老逻辑只读 env 不可靠 — env
    可能从任何 init script / launchd plist 传, 难根治.

    新逻辑: yaml `wiki.auto_ingest` 优先 (永久声明性配置, 跟 env 解耦).
    yaml 配 false → env 设 =1 也不动. yaml 不配 → fallback env (向后兼容).
    yaml + env 都没配 → default False.

    yaml 配法: ~/.catfish/memory_plugin.yaml 加段:
        wiki:
          auto_ingest: false   # 永远不自动入库, 员工 ChatBubble "💾 存 wiki" 手动入
    """
    # 优先级 1: yaml `wiki.auto_ingest` (单一权威配置)
    try:
        cfg = _load_plugin_config()
        if isinstance(cfg, dict):
            wiki_cfg = cfg.get("wiki", {})
            if isinstance(wiki_cfg, dict):
                ai = wiki_cfg.get("auto_ingest")
                if isinstance(ai, bool):
                    return ai
    except Exception:  # noqa: BLE001 - 配置读失败回退 env, 不挂 plugin
        pass

    # 优先级 2: CATFISH_WIKI_ENABLE env (向后兼容, 历史路径)
    val = os.environ.get("CATFISH_WIKI_ENABLE", "").strip().lower()
    return val in ("1", "true", "yes", "on")


def _extract_message_pairs(messages: List[Dict[str, Any]]) -> List[Tuple[str, str]]:
    """从 hermes message list 抽 (role, content) 对.

    跳过 system / tool / 空 content. 限 _MAX_MESSAGES_PER_SUMMARY 条 (尾部).
    """
    pairs: List[Tuple[str, str]] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = m.get("content")
        if role not in ("user", "assistant"):
            continue
        if not isinstance(content, str) or not content.strip():
            continue
        pairs.append((role, content))
    if len(pairs) > _MAX_MESSAGES_PER_SUMMARY:
        pairs = pairs[-_MAX_MESSAGES_PER_SUMMARY:]
    return pairs


def _format_journal_entry(session_id: str, summary: str) -> str:
    """格式 journal entry. BL-CATFISH-WIKI-MODE P0.3 (6/3): 改用 Karpathy LLM Wiki
    log.md 风格 `## [YYYY-MM-DD HH:MM] kind | title`, 一行可 grep 解析.
    grep '^## \\[' employee_journal.md | tail -5 拉最近 5 条."""
    date_str = time.strftime("%Y-%m-%d %H:%M")
    # 8/4: 写**完整** session_id, 不再截成后 6 位。
    #
    # 老写法 `session_id[-6:]` 把溯源链掐断了: wiki 条目的 sources 只记到日期,
    # journal 只留 6 位后缀 —— 员工看到一条可疑的断言, 回溯不到说这句话的那次
    # 对话。而原始对话其实完整躺在 ~/.hermes/state.db (实测 5329 会话 /
    # 50794 条消息), 信息没丢, 只是指针被截断了。
    #
    # 不可验证 = 不可修正。知识库是四次 LLM 转写的产物 (summarize → analysis →
    # generation → merge), 失真是压缩的物理必然, 消除不了; 但"看到错的能查证"
    # 是可以做到的, 而且只差这一个字段。
    #
    # 存量条目靠后缀 LIKE 匹配仍能溯源 (session id 形如 20260803_195928_f39669,
    # 后 6 位同一天内基本唯一), 见 tool-bridge/wiki_trace.py。新条目从此精确。
    sid = session_id.strip() or "unknown"
    return f"## [{date_str}] session | {sid}\n\n{summary.strip()}\n"


def _append_journal(catfish_home: Path, entry: str) -> None:
    """追加一段 entry 到 catfish_home/employee_journal.md. 自动建父目录."""
    path = catfish_home / "employee_journal.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = entry.strip() + "\n\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(body)


def _read_picker_state_model(catfish_home: Path) -> str:
    """P3.5.2 (6/16 鸿波): 读 ~/.catfish/picker_state.json 拿 companion chat picker 当前 model.

    ⚠⚠ 【原则, 8/4 鸿波明确拍板】蒸馏必须用**员工自己选的那个模型**, 不许换。

    8/4 讨论知识库准确性时提过一个"优化": 蒸馏一天只跑几次, 换个更强的模型成本
    可忽略, 质量还更好 —— 加个 distill_model 独立配置就行。鸿波否掉了, 明确说
    这是原则问题。

    别再提这个"优化"。员工选了哪个模型, 知识库就该是那个模型的产物 —— 换成别的
    模型偷偷生成, 员工看到的东西就不再是他能预期的东西了。质量差是可以接受的,
    不透明不行。

    (下面的注释是原始设计说明, 保留。)

    companion chat.ts 每次 send 前 fire-and-forget 写这个文件, atomic write.
    plugin sync_turn 触发时读, 让 summary model 自动跟随 picker (而不是 yaml 静态).

    设计 (方案 B, 6/16 鸿波拍): hermes MemoryProvider.sync_turn 签名没 picker 入参,
    plugin 拿不到 picker 状态. 文件中转是绕过 hermes API 限制的最简方案.

    优先级 (caller _get_summarize_model): picker_state.json > yaml > env > 空.

    Args:
        catfish_home: ~/.catfish 目录

    Returns:
        picker model 字符串. 文件不存在 / parse 错 / chat_model 字段缺 → 空字符串.
        Caller 看到空就走 fallback (yaml/env).
    """
    path = catfish_home / "picker_state.json"
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
        if isinstance(data, dict):
            model = data.get("chat_model", "")
            if isinstance(model, str) and model.strip():
                return model.strip()
    except (OSError, ValueError, json.JSONDecodeError) as e:
        logger.debug(
            "catfish-memory: read picker_state.json 失败 (fallback yaml/env): %s", e,
        )
    return ""


def _read_full_journal(catfish_home: Path) -> str:
    """全文读 employee_journal.md 给蒸馏用 (不走 5KB inject 截断). 没文件 → 空."""
    path = catfish_home / "employee_journal.md"
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


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


def _mark_distill_run(catfish_home: Path) -> None:
    """写 memory_distill_state.json 记录这次跑过."""
    state_path = catfish_home / "memory_distill_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_run_ts": time.time(),
        "last_run_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": "catfish-memory-plugin",
    }
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


_GATEWAY_URL_DEPRECATION_LOGGED = False


def _gateway_url() -> str:
    """gateway loopback URL.

    P23 (6/5 鸿波): 统一 env 名 `CATFISH_GATEWAY_URL` (base URL, 不带 path) 跟
    Companion / catfish-xcatfish-user plugin 对齐. 老名 `CATFISH_GATEWAY_INTERNAL_URL`
    保留兼容 (含 full path), 设了 → 打 deprecation warning 用一次提醒.

    P24 (6/5 鸿波): yaml 接入 — ~/.catfish/memory_plugin.yaml 加 `gateway.url`
    字段 (跟 env 同义, 但客户端友好 — 不用 launchctl 改).

    优先级 (高→低):
      1. CATFISH_GATEWAY_INTERNAL_URL env (老, full URL e.g. http://x/v1/chat/completions)
      2. CATFISH_GATEWAY_URL env (新统一名, base URL), 自动拼 /v1/chat/completions
      3. yaml gateway.url (base URL), 自动拼 /v1/chat/completions
      4. _DEFAULT_GATEWAY_URL (http://127.0.0.1:8999/v1/chat/completions)
    """
    global _GATEWAY_URL_DEPRECATION_LOGGED
    full = os.environ.get("CATFISH_GATEWAY_INTERNAL_URL", "").strip()
    if full:
        if not _GATEWAY_URL_DEPRECATION_LOGGED:
            logger.warning(
                "P23 deprecation: CATFISH_GATEWAY_INTERNAL_URL 老 env 名, "
                "改用 CATFISH_GATEWAY_URL (base URL, 不带 path). 这次先兼容."
            )
            _GATEWAY_URL_DEPRECATION_LOGGED = True
        return full
    base = os.environ.get("CATFISH_GATEWAY_URL", "").strip().rstrip("/")
    if base:
        return f"{base}/v1/chat/completions"
    # P24 yaml 兜底
    cfg = _load_plugin_config()
    yaml_base = ""
    if isinstance(cfg, dict):
        gw = cfg.get("gateway", {})
        if isinstance(gw, dict):
            yaml_base = str(gw.get("url", "")).strip().rstrip("/")
    if yaml_base:
        return f"{yaml_base}/v1/chat/completions"
    return _DEFAULT_GATEWAY_URL


# ── 文件持久化 buffer + state (BL-MEMORY-SYNC-TURN-REFACTOR Day 2, 5/20) ───
#
# 发现 (5/20 12:30 实测): hermes api_server 模式**每个 chat completion request
# 创建新 AIAgent + 新 plugin instance**. 我们 plugin 内部 instance state
# (_turn_buffer / _turns_since_last_summary / _last_summary_ts) **每次重置**,
# 节流计数器永不累积到 5.
#
# Trace 证据 (5 次同 session_id curl): 5 个不同 instance id
#   112013d10 → 111fa5710 → 1120058d0 → 112022390 → 111fda910
#   counter_before 全 0.
#
# 修法: 节流 state 跨 instance 持久化到文件, plugin 每次 sync_turn 读 file
# 状态做节流判断, 触发后写 file 清空. 文件锁 (fcntl.flock) 防并发写.

#: buffer 文件 — 跨 instance 累积 user/assistant pairs, jsonl 一行一 entry
_BUFFER_FILENAME = ".catfish_memory_buffer.jsonl"

#: state 文件 — 跨 instance 存 last_summary_ts (单 dict json)
_STATE_FILENAME = ".catfish_memory_state.json"


def _buffer_file_path(home: Path) -> Path:
    return home / _BUFFER_FILENAME


def _state_file_path(home: Path) -> Path:
    return home / _STATE_FILENAME


def _read_buffer(home: Path) -> List[Tuple[str, str]]:
    """读 buffer file 返 list of (role, content). 不存在/corrupt 返空."""
    path = _buffer_file_path(home)
    if not path.exists():
        return []
    pairs: List[Tuple[str, str]] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            # shared lock for read (best-effort; macOS/Linux fcntl)
            try:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            except (OSError, ImportError):
                pass
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    role = obj.get("role")
                    content = obj.get("content")
                    if isinstance(role, str) and isinstance(content, str):
                        pairs.append((role, content))
                except (json.JSONDecodeError, AttributeError):
                    continue
    except OSError:
        return []
    return pairs


def _append_to_buffer(home: Path, role: str, content: str, session_id: str) -> int:
    """append entry to buffer file. 返新 pair 数 (len(entries) // 2)."""
    path = _buffer_file_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = json.dumps({
        "ts": time.time(),
        "role": role,
        "content": content,
        "session_id": session_id,
    }, ensure_ascii=False)
    try:
        with path.open("a", encoding="utf-8") as f:
            try:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            except (OSError, ImportError):
                pass
            f.write(entry + "\n")
    except OSError:
        return 0
    # count by re-reading (cheap, file 通常 < 20 entries)
    return len(_read_buffer(home))


def _clear_buffer(home: Path) -> None:
    """清空 buffer file (删除)."""
    path = _buffer_file_path(home)
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


def _read_state(home: Path) -> Dict[str, Any]:
    """读 state file. 不存在/corrupt 返空 dict."""
    path = _state_file_path(home)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, AttributeError):
        return {}


def _write_state(home: Path, state: Dict[str, Any]) -> None:
    """覆盖写 state file."""
    path = _state_file_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass


# ── plugin 配置 yaml (BL-PLUGIN-CONFIG-YAML 5/20 Day 2.5) ─────────
#
# 鸿波拍板: 配置不硬编码 / env, 走 yaml 参数文件, 跟 ~/.catfish/companion.yaml
# 同套路 (catfish 全栈共享配置位置).
#
# 文件路径: ~/.catfish/memory_plugin.yaml
#
# 优先级: yaml > env > hardcoded default. env 兜底兼容老部署.
#
# 内容示例:
#   enabled: true
#   summarize:
#     model: catfish-private-vision
#     every_n_turns: 5
#     min_interval_seconds: 1800

_PLUGIN_CONFIG_FILENAME = "memory_plugin.yaml"


def _plugin_config_path(home: Optional[Path] = None) -> Path:
    """yaml 配置文件位置. 默认 ~/.catfish/memory_plugin.yaml.

    home 参数让单测可指定 fake home; 没传时用 _catfish_home() (env CATFISH_HOME aware).
    """
    return (home or _catfish_home()) / _PLUGIN_CONFIG_FILENAME


def _load_plugin_config(home: Optional[Path] = None) -> Dict[str, Any]:
    """读 yaml 配置. 不存在 / 解析失败返空 dict (走 env / default 兜底).

    PyYAML 不可用时也返空 (优雅降级) — env 仍 work.
    """
    path = _plugin_config_path(home)
    if not path.exists():
        return {}
    try:
        import yaml  # 懒 import, PyYAML 是 hermes 自带依赖
    except ImportError:
        logger.debug("PyYAML 不可用, plugin yaml config 不加载")
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, yaml.YAMLError) as e:
        logger.warning("catfish-memory plugin yaml 配置加载失败 (%s): %s", path, e)
        return {}


def _read_hermes_env_key(key: str) -> str:
    """读 hermes 管理的 env key. 双查 os.environ + ~/.hermes/.env 文件.

    BL-PLUGIN-AUTH-FIX (7/27 鸿波): 为什么必须双查 —
      hermes `hermes_cli/config.py:load_env()` 只**返回 dict 不写 os.environ**.
      写 os.environ 只发生在 `/reload` 命令 (reload_env():8163) 或
      `set_env_value():8046`. 所以 plugin 光 os.environ.get() 可能拿不到.
      hermes 自己的 `get_env_value():8186` 就是双查 (先 os.environ 后 .env 文件),
      本函数语义跟它对齐.

      不 import hermes_cli.config — plugin 不该耦合 hermes 内部模块 (跨版本易断).
      Companion Rust 侧 `dream.rs:read_hermes_dev_env()` 也是直读 .env 文件, 同思路.

    parse 规则跟 dream.rs:262-278 对齐: 跳空行/注释, strip 引号.
    """
    val = os.environ.get(key, "").strip()
    if val:
        return val
    try:
        env_path = Path(os.path.expanduser("~")) / ".hermes" / ".env"
        if not env_path.exists():
            return ""
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if not line.startswith(f"{key}="):
                continue
            raw = line[len(key) + 1:].strip()
            # strip 成对引号 (跟 dream.rs strip_env_quotes 对齐)
            if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
                raw = raw[1:-1]
            return raw.strip()
    except OSError as e:
        logger.debug("catfish-memory 读 ~/.hermes/.env 失败 (%s): %s", key, e)
    return ""


_TOKEN_MISSING_MSG = (
    "catfish-memory: 拿不到 gateway 鉴权 token, %s skip. "
    "查顺序 ① ~/.hermes/.env OPENAI_API_KEY (hermes→gateway service token, "
    "跑 central/llm-gateway/refresh-jwt-hermes-env.sh 刷新) "
    "② env/. env CATFISH_INTERNAL_DEV_TOKEN ③ ~/.catfish/memory_plugin.yaml gateway.token"
)


def _log_token_missing(what: str) -> None:
    """BL-PLUGIN-AUTH-FIX (7/27): 统一 fail-loud. 老代码 4 处静默 return None,
    员工永远不知道 distill/wiki 从没跑过 (Dream Engine 9.7 天没跑就是这么来的).
    只记日志不弹 UI (后台任务失败不该打扰员工 · 鸿波 7/27 拍)."""
    logger.warning(_TOKEN_MISSING_MSG, what)


def _gateway_dev_token() -> str:
    """拿调 gateway 的鉴权 token. 没拿到返空 (caller skip + fail-loud log).

    BL-PLUGIN-AUTH-FIX (7/27 鸿波 catch "Dream Engine 9.7 天没跑"):

    ── 真因 ──
    老实现只读 CATFISH_INTERNAL_DEV_TOKEN. 但那是 **gateway 进程内 loopback 专用**
    (central/llm-gateway/.../auth/dev_token.py:11-15 明写 "启动时随机生成, 进程内存,
    重启即变, 不写 .env 文件"), 真 caller 只有 gateway 自己的 proactive.py /
    conversation_compressor.py. plugin 跑在 **hermes 进程** (另一进程 · 生产还跨机),
    永远拿不到 → _call_distill_llm 静默 return None → Dream Engine 自动蒸馏从没跑过.

    ── 正解 ──
    plugin 在 hermes 进程内, 调的又是 gateway, 就该复用 **hermes → gateway 这一跳**
    的凭证 = .env `OPENAI_API_KEY`:
      - aud=catfish-gateway · token_use=service · scope 含 chat.completions
      - 由 hermes-cli client_credentials 派发 (scope 经 identity 白名单校验, 可信)
      - refresh-jwt-hermes-env.sh 30 天刷新 (已有维护机制)
      - 生产分离时 OPENAI_BASE_URL 指中央 · 这 token 也是中央派发 · **天然跨机**

    链路: Companion ─[API_SERVER_KEY]→ hermes:8642 ─[OPENAI_API_KEY]→ gateway:8999 → LLM
                                          └── 本 plugin (in-process, 复用第 2 跳凭证)

    优先级:
      1. OPENAI_API_KEY (hermes→gateway service token · 生产正路)
      2. CATFISH_INTERNAL_DEV_TOKEN (本机 dev · gateway 同机且手工预设过时用)
      3. yaml gateway.token (P24 6/5 手工预设兜底)
    """
    token = _read_hermes_env_key("OPENAI_API_KEY")
    if token:
        return token
    token = _read_hermes_env_key("CATFISH_INTERNAL_DEV_TOKEN")
    if token:
        return token
    cfg = _load_plugin_config()
    if isinstance(cfg, dict):
        gw = cfg.get("gateway", {})
        if isinstance(gw, dict):
            return str(gw.get("token", "")).strip()
    return ""


# ─────────────────────────────────────────────────────────────────────────
# 8/15 拆分: 2522 行 → 796 + 5 个模块。下面按**依赖顺序**把它们的符号
# re-export 回本模块, 因为外部调用方全都认 catfish_memory_helpers 这个名字:
#
#   · catfish_memory.py  `from .catfish_memory_helpers import ...` 50 个符号
#   · wiki_health.py     `from catfish_memory_helpers import ...` 5 个符号
#   · tests/            `from catfish_memory_helpers import ...` 二十来个
#
# 顺序要紧 (prompts → fm → wiki → llm → merge): 后面的模块 import 前面的,
# 而它们又都回指本模块。本模块执行到这里时, 上面那些 main 层符号 (logger /
# _gateway_url / 各种常量) 已经定义好了, 所以子模块的回指拿得到 ——
# 这也是这个 re-export 块**必须放在文件末尾**的原因。
#
# import 形式: 相对优先、绝对兜底。理由见任一子模块的文件头注释,
# 以及 tests/test_loader_fidelity.py。
# ─────────────────────────────────────────────────────────────────────────

try:
    from .catfish_memory_prompts import (  # noqa: F401
        _SUMMARIZE_PROMPT,
        _DISTILL_PROMPT,
        _ANALYSIS_PROMPT,
        _GENERATION_PROMPT_TEMPLATE,
        _build_generation_prompt,
    )
except ImportError:  # 独立脚本模式 (无父包)
    from catfish_memory_prompts import (  # noqa: F401
        _SUMMARIZE_PROMPT,
        _DISTILL_PROMPT,
        _ANALYSIS_PROMPT,
        _GENERATION_PROMPT_TEMPLATE,
        _build_generation_prompt,
    )

try:
    from .catfish_memory_fm import (  # noqa: F401
        _WIKI_PATH_PATTERN,
        _FILE_SENTINEL,
        _parse_generation_output,
        _FM_LIST_FIELDS_UNION,
        _FM_LIST_RE,
        _FM_SCALAR_RE,
        _split_frontmatter_body,
        _split_top_level,
        _REL_NAME_RE,
        _rel_item_name,
        _parse_frontmatter_lists,
        _parse_frontmatter_scalar,
        _merge_wiki_file,
        _FM_KEY_LINE,
        _FM_FENCE_LINE,
        _ensure_frontmatter_fence,
    )
except ImportError:  # 独立脚本模式 (无父包)
    from catfish_memory_fm import (  # noqa: F401
        _WIKI_PATH_PATTERN,
        _FILE_SENTINEL,
        _parse_generation_output,
        _FM_LIST_FIELDS_UNION,
        _FM_LIST_RE,
        _FM_SCALAR_RE,
        _split_frontmatter_body,
        _split_top_level,
        _REL_NAME_RE,
        _rel_item_name,
        _parse_frontmatter_lists,
        _parse_frontmatter_scalar,
        _merge_wiki_file,
        _FM_KEY_LINE,
        _FM_FENCE_LINE,
        _ensure_frontmatter_fence,
    )

try:
    from .catfish_memory_wiki import (  # noqa: F401
        # ⚠ 这三个是 `_TYPE_ALIASES, _ENTITY_TYPES, _CONCEPT_TYPES = _load_type_vocab()`
        # 元组解包出来的。第一版生成 re-export 时漏了 —— 计算器只认 ast.Assign
        # 里 targets 是 ast.Name 的情况, Tuple 目标整个跳过。判据比真事窄,
        # 靠 test_ontology.py::test_vocab_comes_from_shared_contract 抓出来的。
        _CONCEPT_TYPES,
        _ENTITY_TYPES,
        _TYPE_ALIASES,
        _CONCLUSION_WORDS,
        _scan_conclusion_words,
        _normalize_slug_for_dedup,
        _read_title_of,
        _title_of_content,
        _redirect_to_existing_equivalent,
        _load_type_vocab,
        _canon_subtype,
        _SUBTYPE_LINE,
        _normalize_types,
        _check_dangling_related,
        _AUTHORED_BY_EMPLOYEE,
        _LLM_APPENDIX_HEAD,
        _is_employee_authored,
        _append_as_appendix,
        _write_wiki_files,
        report_ontology_gaps,
    )
except ImportError:  # 独立脚本模式 (无父包)
    from catfish_memory_wiki import (  # noqa: F401
        _CONCEPT_TYPES,
        _ENTITY_TYPES,
        _TYPE_ALIASES,
        _CONCLUSION_WORDS,
        _scan_conclusion_words,
        _normalize_slug_for_dedup,
        _read_title_of,
        _title_of_content,
        _redirect_to_existing_equivalent,
        _load_type_vocab,
        _canon_subtype,
        _SUBTYPE_LINE,
        _normalize_types,
        _check_dangling_related,
        _AUTHORED_BY_EMPLOYEE,
        _LLM_APPENDIX_HEAD,
        _is_employee_authored,
        _append_as_appendix,
        _write_wiki_files,
        report_ontology_gaps,
    )

try:
    from .catfish_memory_llm import (  # noqa: F401
        _call_summarize_llm,
        _call_distill_llm,
        _call_analysis_llm,
        _existing_wiki_index,
        _call_generation_llm,
    )
except ImportError:  # 独立脚本模式 (无父包)
    from catfish_memory_llm import (  # noqa: F401
        _call_summarize_llm,
        _call_distill_llm,
        _call_analysis_llm,
        _existing_wiki_index,
        _call_generation_llm,
    )

try:
    from .catfish_memory_merge import (  # noqa: F401
        _MERGE_PROMPT_TEMPLATE,
        _build_merge_prompt,
        _call_merge_llm,
        _MERGE_MIN_BODY_RATIO,
        _accept_llm_merge,
        merge_files_with_llm,
    )
except ImportError:  # 独立脚本模式 (无父包)
    from catfish_memory_merge import (  # noqa: F401
        _MERGE_PROMPT_TEMPLATE,
        _build_merge_prompt,
        _call_merge_llm,
        _MERGE_MIN_BODY_RATIO,
        _accept_llm_merge,
        merge_files_with_llm,
    )
