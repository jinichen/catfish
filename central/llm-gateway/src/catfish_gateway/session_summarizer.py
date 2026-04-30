"""session_summarizer — 自动总结 session, append 到 employee_journal.

# 触发策略 (异步, 不阻塞主 chat 流程)

每次 chat_completions 进入时, 检测当前 session_id (从 messages 末尾消息算 hash 推断,
或从 X-Catfish-Session-Id header 拿). 跟 ~/.catfish/journaled_sessions.txt 比对,
找出**已结束 + 没总结过**的旧 session, 后台 asyncio 异步总结. 不阻塞当前请求.

# 总结来源

读 ~/.hermes/state.db 拿 session 的所有 messages → 拼成上下文 → 调 catfish-gateway
内的 LLM (qwen-flash 或 gemini-flash) 总结成 1-2 段 markdown → append_to_journal.

# 防滥用

- 不总结 < 3 条 message 的 session (太短没价值)
- 不重复总结 (用 journaled_sessions.txt 标记)
- 一次只总结 1 个 session (avoid 雪崩)
- LLM 调用失败静默跳过 (不影响主流程)

# 总结 prompt

```
你是员工 X 的工作日记写手. 下面是员工跟鲶鱼的一段对话历史. 帮员工写
一段简短日记 (1-2 段, 100-300 字), 包含:
1. 员工要做什么
2. 关键决策 / 偏好
3. 完成度 (是否拿到结果)

风格: 第三人称客观陈述, 不要"我帮员工 ...", 写"员工要 ..." / "员工决定 ...".
不要写没用的 ("员工跟我交流愉快"). 抓重点.

输出格式:
## YYYY-MM-DD HH:MM - <一句话主题>
<日记正文 1-2 段>
```
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from .employee_journal import JOURNAL_PATH, append_to_journal

logger = logging.getLogger("catfish.gateway.session_summarizer")

#: 总结过的 session id 标记文件 (一行一个 id)
JOURNALED_MARKER = Path.home() / ".catfish" / "journaled_sessions.txt"

#: state.db 路径
STATE_DB = Path.home() / ".hermes" / "state.db"

#: 不总结 < 这个消息数的 session
MIN_MESSAGES_TO_SUMMARIZE = 3

#: 一次最多总结几个 session (防雪崩)
MAX_SESSIONS_PER_TRIGGER = 1

#: 总结时取最多多少条 message (防 token 爆炸; 长 session 取头 + 尾)
MAX_MESSAGES_PER_SESSION = 30


def _read_journaled_ids() -> set[str]:
    """读已总结 session id 集合."""
    if not JOURNALED_MARKER.exists():
        return set()
    try:
        return {
            line.strip()
            for line in JOURNALED_MARKER.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
    except OSError:
        return set()


def _mark_journaled(session_id: str) -> None:
    JOURNALED_MARKER.parent.mkdir(parents=True, exist_ok=True)
    with JOURNALED_MARKER.open("a", encoding="utf-8") as f:
        f.write(session_id + "\n")


def _list_unjournaled_finished_sessions(
    current_session_id: str | None = None,
) -> list[tuple[str, float, int]]:
    """找已结束 (current 之外) 且没总结过的 session.

    返回 [(id, started_at, msg_count)], 按时间倒序, 长度限 MAX_SESSIONS_PER_TRIGGER.
    """
    if not STATE_DB.exists():
        return []

    journaled = _read_journaled_ids()

    try:
        conn = sqlite3.connect(
            f"file:{STATE_DB}?mode=ro", uri=True, timeout=1.0
        )
        rows = conn.execute(
            """
            SELECT id, started_at, message_count
            FROM sessions
            WHERE message_count >= ?
            ORDER BY started_at DESC
            LIMIT 50
            """,
            (MIN_MESSAGES_TO_SUMMARIZE,),
        ).fetchall()
        conn.close()
    except sqlite3.Error as e:
        logger.warning("list_unjournaled: 读 state.db 失败: %s", e)
        return []

    candidates = []
    for sid, started_at, msg_count in rows:
        if sid == current_session_id:
            continue  # 不总结当前 session (还在进行)
        if sid in journaled:
            continue  # 已总结过
        candidates.append((sid, started_at, msg_count))
        if len(candidates) >= MAX_SESSIONS_PER_TRIGGER:
            break
    return candidates


def _read_session_messages(session_id: str) -> list[tuple[str, str]]:
    """取 session 的 (role, content) 列表. 长 session 取头 10 + 尾 20 防 token 爆."""
    if not STATE_DB.exists():
        return []
    try:
        conn = sqlite3.connect(
            f"file:{STATE_DB}?mode=ro", uri=True, timeout=1.0
        )
        rows = conn.execute(
            """
            SELECT role, content FROM messages
            WHERE session_id = ?
            ORDER BY id
            """,
            (session_id,),
        ).fetchall()
        conn.close()
    except sqlite3.Error as e:
        logger.warning("read_session_messages 失败: %s", e)
        return []

    if len(rows) > MAX_MESSAGES_PER_SESSION:
        head_count = 10
        tail_count = MAX_MESSAGES_PER_SESSION - head_count
        rows = rows[:head_count] + [("...", f"(中间省略 {len(rows) - MAX_MESSAGES_PER_SESSION} 条)")] + rows[-tail_count:]
    return rows


_SUMMARY_PROMPT = """你是员工的工作日记写手. 下面是员工跟鲶鱼 (catfish, 员工的 AI 副手) 的一段对话历史.
帮员工写一段简短日记 (1-2 段, 100-300 字), 抓 3 个重点:
1. **员工要做什么**: 这次会话的核心需求 / 任务
2. **关键决策 / 偏好**: 员工拍板的内容 / 表达的偏好 / 反馈
3. **完成度**: 是否拿到结果 (生成了文件 / 解决了问题), 还是中断了

