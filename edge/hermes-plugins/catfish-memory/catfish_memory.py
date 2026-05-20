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

    def _sync_turn_impl(
        self,
        user_content: str,
        assistant_content: str,
        session_id: str,
    ) -> None:
        # Env gate
        if os.environ.get("CATFISH_PLUGIN_SUMMARIZE", "1") == "0":
            return
        model = os.environ.get("CATFISH_PLUGIN_SUMMARIZE_MODEL", "").strip()
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
        if os.environ.get("CATFISH_PLUGIN_SUMMARIZE", "1") == "0":
            return
        model = os.environ.get("CATFISH_PLUGIN_SUMMARIZE_MODEL", "").strip()
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
        """读 env 拿节流 A 阈值 (每 N 轮). 默认 5."""
        raw = os.environ.get("CATFISH_PLUGIN_SUMMARIZE_EVERY_N_TURNS", "").strip()
        if not raw:
            return _DEFAULT_TURNS_BETWEEN_SUMMARY
        try:
            n = int(raw)
            return max(1, n)  # >=1, 不接受 0 或负
        except ValueError:
            return _DEFAULT_TURNS_BETWEEN_SUMMARY

    def _get_min_interval_seconds(self) -> int:
        """读 env 拿节流 B 阈值 (秒). 默认 1800 (30min)."""
        raw = os.environ.get("CATFISH_PLUGIN_SUMMARIZE_MIN_INTERVAL_SECONDS", "").strip()
        if not raw:
            return _DEFAULT_MIN_SUMMARY_INTERVAL_SECONDS
        try:
            n = int(raw)
            return max(0, n)  # >=0, 0 = 时间间隔禁用 (只靠 N 轮)
        except ValueError:
            return _DEFAULT_MIN_SUMMARY_INTERVAL_SECONDS

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
