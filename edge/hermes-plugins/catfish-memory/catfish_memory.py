"""CatfishMemoryProvider — hermes MemoryProvider 实现.

# 设计 (BL-MEMORY-OWNERSHIP-FIX Phase 2 POC, 5/19 凌晨)
# Week 2 扩展 (BL-GATEWAY-CLEANUP-POST-HERMES, 5/19 晚): 加 on_session_end
# 写路径, 替代 gateway 旧 session_summarizer + memory_distill module.

催 5 个 catfish 边缘数据源, 通过 hermes MemoryProvider 接口 `prefetch()`
每轮注入到 system prompt. 替代 gateway 老 memory_registry 反 pattern.

5 个数据源 (read-only, 全部在 `~/.catfish/`):

| 数据源 | 文件 | 老 gateway provider | 用途 |
|---|---|---|---|
| employee_journal | `~/.catfish/employee_journal.md` + `distilled_facts.md` | EmployeeJournalProvider (priority=60, 5KB) | 员工长期日志 |
| skills_catalog | `~/.catfish/skills/` 元信息 | SkillsCatalogProvider (40, 20KB) | catfish 技能列表 |
| feedback | `~/.catfish/feedback.jsonl` | FeedbackProvider (70, 2KB) | 员工 👍/👎 反馈 |
| session_meta | `~/.catfish/session_meta.json` | SessionMetaProvider (30, 500B) | 时间感 (距上次 N 天) |
| skill_guard | 静态 prompt (条件触发) | SkillGuardProvider (55, 3KB) | 员工提 skill 时铁律 |

# 接口实现

继承 hermes `agent/memory_provider.py:MemoryProvider` ABC. 关键方法:
  - `name`: "catfish-memory"
  - `is_available()`: 检查 `~/.catfish/` 存在
  - `initialize(session_id, **kwargs)`: 记 hermes_home (kwargs 里), 不连任何东西
  - `system_prompt_block()`: 返空 (我们 prompt 内容走 prefetch 动态)
  - `prefetch(query, *, session_id)`: **核心** — 每轮读 5 个数据源拼好返
  - `get_tool_schemas()`: 返 `[]` (tool 走 catfish-tool-bridge, 不在这里重复)
  - 其它 (handle_tool_call / sync_turn / on_session_end / shutdown): no-op

# 性能

read-only 文件读, 同步, < 5ms 总. 不需要 queue_prefetch 后台预热.

文件不存在 / 读失败 → 该数据源跳过, 不影响其它. Catfish 边缘文件本来就允许
缺 (员工没用过 skill 就没 ~/.catfish/skills/).

# 安全 / 红线

- read-only 永远只读 `~/.catfish/` 文件, 不写
- 不上行任何中央 (hermes 拼 prompt 后再发 gateway, gateway 看到的是拼好的)
- LLM 调用瞬间 prompt 含这些 memory 上行上游 LLM, **这是 LLM 调用必然**,
  我们能控制的部分: catfish 中央 PG 永不存 memory 内容
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# hermes 项目导入路径 (plugin 装在 $HERMES_HOME/plugins/catfish-memory/ 后,
# hermes_agent 在 sys.path 里, 这个 import 能 work)
try:
    from agent.memory_provider import MemoryProvider
except ImportError:  # pragma: no cover - dev / 单测时 hermes 不在 path
    # 给单测 / IDE / dev 一个 fallback ABC, 真装 hermes plugin 走上面
    from abc import ABC, abstractmethod

    class MemoryProvider(ABC):  # type: ignore[no-redef]
        """Fallback ABC used when hermes_agent is not importable."""

        @property
        @abstractmethod
        def name(self) -> str: ...

        @abstractmethod
        def is_available(self) -> bool: ...

        @abstractmethod
        def initialize(self, session_id: str, **kwargs: Any) -> None: ...

        def system_prompt_block(self) -> str:
            return ""

        def prefetch(self, query: str, *, session_id: str = "") -> str:
            return ""

        def queue_prefetch(self, query: str, *, session_id: str = "") -> None: ...

        def sync_turn(
            self,
            user_content: str,
            assistant_content: str,
            *,
            session_id: str = "",
        ) -> None: ...

        @abstractmethod
        def get_tool_schemas(self) -> List[Dict[str, Any]]: ...

        def handle_tool_call(
            self, tool_name: str, args: Dict[str, Any], **kwargs: Any
        ) -> str:
            raise NotImplementedError

        def shutdown(self) -> None: ...

        def on_turn_start(
            self, turn_number: int, message: str, **kwargs: Any
        ) -> None: ...

        def on_session_end(self, messages: List[Dict[str, Any]]) -> None: ...


logger = logging.getLogger("catfish.memory.plugin")


#: catfish 边缘数据目录 (默认). 可被 env CATFISH_HOME 覆盖, 给测试用.
_DEFAULT_CATFISH_HOME = Path.home() / ".catfish"


# ── BL-MEMORY-P2-2 (2026-06-03): query 相关性 helper ─────────────────
# 真简单字符级 Jaccard, 真不依赖 jieba (plugin 真 light, jieba 真启动 100ms+).
# 真给 _render_skills_catalog 真 top-K 排序用. 真不调 LLM (省钱, prefetch 每轮跑).
#
# 真效果跟 jieba 差不多 (中文 char-level overlap 真粗但 OK), 真适合短 query
# (用户 message 真平均 < 100 chars).

















# 5/21 拆: 50+ helpers 抽到 catfish_memory_helpers.py (~523 行)
# 5/28 鸿波修: 原注释里说"不能 relative import" 是错的 — hermes plugin loader
# (~/.hermes/hermes-agent/plugins/memory/__init__.py line 240-255) 用
# `spec_from_file_location` 给 plugin 设 `submodule_search_locations=[provider_dir]`,
# 这让 **relative import** 真 work, 但 **absolute import** 找不到 sibling
# (plugin 目录不在 sys.path). 原 `from catfish_memory_helpers import ...` 走
# absolute, hermes 加载时静默失败 (logger.debug, 默认不显示), register() 也
# 跟着挂, hermes 报"loaded but no provider instance found". plugin 5/19 装好
# 9 天没工作的根因之一. 改 relative import 修.
from .catfish_memory_helpers import (  # noqa: F401
    # 函数
    _append_journal,
    _append_to_buffer,
    _buffer_file_path,
    _call_analysis_llm,
    _call_distill_llm,
    _call_generation_llm,
    _call_summarize_llm,
    _catfish_home,
    _clear_buffer,
    _extract_message_pairs,
    _format_journal_entry,
    _gateway_dev_token,
    _gateway_url,
    _list_pending_queries,
    _list_pending_sources,
    _load_plugin_config,
    merge_files_with_llm,
    _mark_distill_run,
    _mark_wiki_queries_ingested,
    _mark_wiki_sources_ingested,
    _parse_generation_output,
    _plugin_config_path,
    _read_buffer,
    _read_full_journal,
    _read_jsonl_tail,
    _read_picker_state_model,
    _read_queries_concat,
    _read_sources_concat,
    _read_state,
    _read_text_safe,
    _should_run_distill,
    _state_file_path,
    _wiki_enabled,
    _write_distilled,
    _write_state,
    _write_wiki_files,
    # module-level 常量
    _BUDGETS,
    _BUFFER_FILENAME,
    _DEFAULT_GATEWAY_URL,
    _DEFAULT_MIN_SUMMARY_INTERVAL_SECONDS,
    _DEFAULT_TURNS_BETWEEN_SUMMARY,
    _DISTILL_CHUNK_CHARS,
    _DISTILL_COOLDOWN_SECONDS,
    _DISTILL_PROMPT,
    _LLM_HTTP_TIMEOUT,
    _MAX_MESSAGES_PER_SUMMARY,
    _PLUGIN_CONFIG_FILENAME,
    _STATE_FILENAME,
    _SUMMARIZE_DEDUP_SECONDS,
    _SUMMARIZE_PROMPT,
)

# 8/15 拆分: prefetch 的十几段渲染搬到 catfish_memory_render.py, 用 mixin 挂回来。
# 选 mixin 不选模块级函数, 是为了不动 prefetch 里 14 处 `self._render_xxx()`
# 和测试里的 `provider._render_schema()` —— 详见那个文件的模块 docstring。
try:
    from .catfish_memory_render import _RenderMixin
    from .catfish_memory_sections import _SectionsMixin
    from .catfish_memory_expense import (  # noqa: F401
        _ExpenseMixin,
        # 下面 5 个是 re-export: tests/test_expense.py 直接
        # `from catfish_memory import _expense_read_all, ...`
        _expense_append_record,
        _expense_gen_id,
        _expense_parse_date_to_iso,
        _expense_read_all,
        _expense_summarize_window,
    )
    from .catfish_memory_distill import (  # noqa: F401
        _DistillMixin,
        # re-export: dream_cli.py:113 走 `mem_mod.run_distill_for_dream_engine`
        run_distill_for_dream_engine,
    )
    from .catfish_memory_tools import _ToolsMixin
except ImportError:  # 独立脚本模式 (无父包)
    from catfish_memory_render import _RenderMixin
    from catfish_memory_sections import _SectionsMixin
    from catfish_memory_expense import (  # noqa: F401
        _ExpenseMixin,
        _expense_append_record,
        _expense_gen_id,
        _expense_parse_date_to_iso,
        _expense_read_all,
        _expense_summarize_window,
    )
    from catfish_memory_distill import (  # noqa: F401
        _DistillMixin,
        run_distill_for_dream_engine,
    )
    from catfish_memory_tools import _ToolsMixin


class CatfishMemoryProvider(
    _SectionsMixin, _RenderMixin, _ExpenseMixin, _DistillMixin, _ToolsMixin,
    MemoryProvider,
):
    """聚合 catfish 5 个边缘数据源的 hermes MemoryProvider."""

    def __init__(self) -> None:
        self._session_id: str = ""
        self._hermes_home: Optional[Path] = None
        self._catfish_home_cached: Optional[Path] = None
        self._initialized = False

        # ── sync_turn 节流状态 (BL-MEMORY-SYNC-TURN-REFACTOR Day 1, 5/20) ──
        # 替代 on_session_end (hermes 在 per-chat 不调那个 hook, run_agent.py:16078).
        # sync_turn 每轮 hermes 调一次, 我们累积到 buffer, 满足节流条件再触发 summary.
        #
        # 节流: 每 N 轮 (default 5) OR 距上次 summary >= 30min, 任一满足.
        # 触发后清 buffer + 重置 counter, 防止下次又把同一段内容总结一遍.
        #
        # _buffer_lock 保护多线程访问 (hermes 可能在不同 thread 调 sync_turn,
        # 或者 sync_turn 主线程跟后台 summary thread 并发改 buffer).
        self._turn_buffer: List[Tuple[str, str]] = []
        self._turns_since_last_summary: int = 0
        self._last_summary_ts: float = 0.0  # epoch seconds; 0 = 没跑过
        self._buffer_lock = threading.Lock()

    @property
    def name(self) -> str:
        return "catfish-memory"

    # ── 必须实现 ─────────────────────────────────────────

    def is_available(self) -> bool:
        """`~/.catfish/` 存在即视为 available. 5 个子数据源都允许个别缺.

        catfish 安装时建 `~/.catfish/`, 缺 = catfish 没装, 这个 plugin 不该激活.
        """
        return _catfish_home().is_dir()

    def initialize(self, session_id: str, **kwargs: Any) -> None:
        """记 session_id + hermes_home. 不连任何外部资源.

        kwargs 关心的:
          - hermes_home (str): hermes 自己的 home (跟 ~/.hermes/ 可能不同, 比如
            子 profile / 测试 profile). 我们 catfish 不依赖 hermes_home, 只用
            CATFISH_HOME env / `~/.catfish/`, 但记下供 debug.

        BL-MEMORY-SYNC-TURN-REFACTOR (5/20): 初始化 _last_summary_ts = now, 防止
        plugin 第 1 轮 sync_turn 撞 "elapsed >= min_interval" trivially trigger
        (没跑过 summary 时 _last_summary_ts=0 → elapsed=inf → 第 1 轮就触发,
        违背"30min 间隔"语义). 用 now 作 baseline, 之后 elapsed 才有意义.
        """
        self._session_id = session_id
        hh = kwargs.get("hermes_home")
        if isinstance(hh, str):
            self._hermes_home = Path(hh)
        self._catfish_home_cached = _catfish_home()
        self._last_summary_ts = time.time()  # baseline, 防 trivially trigger
        self._initialized = True
        logger.info(
            "catfish-memory initialize: session=%s catfish_home=%s hermes_home=%s",
            session_id, self._catfish_home_cached, self._hermes_home,
        )

    def system_prompt_block(self) -> str:
        """BL-CATFISH-WIKI-MODE P3.3.13 (6/4): system prompt 禁编造 rule 强 instruction.

        6/4 17:39 chat qwen_v3_5_122b 严重 hallucinate (陈淡孜儿子 / Demo User /
        公司资质管理办法修订版). prefetch user msg 末尾真 rule LLM 真ignore真.
        改用 system_prompt_block 真 static system prompt — LLM 严守 rule真.

        放 wiki title list 在 system prompt 真LLM 真真每轮**看精确真
        reference target.
        """
        if not self._initialized:
            return ""
        catfish_home = self._catfish_home_cached or _catfish_home()
        wiki_dir = catfish_home / "wiki"
        if not wiki_dir.is_dir():
            return ""

        entities: list[str] = []
        concepts: list[str] = []
        try:
            ent_dir = wiki_dir / "entities"
            if ent_dir.is_dir():
                for f in sorted(ent_dir.glob("*.md")):
                    entities.append(f.stem)
            con_dir = wiki_dir / "concepts"
            if con_dir.is_dir():
                for f in sorted(con_dir.glob("*.md")):
                    concepts.append(f.stem)
        except OSError:
            return ""

        if not entities and not concepts:
            return ""

        lines = [
            "## 🚨 catfish 信息源 + 禁编造 铁律 (catfish-memory plugin P3.3.13)",
            "",
            "回答员工有关 **业务 / 人物 / 项目 / 决策 / 事件** 真问题时, **必须**真"
            "依据下面这些**真实信息源**:",
            "",
            "1. USER PROFILE** (~/.hermes/memories/USER.md) — 员工身份/偏好/昵称",
            "2. **MEMORY.md** (~/.hermes/memories/MEMORY.md) — 项目真真真fact**真",
            "3. **employee_journal.md** (~/.catfish/employee_journal.md) — 时间线日志",
            "4. **distilled_facts.md** (~/.catfish/distilled_facts.md) — 24h 蒸馏长期",
            "5. **catfish wiki** (~/.catfish/wiki/) — 见下面 title list, 用 [[标题]] reference",
            "",
            "### ❌ 禁止 行为 (严守, 违反直接真真扣信任分):",
            "",
            "- 不允许编造**没在上面 5 个源里出现**人物 / 项目代号 / 决策 / 事件.",
            "- 没记录就**真直接说 \"我没在 catfish 记忆里找到这条\"**, 不要靠 training prior 编.",
            "- catfish / 鲶鱼 / 小鲶 / 胖胖 是 **AI 副手 + 员工个人开源项目**, 不真"
            "真真业务 entity (跟周报 / 汇报 / 待办 / 工作总结 不沾边).",
            "",
            "### ✅ wiki 已有 title (chat reference真用 `[[标题]]`):",
            "",
        ]
        if entities:
            lines.append(f"**实体 ({len(entities)})**: " + " · ".join(f"`[[{n}]]`" for n in entities[:50]))
        if concepts:
            lines.append(f"\n**概念 ({len(concepts)})**: " + " · ".join(f"`[[{n}]]`" for n in concepts[:50]))
        lines.append("")
        lines.append("员工 chat 提到上述 title 直接reference真, 不要重复抽真.")

        return "\n".join(lines)

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """不暴露 tool — catfish tool 走 catfish-tool-bridge plugin, 不重复.

        如果未来要让 LLM 主动调"刷新员工 journal" 之类, 在这里加 tool schema.
        """
        return []

    # ── 核心: prefetch ───────────────────────────────────

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """每轮读 5 个数据源拼一段 markdown 注入 system prompt.

        query 是当前 user message, 可用来按相关性筛选 (POC 阶段不做, 全 inject).
        失败的子数据源跳过, 不影响其它.

        返空字符串 = 这轮没有 memory context (hermes 会 skip 注入).

        # P3.5.5 (6/16 鸿波) advisor sparse mode

        catfish-advisor 流程在 user prompt 顶部加 marker `<!-- catfish:advisor-sparse -->`.
        plugin 检测到 → 只返核心 4 段 (purpose + discipline + safety_redline + session_meta,
        ~3-4KB), 砍其余 7 段 (schema/journal/wiki/skills_catalog/strategic_docs/feedback/
        skill_guard, ~30KB).

        真因: catfish-advisor 自己 user prompt 已经注入员工画像 / 长期画像 (distilled) /
        近期事项 (memory) / todos / emails. plugin 全量 prefetch 38KB 跟它**重复**, 而且
        advisor 不调 catfish skill (不需要 skills_catalog), 不引用 wiki / strategic_docs.
        实测鸿波 6/16 advisor Qwen 内网 prompt 44K 跑 100-200s, sparse 后预期 20-30s.

        chat / dream / 别的路径 (没 marker) 走全量, 行为不变.
        """
        if not self._initialized:
            return ""

        # P3.5.5: sparse mode 检测
        is_advisor_sparse = bool(query) and "catfish:advisor-sparse" in query

        catfish_home = self._catfish_home_cached or _catfish_home()
        sections: List[str] = []

        # BL-CATFISH-WIKI-MODE P0.1 (6/3): 借鉴 Karpathy LLM Wiki + llm_wiki 真
        # purpose 概念. 在所有 memory 内容最前注入"员工身份+目的"提示, 让 LLM 真
        # 知道当前服务的员工是谁、做什么场景, 不再当成 generic chatbot.
        sections.append(self._render_purpose())

        # BL-CATFISH-WIKI-MODE P0.2 (6/3): schema 注入 — 显式 5 kind router 规则
        # + journal/distilled/USER/MEMORY 分工. 借鉴 llm_wiki "AUTHORITATIVE" 标记,
        # 跟 _render_memory_discipline 互补: discipline 教写什么不该写, schema 教
        # 该写到哪里去.
        # P3.5.5 sparse: 跳过 — advisor 不写 memory, 不需要 router schema.
        if not is_advisor_sparse:
            sections.append(self._render_schema())

        # 0. BL-MEMORY-DISCIPLINE (5/24 鸿波"hermes memory 70% 内容跑偏"): 在所有
        # memory 内容前面注入"写入纪律"提示, 让 LLM 调 memory_update 前自查.
        # 不强制 enforce (hermes 端没钩子), 但 LLM 看到这段会显著降低乱写.
        # 配合 hm 脚本做反向治理 — 双管齐下.
        sections.append(self._render_memory_discipline())

        # BL-LLM-NO-TERMINAL-BYPASS-V2 (6/3): LLM 自主越权信号触发 (chat verbatim:
        # '改调 terminal 绕过 execute_code 审批'). hermes hook 无 parent_tool 无法
        # 区分 LLM 直调 vs skill 内部, 不能一刀切 block. 注入红线 prompt 让 LLM
        # 自查 — 禁绕 sandbox.
        sections.append(self._render_safety_redline())

        # P3.5.211 (7/10 鸿波 catch, 通过 hermes request dump 铁证):
        # Companion 塞的 buildTaskSystemPrompt (含 P3.5.209/210 事实为准段) 100%
        # 被 hermes API server 丢弃, LLM 从没看到. 军规必须放在 hermes 内部
        # SystemPromptProvider hook 走 prefetch 才能真正生效. 这段影响所有走
        # hermes 的 chat (task chat / 工作台 chat), advisor 走 gateway 不受影响
        # (它有 P3.5.206 SYSTEM_PROMPT 里的同款约束).
        sections.append(self._render_fact_first_discipline())

        # 1. session_meta — 时间感 (距上次 N 天)
        meta = self._render_session_meta(catfish_home)
        if meta:
            sections.append(meta)

        # 1b. P3.5.78 (6/22 鸿波): expense summary — 注入最近收支 (今日/本月/累计).
        # 跟 session_meta 同款轻量化, 让 LLM 看 prefetch 知道 expense 数据存在,
        # 员工问 "今天花了多少" 直接基于 prefetch 答, 不需要 query tool.
        expense_summary = self._render_expense_summary(catfish_home)
        if expense_summary:
            sections.append(expense_summary)

        # 1c. P3.5.203 (β 7/9 鸿波 5 层记忆 audit): task status 从 advisor_cache
        # (P3.5.202 C 方案 taskChatSummaries.chatStatus) 拉过来, 让 chat /
        # proactive / briefing 三处主 LLM 都拿到员工对具体事的最近表态.
        # sparse mode (advisor) 也带 — advisor 自己走 filterResolvedTasks 用同款
        # 数据, 但这里注入让主 LLM 在写 mainTasks 时就有语义信号, 不是 LLM 出完
        # 再 client-side hard filter (双保险).
        task_status = self._render_task_status(catfish_home)
        if task_status:
            sections.append(task_status)

        # P3.5.5 sparse: 下面 7 段是 advisor 不需要的 (employee_journal / wiki /
        #   skills_catalog / strategic_docs / feedback / skill_guard / schema 已上面跳过).
        #   advisor 自己 user prompt 已注入 distilled + memory + todos + emails 完整上下文.
        if not is_advisor_sparse:
            # 2. employee_journal — 员工长期记忆
            journal = self._render_employee_journal(catfish_home)
            if journal:
                sections.append(journal)

            # 2b. BL-CATFISH-WIKI-MODE P3.3.11 (6/4): wiki summary —
            # 列 wiki/entities + concepts top hub 让 LLM chat 时知道
            # 员工 wiki 已有 entity / concept 避免重复抽 + reference 精确
            wiki = self._render_wiki_summary(catfish_home)
            if wiki:
                sections.append(wiki)

            # 3. skills_catalog — 可用 catfish 技能 (BL-MEMORY-P2-2: query top-K 筛)
            skills = self._render_skills_catalog(catfish_home, query=query)
            if skills:
                sections.append(skills)

            # 3b. BL-STRATEGIC-DOC-SYNC (6/7): 战略 / 设计 doc 注入 (manifesto /
            # patent landscape / moat assessment / capability gaps 等). 跟
            # _render_skills_catalog 同 pattern: query 空 → 全注入字母序, query 有
            # → Jaccard top-K cap 5KB. 文件在 ~/.catfish/strategic_docs/*.md.
            # 跟 wiki/concepts/ 分开 (避免污染 catfish-memory distill 真 entity 抽取).
            strategic = self._render_strategic_docs(catfish_home, query=query)
            if strategic:
                sections.append(strategic)

            # 4. feedback — 员工 thumbs 反馈
            feedback = self._render_feedback(catfish_home)
            if feedback:
                sections.append(feedback)

            # 5. skill_guard — 员工提 skill 时铁律 (条件触发)
            guard = self._render_skill_guard(query)
            if guard:
                sections.append(guard)

        if not sections:
            return ""
        result = "\n\n".join(sections)

        # P3.5.5 sparse mode 单独 log, 跟原 log 区分
        if is_advisor_sparse:
            logger.info(
                "catfish-memory prefetch SPARSE (advisor): %d chars (砍 7 段 ~30KB, 留核心 4 段)",
                len(result),
            )
            return result
        # BL-CATFISH-WIKI-MODE diag (6/4 凌晨): 6 小时 audit 没找到 4 marker 注入,
        # 直接调 plugin prefetch 返 10395c 全 ✓, 但 dump 真 user message 0 marker.
        # 加 log 看 runtime prefetch 真实际返值 — 看是不是 hermes 注入 path 真问题.
        # 24h 观察后删掉这行 log 真.
        try:
            logger.info(
                "catfish-memory prefetch: returning %d chars "
                "(markers: purpose=%s schema=%s disc=%s safe=%s, query_len=%d, session=%s)",
                len(result),
                '员工身份与目的' in result,
                'catfish memory schema' in result,
                'memory 写入纪律' in result,
                '安全红线' in result,
                len(query or ""),
                session_id or self._session_id or "?",
            )
        except Exception:
            pass
        return result








    def _tick_session_meta(self) -> None:
        """BL-SESSION-META-PLUGIN-TAKEOVER (5/26): plugin 接管 session_meta.json 写.

        老逻辑: gateway/session_meta.py 的 tick() 在 /v1/chat/completions 完成后调,
        gateway 跑员工 mac 写 ~/.catfish/session_meta.json. SaaS 化后 gateway 跑客户
        机房, 写不到员工 mac → 时间感段永远渲染空 (last_chat_iso 字段永远不更新).

        新逻辑: plugin 跑员工 mac (跟 gateway 不同进程, 在 hermes 进程里), 写自己 fs
        合规. sync_turn hook 已经 per-turn 触发, 这里跟着 tick 不需要新触发器.

        字段对齐 _render_session_meta() 读的格式:
          {
            "last_chat_iso": "<UTC isoformat>",
            "today_count": <int>,
            "today_date":  "YYYY-MM-DD"
          }
        跨天 today_count reset 1, 同天 += 1. 写挂日志警告不抛 (不能让 sync_turn 失败).
        """
        home = self._catfish_home_cached or _catfish_home()
        path = home / "session_meta.json"
        now = datetime.now(timezone.utc).astimezone()
        today_str = now.date().isoformat()
        try:
            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    if not isinstance(data, dict):
                        data = {}
                except (OSError, json.JSONDecodeError):
                    data = {}
            else:
                data = {}
            if data.get("today_date") == today_str:
                data["today_count"] = int(data.get("today_count", 0)) + 1
            else:
                data["today_count"] = 1
                data["today_date"] = today_str
            data["last_chat_iso"] = now.isoformat()
            # 原子写: tmp + rename, 避免半截写挂破坏 JSON
            tmp = path.with_suffix(".json.tmp")
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(path)
        except OSError as e:
            logger.warning("catfish-memory _tick_session_meta 失败: %s", e)









    # ── 可选 hook (我们 read-only, 大部分 no-op) ─────────

    def shutdown(self) -> None:
        logger.info("catfish-memory shutdown: session=%s", self._session_id)







    # ── P3.5.78 (6/22 鸿波): kind=expense 路由 — 收支记账 ──────────────
    #
    # 真因 audit (鸿波 6/22 catch P3.5.75 后真聊 "19号加油300" 跑偏):
    #   P3.5.75 把 bookkeep 做成独立 plugin (bookkeep_add/query/summarize tool).
    #   但 catfish-memory `_render_schema` 5 kind 决策树是 AUTHORITATIVE 注入 LLM,
    #   LLM 看 "19号加油300" → 命中第 4 条 (会话事件) → journal, **看不见 bookkeep
    #   范式存在**. P22 patch 把 bookkeep_* pin 到 _HERMES_CORE_TOOLS 仍解决不了 —
    #   schema prime 比 tool list 强势.
    #
    # 治本: bookkeep 整进 memory 第 6 kind, 跟 todo/journal 同款"分流到专用文件".
    # 决策树自然命中 expense, 不再硬编码 SOUL.md / bookkeep description.
    #
    # jsonl schema 跟 P3.5.75 完全兼容 (你昨晚 bk_20260622_224824_9868 那条保留):
    #   {"id": "bk_<YYYYMMDD>_<HHMMSS>_<4hex>",
    #    "ts": "<ISO8601 local>",
    #    "kind": "支出"|"收入",    ← LLM schema 用 direction, jsonl 仍叫 kind
    #    "amount": <float CNY>,
    #    "category": "<8 默认或自填>",
    #    "note": "<可空>"}

    #: 默认 8 类 — LLM 可自填新的, 不强校验
    _EXPENSE_DEFAULT_CATEGORIES = [
        "餐饮", "交通", "购物", "工资", "医疗", "转账", "房租", "其他",
    ]




    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        """hermes session 真结束 hook — force flush buffer 剩余对话 (兜底).

        session 真结束的场景 (run_agent.py:16078 注释):
          - CLI atexit
          - `/reset` / `/new` 命令
          - Context compression
          - Gateway session expiry (TTL)

        on_session_end 触发时, buffer 里可能还有没满 N 轮的对话, 这里 force 总结一次,
        不丢最后一段. 然后清 buffer 状态准备下一个 session.

        messages 参数是 hermes 给的全 session messages, 我们**不用**它 — 用 buffer
        里实际 sync_turn 累积的内容 (更精准, 跟节流逻辑一致).
        """
        try:
            self._force_flush()
        except Exception as e:  # noqa: BLE001
            logger.warning("catfish-memory on_session_end force flush 失败: %s", e)











# ──────────────────────────────────────────────────────────────────────
# P3.5.1.2 (6/15 鸿波 Dream Engine): module-level entry — 员工主动触发蒸馏.
#
# 设计核心 (鸿波 6/15 拍方案 D):
#   - 复用 plugin 现有 distill 算法 (_call_distill_llm + _write_distilled + _mark_distill_run),
#     不重复实现.
#   - Model 是显式参数 (Dream Engine 用 companion picker 当前选的 model), 不读 yaml/env —
#     跟 instance method _get_summarize_model() 行为分离, 保留 yaml/env 给 plugin auto path.
#   - force=True 跳 24h cooldown — 员工"现在就想跑"时不该被 cooldown 拦.
#   - 跑完写 _mark_distill_run → plugin auto path 的 _should_run_distill 看 cooldown
#     state 自然 24h skip. **零冲突, 不撞** (这是方案 D 跟 C 的关键差异).
#   - progress_cb 透传给 _call_distill_llm (后者 P3.5.1.1 已支持). dream_cli 用它
#     stdout 流式 JSON 给 companion Tauri 端转 event 显进度.
#
# 调用方: catfish-memory/dream_cli.py (P3.5.1.3) 通过 asyncio.run() 跑.
# 不调 instance, 不依赖 hermes — 单独 CLI 直跑.
# ──────────────────────────────────────────────────────────────────────




# 定时蒸馏不在这里 —— 见 companion-app/src-tauri/src/services/distill_scheduler.rs
#
# 8/4 一度把定时器写在 CatfishMemoryProvider.__init__ 里, 那是**死代码**:
# 实测 ~/.hermes/config.yaml 的 plugins.enabled 里根本没有 catfish-memory,
# Companion 全部 Rust 代码也没有一处把它当 plugin 注册 —— 每一处都是把它当
# 源码目录用来 spawn dream_cli.py。所以 provider 在这台机器上从没被构造过,
# sync_turn / on_session_end 这些钩子一次都没被调过。
# 旁证: ~/.catfish/.catfish_memory_buffer.jsonl 最后修改停在 7/18。
#
# 唯一会被执行到的形态是 Companion spawn 的 dream_cli.py 子进程, 定时器因此
# 必须在 Companion 那边。留这段注释是为了下次有人想在这里加定时器时先看到。
