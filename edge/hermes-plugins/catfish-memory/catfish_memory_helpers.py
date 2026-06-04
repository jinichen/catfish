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
_BUDGETS: Dict[str, int] = {
    "employee_journal": 5000,
    "skills_catalog": 20000,
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
#: 60s 真**不够** (6/4 12:55 实测 ReadTimeout). 180s 给 LLM 慢慢生.
_GENERATION_HTTP_TIMEOUT = 180.0

#: 每个 session 取最多 N 条消息进 prompt (防长 session 撑爆 LLM context).
_MAX_MESSAGES_PER_SUMMARY = 60

#: 蒸馏 chunk 大小 (字符), 跟 gateway memory_distill 原 DISTILL_CHUNK_CHARS 对齐.
_DISTILL_CHUNK_CHARS = 8000

#: 总结 LLM prompt — 跟老 gateway 风格一致, 让员工 journal 风格连续.
_SUMMARIZE_PROMPT = (
    "你是企业员工的工作日志助手. 下面是员工跟 AI 副手的一次对话. "
    "用 1-2 个简短段落 (中文, ≤200 字) 总结这次对话的**关键决策、偏好、做出的事**. "
    "格式: 第一行 `### 主题`, 后面正文. "
    "**不要复述 AI 回答**, 只记员工立场 / 输出 / 偏好. "
    "如果没有实质内容 (比如员工只是闲聊或问候), 输出空字符串.\n"
)

#: 蒸馏 LLM prompt — 跟老 gateway memory_distill 一致 (抽人/项目/偏好/决策).
_DISTILL_PROMPT = (
    "你是员工长期记忆蒸馏师. 下面是员工最近的工作日志. "
    "抽出**人物、项目、偏好、决策**这 4 类关键事实, 每条 1 行, ≤300 字总. "
    "格式 markdown bullet, 一类一段. 不复述原文, 只抽结论性事实.\n"
)

# ── BL-CATFISH-WIKI-MODE P1.1 (6/4) ──────────────────────
# 借 llm_wiki buildAnalysisPrompt + buildGenerationPrompt 拆 distill 为两步:
# Step 1 Analysis — 结构化抽 entities / concepts / decisions / contradictions
# Step 2 Generation — 每 entity/concept 生成 1 markdown page (frontmatter +
#                     ---FILE: <path>--- sentinel parser)
# 输出 ~/.catfish/wiki/entities/ + ~/.catfish/wiki/concepts/
# CATFISH_WIKI_ENABLE env toggle 默认 off (LLM 调用贵, 24h 1 次).

#: Step 1 Analysis prompt — 结构化抽 4 类. 中文优先 (员工日志中文为主).
#: P1.1.1 polish (6/4): 加员工偏好 skip rule 真**catfish/鲶鱼 个人开源项目 不抽** —
#: 之前 catfish.md 真**hallucinate 成"工作 system + CI/Security audit"** 违 USER PROFILE.
_ANALYSIS_PROMPT = (
    "你是企业知识体系分析师. 下面是员工工作日志, 抽以下 4 类结构化信息.\n\n"
    "**重要 skip rule (优先级最高)**:\n"
    "- `catfish` / `鲶鱼` / `小鲶` / `胖胖` 这些名字 = 员工的 AI 副手 / 个人开源项目, "
    "**不是企业项目**, **不要抽成 entity**.\n"
    "- 员工的 chat 工具 / AI 助手相关 = 跨工具元话题, **不抽** (这些不构成业务知识).\n"
    "- 同样**不抽**: claude / hermes / openai / deepseek / qwen 等 AI 模型 / 真**工具名**.\n\n"
    "**输出格式严格**:\n\n"
    "## Entities\n"
    "- <name> | <type: person/org/system/cert/project> | <一句话, ≤50字>\n"
    "- ... (≤6 条 entities, 按重要性排, 真**企业业务相关**)\n\n"
    "## Concepts\n"
    "- <name> | <type: process/rule/principle/standard> | <一句话, ≤50字>\n"
    "- ... (≤6 条 concepts, **必须真**真**至少 3 个** — 真**抽流程/规则/原则/标准**)\n"
    "- 例: 资质评估流程 / 月度通报模板 / 文体规范 / 资质统筹原则 / 申报材料归档规范\n\n"
    "## Decisions\n"
    "- <YYYY-MM-DD> | <who> | <decided what> | <why>\n"
    "- ... (≤5 条, 最近)\n\n"
    "## Contradictions\n"
    "- <pair A vs B>: <冲突点, ≤80字>\n"
    "- ... (≤3 条, 没有就写 `(无)`)\n\n"
    "**约束**:\n"
    "- entity name 简短 (人名/机构缩写/产品名/资质名), 拼写跟员工原文一致\n"
    "- 不抽闲聊 / 待办 (待办在 journal 已有)\n"
    "- 不抽 catfish / AI 工具 / 大模型 (见上 skip rule)\n"
    "- 不复述原文, 只抽结论性事实\n"
    "- 中文优先, 必要时带英文 (e.g. ISO 27001)\n"
    "- 输出≤2000 字总\n"
)

#: Step 2 Generation prompt — 借 llm_wiki ---FILE: sentinel pattern.
#: 输入 = Analysis 输出, 输出 = 多 file markdown 拼接, 按 ---FILE: <path>--- 切分.
#: P1.1.1 polish (6/4): 1) **concepts 先 entities 后** 保 concepts 不被 token cap 吃;
#: 2) related YAML list 真 `["[[name1]]", "[[name2]]"]` 真**双引号 string list 兼容
#:    Obsidian + YAML 严格** (之前真 `[[[name]]]` 三括号双不兼容).
_GENERATION_PROMPT_TEMPLATE = (
    "你是企业知识体系作者. 下面是结构化分析结果 (Entities + Concepts + Decisions + "
    "Contradictions). 为**每个 entity 和 concept** 各生成 1 个 markdown 页, "
    "用 sentinel 切分.\n\n"
    "**生成顺序 (重要, 真**严守**)**:\n"
    "- **先生 concepts** (流程/规则/原则/标准 — 复用率最高, 不可丢)\n"
    "- 再生 entities (人/机构/系统/资质 — 替换率较高, 后生)\n"
    "- token 紧张时**先丢 entities 尾部**, 真**concepts 必须全生**\n\n"
    "**输出格式严格**:\n\n"
    "```\n"
    "---FILE: wiki/concepts/<slug>.md---\n"
    "---\n"
    "type: concept\n"
    "title: <name>\n"
    "concept_type: <process/rule/principle/standard>\n"
    "created: {today}\n"
    "updated: {today}\n"
    "tags: [<tag1>, <tag2>]\n"
    "related: [\"[[<other concept>]]\", \"[[<other entity>]]\"]\n"
    "sources: [employee_journal]\n"
    "---\n\n"
    "# <name>\n\n"
    "<3-5 段正文, 总 ≤600 字. 定义 / 适用场景 / 跟其它 concept 真区别 / 案例.>\n"
    "\n"
    "## Related\n"
    "- [[<other entity/concept>]] — <为什么相关, ≤30字>\n"
    "- ... (≤4 条)\n"
    "\n"
    "---FILE: wiki/entities/<slug>.md---\n"
    "---\n"
    "type: entity\n"
    "title: <name>\n"
    "entity_type: <person/org/system/cert/project>\n"
    "created: {today}\n"
    "updated: {today}\n"
    "tags: [<tag1>, <tag2>]\n"
    "related: [\"[[<other entity>]]\", \"[[<other concept>]]\"]\n"
    "sources: [employee_journal]\n"
    "---\n\n"
    "# <name>\n\n"
    "<2-4 段正文, 总 ≤400 字. 1 段概述, 1 段关键关系/决策, 1 段贡献/角色.>\n"
    "\n"
    "## Related\n"
    "- [[<other entity>]] — <为什么相关, ≤30字>\n"
    "- ... (≤4 条)\n"
    "```\n\n"
    "**约束**:\n"
    "- slug = name 小写 + 中文转拼音首字母 + 连字符 (e.g. ISO 27001 → iso-27001, "
    "陈鸿波 → chenhongbo, 中电福富 → zdff). entity slug 跟 concept slug 不冲突\n"
    "- frontmatter YAML 严格合法 (Obsidian 解析)\n"
    "- `related:` 真**必须真**真**双引号 string list** — 真**正确**: "
    "`related: [\"[[陈鸿波]]\", \"[[FFCS]]\"]`. "
    "**错**: `related: [[[陈鸿波]]]` (3 个 `[` YAML 真 inline list of list, "
    "Obsidian 真**不能 parse**)\n"
    "- 每 file 真**title 不重复**\n"
    "- related wikilinks 真 `[[name]]` 必须真**指**真 Analysis 里出现真 name\n"
    "- 同 slug 真 entity vs concept 真不允许 (按 type 分)\n"
    "- 全部输出 ≤6000 字\n"
)


def _build_generation_prompt() -> str:
    """注 {today} 真生成 prompt."""
    return _GENERATION_PROMPT_TEMPLATE.format(today=time.strftime("%Y-%m-%d"))


def _wiki_enabled() -> bool:
    """CATFISH_WIKI_ENABLE env 控 P1.1 wiki two-step 开关. 默认 off (LLM 调用贵)."""
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
    sid_short = (session_id[-6:] if len(session_id) > 6 else session_id) or "unknown"
    return f"## [{date_str}] session | …{sid_short}\n\n{summary.strip()}\n"


def _append_journal(catfish_home: Path, entry: str) -> None:
    """追加一段 entry 到 catfish_home/employee_journal.md. 自动建父目录."""
    path = catfish_home / "employee_journal.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = entry.strip() + "\n\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(body)


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

    格式: 每 file 加 `### query: <filename>` 头. content 真**整 file** (含
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
# + 全文 body, Companion wiki_ingest_source 真**`Tauri command 写**真). 这
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

    格式: 每 file 加 `### source: <filename>` 头. content 真**整 file** 含
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


def _gateway_url() -> str:
    """gateway loopback URL — env CATFISH_GATEWAY_INTERNAL_URL 覆盖默认."""
    return os.environ.get("CATFISH_GATEWAY_INTERNAL_URL", _DEFAULT_GATEWAY_URL).strip() or _DEFAULT_GATEWAY_URL


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


def _gateway_dev_token() -> str:
    """从 env 拿 gateway internal dev token. 没设返空 (caller skip).

    设计取舍 (Week 2 Step D 部署前确认): 不依赖 gateway 那边的
    ensure_internal_dev_token() runtime 生成 — plugin 是 in-hermes 进程, import
    gateway code 跨进程不健康. 必须**两个进程都从同一个 env 读**, plugin (hermes
    进程) 和 gateway 进程的 env 都设这个值.

    BL-FIX37 妥协 (5/19 Week 2 Step D): 老 BL-FIX37 设计是 gateway 启动自动
    生成 random + 不落盘 (外部抓不到, 重启即变). plugin 在另一进程必须能拿
    同一个值 → 退让成显式预设到 .env 文件. 文件 chmod 600 + .gitignore 兜底
    安全性. 跟 HERMES_SERVICE_TOKEN 同套路.

    env: CATFISH_INTERNAL_DEV_TOKEN (跟 gateway auth/dev_token.py 同名,
    部署时设一处, 两进程共享)
    """
    return os.environ.get("CATFISH_INTERNAL_DEV_TOKEN", "").strip()


async def _call_summarize_llm(
    pairs: List[Tuple[str, str]], model: str,
) -> Optional[str]:
    """调 gateway loopback /v1/chat/completions 总结一次. 失败返 None.

    跟老 session_summarizer._summarize_with_llm 行为等价:
      - X-Catfish-Skip-Identity: 防 gateway 给这次内部调用又注入 SOUL/journal
      - X-Catfish-Internal: 跳 quota check
      - Authorization Bearer <dev_token>
      - temperature 0.3, max_tokens 600 (短总结)
    """
    if not pairs:
        return None
    token = _gateway_dev_token()
    if not token:
        logger.info(
            "catfish-memory: CATFISH_GATEWAY_DEV_TOKEN 没设, summarize skip (返空)"
        )
        return None

    # 拼上下文
    context_lines = [f"[{role}]: {content[:500]}" for role, content in pairs]
    user_prompt = _SUMMARIZE_PROMPT + "\n\n会话历史:\n\n" + "\n\n".join(context_lines)

    try:
        import httpx  # 懒 import, plugin 装时已经依赖 hermes 全套
    except ImportError:
        logger.warning("catfish-memory: httpx 不可用, summarize skip")
        return None

    try:
        async with httpx.AsyncClient(timeout=_LLM_HTTP_TIMEOUT) as client:
            resp = await client.post(
                _gateway_url(),
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Catfish-Skip-Identity": "true",
                    "X-Catfish-Internal": "true",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": user_prompt}],
                    "temperature": 0.3,
                    "max_tokens": 600,
                    "stream": False,
                },
            )
            if resp.status_code != 200:
                logger.warning(
                    "catfish-memory summarize: HTTP %d (%s), skip",
                    resp.status_code, resp.text[:200],
                )
                return None
            data = resp.json()
            text = data.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
            text = text.strip()
            return text or None
    except Exception as e:  # noqa: BLE001
        logger.warning("catfish-memory summarize 异常: %s", e)
        return None


async def _call_distill_llm(
    journal_text: str, model: str,
) -> Optional[str]:
    """调 gateway 蒸馏老 journal. 失败返 None.

    跟 memory_distill.maybe_run_llm_distillation 行为等价 (简化版):
      - 切 _DISTILL_CHUNK_CHARS 大小 chunk
      - 每 chunk 走 gateway 抽人/项目/偏好/决策
      - 全部失败返 None, 部分成功合并返
    """
    if not journal_text.strip():
        return None
    token = _gateway_dev_token()
    if not token:
        return None
    try:
        import httpx
    except ImportError:
        return None

    # 简单切 chunk (按字符, 不按 ## 段边界 — POC 阶段够用; 老 gateway 切段边界更精细)
    chunks: List[str] = []
    remaining = journal_text
    while remaining:
        chunks.append(remaining[:_DISTILL_CHUNK_CHARS])
        remaining = remaining[_DISTILL_CHUNK_CHARS:]
    if not chunks:
        return None

    results: List[str] = []
    async with httpx.AsyncClient(timeout=_LLM_HTTP_TIMEOUT) as client:
        for chunk in chunks:
            try:
                resp = await client.post(
                    _gateway_url(),
                    headers={
                        "Authorization": f"Bearer {token}",
                        "X-Catfish-Skip-Identity": "true",
                        "X-Catfish-Internal": "true",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": _DISTILL_PROMPT + "\n\n" + chunk}],
                        "temperature": 0.2,
                        "max_tokens": 1000,
                        "stream": False,
                    },
                )
                if resp.status_code != 200:
                    logger.debug(
                        "catfish-memory distill chunk HTTP %d, skip 这段",
                        resp.status_code,
                    )
                    continue
                data = resp.json()
                text = data.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
                text = text.strip()
                if text:
                    results.append(f"### 蒸馏段 {len(results) + 1}\n\n{text}")
            except Exception as e:  # noqa: BLE001
                logger.debug("catfish-memory distill chunk 异常 (跳过): %s", e)
                continue

    if not results:
        return None
    return "\n\n".join(results)


# ── BL-CATFISH-WIKI-MODE P1.1 wiki two-step ─────────────────────

async def _call_analysis_llm(
    journal_text: str, model: str,
) -> Optional[str]:
    """Step 1 Analysis: 结构化抽 entities/concepts/decisions/contradictions.

    输入 = 全 journal_text (≤ _DISTILL_CHUNK_CHARS 真 chunk).
    输出 = markdown 结构化 (4 个 ## 段) 或 None (失败).

    比 _call_distill_llm 真区别: 输出结构化 (parseable), 不是 bullet list.
    """
    if not journal_text.strip():
        return None
    token = _gateway_dev_token()
    if not token:
        return None
    try:
        import httpx
    except ImportError:
        return None

    # 单 chunk 走 (journal 真大时切前 _DISTILL_CHUNK_CHARS — 最新优先 tail).
    chunk = journal_text[-_DISTILL_CHUNK_CHARS:] if len(journal_text) > _DISTILL_CHUNK_CHARS else journal_text

    try:
        async with httpx.AsyncClient(timeout=_LLM_HTTP_TIMEOUT) as client:
            resp = await client.post(
                _gateway_url(),
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Catfish-Skip-Identity": "true",
                    "X-Catfish-Internal": "true",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": _ANALYSIS_PROMPT + "\n\n" + chunk}],
                    "temperature": 0.2,
                    "max_tokens": 2500,
                    "stream": False,
                },
            )
            if resp.status_code != 200:
                logger.warning(
                    "catfish-memory analysis: HTTP %d (%s), skip",
                    resp.status_code, resp.text[:200],
                )
                return None
            data = resp.json()
            text = data.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
            # P1.1.1 debug (6/4): 写 raw Step 1 output 到 file. 跑通后删.
            try:
                _debug_dump = Path.home() / ".catfish" / "last_wiki_analysis.txt"
                _debug_dump.write_text(text, encoding="utf-8")
            except Exception:  # noqa: BLE001
                pass
            return text.strip() or None
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "catfish-memory analysis 异常 [%s]: %r",
            type(e).__name__, e,
        )
        return None