风格:
- 第三人称客观陈述. 写"员工要 ..." / "员工决定 ..." / "鸿波偏好 ...", **不要**写"我帮员工 ..."
- 抓重点, 不要事无巨细
- 不要写没用的客套 ("员工跟我交流愉快")

⚠️ 输出格式 (严格按这个, 不加别的):
- 第一行**必须**就是: `### <一句话主题, 不超 20 字>` (注意是三个 #, 不是两个; 不要带日期 — 日期由系统自动加)
- 第二行起: 日记正文 1-2 段

不要包含任何其他内容 (没有"以下是日记" / "总结如下" / 引言 / 结语 / 反引号等):
"""


async def _summarize_with_llm(
    session_id: str, started_at: float, messages_pairs: list[tuple[str, str]]
) -> str | None:
    """调 LLM 总结. 失败返 None.

    用 catfish-public-qwen-flash (国内可达). 如果不可达自动 fallback (gateway 自带 fallback chain).
    """
    if not messages_pairs:
        return None

    # 拼上下文
    context_lines = []
    for role, content in messages_pairs[:MAX_MESSAGES_PER_SESSION]:
        snippet = (content or "")[:500]
        context_lines.append(f"[{role}]: {snippet}")
    context = "\n\n".join(context_lines)

    user_prompt = _SUMMARY_PROMPT + f"\n\n会话历史:\n\n{context}"

    # 调 LiteLLM 总结. 用 catalog 里 catfish-public-qwen-flash 的真实后端模型名.
    # 没 DASHSCOPE_API_KEY 直接早返, 不打扰主流程.
    import os  # noqa: PLC0415

    api_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        logger.info(
            "summarize_with_llm 跳过 session=%s: DASHSCOPE_API_KEY 未设, 总结功能禁用",
            session_id,
        )
        return None

    try:
        import litellm  # noqa: PLC0415

        response = await litellm.acompletion(
            # 跟 catalog (config/models.yaml) catfish-public-qwen-flash 一致
            model="openai/qwen3.5-flash-2026-02-23",
            messages=[{"role": "user", "content": user_prompt}],
            api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key=api_key,
            temperature=0.3,
            max_tokens=600,
            timeout=30,
        )
        text = response.choices[0].message.content or ""
        return text.strip()
    except Exception as e:
        logger.warning(
            "summarize_with_llm 失败 session=%s: %s", session_id, e
        )
        return None


async def summarize_one_session(
    session_id: str, started_at: float, msg_count: int
) -> bool:
    """总结一个 session, 写到 journal. 成功返 True.

    格式约定 (gateway 加日期前缀, LLM 只填 ### 主题 + 正文):
        ## YYYY-MM-DD HH:MM · <session_id 前 8 字符>
        ### <LLM 写的主题>
        <LLM 写的正文>

    用 ## 二级标题做日期分隔, ### 三级标题做主题, 视觉清晰. session_id 前缀
    防止"同主题不同 session" 难区分.
    """
    date_str = datetime.fromtimestamp(started_at).strftime("%Y-%m-%d %H:%M")
    logger.info(
        "summarize_one_session: session=%s, msg=%d, started=%s",
        session_id,
        msg_count,
        date_str,
    )

    msgs = _read_session_messages(session_id)
    if not msgs:
        # 没消息可读 — mark 防再扫到 (这种 session 永远总结不了)
        _mark_journaled(session_id)
        return False

    summary = await _summarize_with_llm(session_id, started_at, msgs)
    if summary is None or not summary.strip():
        # LLM 调用失败 — **不 mark**, 留给下次重试 (可能是网络抖 / API key 问题)
        # 鸿波 4-30 踩过坑: 提前 mark 导致 litellm 没装这种环境问题把 10 个
        # session 永久 mark, 永远不再总结. 改成只有真生成了才 mark.
        return False

    # gateway 加日期前缀 + session id 后缀, 让 LLM 不能脑补错时间
    sid_short = session_id[-6:] if len(session_id) > 6 else session_id
    full_entry = f"## {date_str} · session `…{sid_short}`\n\n{summary.strip()}\n"

    try:
        append_to_journal(full_entry)
        # 只有 journal 真写入了, 才 mark
        _mark_journaled(session_id)
        return True
    except Exception as e:
        logger.warning("append_to_journal 失败 session=%s: %s", session_id, e)
        return False


async def trigger_background_summary(
    current_session_id: str | None = None,
) -> None:
    """后台异步触发 1 次总结. 不抛异常, 失败静默. 主调用方 fire-and-forget.

    用 asyncio.create_task() 调用即可, 不要 await.
    """
    try:
        candidates = _list_unjournaled_finished_sessions(current_session_id)
        if not candidates:
            return
        for sid, started_at, msg_count in candidates:
            ok = await summarize_one_session(sid, started_at, msg_count)
            if ok:
                logger.info(
                    "session_summarizer: ✓ 总结完 session=%s", sid[:21]
                )
    except Exception as e:
        logger.warning("trigger_background_summary 失败: %s", e)


__all__ = [
    "trigger_background_summary",
    "summarize_one_session",
    "JOURNALED_MARKER",
]
