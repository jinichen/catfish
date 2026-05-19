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

#: gateway loopback URL (env 覆盖, 默认 8999).
_DEFAULT_GATEWAY_URL = "http://127.0.0.1:8999/v1/chat/completions"

#: HTTP 超时 (LLM 总结+蒸馏不应该超 60s; 真超就 cooldown 等下次).
_LLM_HTTP_TIMEOUT = 60.0

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
    """格式 journal entry — 加日期 + session_id 短前缀, 跟老 gateway 输出一致."""
    date_str = time.strftime("%Y-%m-%d %H:%M")
    sid_short = (session_id[-6:] if len(session_id) > 6 else session_id) or "unknown"
    return f"## {date_str} · session `…{sid_short}`\n\n{summary.strip()}\n"


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


class CatfishMemoryProvider(MemoryProvider):
    """聚合 catfish 5 个边缘数据源的 hermes MemoryProvider."""

    def __init__(self) -> None:
        self._session_id: str = ""
        self._hermes_home: Optional[Path] = None
        self._catfish_home_cached: Optional[Path] = None
        self._initialized = False

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
        """
        self._session_id = session_id
        hh = kwargs.get("hermes_home")
        if isinstance(hh, str):
            self._hermes_home = Path(hh)
        self._catfish_home_cached = _catfish_home()
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

        # 1. session_meta — 时间感 (距上次 N 天)
        meta = self._render_session_meta(catfish_home)
        if meta:
            sections.append(meta)

        # 2. employee_journal — 员工长期记忆
        journal = self._render_employee_journal(catfish_home)
        if journal:
            sections.append(journal)

        # 3. skills_catalog — 可用 catfish 技能
        skills = self._render_skills_catalog(catfish_home)
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
        return "\n\n".join(sections)

    # ── 5 个数据源 render helper ──────────────────────────

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

    def _render_skills_catalog(self, catfish_home: Path) -> str:
        skills_dir = catfish_home / "skills"
        if not skills_dir.is_dir():
            return ""
        # 简单列子目录名 + 每个 skill 的 SKILL.md 头部 (Phase 2 POC, 不做
        # 复杂相关性筛选)
        entries: List[str] = []
        budget = _BUDGETS["skills_catalog"]
        try:
            for child in sorted(skills_dir.iterdir()):
                if not child.is_dir():
                    continue
                manifest = child / "SKILL.md"
                if not manifest.exists():
                    continue
                head = _read_text_safe(manifest, 800)  # 每个 skill 800 字节摘要
                if not head:
                    continue
                entry = f"### {child.name}\n\n{head.strip()[:600]}\n"
                if len(entry) > budget:
                    break
                entries.append(entry)
                budget -= len(entry)
        except OSError:
            pass
        if not entries:
            return ""
        return "## 🛠 可用技能 (catfish skills)\n\n" + "\n".join(entries)

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

    # ── 写路径: on_session_end (BL-GATEWAY-CLEANUP-POST-HERMES Week 2) ───
    #
    # 替代 gateway 旧 session_summarizer + memory_distill module.
    # hermes 在 session 结束时调用这个 hook (run_agent.py:5809/5816/5840/16085),
    # 我们做两件事:
    #   1. 总结当前 session → 追加 ~/.catfish/employee_journal.md
    #   2. 满足 24h cooldown → 蒸馏老 journal → 覆盖写 ~/.catfish/distilled_facts.md
    #
    # 设计原则:
    #   - **fire-and-forget**: hermes 调进来 sync, 我们 spawn daemon thread 跑 async
    #     LLM 调用, 不阻塞 hermes session shutdown
    #   - **双写期幂等**: Step B-Step C 过渡期 gateway 旧 caller 也在跑, 用
    #     journal_path mtime < SUMMARIZE_DEDUP_SECONDS skip 防 dup. 一旦 Step C
    #     env gate 关掉 gateway 那条, 这个保护自动失效 (但也无害, 因为只有 plugin 自己写)
    #   - **不抛**: 整个 on_session_end 包 try/except, 任何错都不能让 hermes 挂

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        """hermes session 结束 hook — 后台总结 + 蒸馏 (fire-and-forget)."""
        try:
            self._on_session_end_impl(messages)
        except Exception as e:  # noqa: BLE001 - 全 catch, 不能让 hermes 挂
            logger.warning("catfish-memory on_session_end 失败 (静默): %s", e)

    def _on_session_end_impl(self, messages: List[Dict[str, Any]]) -> None:
        # Env gate: CATFISH_PLUGIN_SUMMARIZE=0 整体关掉 (回滚兜底)
        if os.environ.get("CATFISH_PLUGIN_SUMMARIZE", "1") == "0":
            logger.debug("CATFISH_PLUGIN_SUMMARIZE=0, 跳过 on_session_end")
            return
        if not messages:
            return

        # 双写期幂等 — gateway 刚写过 (< 5min) 就 skip
        if self._journal_recently_written():
            logger.info(
                "catfish-memory: journal mtime < %ds, 假设 gateway 刚写过, skip summarize",
                _SUMMARIZE_DEDUP_SECONDS,
            )
            return

        # model 必须从 env 拿 — 没 env = Step B 阶段 gateway 还在跑严格 model 跟随,
        # plugin 这条路径不该写. Step C 之后 deploy 时再设这个 env 让 plugin 接管.
        model = os.environ.get("CATFISH_PLUGIN_SUMMARIZE_MODEL", "").strip()
        if not model:
            logger.debug(
                "CATFISH_PLUGIN_SUMMARIZE_MODEL 未设, 跳过 (Step B 阶段正常行为)"
            )
            return

        pairs = _extract_message_pairs(messages)
        if not pairs:
            logger.debug("on_session_end: 没 user/assistant 对话对, 跳过")
            return

        # fire-and-forget — 后台 thread 跑 async LLM 调用
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
            "catfish-memory: 起后台 thread summarize session=%s msgs=%d",
            sid, len(pairs),
        )

    def _journal_recently_written(self) -> bool:
        """journal 文件 mtime < SUMMARIZE_DEDUP_SECONDS 视为刚被写过 (双写保护).

        典型场景: gateway 旧 caller 在 chat 完成 fire-and-forget summarize,
        几秒后 hermes session 结束触发我们这个 hook — 我们 skip 防 dup.
        """
        path = (self._catfish_home_cached or _catfish_home()) / "employee_journal.md"
        try:
            if not path.exists():
                return False
            age = time.time() - path.stat().st_mtime
            return age < _SUMMARIZE_DEDUP_SECONDS
        except OSError:
            return False

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
                logger.info(
                    "catfish-memory bg session=%s: LLM 总结返空, 跳过 (不写 journal)",
                    session_id,
                )
                return

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
            distilled = await _call_distill_llm(journal_text, model)
            if distilled:
                _write_distilled(catfish_home, distilled)
                _mark_distill_run(catfish_home)
                logger.info(
                    "catfish-memory bg session=%s: ✓ 蒸馏 %d 字节",
                    session_id, len(distilled.encode("utf-8")),
                )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "catfish-memory bg session=%s 异常 (静默): %s", session_id, e,
            )