async def _call_generation_llm(
    analysis: str, model: str,
) -> Optional[str]:
    """Step 2 Generation: 把 analysis 转 wiki pages (---FILE: sentinel).

    输入 = Step 1 真 analysis text (~2000 字).
    输出 = ---FILE: <path>--- 切分真 multi-file markdown 或 None.
    """
    if not analysis.strip():
        return None
    token = _gateway_dev_token()
    if not token:
        return None
    try:
        import httpx
    except ImportError:
        return None
    try:
        # P1.1.1 fix (6/4): generation 单独 180s timeout — 4096 tokens 真**生 LLM**
        # 60s 真**不够** (12:55 ReadTimeout 实测).
        async with httpx.AsyncClient(timeout=_GENERATION_HTTP_TIMEOUT) as client:
            resp = await client.post(
                _gateway_url(),
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Catfish-Skip-Identity": "true",
                    "X-Catfish-Internal": "true",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "user",
                         "content": _build_generation_prompt() + "\n\n## Analysis\n\n" + analysis}
                    ],
                    "temperature": 0.3,
                    # P1.1.1 fix (6/4): 7000 → 4096 兼容 deepseek-flash /
                    # catfish-private-main 真 output cap. Generation 真
                    # ~3 concept + ~5 entity 估 3500 tokens 够.
                    "max_tokens": 4096,
                    "stream": False,
                },
            )
            if resp.status_code != 200:
                logger.warning(
                    "catfish-memory generation: HTTP %d (%s), skip",
                    resp.status_code, resp.text[:200],
                )
                return None
            data = resp.json()
            text = data.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
            # P1.1.1 debug (6/4): 写 raw LLM output 到 file 看 sentinel 不符原因.
            # 跑通后删 (BL-CATFISH-WIKI-MODE P1.1.2 cleanup).
            try:
                _debug_dump = Path.home() / ".catfish" / "last_wiki_generation.txt"
                _debug_dump.write_text(text, encoding="utf-8")
            except Exception:  # noqa: BLE001
                pass
            return text.strip() or None
    except Exception as e:  # noqa: BLE001
        # P1.1.1 fix (6/4): str(e) 真**空时 type(e).__name__ + repr** 真 hint —
        # 之前 'generation 异常: ' 空 message 真**直接看不出真什么 error**.
        logger.warning(
            "catfish-memory generation 异常 [%s]: %r",
            type(e).__name__, e,
        )
        return None


