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

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from .employee_journal import append_to_journal

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


def _extract_short_title(summary: str) -> str:
    """BL-SESSION-MGMT B (5/15): 从 summary 抽 ≤30 字短标题给 sidebar 显.

    取首句 (中英文标点切), 截到 30 字. 没首句兜底用前 30 字.
    """
    import re  # noqa: PLC0415
    if not summary or not summary.strip():
        return ""
    s = summary.strip()
    # 第一个中/英文句子结束符为界 (含英文 `. `, 中文 。!？!?, 换行)
    # 注意: 英文 `.` 用 `\. ` (后跟空格) 避免误切 ISO 9001 / 1.5 这种小数点
    parts = re.split(r"[。!?！？\n]|\. ", s, maxsplit=1)
    first = (parts[0] if parts else s).strip()
    if not first:
        return s[:30]
    return first[:30]


def _update_session_title(session_id: str, title: str) -> bool:
    """BL-SESSION-MGMT B (5/15): 写 state.db sessions.title.

    只 UPDATE WHERE title IS NULL OR title = '' — 不覆盖员工手起的 / hermes 已生成的名.
    失败 silent 不阻塞主流程 (state.db 可能被 hermes 锁着).
    """
    if not STATE_DB.exists():
        return False
    try:
        conn = sqlite3.connect(str(STATE_DB), timeout=2.0)
        try:
            cur = conn.execute(
                "UPDATE sessions SET title = ? "
                "WHERE id = ? AND (title IS NULL OR title = '')",
                (title, session_id),
            )
            conn.commit()
            updated = cur.rowcount > 0
        finally:
            conn.close()
        if updated:
            logger.info(
                "session title 写回: session=%s title=%r", session_id, title[:50]
            )
        return updated
    except sqlite3.Error as e:
        logger.warning(
            "update_session_title 失败 (sqlite busy?) session=%s: %s",
            session_id, e,
        )
        return False


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


# BL-F15 (5/5): session 级失败冷却, 防 quota_exceeded 死循环
# session_id → 重试时间戳 (epoch 秒). 在此时间前不再尝试 summarize 该 session.
_SESSION_COOL_DOWN: dict[str, float] = {}
_COOL_DOWN_SECONDS = 300  # 5 分钟


# 5/5 凌晨修 race condition: 每条 chat 都 fire trigger_background_summary, 多个并发 task
# 查 _list_unjournaled 都看到 session X 没 journaled, 同时跑 summarize, 各自 append, mark
# 来不及拦. 鸿波看到 1 个 session_id 被总结了 6 次, journal 6 条重复条目.
# 修: in-memory 锁, 进 summarize 前查锁, 已在跑就 skip.
_CURRENTLY_SUMMARIZING: set[str] = set()


def _is_session_cooling_down(session_id: str) -> bool:
    import time  # noqa: PLC0415
    until = _SESSION_COOL_DOWN.get(session_id)
    if until is None:
        return False
    if time.time() >= until:
        # 过期清理
        _SESSION_COOL_DOWN.pop(session_id, None)
        return False
    return True


def _mark_session_cool_down(session_id: str) -> None:
    import time  # noqa: PLC0415
    _SESSION_COOL_DOWN[session_id] = time.time() + _COOL_DOWN_SECONDS


