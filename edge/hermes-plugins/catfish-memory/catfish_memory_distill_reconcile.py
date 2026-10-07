"""蒸馏结果的"当前状态"合并 + 新在前排序 (9/24)。

# 为什么 (9/24 鸿波"CMMI-5 不是已经拿到证书了, 怎么又出来了")

distilled_facts.md 是把整本 employee_journal.md 按 8000 字切段、**每段单独**蒸馏
再拼起来的。6 月那段写「CMMI-5 评审进行中」对那一段来说是对的 —— 它看不到 8/11
拿证那段。拼接结果里两种说法并存, 而且:

  · 段按时间**从旧到新**排, 第 1 段就是 6/5 的内容;
  · 早安画像/上下文 (briefing_context.rs) 只读文件**前 3000 字节** —— 读到的恰好是最旧的;
  · 画像据此把「CMMI-5 级评审 (进行中)」列进重点项目, 早安顾问又把它变成了卡片。

8/14、8/24、9/24 三次复发, 每次手改 distilled_facts 都会在下一轮 24h 重蒸时被覆盖。
改提示词也不够: 单段蒸馏时根本没有后面的信息。

# 这里做什么

1. 每段标日期范围 (取段内出现的 YYYY-MM-DD 最早/最晚)。
2. 所有段蒸完后, 把各段的「项目 / 任务状态 / 决策」按新→旧交给 LLM 合并一次,
   得到「当前状态」: 进行中 / 已完结 / 暂停, 以最新说法为准。LLM 失败就用确定性兜底
   (只看「任务状态」段, 新段优先)。
3. 输出顺序: 当前状态在最前, 然后各段**新在前**。只读开头的消费方看到的是现在。
"""
from __future__ import annotations

import re
import time
from typing import List, Optional, Tuple

_DATE = re.compile(r"(20\d\d)-(\d\d)-(\d\d)")
_SECTION = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
#: 合并时带上的段 (人物/偏好跟"状态"无关, 不带, 省 token)
_STATUS_SECTIONS = ("项目", "任务状态", "决策")
#: 交给合并 LLM 的输入上限 (字符)。新段在前, 超出截掉的是最旧的。
_RECONCILE_INPUT_CHARS = 24000
#: 确定性兜底各段最多列几条 (新的在前)
_FALLBACK_DONE, _FALLBACK_PAUSED = 15, 6


def chunk_date_range(chunk: str) -> str:
    # 未来日期 (证书有效期至 2029-08-10 这类) 不是这段日志的时间, 不算
    today = time.strftime("%Y-%m-%d")
    dates = sorted({m.group(0) for m in _DATE.finditer(chunk) if m.group(0) <= today})
    if not dates:
        return ""
    return dates[0] if dates[0] == dates[-1] else f"{dates[0]} ~ {dates[-1]}"


def _sections(text: str) -> dict:
    out: dict = {}
    marks = list(_SECTION.finditer(text))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out[m.group(1).strip()] = text[m.end():end].strip()
    return out


def status_digest(segments: List[Tuple[str, str]]) -> str:
    """segments: [(日期范围, 该段蒸馏文本)] 按时间旧→新。返回新→旧的状态摘要。"""
    parts: List[str] = []
    for label, text in reversed(segments):
        secs = _sections(text)
        body = "\n".join(
            f"[{name}]\n{secs[name]}" for name in _STATUS_SECTIONS if secs.get(name)
        )
        if body:
            parts.append(f"=== {label or '日期不明'} ===\n{body}")
    digest = "\n\n".join(parts)
    return digest[:_RECONCILE_INPUT_CHARS]


def _item_key(line: str) -> str:
    item = line.lstrip("-* ").split("|")[0]
    item = re.split(r"[（(：:]", item)[0]
    return re.sub(r"[\s\-_·]", "", item).lower()


def fallback_current_status(segments: List[Tuple[str, str]]) -> str:
    """合并 LLM 不可用时的确定性兜底: 只看「任务状态」段, 同一事项以最新段为准。"""
    seen, done, paused = set(), [], []
    for label, text in reversed(segments):
        for line in _sections(text).get("任务状态", "").splitlines():
            if "|" not in line:
                continue
            key = _item_key(line)
            # 「CMMI-5 级评审准备」和「CMMI-5 级评审」是同一件事: 一个是另一个的前缀就算同一项
            if not key or any(key.startswith(k) or k.startswith(key) for k in seen):
                continue
            seen.add(key)
            state = line.rsplit("|", 1)[1].strip().lower()
            entry = f"- {line.lstrip('-* ').split('|')[0].strip()}（{label or '日期不明'}）"
            if state.startswith("resolved"):
                done.append(entry)
            elif state.startswith("paused"):
                paused.append(entry)
    # 兜底只给最近的: 开头这段会被只读前 3000 字节的消费方整段吃进去, 不能是一长串陈年琐事
    return (
        "## 已完结（不再进待办）\n" + ("\n".join(done[:_FALLBACK_DONE]) or "(无)")
        + "\n\n## 暂停/搁置\n" + ("\n".join(paused[:_FALLBACK_PAUSED]) or "(无)")
    )