# 路径白名单 — 防 LLM 输出真 ---FILE: 真**逃逸 wiki/ 根**.
# P1.1.1 fix (6/4): \w + re.UNICODE 让 slug 接受中文 (LLM 不遵守拼音, 直接用中文 name —
# Obsidian 真**也支持 unicode slug**, 没必要强制 ASCII).
_WIKI_PATH_PATTERN = __import__("re").compile(
    r"^wiki/(entities|concepts)/[\w][\w_-]*\.md$",
    __import__("re").UNICODE,
)

# sentinel pattern 真 ---FILE: <path>--- 行.
_FILE_SENTINEL = __import__("re").compile(r"^---FILE:\s*(.+?)\s*---\s*$", __import__("re").MULTILINE)


def _parse_generation_output(text: str) -> Dict[str, str]:
    """切 LLM 输出按 ---FILE: <path>--- sentinel, 返 {rel_path: content} dict.

    路径白名单 _WIKI_PATH_PATTERN — 只允 wiki/entities/<slug>.md /
    wiki/concepts/<slug>.md. 其它 path silent skip (LLM 真乱写 / 真逃逸防护).
    """
    if not text:
        return {}
    # 找所有 sentinel 真 (path, start_offset) 真
    matches = list(_FILE_SENTINEL.finditer(text))
    if not matches:
        return {}

    files: Dict[str, str] = {}
    for i, m in enumerate(matches):
        rel_path = m.group(1).strip()
        # 白名单验
        if not _WIKI_PATH_PATTERN.match(rel_path):
            logger.debug("catfish-memory wiki parse: skip 非白名单 path %r", rel_path)
            continue
        content_start = m.end()
        content_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = text[content_start:content_end].strip()
        # 真 strip trailing fence 真 ``` (LLM 真有时 wrap markdown)
        if content.endswith("```"):
            content = content[:-3].rstrip()
        if content.startswith("```"):
            # 真 strip first line 真 ``` / ```markdown 之类
            content = content.split("\n", 1)[-1].lstrip()
        if not content:
            continue
        files[rel_path] = content
    return files


