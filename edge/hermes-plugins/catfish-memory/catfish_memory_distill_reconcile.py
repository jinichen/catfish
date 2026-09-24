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


def assemble(segments: List[Tuple[str, str]], current: Optional[str]) -> str:
    """当前状态在最前, 其后各段新在前。段头保留「### 蒸馏段 N」(advisor_relevance 按它切)。"""
    head = (
        f"### 蒸馏段 0 · 当前状态（截至 {time.strftime('%Y-%m-%d')}，以此为准）\n\n"
        "> 下面各段是不同时期的记录, 新的在前; 旧段里的「进行中」可能早已结束, 以本段为准。\n\n"
        + (current or fallback_current_status(segments)).strip()
    )
    body = [
        f"### 蒸馏段 {i + 1}" + (f"（{label}）" if label else "") + f"\n\n{text}"
        for i, (label, text) in enumerate(segments)
    ]
    return "\n\n".join([head, *reversed(body)])