async def _summarize_with_llm(
    session_id: str, started_at: float, messages_pairs: list[tuple[str, str]]
) -> str | None:
    """调 LLM 总结. 失败返 None.

    走本机 gateway HTTP (BL-F12 5/4 鸿波 explicit 拍板, 之前直接 import litellm 绕过 gateway):
    - 自动 fallback (catalog 里 catfish-public-qwen-flash 的 chain: qwen → gemini-flash)
    - 复用 quota / metrics / brand scrub
    - catalog 改了 summarizer 跟着改, 不再写死模型名
    - 跟主 chat 同一套机制, 一个改全跟

    跟主 chat 区分:
    - 加 X-Catfish-Skip-Identity: true 防 SOUL/journal/skills/session_history 等再次注入
      (我们就是在生成 journal 内容, 让 LLM 反过来引一遍 journal 是循环)

    BL-F15 (5/5): 修 quota_exceeded 死循环
    - 真问题: gateway quota check 在 with_fallback 之前抛 429, fallback chain 不接.
      summarizer 一直撞主模型 quota → 撞错 → 立即被下一条 chat 触发又跑 → 死循环
    - 修法 1: 用 picker 的**候选列表**, 收到 429 就切下一个 (绕过 quota check 限制)
    - 修法 2: 全候选都 429 → 标 session 5 分钟冷却, 不再 hammer
    - 长期: BL-F16 把 quota check 移进 with_fallback (demo 后)
    """
    if not messages_pairs:
        return None

    # BL-F15: 冷却中的 session 不重试, 防 hammer
    if _is_session_cooling_down(session_id):
        return None

    # 拼上下文
    context_lines = []
    for role, content in messages_pairs[:MAX_MESSAGES_PER_SESSION]:
        snippet = (content or "")[:500]
        context_lines.append(f"[{role}]: {snippet}")
    context = "\n\n".join(context_lines)

    user_prompt = _SUMMARY_PROMPT + f"\n\n会话历史:\n\n{context}"

    import os  # noqa: PLC0415

    import httpx  # noqa: PLC0415

    from .config import load_config  # noqa: PLC0415
    from .internal_models import pick_internal_models_ordered  # noqa: PLC0415

    # BL-F14 + F15: 拿候选列表 (不只 1 个), 第 1 个撞 quota 就切第 2 个.
    # 顺序: tag+private → tag+public → 兜底 private → 兜底 public.
    config = load_config()
    candidates = pick_internal_models_ordered("summarizer", config)
    if not candidates:
        logger.info(
            "summarize_with_llm 跳过 session=%s: catalog 没可用 chat 模型 (api keys 全没配?)",
            session_id,
        )
        return None

    port = os.environ.get("PORT", "8999")
    gateway_url = os.environ.get(
        "CATFISH_GATEWAY_INTERNAL_URL",
        f"http://127.0.0.1:{port}/v1/chat/completions",
    )
    # BL-FIX37 (5/10): internal-only token. BL-FIX29 关员工 dev_token 后, 必须
    # 用 ensure_internal_dev_token() 拿 internal random, 否则 session_summarizer
    # 调 gateway loopback 401 → employee_journal 不再更新.
    from .auth.dev_token import ensure_internal_dev_token  # 懒 import
    dev_token = ensure_internal_dev_token()

    last_error: str | None = None
    for attempt_idx, chosen_model in enumerate(candidates, start=1):
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    gateway_url,
                    headers={
                        "Authorization": f"Bearer {dev_token}",
                        "X-Catfish-Skip-Identity": "true",
                        "X-Catfish-Internal": "true",  # BL-F17: 跳 quota check + 不算 user_day
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": chosen_model.name,
                        "messages": [{"role": "user", "content": user_prompt}],
                        "temperature": 0.3,
                        "max_tokens": 600,
                        "stream": False,
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    text = data.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
                    if attempt_idx > 1:
                        logger.info(
                            "summarize_with_llm session=%s 切换到第 %d 候选 %s 成功",
                            session_id, attempt_idx, chosen_model.name,
                        )
                    return text.strip() or None
                # 429 quota_exceeded → 切下一个候选 (BL-F15 关键)
                if resp.status_code == 429:
                    logger.info(
                        "summarize_with_llm session=%s: %s 撞 429 quota, 切下一个候选 (剩 %d)",
                        session_id, chosen_model.name, len(candidates) - attempt_idx,
                    )
                    last_error = f"429 quota: {chosen_model.name}"
                    continue
                # BL-F15 (5/5) 老写法 break 不切. 5/17 鸿波本机不在内网撞:
                # 第 1 候选 catfish-private-main 撞 ServerDisconnected →
                # gateway BL-FALLBACK-TOGGLE=False 返 502 → break → 不试公网
                # 候选. 但 candidates 列表第 2+ 项就是公网 qwen-flash/
                # deepseek/gemini, 应该试. 5xx (上游 / 网络问题) 切候选;
                # 4xx (client request 格式 / RBAC 等) 切候选也没用, break.
                last_error = f"{resp.status_code}: {resp.text[:200]}"
                if 500 <= resp.status_code < 600:
                    logger.info(
                        "summarize_with_llm session=%s: %s 撞 %d (5xx 上游), "
                        "切下一个候选 (剩 %d)",
                        session_id, chosen_model.name, resp.status_code,
                        len(candidates) - attempt_idx,
                    )
                    continue
                logger.warning(
                    "summarize_with_llm session=%s: gateway 返 %d (%s, 非 5xx), 不再切候选",
                    session_id, resp.status_code, chosen_model.name,
                )
                break
        except Exception as e:
            last_error = f"exception: {type(e).__name__}: {e}"
            logger.warning(
                "summarize_with_llm session=%s 候选 %s 异常 %s, 切下一个",
                session_id, chosen_model.name, e,
            )
            continue

    # 所有候选都失败 → 标记冷却, 防下次 chat 立即又来 hammer
    logger.warning(
        "summarize_with_llm session=%s 全 %d 候选都失败, 标记 %ds 冷却. 最后错: %s",
        session_id, len(candidates), _COOL_DOWN_SECONDS, last_error,
    )
    _mark_session_cool_down(session_id)
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

    # 5/5 凌晨修 race condition: 多 chat 并发 fire trigger_background_summary,
    # 都查到同一 session 没 journaled → 都跑 summarize → journal 重复条目.
    # in-memory 锁: 已在跑就 skip 不再开第 2 个 task.
    if session_id in _CURRENTLY_SUMMARIZING:
        logger.debug(
            "summarize_one_session skip session=%s: 已有 task 在跑 (race condition 防御)",
            session_id,
        )
        return False
    _CURRENTLY_SUMMARIZING.add(session_id)

    logger.info(
        "summarize_one_session: session=%s, msg=%d, started=%s",
        session_id,
        msg_count,
        date_str,
    )

    try:
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
            # BL-SESSION-MGMT B (5/15): 顺手把短标题写回 state.db sessions.title,
            # 让 Companion sidebar 显真主题 (不是裸 timestamp).
            # 取 summary 第一句, ≤30 字. 只更新原本 title 为空的 session — 不覆盖
            # 员工手起的名 / hermes 已生成的名.
            short_title = _extract_short_title(summary)
            if short_title:
                _update_session_title(session_id, short_title)
            # 只有 journal 真写入了, 才 mark
            _mark_journaled(session_id)
            return True
        except Exception as e:
            logger.warning("append_to_journal 失败 session=%s: %s", session_id, e)
            return False
    finally:
        # 必移除锁, 即使中间挂了 (防 session 永久不能再 summarize)
        _CURRENTLY_SUMMARIZING.discard(session_id)


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