_FM_LIST_FIELDS_UNION = ("tags", "related", "sources", "aliases")
_FM_LIST_RE = re.compile(r"^([a-z_]+):\s*\[(.*?)\]\s*$", re.MULTILINE)
_FM_SCALAR_RE = re.compile(r"^([a-z_]+):\s*(.+?)\s*$", re.MULTILINE)


def _split_frontmatter_body(text: str) -> Tuple[str, str]:
    """切 markdown 真 (frontmatter_yaml, body). 没 frontmatter 返 ('', text)."""
    if not text.startswith("---\n"):
        return "", text
    end = text.find("\n---\n", 4)
    if end < 0:
        return "", text
    return text[4:end], text[end + 5 :]


def _parse_frontmatter_lists(fm: str) -> Dict[str, List[str]]:
    """从 YAML frontmatter 抠 list 字段 ([\"[[a]]\", \"b\"]). 简单 regex,
    不全 YAML, 但对 prompt 真**`生成`** 真 format 够用.

    fix (6/5 测): 之前 regex `([^,]+)` 把 `, ` 真**`分隔符`** 真**也 match`** 当 item
    → tags 重复. 改用先 split 再清, 简单稳.
    """
    out: Dict[str, List[str]] = {}
    for m in _FM_LIST_RE.finditer(fm):
        key = m.group(1)
        if key not in _FM_LIST_FIELDS_UNION:
            continue
        inner = m.group(2).strip()
        if not inner:
            out[key] = []
            continue
        # 先 split by `,` (不在 `[[..]]` 内), 再 strip quotes
        # 简化: regex 抓所有 quoted (带 [[..]] 或纯字符串) 优先, 否则裸 token
        parts = re.findall(r'"([^"]+)"|\'([^\']+)\'', inner)
        items = [a or b for (a, b) in parts]
        if not items:
            # 没 quoted, 退裸 split (e.g. `tags: [a, b, c]`)
            items = [s.strip() for s in inner.split(",")]
        items = [s for s in items if s and s not in ("[", "]")]
        out[key] = items
    return out


