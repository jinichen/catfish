"""按当前问题从长期记忆里捞相关的几条 (10/22 P1)。

# 病

prefetch 原来给主聊天的长期记忆是 distilled_facts.md 的**前 3KB** —— 按文件位置截,
跟员工问的是什么无关。10/22 那份文件 207KB / 192 段, 前 3KB 正好是「当前状态」段
加最新一段的几行; 两个月前定下的偏好、某个人是谁、某件事当时怎么决定的, 主聊天
永远看不到, 只能指望模型自己想起来去调工具。

# 做法

  · 「当前状态」段 (段 0, 蒸馏时 LLM 合并的进行中 / 已完结 / 暂停) 照旧整段给 ——
    它本来就是按"现在"写的, 跟问题无关也该知道。
  · 其余各段拆成一条条 (段日期, 小节, 一行), 用员工这句话做 BM25 检索, 取最相关的
    几条, 带日期给出去。同一句话在多段里重复出现只留最新那条。
  · 分词: jieba (hermes 环境里本来就装着, Companion 的依赖检查里有它); 万一没有,
    退回中文双字 + 英文词。索引按文件 mtime 缓存在进程里 —— distilled 一天才变一次,
    每轮只算一次问题的分词。

# 不做

  · 不调 LLM、不算向量: prefetch 每轮都跑, hermes 给它 8 秒超时。向量检索在
    Companion 侧 (BGE-M3) 有, 但 Intel Mac 没有本地运行时, 这里要处处能用。
"""
from __future__ import annotations

import math
import re
import threading
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    from .catfish_memory_distill_reconcile import parse_segments
except ImportError:  # 独立脚本模式 (无父包)
    from catfish_memory_distill_reconcile import parse_segments

#: 段 0 的标题 (见 catfish_memory_distill_reconcile.assemble)
_HEAD_RE = re.compile(r"^### 蒸馏段 0\b.*$", re.MULTILINE)
_NEXT_SEG_RE = re.compile(r"^### 蒸馏段 [1-9]\d*", re.MULTILINE)
_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$")

#: 这些字不构成检索信号
_STOP = set("的了是在我你他她它们和跟与及也都就这那有没不吗呢吧啊呀哦么个把被给让对从到为以而或")
#: 问句里的功能词。distilled 是陈述句, 这些词在里面反而稀少 → IDF 很高 → 「今天先做什么」
#: 会因为「什么 / 先 / 做」撞上一堆无关条目 (10/22 实测 IDF 7.7 / 4.9 / 5.6)。只从问题侧去掉。
_QUERY_STOP = {
    "什么", "怎么", "怎样", "如何", "哪些", "哪个", "哪里", "为什么", "是不是", "能不能", "可以", "一下",
    "帮", "帮我", "看看", "看", "现在", "还", "要", "做", "先", "今天", "明天", "昨天", "以后", "之前",
    "这个", "那个", "一个", "是谁", "多少", "有没有", "需要", "应该", "请", "下", "再", "吗", "呢",
}
_BM25_K1 = 1.5
_BM25_B = 0.75
#: 低于这个分数的不给。问句噪音主要靠 _QUERY_STOP 挡, 这道是给大语料兜底的。
#: 小语料 (新装机器只有几段蒸馏) IDF 整体偏低, 真命中也就 0.9 分 —— 条目少于
#: _MIN_SCORE_FROM 时不设门槛, 撞上就算 (条目少, 本来也没多少噪音)。
_MIN_SCORE = 1.0
_MIN_SCORE_FROM = 200


def current_status_block(distilled: str, max_chars: int = 2400) -> str:
    """段 0「当前状态」整段 (含标题)。没有段 0 (老格式 / 合并从没成过) 返空。"""
    m = _HEAD_RE.search(distilled)
    if not m:
        return ""
    nxt = _NEXT_SEG_RE.search(distilled, m.end())
    block = distilled[m.start(): nxt.start() if nxt else len(distilled)].strip()
    if len(block) > max_chars:
        cut = block.rfind("\n", 0, max_chars)
        block = block[: cut if cut > 0 else max_chars].rstrip() + "\n…"
    return block


# ── 分词 ──────────────────────────────────────────────────────

_jieba = None
_jieba_tried = False
_jieba_lock = threading.Lock()


def _get_jieba():
    global _jieba, _jieba_tried
    if _jieba_tried:
        return _jieba
    with _jieba_lock:
        if not _jieba_tried:
            try:
                import jieba  # type: ignore

                jieba.setLogLevel(60)  # 别往 hermes 日志里刷 "Building prefix dict"
                _jieba = jieba
            except Exception:  # noqa: BLE001 —— 没装就退回双字
                _jieba = None
            _jieba_tried = True
    return _jieba