_SEG_HEAD = re.compile(r"^### 蒸馏段 (\d+)(?:（(.*?)）)?\s*$", re.MULTILINE)
#: journal 条目头 `## [YYYY-MM-DD HH:MM] kind | sid` —— 切段只在这上面切
_ENTRY_HEAD = re.compile(r"^## \[", re.MULTILINE)


def parse_segments(distilled: str) -> List[Tuple[str, str]]:
    """把上一次写出的 distilled_facts.md 还原成 [(日期范围, 段文本)] **旧→新**。

    10/7 增量蒸馏用: 没变的旧 journal 不重蒸, 直接复用上次的段。段 0 (当前状态)
    是合并产物不是原材料, 跳过; 文件里新在前, 按段号升序排回旧→新。
    """
    marks = list(_SEG_HEAD.finditer(distilled))
    out: List[Tuple[int, str, str]] = []
    for i, m in enumerate(marks):
        n = int(m.group(1))
        if n == 0:
            continue
        end = marks[i + 1].start() if i + 1 < len(marks) else len(distilled)
        out.append((n, m.group(2) or "", distilled[m.end():end].strip()))
    out.sort(key=lambda t: t[0])
    return [(label, text) for _, label, text in out if text]


def split_journal_entries(text: str, max_chars: int) -> List[str]:
    """按 journal 条目边界切段, 每段 ≤ max_chars (单条超长的条目自成一段)。

    老切法是按字符数硬切, 一条 session 摘要会被劈成两半落在两段里, 两段各自
    蒸出半句话的状态 —— 那是"同一事件不同状态并存"的来源之一。单条超长时
    (raw fallback 能到几 KB) 不再硬切, 整条给 LLM。
    """
    if not text.strip():
        return []
    starts = [m.start() for m in _ENTRY_HEAD.finditer(text)]
    if not starts or starts[0] != 0:
        starts.insert(0, 0)
    entries = [text[a:b] for a, b in zip(starts, starts[1:] + [len(text)])]
    chunks: List[str] = []
    buf = ""
    for e in entries:
        if buf and len(buf) + len(e) > max_chars:
            chunks.append(buf)
            buf = ""
        buf += e
    if buf.strip():
        chunks.append(buf)
    return chunks


def merge_segments_by_date(
    prior: List[Tuple[str, str]], new: List[Tuple[str, str]],
) -> List[Tuple[str, str]]:
    """把本次蒸出的段按日期插进旧段序列 (旧→新), 稳定排序。

    补缺口 (10/7 晚) 蒸出的段落在中间, 不能一律追加到最后。排序键 = 段标签的
    起始日期; 没标签的段沿用它前面最近一个有标签段的日期 (保持相对位置)。
    """
    items: List[Tuple[str, Tuple[str, str]]] = []
    for seq in (prior, new):
        key = ""
        for label, text in seq:
            m = _DATE.search(label or "")
            if m:
                key = m.group(0)
            items.append((key, (label, text)))
    items.sort(key=lambda t: t[0])  # sort 是稳定的: 同日期保持 prior 在前、原序不变
    return [seg for _, seg in items]


def assemble(
    segments: List[Tuple[str, str]],
    current: Optional[str],
    *,
    chunks_total: int = 0,
    chunks_failed: int = 0,
) -> str:
    """当前状态在最前, 其后各段新在前。段头保留「### 蒸馏段 N」(advisor_relevance 按它切)。

    10/7: 头部只在 LLM 真合并过时才写「截至今天, 以此为准」。10/7 实测的事故是:
    合并调用超时 → 走确定性兜底 → 兜底只会列 8 月的 resolved/paused, 头上却仍
    盖着「截至 2026-10-07, 以此为准」—— 主聊天把两个月前的状态当成了现在。
    现在兜底头写清楚"基于 ≤ 哪天的记录、未经 LLM 合并", 段失败也写进去。
    """
    latest = next((label for label, _ in reversed(segments) if label), "")
    latest_day = latest.split("~")[-1].strip() if latest else "日期不明"
    if current:
        title = f"### 蒸馏段 0 · 当前状态（截至 {time.strftime('%Y-%m-%d')}，以此为准）"
        note = "> 下面各段是不同时期的记录, 新的在前; 旧段里的「进行中」可能早已结束, 以本段为准。"
    else:
        title = f"### 蒸馏段 0 · 当前状态（兜底生成，仅基于 ≤ {latest_day} 的记录，未经 LLM 合并）"
        note = (
            "> 状态合并调用失败, 本段是从各段「任务状态」机械取最新得到的, 只有已完结/暂停两类, "
            "**不代表截至今天的进展**; 比它新的事以「近期流水」和下面最新的段为准。"
        )
    if chunks_failed:
        note += (
            f"\n> 本次蒸馏 {chunks_failed}/{chunks_total} 段失败, 对应时期的记录缺失, "
            "下次蒸馏会补。"
        )
    head = f"{title}\n\n{note}\n\n" + (current or fallback_current_status(segments)).strip()
    body = [
        f"### 蒸馏段 {i + 1}" + (f"（{label}）" if label else "") + f"\n\n{text}"
        for i, (label, text) in enumerate(segments)
    ]
    return "\n\n".join([head, *reversed(body)])