def _parse_frontmatter_scalar(fm: str, key: str) -> Optional[str]:
    """抠 scalar 字段 (created / type / title 这种). 跳过 list ([..])."""
    for m in _FM_SCALAR_RE.finditer(fm):
        if m.group(1) == key:
            val = m.group(2).strip()
            if val.startswith("["):
                continue
            return val.strip('"').strip("'")
    return None


def _merge_wiki_file(old_text: str, new_text: str) -> str:
    """P18 (6/5 鸿波) — 重名 entity/concept merge 法 (纯 Python, 不烧 LLM).

    策略:
      frontmatter list 字段 (tags/related/sources/aliases): 旧 ∪ 新 (去重保序)
      frontmatter scalar:
        created: 保留旧 (entity 真**`真**`身份历史不丢`**)
        updated: 用新 (今天日期)
        title / *_type: 用新 (允许 reclassify)
      body: 用新, 旧 body 转 HTML 注释 `<!-- legacy body (created=<旧updated>) -->`
            放文末, 便于人工对照. 多次 update 真**`只保留最近一份 legacy`**.
    """
    old_fm, old_body = _split_frontmatter_body(old_text)
    new_fm, new_body = _split_frontmatter_body(new_text)
    if not new_fm:  # 新 file 没 frontmatter → 异常, 直接返新 (上层 fallback)
        return new_text

    old_lists = _parse_frontmatter_lists(old_fm)
    new_lists = _parse_frontmatter_lists(new_fm)

    # 1. list 字段并集替换到 new_fm
    merged_fm = new_fm
    for field in _FM_LIST_FIELDS_UNION:
        union = list(old_lists.get(field, []))
        for v in new_lists.get(field, []):
            if v not in union:
                union.append(v)
        if not union:
            continue
        # 双引号包每个 item (跟 Generation prompt 规范一致)
        quoted = ", ".join(f'"{v}"' if not v.startswith('"') else v for v in union)
        new_line = f"{field}: [{quoted}]"
        # 替已存的 list 字段; 没的话不动 (let new_fm 真**自然的没**)
        merged_fm = re.sub(
            rf"^{field}:\s*\[.*?\]\s*$",
            new_line,
            merged_fm,
            count=1,
            flags=re.MULTILINE,
        )

    # 2. created 保留旧 (新生成的 created=今天, 改回旧)
    old_created = _parse_frontmatter_scalar(old_fm, "created")
    if old_created:
        merged_fm = re.sub(
            r"^created:\s*.+$",
            f"created: {old_created}",
            merged_fm,
            count=1,
            flags=re.MULTILINE,
        )

    # 3. body: 新 + 旧 legacy 注释
    old_updated = _parse_frontmatter_scalar(old_fm, "updated") or "unknown"
    stripped_old = old_body.strip()
    if stripped_old:
        # 防嵌套: 如果旧 body 已含 legacy 注释, 抽 inner 替, 不层叠
        inner_old = re.sub(
            r"<!--\s*legacy body \(.*?\)\s*-->\n?(.*?)\n?<!--\s*/legacy\s*-->",
            "",
            stripped_old,
            flags=re.DOTALL,
        ).strip()
        if inner_old:
            legacy_block = (
                f"\n\n<!-- legacy body (last updated={old_updated}) -->\n"
                f"{inner_old}\n"
                f"<!-- /legacy -->\n"
            )
            new_body = new_body.rstrip() + legacy_block

    return f"---\n{merged_fm}\n---\n{new_body}"