def tokenize(text: str) -> List[str]:
    text = text.lower()
    jb = _get_jieba()
    out: List[str] = []
    if jb is not None:
        for w in jb.lcut_for_search(text):
            w = w.strip()
            if not w or all(ch in _STOP or not ch.isalnum() for ch in w):
                continue
            out.append(w)
        return out
    # 兜底: 英文 / 数字整词 + 中文双字 (单字太泛)
    for word in re.findall(r"[a-z0-9][a-z0-9\-_.]*", text):
        out.append(word)
    han = [ch for ch in text if "一" <= ch <= "鿿" and ch not in _STOP]
    out.extend(a + b for a, b in zip(han, han[1:]))
    return out


# ── 索引 ──────────────────────────────────────────────────────

Item = Tuple[str, str, str]  # (段日期, 小节, 一行)


def split_items(distilled: str) -> List[Item]:
    """各段 (段 0 除外) 拆成 (日期, 小节, 行), 新段在前; 同一行多段重复只留最新。"""
    items: List[Item] = []
    seen = set()
    for label, text in reversed(parse_segments(distilled)):  # parse_segments 是旧→新
        section = ""
        for raw in text.splitlines():
            line = raw.strip()
            m = _SECTION_RE.match(line)
            if m:
                section = m.group(1)
                continue
            line = line.lstrip("-*• ").strip()
            if len(line) < 4 or line in ("(无)", "无"):
                continue
            key = re.sub(r"\s+", "", line)
            if key in seen:
                continue
            seen.add(key)
            items.append((label or "日期不明", section, line))
    return items


class _Index:
    def __init__(self, items: List[Item]):
        self.items = items
        self.docs = [Counter(tokenize(f"{sec} {line}")) for _, sec, line in items]
        self.lens = [sum(d.values()) for d in self.docs]
        self.avg = (sum(self.lens) / len(self.lens)) if self.lens else 1.0
        df: Counter = Counter()
        for d in self.docs:
            df.update(d.keys())
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def search(self, query: str, top_k: int) -> List[Tuple[float, Item]]:
        q = [t for t in set(tokenize(query)) if t in self.idf and t not in _QUERY_STOP]
        if not q:
            return []
        scored = []
        n = len(self.docs)
        floor = _MIN_SCORE if n >= _MIN_SCORE_FROM else 1e-9
        for i, d in enumerate(self.docs):
            s = 0.0
            norm = _BM25_K1 * (1 - _BM25_B + _BM25_B * self.lens[i] / self.avg)
            for t in q:
                f = d.get(t)
                if f:
                    s += self.idf[t] * f * (_BM25_K1 + 1) / (f + norm)
            if s >= floor:
                # items 新段在前: 同样相关时新的排前面 (最新的那条最高 +25%)
                scored.append((s * (1 + 0.25 * (1 - i / n)), i))
        scored.sort(key=lambda x: -x[0])
        return [(s, self.items[i]) for s, i in scored[:top_k]]


_cache: Dict[str, Tuple[Tuple[float, int], _Index]] = {}
_cache_lock = threading.Lock()


def _index_for(path: Path, distilled: str) -> _Index:
    try:
        st = path.stat()
        stamp = (st.st_mtime, st.st_size)
    except OSError:
        stamp = (0.0, len(distilled))
    key = str(path)
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] == stamp:
            return hit[1]
    idx = _Index(split_items(distilled))
    with _cache_lock:
        _cache[key] = (stamp, idx)
    return idx


#: 10/23 评测 (scripts/eval_memory_recall.py, 35 题): 12 条 → 29/35, 20 条 → 31/35 (检索段平均
#: 644 → 1006 字), 再往上 30 条才多 1 题、字数翻倍。剩下没命中的是同义改写 (起草/草稿), 词面检索管不到。
_TOP_K = 20


def recall_block(path: Path, distilled: str, query: str, budget: int, top_k: int = _TOP_K) -> str:
    """跟 query 最相关的几条长期记忆 (带段日期)。query 空 / 没有够格的 → 空串。"""
    if not query or not query.strip() or not distilled.strip():
        return ""
    hits = _index_for(path, distilled).search(query, top_k)
    if not hits:
        return ""
    lines: List[str] = []
    used = 0
    for _, (label, section, line) in hits:
        row = f"- [{label}{' · ' + section if section else ''}] {line}"
        if used + len(row) > budget:
            break
        lines.append(row)
        used += len(row) + 1
    if not lines:
        return ""
    return (
        "## 🔎 跟这个问题相关的长期记忆 (按问题检索; 方括号里是那段记录的日期, 越新越可信)\n\n"
        + "\n".join(lines)
    )


def prewarm(path: Optional[Path]) -> None:
    """后台线程里先加载 jieba 词典并建好索引, 省得第一轮对话多等一两秒。失败无所谓。"""
    try:
        _get_jieba()
        if path is not None and path.exists():
            _index_for(path, path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:  # noqa: BLE001
        pass
