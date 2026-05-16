"""inject_session_history — 跨 session 上下文档 1.

# 为啥需要 (鸿波 2026-04-30 反馈"跨对话信息割裂, 不像真实个体")

LLM 应用最深的痛点 — 每个 session 是孤岛, 模型不知道员工"上次/之前/上周"做了什么.
鸿波 195 条对话里协商出来的"4 段框架 + 表格附件化"决策, 新对话**等于零**.

# 怎么做 (档 1 — 极简版)

读 `~/.hermes/state.db` 取最近 7 天 session 元信息 (id / 时间 / 消息数 / 首条 user message),
注入 system prompt 顶部. 模型每次推理都看到这些**历史索引**, 至少**意识到**有这些 session 存在.

如果员工说"按上次格式写", 模型可以:
  1. 看注入的 session 概览 → 找到"4-29 协商汇报材料 4 段框架"那条
  2. 用 `session_search` 工具拉详细内容
  3. 引用具体决策

档 1 只解决"模型知道有历史"问题. 档 2 (employee_journal) 解决"模型知道历史里讲了啥".

# BL-MEMORY-FTS5-RECALL (5/16 鸿波 'inject 改相关性召回')

旧逻辑: 按时间倒序拿最近 10 个 session, 不管员工**这次问的是什么主题**. 跟员工
当前问题不相关的老 session 占 token. 改成:

  1. 抽当前 chat 最后一条 user message 关键词 (短句直接传, 长 prompt 取首 200 字)
  2. 多 token AND 子串搜索 (LIKE) message content → 拿命中 session_id, 按 message ID
     倒序排 (近期权重高)
  3. join sessions 表拿元信息 → 注入 top-K (默认 5) 命中 session
  4. **没查询** 或 **无命中** → fallback 走老 "最近时间" 逻辑 (兼容)

为什么不用 FTS5: hermes state.db 虽然有 messages_fts 表, 但 FTS5 默认 tokenizer
对中文分词不可靠 (沙盒 sqlite 3.37 不支持中文 trigram, 本机 3.40+ 行为又不同).
LIKE %query% 跨版本一致, hermes messages 表也就几千行, LIKE 扫完 <10ms. 等
hermes 把 FTS5 tokenizer 标准化后再切, 不抢这一步.

效果: 员工问"上次资质方案怎么定的" → 命中"资质" 子串的老 session 排前, 不再被
时间窗口里跟资质无关的 5 个 session 挤掉. token 跟老版一样, 但召回相关性高一个量级.

# 跟其他 inject 的关系 (顺序很重要)

  identity → session_facts → stats_guard → skills_catalog → skill_guard →
  **session_history** (本模块, 在 chat 主体之前提供历史背景) → multimodal → tool_capability

放最后 — 它是"跨 session 元信息", 不影响其他 inject.

# Token 限制

最近 10 个 session × ~50 token/session = ~500 token. 可控.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger("catfish.gateway.session_history")

#: 看回过去多少天
DAYS_BACK = 7

#: 最多列多少个 session (防 token 爆炸)
MAX_SESSIONS = 10

#: FTS5 召回的 top-K (BL-MEMORY-FTS5-RECALL): 比时间窗口小一半, 因为相关性高 → 5 个够用
MAX_RELEVANT_SESSIONS = 5

#: 首条 user message 截断字符数
PREVIEW_CHARS = 100

#: 拿 user message 前 N 字符作 FTS5 query (太长的 query FTS5 性能差且容易没结果)
QUERY_MAX_CHARS = 200


def get_state_db_path() -> Path | None:
    """hermes session DB. 不存在返 None (hermes 没用过)."""
    p = Path.home() / ".hermes" / "state.db"
    return p if p.exists() else None


def get_recent_sessions() -> list[tuple[str, float, int, str | None, str | None]]:
    """读 state.db 最近 7 天 sessions, 返回 [(id, started_at, msg_count, title, first_user_msg)].

    安全: read-only 连接, 1 秒 timeout, 异常吞掉返空.
    """
    db_path = get_state_db_path()
    if db_path is None:
        return []

    cutoff_ts = (datetime.now() - timedelta(days=DAYS_BACK)).timestamp()

    try:
        conn = sqlite3.connect(
            f"file:{db_path}?mode=ro", uri=True, timeout=1.0
        )
        rows = conn.execute(
            """
            SELECT
                s.id,
                s.started_at,
                s.message_count,
                s.title,
                (SELECT m.content
                 FROM messages m
                 WHERE m.session_id = s.id AND m.role = 'user'
                 ORDER BY m.id LIMIT 1) AS first_user_msg
            FROM sessions s
            WHERE s.started_at >= ?
              AND s.message_count > 1
            ORDER BY s.started_at DESC
            LIMIT ?
            """,
            (cutoff_ts, MAX_SESSIONS),
        ).fetchall()
        conn.close()
        return rows
    except sqlite3.Error as e:
        logger.warning("inject_session_history: 读 state.db 失败: %s", e)
        return []


# ============================================================
# BL-MEMORY-FTS5-RECALL — 相关性召回
# ============================================================


#: 停用词 — 中英文高频词, 搜了等于全表 (LIKE %的% 命中所有有 "的" 的 message)
_STOPWORDS = frozenset({
    # 英文
    "the", "a", "an", "is", "are", "was", "were", "be", "to", "of", "in",
    "on", "for", "and", "or", "but",
    # 中文代词
    "我", "你", "他", "她", "它", "我们", "你们", "他们",
    # 中文虚词
    "的", "了", "是", "在", "和", "或者", "但是", "怎么", "什么", "如何",
    # 中文常用动作 / 修饰
    "请", "帮我", "给我", "把", "用", "做", "写", "上次", "之前",
    "下面", "这个", "那个", "一个",
})


def _extract_query_tokens(text: str) -> list[str]:
    """把 user message 切成关键词 tokens, 供 LIKE 子串搜索用.

    处理:
      1. 截首 QUERY_MAX_CHARS 防超长
      2. 标点 / 引号 替空格 (LIKE 参数绑定本身已防注入; 切词用)
      3. 切词, 滤 1 字符 noise + 停用词
      4. 去重保序, 最多 6 个

    返 ['资质', 'EIS', '登录'] 这种 token 列表 (空 list 表示无可搜内容).
    """
    if not text:
        return []
    s = text[:QUERY_MAX_CHARS]
    # 标点 / 引号 替空格 (LIKE 参数绑定本身已防注入)
    s = re.sub(r'[\'"`\\\(\)\*]', " ", s)
    s = re.sub(r"[，。！？；：、,.!?;:/\\|<>{}\[\]@#$%^&+=~`]", " ", s)
    # jieba 中文分词 (pyproject 已 depend jieba 0.42) — 之前用 split() 中文整段
    # 当一 token, LIKE %登录怎么做的% 命中不了 "登录技能" 子串. jieba 切成
    # "登录"+"怎么"+"做"+"的" 后, "登录" 能匹配.
    try:
        import jieba  # noqa: PLC0415

        # cut_for_search 比 cut 切得更细, 利于子串召回
        raw = list(jieba.cut_for_search(s))
    except ImportError:
        # jieba 没装 fallback 简单 split (中文召回精度差, 但不崩)
        raw = s.split()
    seen: set[str] = set()
    deduped: list[str] = []
    for t in raw:
        t = t.strip()
        if not t or t.isspace():
            continue
        if t.lower() in _STOPWORDS:
            continue
        if len(t) < 2:  # 单字符 noise 多 (中英文都)
            continue
        # BL-MEMORY-POLISH (5/16 鸿波实盘 tokens=['2026','05','12','09','26',...]):
        # 纯数字 < 4 字符 skip — 日期 / 时间 / 编号 (2026 / 05 / 12 等) jieba 切出来
        # 大量噪声 token, AND 之后 LIKE 召回精度低. 4 字符以上数字 (例 "2026") 留下
        # 是因为年份等长数字仍有定位价值. 注意"2026"是 4 字符正好留 (≥ 4).
        if t.isdigit() and len(t) < 4:
            continue
        if t not in seen:
            seen.add(t)
            deduped.append(t)
        if len(deduped) >= 6:
            break
    return deduped


def _fts5_sanitize(text: str) -> str:
    """FTS5 query syntax sanitize.

    FTS5 对引号 / AND OR NOT NEAR / 圆括号 / 星号 敏感 — 直接传原文容易 'syntax error'.
    处理:
      1. 引号 / 反斜杠 / 圆括号 / 星号 / 加号 / 减号 替空格 (FTS5 reserved char)
      2. AND OR NOT NEAR (大写) 替小写 (FTS5 只把大写当 operator)
    """
    if not text:
        return ""
    s = re.sub(r'[\'"`\\()*+\-^]', " ", text)
    s = re.sub(r"\b(AND|OR|NOT|NEAR)\b", lambda m: m.group(0).lower(), s)
    return s.strip()


def _which_fts5_table(conn: sqlite3.Connection) -> str | None:
    """检测 hermes state.db 有哪个 FTS5 表 (trigram 优先, fallback unicode61).

    返表名 ('messages_fts_trigram' / 'messages_fts' / None).
    没 FTS5 表 → 调用方 fallback LIKE.
    """
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name IN ('messages_fts_trigram', 'messages_fts')"
        ).fetchall()
        names = {r[0] for r in rows}
        if "messages_fts_trigram" in names:
            return "messages_fts_trigram"  # 中文 trigram 优先
        if "messages_fts" in names:
            return "messages_fts"
        return None
    except sqlite3.Error:
        return None


def _query_via_fts5(
    conn: sqlite3.Connection, table: str, tokens: list[str], top_k: int,
) -> list[tuple] | None:
    """走 FTS5 trigram / unicode61 表查询, 返 session 元信息列表.

    成功返 rows (可能空), 失败返 None (调用方 fallback LIKE).

    Query 构造: tokens 用 AND 连接 (FTS5 默认 AND, 显式写防 syntax 歧义).
    Ranking: bm25() 越低越相关.
    """
    # 拼 query: 'tok1 AND tok2 AND tok3'. 单 token 直接传.
    sanitized = [_fts5_sanitize(t) for t in tokens if t.strip()]
    sanitized = [t for t in sanitized if t]
    if not sanitized:
        return None
    fts_query = " AND ".join(sanitized) if len(sanitized) > 1 else sanitized[0]

    sql = f"""
        SELECT
            s.id,
            s.started_at,
            s.message_count,
            s.title,
            (SELECT m2.content FROM messages m2
             WHERE m2.session_id = s.id AND m2.role = 'user'
             ORDER BY m2.id LIMIT 1) AS first_user_msg
        FROM sessions s
        INNER JOIN (
            SELECT
                m.session_id AS session_id,
                MIN(bm25({table})) AS best_score
            FROM {table}
            INNER JOIN messages m ON {table}.rowid = m.id
            WHERE {table} MATCH ?
            GROUP BY m.session_id
            ORDER BY best_score ASC
            LIMIT ?
        ) hits ON s.id = hits.session_id
        WHERE s.message_count > 1
        ORDER BY hits.best_score ASC
    """
    try:
        rows = conn.execute(sql, (fts_query, top_k)).fetchall()
        return rows
    except sqlite3.Error as e:
        # FTS5 syntax error / 其它 → caller fallback LIKE
        logger.warning(
            "FTS5 (%s) query 失败 (fallback LIKE): %s. q=%r",
            table, e, fts_query[:80],
        )
        return None


def _query_via_like(
    conn: sqlite3.Connection, tokens: list[str], top_k: int,
) -> list[tuple]:
    """LIKE 子串搜索 (FTS5 失败 / 没 FTS5 表时的最终 fallback).

    多 token AND `m.content LIKE %t%`. 按 MAX(m.id) 近期命中排序.
    """
    like_clauses = " AND ".join(["m.content LIKE ?"] * len(tokens))
    like_params: list[str | int] = [f"%{t}%" for t in tokens]
    like_params.append(top_k)

    sql = f"""
        SELECT
            s.id,
            s.started_at,
            s.message_count,
            s.title,
            (SELECT m2.content FROM messages m2
             WHERE m2.session_id = s.id AND m2.role = 'user'
             ORDER BY m2.id LIMIT 1) AS first_user_msg
        FROM sessions s
        INNER JOIN (
            SELECT
                m.session_id AS session_id,
                MAX(m.id) AS last_hit_id
            FROM messages m
            WHERE {like_clauses}
            GROUP BY m.session_id
            ORDER BY last_hit_id DESC
            LIMIT ?
        ) hits ON s.id = hits.session_id
        WHERE s.message_count > 1
        ORDER BY hits.last_hit_id DESC
    """
    try:
        return conn.execute(sql, like_params).fetchall()
    except sqlite3.Error as e:
        logger.warning("LIKE 召回失败: %s", e)
        return []


def get_relevant_sessions(
    query: str, top_k: int = MAX_RELEVANT_SESSIONS
) -> list[tuple[str, float, int, str | None, str | None]]:
    """3 层降级召回相关 session 元信息.

    BL-MEMORY-FTS5-REAL (5/16 鸿波 'Step 2 FTS5 真集成'): 改成 FTS5 优先 + LIKE 兜底.

    流程:
      1. 抽 query tokens (jieba 中文分词 + 去停用词 / 单字符)
      2. 检测 hermes state.db FTS5 表 (鸿波本机有 messages_fts_trigram + messages_fts):
         a. trigram 表存在 (sqlite 3.34+, 中文 3-gram) → 优先用
         b. 否则 unicode61 表 (中文整段当 token, 准确度差但能跑)
      3. FTS5 query (BM25 ranking, 越低越相关)
      4. FTS5 0 hits 或 syntax error → fallback LIKE %t% AND ...
      5. 都失败 → 返空, 调用方 fallback 时间窗口

    跨 sqlite 版本兼容:
      - 沙盒 3.37 没 trigram 表 / 中文 2 字符 query 不命中 — fallback LIKE
      - 本机 3.40+ + hermes 创建 trigram 表 — FTS5 BM25 准
      - 任意环境失败都不抛, 静默降级

    返回: 同 get_recent_sessions 形式. 没命中返空.
    """
    db_path = get_state_db_path()
    if db_path is None:
        return []

    tokens = _extract_query_tokens(query)
    if not tokens:
        return []

    try:
        conn = sqlite3.connect(
            f"file:{db_path}?mode=ro", uri=True, timeout=1.0
        )
    except sqlite3.Error as e:
        logger.warning("connect state.db 失败: %s", e)
        return []

    try:
        # Layer 1: FTS5 (trigram 优先)
        fts_table = _which_fts5_table(conn)
        rows: list[tuple] = []
        used_layer = "none"
        if fts_table:
            fts_rows = _query_via_fts5(conn, fts_table, tokens, top_k)
            if fts_rows is not None and fts_rows:
                rows = fts_rows
                used_layer = f"fts5({fts_table})"

        # Layer 2: LIKE fallback (FTS5 0 hits / 不可用)
        if not rows:
            rows = _query_via_like(conn, tokens, top_k)
            if rows:
                used_layer = "like"

        logger.info(
            "BL-MEMORY-FTS5-RECALL: tokens=%r 命中 %d session via %s",
            tokens, len(rows), used_layer,
        )
        return rows
    finally:
        conn.close()


def _extract_user_query(messages: list[dict[str, Any]]) -> str:
    """从 messages 抽最后一条 user message 文本作 FTS5 query.

    multimodal user message (content 是 list, 含 image_url / text 等) 也兼容: 拼 text 段.
    返空 str → 调用方 fallback 时间窗口.
    """
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    t = item.get("text", "")
                    if isinstance(t, str):
                        parts.append(t)
            return " ".join(parts).strip()
        # 其它类型 (None / dict) → skip
        return ""
    return ""


def format_block(sessions: list[tuple], *, relevant_mode: bool = False) -> str:
    """渲染成 system prompt 用的 markdown 块.

    relevant_mode=True (BL-MEMORY-FTS5-RECALL): 标题说明是按当前提问相关性召回的,
    不是按时间. 这让模型知道列表里都是**跟当前问题相关**的老 session.
    """
    if not sessions:
        return ""

    if relevant_mode:
        header = "## 📅 跟你当前提问最相关的 session (FTS5 召回, 按相关性排)"
        instruction = (
            "下面 session 是按**跟当前 user message 相关性**召回的, 不是时间. "
            "员工说「上次 / 之前 / 那个 X / 我们讨论过的」**优先在这里找**, "
            "必要时调 `session_search` 拉详细内容. **不要假装从零开始**."
        )
    else:
        header = "## 📅 员工最近 7 天 session 历史 (gateway 自动注入)"
        instruction = (
            "员工的每次对话都在下面. **你必须意识到这些历史存在** —— "
            "员工说「上次 / 之前 / 那个 X / 上周 / 昨天 / 我们讨论过的 / 之前定的」时, "
            "**优先在这里找相关 session**, 必要时调 `session_search` 拉详细内容. "
            "**不要假装从零开始** — 员工会觉得你是 100 个素不相识的人轮流帮他."
        )
    lines = ["", header, "", instruction, ""]
    for sid, started_at, msg_count, title, first_msg in sessions:
        try:
            date_str = datetime.fromtimestamp(started_at).strftime("%m-%d %H:%M")
        except Exception:
            date_str = "?"
        title_part = f" · 「{title}」" if title else ""
        preview = (first_msg or "(无 user message)").replace("\n", " ").strip()
        if len(preview) > PREVIEW_CHARS:
            preview = preview[:PREVIEW_CHARS] + "…"
        # 只显示 session id 前 8 位 (跟 hermes UI 一致)
        sid_short = sid[:21] if len(sid) > 21 else sid
        lines.append(f"- **{date_str}**{title_part} · `{sid_short}` · {msg_count} 条")
        lines.append(f"    > {preview}")
    lines.append("")
    return "\n".join(lines)


def inject_session_history(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """在最后一条 system message 末尾追加 session 历史块.

    BL-MEMORY-FTS5-RECALL (5/16): 优先按相关性召回, fallback 时间窗口.
      1. 抽当前 user message → FTS5 search hermes state.db messages_fts
      2. 命中 ≥ 1 个 session → 用相关 session (relevant_mode=True, 注入标题强调按相关性)
      3. 命中 0 / FTS5 不可用 / 无 query → fallback 最近时间 N 个 session

    没 messages / 没 system message / 没 history → 原样返.
    幂等 (block 已存在不重复加).
    """
    if not messages:
        return messages

    # 优先 FTS5 相关性召回 (BL-MEMORY-FTS5-RECALL)
    user_query = _extract_user_query(messages)
    relevant_mode = False
    sessions: list[tuple] = []
    if user_query:
        sessions = get_relevant_sessions(user_query)
        if sessions:
            relevant_mode = True

    # 没相关 session → fallback 时间窗口 (老行为, 兼容)
    if not sessions:
        sessions = get_recent_sessions()

    block = format_block(sessions, relevant_mode=relevant_mode)
    if not block:
        return messages

    last_system_idx = -1
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "system":
            last_system_idx = i
            break
    if last_system_idx < 0:
        return messages

    cur = messages[last_system_idx].get("content", "")
    if not isinstance(cur, str):
        return messages
    # 幂等 — 两种 mode 标题都检 (BL-MEMORY-FTS5-RECALL 加了第二种)
    if (
        "员工最近 7 天 session 历史" in cur
        or "跟你当前提问最相关的 session" in cur
    ):
        return messages

    out = deepcopy(messages)
    out[last_system_idx]["content"] = (
        out[last_system_idx]["content"].rstrip() + "\n" + block
    )
    if logger.isEnabledFor(logging.INFO):
        logger.info(
            "inject_session_history: 注入 %d 个 session, block %d 字节",
            len(sessions), len(block),
        )
    return out


__all__ = [
    "get_state_db_path",
    "get_recent_sessions",
    "get_relevant_sessions",
    "format_block",
    "inject_session_history",
]
