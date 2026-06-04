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

_STOPCHARS = set("的了是在我你他她我们你们和跟也都就这那有没不,.,。?!、 \n()—,—:;\"'")


def _query_token_set(text: str) -> set:
    """字符级 set (去停用字), 真返用作 Jaccard 输入. 真不分词省 jieba 启动."""
    if not text:
        return set()
    return {c for c in text if c not in _STOPCHARS and c.strip()}


def _jaccard_similarity(a: set, b: set) -> float:
    """Jaccard |a ∩ b| / |a ∪ b|. 真空返 0."""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union > 0 else 0.0


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
    _load_plugin_config,
    _mark_distill_run,
    _parse_generation_output,
    _plugin_config_path,
    _read_buffer,
    _read_full_journal,
    _read_jsonl_tail,
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

class CatfishMemoryProvider(MemoryProvider):
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
        """
        if not self._initialized:
            return ""

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

        # 1. session_meta — 时间感 (距上次 N 天)
        meta = self._render_session_meta(catfish_home)
        if meta:
            sections.append(meta)

        # 2. employee_journal — 员工长期记忆
        journal = self._render_employee_journal(catfish_home)
        if journal:
            sections.append(journal)

        # 3. skills_catalog — 可用 catfish 技能 (BL-MEMORY-P2-2: query top-K 筛)
        skills = self._render_skills_catalog(catfish_home, query=query)
        if skills:
            sections.append(skills)

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

    # ── 5 个数据源 render helper ──────────────────────────

    def _render_purpose(self) -> str:
        """BL-CATFISH-WIKI-MODE P0.1 (2026-06-03): 员工身份 + catfish 服务目的.

        借鉴 Karpathy LLM Wiki gist 的 purpose.md 概念 — 让 LLM 知道当前服务的
        员工是谁、做什么场景, 不再当 generic chatbot. llm_wiki 实现 (ingest.ts
        buildAnalysisPrompt) 真把 purpose 作 string 注入 prompt 末尾, 标 "for
        context", catfish 跟 _render_memory_discipline 同套路注入到 user message.

        # 为啥重要
        - LLM 不知道员工身份 → 回答时按 generic 写, 不带行业知识 / 公文体
        - 不知道服务场景 → 工具选错 (该走 catfish-weekly-report 时去 generic markdown)
        - 不知道员工偏好 → 周报里塞 catfish 个人开源项目 (已在 USER.md 修)

        # 内容来源
        鸿波亲笔写, 描述自己是谁 + 用 catfish 做啥 + 不做啥. 当前 v1 是占位 —
        鸿波下次拍板时可改 catfish-memory/purpose.txt (TODO P1 真接配置文件).
        """
        return (
            "## 🎯 员工身份与目的 (purpose)\n\n"
            "你服务的员工: **陈鸿波** (FFCS 数字鲶鱼项目发起人, 中电福富 + 法律部 / 企业发展与风控部).\n\n"
            "**主要工作场景**:\n"
            "- 企业资质管理 (高企 / ITSS / ISO 27001 / CMMI / CSMM / 数据安全)\n"
            "- 资质评估 + 申报 (含咨询公司参与决策)\n"
            "- 公司向上汇报 (周报 / 月度通报 / 立项材料 / 项目可研)\n"
            "- ISO 现场审核 / 评审准备\n"
            "- 跨部门沟通 (中电福富部门 + 九地市分公司)\n\n"
            "**员工偏好**:\n"
            "- 跳过铺垫直接交付结果, 拒绝估算 (要精确数据)\n"
            "- 偏好杂志风 / 花叔风 PPT, 公文体严谨\n"
            "- 文件输出到 ~/.catfish/output/<日期>/, 不再问存哪\n"
            "- 个人开源项目 catfish/鲶鱼**不要**进周报 / 汇报 / 待办 (是个人事, 不是公司工作)\n\n"
            "**不该做的事**:\n"
            "- 不要自动跑周报 (员工每周手动提)\n"
            "- 不要绕 execute_code 审批走 terminal (见安全红线)\n"
            "- 不要乱写 MEMORY.md 当 skill spec 用 (见 memory 写入纪律)\n"
        )

    def _render_schema(self) -> str:
        """BL-CATFISH-WIKI-MODE P0.2 (2026-06-03): catfish memory schema (AUTHORITATIVE).

        借鉴 llm_wiki buildGenerationPrompt 的 "Project Schema and Routing
        (AUTHORITATIVE)" 标记. schema 教 LLM 写到哪里去 (路由规则),
        memory_discipline 教写什么不该写 (内容纪律). 互补.

        当前 schema 显式列 5 kind router + 4 个长期存储位置的分工.
        """
        return (
            "## 📐 catfish memory schema (AUTHORITATIVE)\n\n"
            "**5 kind memory router** (调 `memory` tool 时 `kind` 必填):\n\n"
            "| kind | 路由到哪 | 用来存什么 |\n"
            "|---|---|---|\n"
            "| `identity` | `~/.hermes/memories/USER.md` (cap 3500 chars) | 员工本人 — 身份/偏好/习惯/昵称/关系 |\n"
            "| `project_fact` | `~/.hermes/memories/MEMORY.md` (cap 5000 chars) | 项目/技术常量 — 资质评估流程/工具配置/平台特征 |\n"
            "| `workflow` | hint → 调 `catfish_propose_skill` | 多步流程 — 有 step 序列的全部 |\n"
            "| `journal` | `~/.catfish/employee_journal.md` (append) | 本次会话总结 / 已发生事件 / pending TODO |\n"
            "| `todo` | hint → 调 `catfish_reminder_create` | 带 deadline 的任务 (会写 Reminders.app) |\n\n"
            "**4 个长期存储分工**:\n\n"
            "- **USER.md (identity)** — 员工本人, 一年后还成立. 例: 偏好直接输出不要确认.\n"
            "- **MEMORY.md (project_fact)** — 项目/技术常量, 跨 session 稳定. 例: 资质评估流程.\n"
            "- **employee_journal.md (chronological)** — 时间线日志, append-only. 格式严格:\n"
            "  `## [YYYY-MM-DD HH:MM] kind | title` 一行 (parseable by `grep '^## \\['`).\n"
            "- **distilled_facts.md (LLM 蒸馏)** — 自动从 journal 蒸馏的长期记忆,\n"
            "  每 24h 由 catfish-memory plugin 跑. 员工只读不写.\n\n"
            "**SKILL.md (~/.hermes/skills/<name>/SKILL.md)** — 真正的 workflow / spec\n"
            "/ 触发词住这里, **不要**写进 MEMORY.md.\n\n"
            "**路由决策树** (调 memory 前自查):\n"
            "1. 员工本人的事? → identity → USER.md\n"
            "2. 项目/技术常量? → project_fact → MEMORY.md\n"
            "3. 多步流程? → workflow → 改调 catfish_propose_skill\n"
            "4. 这次会话的事 / 已发生事件? → journal → employee_journal.md\n"
            "5. 带 deadline 的任务? → todo → 改调 catfish_reminder_create\n"
            "6. 拿不准 → 80% 概率属于 journal, 不属于 identity/project_fact\n"
        )

    def _render_safety_redline(self) -> str:
        """BL-LLM-NO-TERMINAL-BYPASS-V2 (2026-06-03): 安全红线 prompt.

        # 真触发场景
        6/3 下午员工 chat 真生产: LLM 真自己说 "execute_code 卡住, 我换个方案:
        直接用 terminal 调 pdftotext / python -c, 不经过 execute_code 审批流程."

        真**LLM 真自主越权信号** — 真 catfish 真所有代码执行 (Python / bash) 真该走
        execute_code → sandbox-exec / nsjail + 员工审批. terminal 真 hermes builtin
        默认 local 真直接 host 跑, 真**绕**真审批 + sandbox.

        # 真为啥不 hook block (v1 撤回)
        hermes pre_tool_call hook 真**无** parent_tool 真字段, 真无法区分 LLM 直调
        vs catfish_run_skill / skill 内部真用 terminal. 真一刀切 block 真**误伤**
        员工真合法 skill 路径. 改成注入红线 prompt 让 LLM 真**自查**.

        # 真根治在别处
        - BL-TOOLS-SANITIZER-DROP-DEPRECATED (6/3 BACKLOG): 修 execute_code 真
          tool_call/tool_describe 死循环, LLM 真有正路可走真没动机绕.
        - audit log post_tool_call 真 LLM 直调 terminal 真触发警告 entry (后续).
        """
        return (
            "## 🚨 安全红线 (catfish 强约束)\n\n"
            "你**永远不要**用 `terminal` 工具跑代码 (Python / bash / shell).\n\n"
            "**理由**: terminal 真 hermes builtin 默认 local 真**直接** host 上跑命令, "
            "绕过 catfish 真 sandbox-exec / nsjail + 员工审批. catfish 真红线 — "
            "LLM 真所有代码执行真**必须**走 sandbox + 员工 review.\n\n"
            "**正路**:\n"
            "- 跑代码: `execute_code(lang='python'|'bash', code='...')` → catfish sandbox\n"
            "- 读 PDF / Excel / docx: `execute_code` 真里调 pypdf / openpyxl / python-docx\n"
            "- 查文件: `read_file` / `glob` / `grep`\n"
            "- 浏览器自动化: `catfish_browser_*` 四件套 (goto/click/fill/snapshot)\n\n"
            "**禁用 terminal 真场景**:\n"
            "- ❌ 'execute_code 卡住, 改 terminal 绕过审批' — 真**主动越权**, 拒\n"
            "- ❌ 'terminal 调 pdftotext 直接读' — 真**绕 sandbox**, 拒\n"
            "- ❌ 'terminal 调 curl 拉数据' — 改 `execute_code(bash)` 或 `web_fetch`\n\n"
            "**唯一合法场景**: 员工真**自己**真本机 shell 跑命令 (员工自己输, 不是你调).\n"
        )

    def _render_memory_discipline(self) -> str:
        """BL-MEMORY-DISCIPLINE (5/24): hermes memory_update 写入纪律.

        # 真问题
        hermes 原生 memory_update tool 没硬约束, LLM 倾向于"对未来的我有用就写".
        结果鸿波实盘 USER.md + MEMORY.md 21 条 entry, **70% 跑偏**:
          - 5 条 skill 完整 spec (该进 ~/.hermes/skills/<name>/SKILL.md)
          - 6 条 session log / 已发生事件 (该进 ~/.catfish/employee_journal.md)
          - 3 条带 deadline 的具体任务 (该进 TODO)
          - 1 条 USER 内容写到了 MEMORY 名下 (员工身份 vs 项目知识混淆)
        累积导致 MEMORY.md 单 entry 撞 2401 chars (> 2200 cap), 仪表盘视觉满.

        # 这段干嘛
        在 system prompt 顶部硬注入"决策树", 让 LLM 调 memory_update 前自查 4 个反例.
        不强制 enforce (hermes 端无 hook), 靠 LLM 看到这段后改判定. 实战经验: prompt
        约束对 reasoning model 命中率 70%+.

        # 配套治理
        - 反向: ~/person_task/catfish/scripts/hermes-memory-cleanup.py (hm 脚本)
          员工每周手扫一次, 删 LLM 误写进去的.
        - 长期: catfish_memory_audit cron skill (未来)
        """
        return (
            "## ⚙ hermes memory 写入纪律 (catfish 强约束)\n\n"
            "调 `memory_update` (写 USER.md / MEMORY.md) 前必须自查:\n\n"
            "**✅ 该写的, 同时满足这 3 条:**\n"
            "1. 跨 session 稳定 — 一年后还成立 (员工身份/偏好/技术常量)\n"
            "2. 没有 deadline / 不会过期\n"
            "3. 不是 skill 的工作流, 不是会话总结, 不是单次任务状态\n\n"
            "**❌ 不该写的 (即使有 user 价值也别写 memory, 走下面对的地方):**\n"
            "- skill 完整 spec / 输出格式 / 触发词 / workflow → `~/.hermes/skills/<name>/SKILL.md`\n"
            "- 本次会话的总结 / 进度 / pending TODO → `~/.catfish/employee_journal.md`\n"
            "- 带具体日期的任务 (5/30 截止之类) → TODO 工具 / journal\n"
            "- 已发生事件的状态变更 (X 会议结束 / Y 已完成) → journal\n"
            "- 'next time when X is available, do Y' 类待办 → journal / issue\n"
            "- skill 创建/更新的事件记录 (filesystem 自己有) → 不写\n\n"
            "**target 怎么选:**\n"
            "- `target=user`: 关于员工**这个人**的事 (偏好/习惯/身份/关系)\n"
            "- `target=memory`: **项目/技术**事实 (API 字段含义、output 路径约定、客户机房 IP)\n"
            "- 拿不准 → 80% 概率属于 journal, 不属于 memory\n\n"
            "**长度纪律:**\n"
            "- USER.md 每 entry ≤ 1375 字符, MEMORY.md ≤ 2200. 接近上限的就拆 / 砍.\n"
            "- 写得超长的几乎都是把 spec / workflow 当 memory 写, 应改去 SKILL.md.\n"
        )

    def _render_session_meta(self, catfish_home: Path) -> str:
        path = catfish_home / "session_meta.json"
        try:
            if not path.exists():
                return ""
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return ""
            last = data.get("last_chat_iso")
            if not last:
                return ""
            return f"## 🕒 时间感\n\n上次聊天: {last} (catfish session_meta)"
        except (OSError, json.JSONDecodeError) as e:
            logger.debug("session_meta render 失败 %s", e)
            return ""

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

    def _render_employee_journal(self, catfish_home: Path) -> str:
        # 优先 distilled_facts.md (LLM 蒸馏过), fallback employee_journal.md
        distilled = _read_text_safe(
            catfish_home / "distilled_facts.md",
            _BUDGETS["employee_journal"],
        )
        if distilled.strip():
            return f"## 📝 员工长期记忆 (catfish distilled)\n\n{distilled}"
        raw = _read_text_safe(
            catfish_home / "employee_journal.md",
            _BUDGETS["employee_journal"],
        )
        if raw.strip():
            return f"## 📝 员工长期日记 (catfish)\n\n{raw}"
        return ""

    def _render_skills_catalog(
        self, catfish_home: Path, query: str = "",
    ) -> str:
        """BL-MEMORY-P2-4 (2026-06-03): dual-path fallback.
        BL-MEMORY-P2-2 (2026-06-03): query 相关性 top-K 真筛.

        真问题: 老版只扫 ~/.catfish/skills/, 真生产员工 mac 真无目录, 真返空.
        6/3 真生产 dump 真验 5 数据源里 skills_catalog 真缺位 (鸿波 BL-TOKEN-AUDIT
        '5 数据源痕迹没看见' 真因之一).

        真新逻辑 (按优先级扫, 真合并 budget):
        1. ~/.catfish/skills/ (员工录的 RecMode + propose_skill 真生成的, 真用户首选)
        2. ~/.hermes/skills/ (hermes 自带 29 skill + catfish 真 symlink 装的)

        真 dedupe by skill 真名 (优先 catfish 自家版本, 真覆盖 hermes 默认).

        真 query 筛 (P2-2):
        - query 真空 (initial / no message) → 全注入按字母序 (旧行为)
        - query 真有 → 算每 skill name+desc head 的 jieba/字符级 Jaccard, top-K 留
          (按 budget 截断, 真不漏高分 skill)
        """
        candidates: List[Path] = []
        catfish_skills = catfish_home / "skills"
        if catfish_skills.is_dir():
            candidates.append(catfish_skills)
        # 真 fallback: hermes skills (真常用真路径)
        hermes_skills = Path.home() / ".hermes" / "skills"
        if hermes_skills.is_dir():
            candidates.append(hermes_skills)

        if not candidates:
            return ""

        # 真先收集 (skill_name, skill_head) 真候选 list, 真不截 budget
        candidates_list: List[tuple] = []
        seen_names: set = set()
        try:
            for skills_root in candidates:
                # 真两层: skills_root 直接含 skill 目录 (catfish_home/skills),
                # 或 skills_root 含 category/skill 二层 (hermes 真 productivity/, devops/ 等)
                skill_dirs: List[Path] = []
                for child in sorted(skills_root.iterdir()):
                    if not child.is_dir():
                        continue
                    # 真 child 自身有 SKILL.md? → 它就是 skill 真目录
                    if (child / "SKILL.md").exists():
                        skill_dirs.append(child)
                    else:
                        # 真 category 层, 真扫一级子目录
                        try:
                            for grandchild in sorted(child.iterdir()):
                                if (grandchild.is_dir() and
                                        (grandchild / "SKILL.md").exists()):
                                    skill_dirs.append(grandchild)
                        except OSError:
                            continue

                for skill_dir in skill_dirs:
                    name = skill_dir.name
                    if name in seen_names:
                        continue  # 真 dedupe (优先 catfish 自家版本)
                    head = _read_text_safe(skill_dir / "SKILL.md", 800)
                    if not head:
                        continue
                    candidates_list.append((name, head.strip()[:600]))
                    seen_names.add(name)
        except OSError:
            pass

        if not candidates_list:
            return ""

        # 真 P2-2: query 真打分排序
        query_clean = (query or "").strip()
        if query_clean:
            # 真用 char-level set 真简单 Jaccard (无 jieba 依赖, plugin 真 light)
            q_chars = _query_token_set(query_clean)
            scored: List[tuple] = []
            for name, head in candidates_list:
                # 真 name 真权重更高 (skill 真定位作用)
                name_score = _jaccard_similarity(q_chars, _query_token_set(name)) * 3.0
                head_score = _jaccard_similarity(q_chars, _query_token_set(head))
                total = name_score + head_score
                scored.append((total, name, head))
            scored.sort(key=lambda t: -t[0])
            ordered = [(name, head) for _score, name, head in scored]
        else:
            # 真 query 空 → 按字母序 (老行为)
            ordered = candidates_list

        # 真按 budget 真截
        entries: List[str] = []
        budget = _BUDGETS["skills_catalog"]
        # 真 P2-2: query 真有 → 真 cap 砍到 5000 chars (top-K 真够, 减 system prompt)
        if query_clean:
            budget = min(budget, 5000)
        for name, head in ordered:
            entry = f"### {name}\n\n{head}\n"
            if len(entry) > budget:
                break
            entries.append(entry)
            budget -= len(entry)
            if budget <= 0:
                break

        if not entries:
            return ""
        title = "## 🛠 可用技能 (catfish skills)"
        if query_clean and len(entries) < len(candidates_list):
            title += f" — 按当前话题筛 top {len(entries)}/{len(candidates_list)}"
        return f"{title}\n\n" + "\n".join(entries)

    def _render_feedback(self, catfish_home: Path) -> str:
        records = _read_jsonl_tail(
            catfish_home / "feedback.jsonl",
            max_lines=10,
            max_bytes=_BUDGETS["feedback"],
        )
        if not records:
            return ""
        lines = []
        for r in records:
            verdict = r.get("verdict") or r.get("rating") or ""
            note = r.get("note") or r.get("comment") or ""
            icon = "👍" if verdict in ("up", "good", "👍") else "👎" if verdict in ("down", "bad", "👎") else "·"
            if note:
                lines.append(f"- {icon} {note}")
        if not lines:
            return ""
        return "## 💬 员工最近反馈 (catfish feedback)\n\n" + "\n".join(lines)

    def _render_skill_guard(self, query: str) -> str:
        """员工 query 提 skill 关键词时, 注入"用 catfish_run_skill" 铁律.

        简单 substring 触发: query 含 'skill' / '技能' / '跑技能' 等关键词才注入,
        其它 80% chat 不注入 (避免 prompt 噪音).
        """
        if not query:
            return ""
        q_lower = query.lower()
        keywords = ("skill", "技能", "跑技能", "做技能", "用技能", "调技能")
        if not any(k.lower() in q_lower for k in keywords):
            return ""
        return (
            "## ⚠ Skill Guard (catfish)\n\n"
            "员工提到 skill — 用 `catfish_run_skill` tool, 不要绕过. "
            "skill 是 catfish 自动化流水线 (e.g. 生成 ppt / 写 docx / 跑批),"
            "你自己写脚本=违规."
        )

    # ── 可选 hook (我们 read-only, 大部分 no-op) ─────────

    def shutdown(self) -> None:
        logger.info("catfish-memory shutdown: session=%s", self._session_id)

    # ════════════════════════════════════════════════════════════════════════
    # BL-MEMORY-ROUTER-A2 (6/2 凌晨鸿波拍): catfish-memory 接管 memory tool
    # ════════════════════════════════════════════════════════════════════════
    #
    # # 为啥
    # hermes 原生 memory tool 只 2 仓库 (USER.md / MEMORY.md), LLM 没分类指导,
    # 5/24 鸿波实盘 70% 跑偏 (skill spec / journal / todo 全塞 memory).
    #
    # # 修法 (browser_navigate 5/6 同 pattern)
    # __init__.py register(ctx) 调 ctx.register_tool(name="memory", override=True)
    # 替换 hermes builtin memory tool. 新 schema 加 kind 必填 (5 选 1), handler
    # 调 self.handle_memory_tool 按 kind 路由到对应仓库.
    #
    # # 5 个 kind 路由
    # | kind          | 真存储                                                 |
    # |---------------|--------------------------------------------------------|
    # | identity      | hermes 原 memory_tool(target=user) → USER.md         |
    # | project_fact  | hermes 原 memory_tool(target=memory) → MEMORY.md     |
    # | workflow      | catfish_propose_skill (BL-MM9, 5/8 ship)              |
    # | journal       | ~/.catfish/employee_journal.md (catfish 现有)          |
    # | todo          | catfish_reminder_create (5/13 ship, macOS Reminders)  |
    #
    # # 性能
    # LLM 在 tool call 时自己填 kind, plugin 直接路由 (0 后端 LLM 调用).
    # 单次 write 延迟 < 50ms 跟 hermes 原生一样, 0 性能损失.
    #
    # # enforcement
    # LLM 看到的 memory tool 就是 catfish 的 (override=True 让 builtin 不出现),
    # 5 选 1 + schema 清晰 description → 命中率 95%+ (从原 30% 降到 5% 跑偏).

    def get_catfish_memory_schema(self) -> Dict[str, Any]:
        """LLM 看到的 memory tool schema. 替换 hermes 原 2 选 1 target 为 5 选 1 kind."""
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add", "replace", "remove"],
                    "description": "add (新加) / replace (改) / remove (删)",
                },
                "kind": {
                    "type": "string",
                    "enum": ["identity", "project_fact", "workflow", "journal", "todo"],
                    "description": (
                        "内容性质 (必填, 决定存哪):\n"
                        "- identity: 关于员工**这个人**的稳定事实 (姓名/部门/偏好/沟通风格) → USER.md\n"
                        "- project_fact: **项目/技术**事实 (API 字段含义/客户机房 IP/工具约定) → MEMORY.md\n"
                        "- workflow: 工作**流程** (有 input/output/step 序列) → 自动提议存成 skill\n"
                        "- journal: 已发生**事件**/session 总结/会议记录 → 写 catfish 员工日志 (不是 memory)\n"
                        "- todo: 带 deadline 的**待办任务** → 自动转 macOS Reminders (不是 memory)\n"
                        "拿不准 → 80% 概率是 journal 不是 identity/project_fact."
                    ),
                },
                "content": {
                    "type": "string",
                    "description": "要存的内容 (action=add/replace 必填)",
                },
                "old_text": {
                    "type": "string",
                    "description": "要替换/删除的旧文本 (action=replace/remove 必填, 唯一短 substring)",
                },
            },
            "required": ["action", "kind"],
        }

    def handle_memory_tool(self, args: Dict[str, Any], **kw: Any) -> str:
        """catfish memory tool 真 handler. ctx.register_tool(name="memory") 调这.

        按 kind 路由到 5 个仓库. 0 后端 LLM 调用 (LLM 自己填 kind), 0 性能损失.
        """
        import json as _json

        action = args.get("action", "add")
        kind = args.get("kind")
        content = args.get("content")

        if not kind:
            return _json.dumps({
                "success": False,
                "error": "kind 必填 (identity/project_fact/workflow/journal/todo).",
            }, ensure_ascii=False)

        # action=replace / remove 仍走 hermes 原生 (改 USER.md / MEMORY.md 入口)
        if action in ("replace", "remove"):
            return self._call_hermes_original_memory_tool(args, **kw)

        # action=add: 按 kind 路由
        try:
            if kind == "todo":
                return self._route_to_reminder(content)
            elif kind == "journal":
                return self._route_to_journal(content)
            elif kind == "workflow":
                return self._route_to_propose_skill(content)
            elif kind == "identity":
                return self._call_hermes_original_memory_tool(
                    {**args, "target": "user"}, **kw
                )
            elif kind == "project_fact":
                return self._call_hermes_original_memory_tool(
                    {**args, "target": "memory"}, **kw
                )
            else:
                return _json.dumps({
                    "success": False,
                    "error": f"unknown kind '{kind}'. 看 schema 选 5 个之一.",
                }, ensure_ascii=False)
        except Exception as e:  # noqa: BLE001
            logger.exception("catfish memory router 异常: %s", e)
            return _json.dumps({
                "success": False,
                "error": f"catfish memory router 异常: {e}",
            }, ensure_ascii=False)

    # ── 5 个路由 helper ─────────────────────────────────────

    def _call_hermes_original_memory_tool(
        self, args: Dict[str, Any], **kw: Any
    ) -> str:
        """走 hermes 原生 memory_tool — identity → USER.md / project_fact → MEMORY.md."""
        from tools.memory_tool import memory_tool as _hermes_memory_tool
        return _hermes_memory_tool(
            action=args.get("action", "add"),
            target=args.get("target", "memory"),
            content=args.get("content"),
            old_text=args.get("old_text"),
            store=kw.get("store"),
        )

    def _route_to_reminder(self, content: str) -> str:
        """kind=todo → 调 catfish_reminder_create (macOS Reminders, 5/13 BL-REMINDER).

        catfish-tool-bridge 走 unix socket, 我们 plugin 在 hermes 进程内, 用
        subprocess 调 catfish-cli 触发 (松耦合, 不直接 import tool-bridge).

        Fallback: 没 deadline / catfish-cli 没装 → 转 journal 兜底.
        """
        import json as _json
        # 简单实现: 没办法从 plugin 直接调 tool-bridge tool, 写"建议" 到 journal,
        # LLM 看到 result 后自己再调 reminder_create. 未来 PR 真接 socket.
        catfish_home = self._catfish_home_cached or _catfish_home()
        # BL-CATFISH-WIKI-MODE P0.3 (6/3): 格式 `## [YYYY-MM-DD HH:MM] kind | title`
        ts_short = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
        # title 取 content 首 50 字, 单行
        title = (content or "").strip().replace("\n", " ")[:50] or "unknown"
        entry = f"\n## [{ts_short}] todo | {title}\n\n{content}\n"
        try:
            (catfish_home / "employee_journal.md").parent.mkdir(
                parents=True, exist_ok=True
            )
            with open(catfish_home / "employee_journal.md", "a", encoding="utf-8") as f:
                f.write(entry)
            return _json.dumps({
                "success": True,
                "routed_to": "journal (todo 兜底)",
                "hint": "提示: 这是 todo, 建议 LLM 再调 catfish_reminder_create 真存 Reminders.app",
            }, ensure_ascii=False)
        except Exception as e:  # noqa: BLE001
            return _json.dumps({
                "success": False,
                "error": f"todo 路由失败: {e}",
            }, ensure_ascii=False)

    def _route_to_journal(self, content: str) -> str:
        """kind=journal → append ~/.catfish/employee_journal.md."""
        import json as _json
        catfish_home = self._catfish_home_cached or _catfish_home()
        journal_path = catfish_home / "employee_journal.md"
        # BL-CATFISH-WIKI-MODE P0.3 (6/3): 格式 `## [YYYY-MM-DD HH:MM] kind | title`
        ts_short = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
        title = (content or "").strip().replace("\n", " ")[:50] or "unknown"
        entry = f"\n## [{ts_short}] journal | {title}\n\n{content}\n"
        try:
            journal_path.parent.mkdir(parents=True, exist_ok=True)
            with open(journal_path, "a", encoding="utf-8") as f:
                f.write(entry)
            return _json.dumps({
                "success": True,
                "routed_to": "employee_journal.md",
                "path": str(journal_path),
            }, ensure_ascii=False)
        except Exception as e:  # noqa: BLE001
            return _json.dumps({
                "success": False,
                "error": f"journal 写失败: {e}",
            }, ensure_ascii=False)

    def _route_to_propose_skill(self, content: str) -> str:
        """kind=workflow → 提议存 skill (LLM 看到 hint 后再调 catfish_propose_skill)."""
        import json as _json
        # plugin 跟 catfish-tool-bridge 进程隔离, 不能直接调 catfish_propose_skill.
        # 返提示让 LLM 自己再调 (一次 turn 内 LLM 能补调).
        return _json.dumps({
            "success": True,
            "routed_to": "propose_skill_hint",
            "hint": (
                "这是 workflow (有 step 序列), 不该写 memory. "
                "请改调 catfish_propose_skill 工具, 把 name + reason + action_steps "
                "传过去, 走员工 confirm 门槛固化为 skill."
            ),
            "content_recap": content[:200],
        }, ensure_ascii=False)

    # ── 写路径: sync_turn + 节流 (BL-MEMORY-SYNC-TURN-REFACTOR, 5/20) ───
    #
    # 替代 gateway 旧 session_summarizer + memory_distill module.
    #
    # # 为啥不是 on_session_end (Step D 失败教训, 2026-05-20 早 6h debug)
    #
    # hermes 的 memory_provider.on_session_end 只在 **真 session boundary** 触发:
    #   - CLI atexit / `/reset` / `/new` / context compression / gateway session expiry
    #   - 详见 run_agent.py:16078-16091 注释 "Memory provider on_session_end NOT called
    #     per turn — would kill provider before second message"
    #
    # Companion 切 session 在 hermes 这边是 "SSE disconnected; interrupted agent task",
    # 不走 session_end path → 我们的 on_session_end 实现**永远不会** trigger →
    # journal 永远不被写. 5/20 早实测验证了这点 (catfish-debt-audit 2026-05-19).
    #
    # # 正确机制: sync_turn + 节流
    #
    # sync_turn 是 hermes per-turn hook (每轮 user+assistant 完成调一次).
    # 但每轮都跑 LLM summary 太贵 (烧 token), 用节流:
    #   - 节流 A: 累积 N 轮 (default 5, env CATFISH_PLUGIN_SUMMARIZE_EVERY_N_TURNS)
    #   - 节流 B: 距上次 summary >= X 秒 (default 1800=30min, env CATFISH_PLUGIN_SUMMARIZE_MIN_INTERVAL_SECONDS)
    #   - 任一满足触发. 触发后 reset counter + buffer.
    #
    # # 兜底: on_session_end 仍保留作 force-flush
    #
    # session 真结束时 (CLI exit / reset), 把没满 N 轮的剩余 buffer 也 summarize 一次,
    # 不丢最后一段对话.
    #
    # # 设计原则
    #   - **fire-and-forget**: hermes 同步调 sync_turn, 我们 spawn daemon thread 跑 async
    #     LLM 调用, 不阻塞 hermes 主流程
    #   - **双写期幂等**: deploy 时 gateway 旧 caller 还在跑, journal mtime <
    #     SUMMARIZE_DEDUP_SECONDS 视为 gateway 刚写过, plugin skip 防 dup
    #   - **不抛**: 全 try/except, 任何错都不能让 hermes 挂
    #   - **线程安全**: _turn_buffer / _turns_since_last_summary / _last_summary_ts
    #     的访问全部走 _buffer_lock 保护

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
    ) -> None:
        """hermes per-turn hook — 累积 + 节流 trigger summary (fire-and-forget)."""
        # --- BL-MEMORY-SYNC-TURN-REFACTOR Day 2 trace (5/20): 验证 hermes 调到 + 节流状态
        try:
            _home = self._catfish_home_cached or _catfish_home()
            _file_pairs = len(_read_buffer(_home))
            _file_state = _read_state(_home)
            with open(os.path.expanduser("~/.catfish/_sync_turn_trace.log"), "a") as _tf:
                _tf.write(
                    f"{time.strftime('%Y-%m-%d %H:%M:%S')} "
                    f"sync_turn: inst={id(self):x} "
                    f"file_pairs_before={_file_pairs} "
                    f"last_summary_ts={_file_state.get('last_summary_ts', 0)} "
                    f"u_len={len(user_content or '')} "
                    f"a_len={len(assistant_content or '')} "
                    f"sid={session_id[:12]}\n"
                )
        except Exception:
            pass
        # --- END trace
        try:
            self._sync_turn_impl(user_content, assistant_content, session_id)
        except Exception as e:  # noqa: BLE001 - 全 catch, 不能让 hermes 挂
            logger.warning("catfish-memory sync_turn 失败 (静默): %s", e)
        # BL-SESSION-META-PLUGIN-TAKEOVER (5/26): 每轮 tick 时间感. 老逻辑 gateway
        # session_meta.tick() 在 chat 完成后写, SaaS 化后 gateway 不能写员工本机.
        # plugin 跑员工 mac 写自己 fs 合规. 这里跟 sync_turn buffer 累积同 hook,
        # 不再依赖 gateway 触发. 实际 chat 走 catfish-public 同款规则 (内部调用 + service
        # token 不算"员工跟我聊", 这两个走 gateway 路径不会触发 sync_turn 所以天然 skip).
        try:
            self._tick_session_meta()
        except Exception as e:  # noqa: BLE001
            logger.warning("catfish-memory tick_session_meta 失败 (静默): %s", e)

    def _sync_turn_impl(
        self,
        user_content: str,
        assistant_content: str,
        session_id: str,
    ) -> None:
        # yaml/env 配置 — yaml 优先, env 兜底, default fallback
        if not self._is_summarize_enabled():
            return
        model = self._get_summarize_model()
        if not model:
            return

        # 过滤非 str / 空 content
        if not isinstance(user_content, str) or not isinstance(assistant_content, str):
            return
        if not user_content.strip() or not assistant_content.strip():
            return

        # Update self._session_id 跟最新
        if session_id:
            self._session_id = session_id

        home = self._catfish_home_cached or _catfish_home()

        # 累积到文件 buffer (跨 instance 持久化 — 因为 hermes api_server 每 chat 新 instance)
        _append_to_buffer(home, "user", user_content, session_id)
        new_entry_count = _append_to_buffer(home, "assistant", assistant_content, session_id)
        n_pairs = new_entry_count // 2

        # 读 state (last_summary_ts)
        state = _read_state(home)
        last_ts = float(state.get("last_summary_ts", 0))

        # 节流判断
        n_threshold = self._get_n_turns_threshold()
        min_interval = self._get_min_interval_seconds()
        now = time.time()
        elapsed = (now - last_ts) if last_ts > 0 else float("inf")

        triggered_by_n = n_pairs >= n_threshold
        # 时间节流: 必须 last_ts 真有值 (>0) 且超 min_interval 且至少 1 pair
        triggered_by_time = (
            last_ts > 0 and elapsed >= min_interval and n_pairs >= 1
        )

        if not (triggered_by_n or triggered_by_time):
            return

        # Trigger: snapshot buffer + 清 file + update state
        snapshot_pairs = _read_buffer(home)
        _clear_buffer(home)
        _write_state(home, {"last_summary_ts": now})

        # 双写期幂等 — gateway 刚写过 file mtime 比 plugin 上次 trigger 还新, skip
        if self._journal_written_externally(last_ts):
            logger.info(
                "catfish-memory sync_turn: external journal write detected, skip dup"
            )
            return

        self._spawn_summarize_thread(snapshot_pairs, model)

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

    def _force_flush(self) -> None:
        """触发 buffer 里剩余对话的一次 summary (即使没满 N 轮)."""
        if not self._is_summarize_enabled():
            return
        model = self._get_summarize_model()
        if not model:
            return

        home = self._catfish_home_cached or _catfish_home()
        snapshot_pairs = _read_buffer(home)
        if not snapshot_pairs:
            return

        # 读 state 拿 last_ts (给 mtime check 用), 然后 clear + update
        state = _read_state(home)
        last_ts = float(state.get("last_summary_ts", 0))
        _clear_buffer(home)
        _write_state(home, {"last_summary_ts": time.time()})

        if self._journal_written_externally(last_ts):
            logger.info(
                "catfish-memory force_flush: external journal write detected, skip dup"
            )
            return

        self._spawn_summarize_thread(snapshot_pairs, model)

    def _spawn_summarize_thread(
        self,
        pairs: List[Tuple[str, str]],
        model: str,
    ) -> None:
        """spawn 后台 daemon thread 跑 async LLM summary + 写文件. fire-and-forget."""
        if not pairs:
            return
        sid = self._session_id
        catfish_home = self._catfish_home_cached or _catfish_home()
        thread = threading.Thread(
            target=lambda: asyncio.run(
                self._summarize_and_distill_async(sid, pairs, model, catfish_home)
            ),
            name=f"catfish-memory-summarize-{sid[:8]}" if sid else "catfish-memory-summarize",
            daemon=True,
        )
        thread.start()
        logger.info(
            "catfish-memory sync_turn trigger: session=%s pairs=%d (n_turns=%d / threshold=%d)",
            sid, len(pairs), len(pairs) // 2,
            self._get_n_turns_threshold(),
        )

    def _get_n_turns_threshold(self) -> int:
        """节流 A 阈值 (每 N 轮). 优先 yaml > env > default 5."""
        # yaml 优先
        cfg = _load_plugin_config(self._catfish_home_cached or _catfish_home())
        yaml_val = cfg.get("summarize", {}).get("every_n_turns") if isinstance(cfg, dict) else None
        if isinstance(yaml_val, int):
            return max(1, yaml_val)
        # env 兜底
        raw = os.environ.get("CATFISH_PLUGIN_SUMMARIZE_EVERY_N_TURNS", "").strip()
        if raw:
            try:
                return max(1, int(raw))
            except ValueError:
                pass
        return _DEFAULT_TURNS_BETWEEN_SUMMARY

    def _get_min_interval_seconds(self) -> int:
        """节流 B 阈值 (秒). 优先 yaml > env > default 1800."""
        cfg = _load_plugin_config(self._catfish_home_cached or _catfish_home())
        yaml_val = cfg.get("summarize", {}).get("min_interval_seconds") if isinstance(cfg, dict) else None
        if isinstance(yaml_val, int):
            return max(0, yaml_val)
        raw = os.environ.get("CATFISH_PLUGIN_SUMMARIZE_MIN_INTERVAL_SECONDS", "").strip()
        if raw:
            try:
                return max(0, int(raw))
            except ValueError:
                pass
        return _DEFAULT_MIN_SUMMARY_INTERVAL_SECONDS

    def _get_summarize_model(self) -> str:
        """LLM model. 优先 yaml > env. 没设返空字符串 (caller skip)."""
        cfg = _load_plugin_config(self._catfish_home_cached or _catfish_home())
        yaml_val = cfg.get("summarize", {}).get("model") if isinstance(cfg, dict) else None
        if isinstance(yaml_val, str) and yaml_val.strip():
            return yaml_val.strip()
        return os.environ.get("CATFISH_PLUGIN_SUMMARIZE_MODEL", "").strip()

    def _is_summarize_enabled(self) -> bool:
        """启用 plugin summary 写路径. 优先 yaml.enabled, env=0 关掉."""
        cfg = _load_plugin_config(self._catfish_home_cached or _catfish_home())
        yaml_val = cfg.get("enabled") if isinstance(cfg, dict) else None
        if isinstance(yaml_val, bool):
            return yaml_val
        # env 兜底: =0 关掉, 否则启用 (默认 enabled)
        return os.environ.get("CATFISH_PLUGIN_SUMMARIZE", "1") != "0"

    def _journal_written_externally(self, our_last_ts: float) -> bool:
        """journal 文件 mtime 显著新于 plugin 自己上次 summary 时间 → 是别人 (gateway) 写的.

        BL-MEMORY-SYNC-TURN-REFACTOR (5/20): 替代老 _journal_recently_written.
        老逻辑用 file mtime vs absolute (5min) 判断"刚写过", 但 plugin 自己写完
        journal 后下次触发时也撞这条, **被自己 skip**. test_sync_turn_buffer_resets
        测试暴露了这个 bug.

        新逻辑: file mtime > our_last_ts + 5s buffer → 真有别人 (gateway 老 caller)
        在 plugin 上次 trigger 之后写了 journal, 双写期防 dup.

        5s buffer 避开:
          - macOS HFS+/APFS timestamp resolution (默认 1s 但可能不准)
          - lock 释放 → spawn thread 跑 LLM 调用 → 写 file 这段 race window
        """
        # our_last_ts <= 0 表示 plugin 没"上次 summary" — 不能判断别人写过. 第一次
        # trigger 时这种情况, journal 即使有 (gateway 时代留下的) 也不该 skip plugin.
        if our_last_ts <= 0:
            return False
        path = (self._catfish_home_cached or _catfish_home()) / "employee_journal.md"
        try:
            if not path.exists():
                return False
            mtime = path.stat().st_mtime
            return mtime > our_last_ts + 5.0
        except OSError:
            return False

    def _build_raw_journal_fallback(
        self, pairs: List[Tuple[str, str]]
    ) -> str:
        """LLM 总结失败时, 保留最近 N pairs raw 作 journal 内容, 避免 silent drop.

        BL-CATFISH-MEMORY-SUMMARIZE-UPSTREAM-502 (6/4 凌晨 ship, BACKLOG 9c15942):
        catfish gateway 上游 100% 502 → 之前 silent drop → journal 全丢.
        改 raw fallback: 保 data 不丢, 符合 catfish 价值观 "中央不存 = 中央不管"
        (raw 留, LLM 总结异步补).

        每 pair 截 200 chars + 最多 keep 5 pairs, 避免 entry 过长.
        """
        keep_last_n = min(5, len(pairs))
        if keep_last_n == 0:
            return "[LLM 总结失败 (上游 502 等), 0 pairs 保留]"
        lines = [
            f"[LLM 总结失败 (上游 502 等), raw {keep_last_n} pairs 保留 — "
            "可下次 sync_turn 重新蒸馏]",
            "",
        ]
        for u, a in pairs[-keep_last_n:]:
            u_short = (u or "").strip().replace("\n", " ")[:200]
            a_short = (a or "").strip().replace("\n", " ")[:200]
            lines.append(f"- 鸿波: {u_short}")
            lines.append(f"- 小鲶: {a_short}")
            lines.append("")
        return "\n".join(lines).rstrip()

    async def _summarize_and_distill_async(
        self,
        session_id: str,
        message_pairs: List[Tuple[str, str]],
        model: str,
        catfish_home: Path,
    ) -> None:
        """后台 thread 主体: 总结 → 写 journal → 看条件蒸馏 → 写 distilled.

        失败静默 (catfish 边缘文件丢一次不致命, 下次 session 还会触发).
        """
        try:
            # 1. 总结
            summary = await _call_summarize_llm(message_pairs, model)
            if not summary:
                # BL-CATFISH-MEMORY-SUMMARIZE-UPSTREAM-502 (6/4 凌晨):
                # 之前 silent drop → catfish gateway 100% 502 时 journal 全丢.
                # 改 raw fallback: 保 data 不丢. LLM 总结异步补 (下次 distill 跑).
                summary = self._build_raw_journal_fallback(message_pairs)
                logger.info(
                    "catfish-memory bg session=%s: LLM 总结返空 → "
                    "改写 raw fallback (%d pairs 保留)",
                    session_id, len(message_pairs),
                )

            # 2. 写 journal
            entry = _format_journal_entry(session_id, summary)
            _append_journal(catfish_home, entry)
            logger.info(
                "catfish-memory bg session=%s: ✓ 写 journal %d 字节",
                session_id, len(entry.encode("utf-8")),
            )

            # 3. 满足 24h 间隔 → 蒸馏
            if not _should_run_distill(catfish_home):
                return
            journal_text = _read_full_journal(catfish_home)
            if not journal_text:
                return

            # 3a. legacy single-step distill (保留 — 兼容老 distilled_facts.md path)
            distilled = await _call_distill_llm(journal_text, model)
            if distilled:
                _write_distilled(catfish_home, distilled)
                _mark_distill_run(catfish_home)
                logger.info(
                    "catfish-memory bg session=%s: ✓ 蒸馏 %d 字节 (distilled_facts.md)",
                    session_id, len(distilled.encode("utf-8")),
                )

            # 3b. BL-CATFISH-WIKI-MODE P1.1 (6/4): wiki two-step ingest
            #     CATFISH_WIKI_ENABLE=1 默认 off (LLM 调用贵, 24h 1 次).
            #     Step 1 Analysis → Step 2 Generation → parse + 写文件 + journal
            if _wiki_enabled():
                try:
                    analysis = await _call_analysis_llm(journal_text, model)
                    if not analysis:
                        logger.info(
                            "catfish-memory bg session=%s: wiki Step 1 analysis 返空 (skip)",
                            session_id,
                        )
                    else:
                        generation = await _call_generation_llm(analysis, model)
                        if not generation:
                            logger.info(
                                "catfish-memory bg session=%s: wiki Step 2 generation 返空 (skip)",
                                session_id,
                            )
                        else:
                            files = _parse_generation_output(generation)
                            n_e, n_c = _write_wiki_files(catfish_home, files)
                            if n_e + n_c > 0:
                                # journal 加 distill entry — Karpathy log.md 风格
                                ts_short = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
                                distill_entry = (
                                    f"\n## [{ts_short}] distill | "
                                    f"{n_e} entities, {n_c} concepts\n\n"
                                    f"wiki/entities/ + wiki/concepts/ 已更新 "
                                    f"({len(files)} pages, ~{len(generation)//1024}KB).\n"
                                )
                                _append_journal(catfish_home, distill_entry)
                                logger.info(
                                    "catfish-memory bg session=%s: ✓ wiki ship %d entities + %d concepts",
                                    session_id, n_e, n_c,
                                )
                            else:
                                logger.info(
                                    "catfish-memory bg session=%s: wiki parse 0 file (LLM 输出不符 sentinel)",
                                    session_id,
                                )
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "catfish-memory bg session=%s: wiki two-step 异常 (静默): %s",
                        session_id, e,
                    )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "catfish-memory bg session=%s 异常 (静默): %s", session_id, e,
            )