def _write_wiki_files(catfish_home: Path, files: Dict[str, str]) -> Tuple[int, int]:
    """写 wiki files 真 ~/.catfish/wiki/entities/ + wiki/concepts/. 返 (n_entities, n_concepts).

    P18 (6/5 鸿波): 同 slug 触发 _merge_wiki_file (frontmatter list 并集 +
    保留 created + body 新+ 旧 legacy 注释留底), 不再无脑 overwrite.
    新建 file (不重名) 沿用 overwrite.
    """
    if not files:
        return (0, 0)
    n_entities = 0
    n_concepts = 0
    for rel_path, content in files.items():
        target = catfish_home / rel_path
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            final_content = content
            if target.exists():
                # 重名 — merge mode
                try:
                    old_text = target.read_text(encoding="utf-8")
                    final_content = _merge_wiki_file(old_text, content)
                    logger.info(
                        "catfish-memory wiki merge: %s (frontmatter 并集 + body 新, 旧版 legacy 注释留底)",
                        rel_path,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "catfish-memory wiki merge %s 失败 (fallback overwrite): %s",
                        rel_path, e,
                    )
                    final_content = content
            target.write_text(final_content + ("\n" if not final_content.endswith("\n") else ""), encoding="utf-8")
            if "entities/" in rel_path:
                n_entities += 1
            elif "concepts/" in rel_path:
                n_concepts += 1
        except OSError as e:
            logger.warning("catfish-memory write wiki file %s 失败: %s", rel_path, e)
    return (n_entities, n_concepts)


