"""知识卡事实核对 v2: 检索原文 + 模型判断 (10/23)。

# 为什么换

v1 (catfish_memory_wiki_verify) 要求模型给每条事实附「一字不差的摘录」, 再拿摘录去原文里做字符串
比对。实测两头都不对:
  · 错杀: 模型的摘录少个字、加个省略号、两段拼一起、标题里的数字没进摘录 …… 事实是对的, 摘录对
    不上就被拿出正文。补一种写法又冒一种 (鸿波 10/23: 「正文里面有, 为什么被判定为没有?」);
  · 漏放: 摘录对得上, 不代表话写对了 —— 摘录抄「省政府印发」, 事实写成「省政府办公厅印发」, 照样过。
核对的对象应该是「这句话」本身, 不是「模型自报的摘录」。

# 做法

  1. 原文切成短段 (按句号 / 分号 / 换行, 长句再按逗号切, 太短的跟下一句并);
  2. 每条事实找候选段: 字面 (字二元 BM25) + 意思 (向量, 借 Companion 的本机 BGE-M3, 见
     catfish_memory_vectors.embed) 两路 RRF 合并取前几段; 摘录要是找得到, 它所在那段也放进来 ——
     摘录降级成线索, 不再是门槛;
  3. 硬约束 (不问模型): 事实里两位以上的数字原文里一处都没有 → 直接判编造;
     原文有但候选段里没有 → 把含这个数字的段补进候选;
  4. 一批事实一次调用, 让模型对着各自的候选段判「支持 / 部分 / 不支持」, 只收「支持」;
     依据 (evidence) 存模型指认的那段原文, 不是模型写的摘录。

一批调不通模型 (没 token / 网关报错 / 输出解析不了) 先重试; 还不行那批的每条退回 v1 摘录比对,
别的批照常 —— 不至于一批失败整段没人查。
CATFISH_WIKI_GROUND=0 关掉, 只用 v1。

# 评测 (10/23, catfish-public-qwen-flash)

scripts/eval_verify.py, 5 份资料蒸馏出的 307 条真事实 (更强的模型对着全文逐条标) + 215 条假事实
(真事实改数字 / 换名字 / 改否定, 摘录照旧留着):
            错杀 (真的拿出正文)   漏放 (假的留在正文)
  v1 摘录比对      5%                 50%   (换名字 34/35、改否定 26/26 全放过)
  v2 本模块        4%                 2-3%  (三遍: 4%/3%, 4%/3%, 4%/2%)
v2 错杀的大多是「部分」: 表里带 * 的 2024 年数据被说成 2025 年这类, 判得比标注还严。
「部分」也收的话错杀降到 ~0, 漏放涨到 ~20% —— 不收。
端到端 (eval_distill.py, 每份 2 遍): 召回 86% (v1 84%), 编造数字 0。
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

try:
    from . import catfish_memory_wiki_verify as verify
    from .catfish_memory_fm import _parse_frontmatter_lists, _split_frontmatter_body
    from .catfish_memory_helpers import _DISTILL_HTTP_TIMEOUT, _gateway_dev_token, _gateway_url, logger
    from .catfish_memory_gateway import with_source
except ImportError:  # 独立脚本模式 (无父包)
    import catfish_memory_wiki_verify as verify
    from catfish_memory_fm import _parse_frontmatter_lists, _split_frontmatter_body
    from catfish_memory_helpers import _DISTILL_HTTP_TIMEOUT, _gateway_dev_token, _gateway_url, logger
    from catfish_memory_gateway import with_source

#: 每条事实给模型看几段候选
TOP_K = 4
#: 一次调用最多判几条 (再多模型容易串行号)
BATCH = 20
#: 同时最多几个判断调用
CONCURRENCY = 3
#: 一批判断调不通 / 输出不对, 再试几次 (10/23 评测: 两路并发时偶尔一批 HTTP 失败)
RETRIES = 2
_SENT_RE = re.compile(r"(?<=[。！？!?；;])|\n+")
_MAX_PASSAGE = 220
_MIN_PASSAGE = 40
_RRF_K = 30
#: 去掉标点空白后不到这么多字的行不算事实 (10/23: 「```」被模型判成「支持」加回了正文)
_MIN_FACT = 4


def enabled() -> bool:
    return os.environ.get("CATFISH_WIKI_GROUND", "1") != "0"


# ---------------------------------------------------------------- 切段

def passages(source: str) -> List[str]:
    """原文 → 短段。长句按逗号切到 ≤220 字, 太短的 (标题、「一、」) 并进下一句。"""
    pieces: List[str] = []
    for sent in _SENT_RE.split(source):
        sent = sent.strip()
        if not sent:
            continue
        while len(sent) > _MAX_PASSAGE:
            cut = max(sent.rfind("，", 0, _MAX_PASSAGE), sent.rfind(",", 0, _MAX_PASSAGE))
            cut = cut + 1 if cut > _MAX_PASSAGE // 3 else _MAX_PASSAGE
            pieces.append(sent[:cut])
            sent = sent[cut:].strip()
        if sent:
            pieces.append(sent)
    out: List[str] = []
    buf = ""
    for p in pieces:
        buf = f"{buf} {p}".strip() if buf else p
        if len(buf) >= _MIN_PASSAGE:
            out.append(buf)
            buf = ""
    if buf:
        if out and len(out[-1]) + len(buf) <= _MAX_PASSAGE + _MIN_PASSAGE:
            out[-1] = f"{out[-1]} {buf}"
        else:
            out.append(buf)
    return out


# ---------------------------------------------------------------- 检索

def _grams(text: str) -> List[str]:
    t = verify.norm(text)
    return [t[i:i + 2] for i in range(len(t) - 1)] or ([t] if t else [])


class _Bm25:
    """字二元 BM25。中文不分词也够用: 事实和原文共享的是专名、数字、动宾短语。"""

    def __init__(self, docs: Sequence[str], k1: float = 1.2, b: float = 0.75):
        self.tf = []
        df: Dict[str, int] = {}
        for d in docs:
            counts: Dict[str, int] = {}
            for g in _grams(d):
                counts[g] = counts.get(g, 0) + 1
            self.tf.append(counts)
            for g in counts:
                df[g] = df.get(g, 0) + 1
        n = max(len(docs), 1)
        self.idf = {g: math.log(1 + (n - c + 0.5) / (c + 0.5)) for g, c in df.items()}
        self.len = [sum(c.values()) for c in self.tf]
        self.avg = sum(self.len) / n if self.len else 1.0
        self.k1, self.b = k1, b

    def rank(self, query: str) -> List[int]:
        q = set(_grams(query))
        scores = []
        for i, counts in enumerate(self.tf):
            s = 0.0
            norm = self.k1 * (1 - self.b + self.b * self.len[i] / (self.avg or 1.0))
            for g in q:
                f = counts.get(g)
                if f:
                    s += self.idf[g] * f * (self.k1 + 1) / (f + norm)
            if s > 0:
                scores.append((s, i))
        scores.sort(key=lambda x: -x[0])
        return [i for _, i in scores]


def _embed_all(texts: List[str]) -> Optional[List[Sequence[float]]]:
    """借长期记忆那边的向量通道 (Companion 本机模型优先)。用不了返 None, 只用字面。"""
    try:
        from . import catfish_memory_vectors as vectors  # noqa: PLC0415
    except ImportError:
        import catfish_memory_vectors as vectors  # noqa: PLC0415
    out: List[Sequence[float]] = []
    for i in range(0, len(texts), 64):
        got = vectors.embed(texts[i:i + 64], 30.0)
        if got is None:
            return None
        out.extend(got[1])
    return out


class Retriever:
    def __init__(self, source: str, use_vectors: bool = True):
        self.source = source
        self.passages = passages(source)
        self.norm = [verify.norm(p) for p in self.passages]
        self.bm25 = _Bm25(self.passages)
        self.vecs = _embed_all(self.passages) if (use_vectors and self.passages) else None

    def candidates(self, facts: List[str], quotes: List[List[str]]) -> List[List[int]]:
        """每条事实的候选段下标 (摘录所在段在前, 再按 RRF)。"""
        fvecs = _embed_all(facts) if self.vecs is not None else None
        out = []
        for n, fact in enumerate(facts):
            score: Dict[int, float] = {}
            for r, i in enumerate(self.bm25.rank(fact)[:20]):
                score[i] = score.get(i, 0.0) + 1.0 / (_RRF_K + r)
            if fvecs is not None:
                fv = fvecs[n]
                sims = sorted(((sum(a * b for a, b in zip(fv, pv)), i) for i, pv in enumerate(self.vecs)), reverse=True)
                for r, (_, i) in enumerate(sims[:20]):
                    score[i] = score.get(i, 0.0) + 1.0 / (_RRF_K + r)
            pinned = []
            for q in quotes[n]:
                nq = verify.norm(q)
                for i, p in enumerate(self.norm):
                    if len(nq) >= verify._MIN_QUOTE and (nq[:12] in p or nq[-12:] in p):
                        pinned.append(i)
                        break
            ranked = list(dict.fromkeys(pinned + [i for i, _ in sorted(score.items(), key=lambda x: -x[1])]))
            out.append(ranked[:TOP_K])
        return out

    def number_check(self, fact: str, cand: List[int]) -> Tuple[Optional[str], List[int]]:
        """硬约束: (编造的数字说明 or None, 补齐后的候选)。"""
        nums = verify.ints(verify._strip_today(fact))
        if not nums:
            return None, cand
        src_nums = verify.ints(self.source)
        fake = sorted(nums - src_nums)
        if fake:
            return "数字原文里没有: " + "、".join(fake[:3]), cand
        have = set()
        for i in cand:
            have |= verify.ints(self.passages[i])
        extra = []
        for num in sorted(nums - have):
            for i, p in enumerate(self.passages):
                if num in verify.ints(p) and i not in cand and i not in extra:
                    extra.append(i)
                    break
        return None, cand + extra


# ---------------------------------------------------------------- 模型判断

_JUDGE_PROMPT = """你是事实核对员。下面每条「说法」后面附了从原文里检索出来的几段 (编号 [段n])。
逐条判断: 只看给出的原文段, 原文是否支持这条说法。

- 支持: 说法里的具体内容 (人名、单位、职务、数字、日期、谁做了什么、肯定还是否定、因果) 在原文段里都有。
  允许换说法、概括、省略; 不允许多出原文没有的内容, 不允许张冠李戴 (把 A 的事说成 B 的)。
- 部分: 大体对, 但有一处原文段里没有或对不上。
- 不支持: 原文段里没有这件事, 或者跟原文矛盾。

说法本身是「X 是一份规划 / 一个单位」这类原文标题就能看出来的, 算支持。

只输出 JSON 数组, 不要别的字:
[{"i": 说法编号, "v": "支持" | "部分" | "不支持", "e": [依据的段号], "why": "部分 / 不支持时一句话说哪里对不上"}]
"""


def _judge_input(items: List[Tuple[str, List[int]]], texts: List[str]) -> str:
    blocks = []
    for n, (fact, cand) in enumerate(items, 1):
        segs = "\n".join(f"[段{i}] {texts[i]}" for i in cand)
        blocks.append(f"### 说法 {n}\n{fact}\n原文段:\n{segs}")
    return _JUDGE_PROMPT + "\n\n" + "\n\n".join(blocks)


_LOOSE_RE = re.compile(r'"i"\s*:\s*(\d+)\s*,\s*"v"\s*:\s*"(支持|部分|不支持)"(?:\s*,\s*"e"\s*:\s*\[([^\]]*)\])?')


def parse_verdicts(text: str, n: int) -> List[Optional[Tuple[str, List[int], str]]]:
    """模型输出 → 每条 (判断, 依据段号, 说明); 模型漏了的那几条是 None (调用方只重试那几条)。"""
    m = re.search(r"\[.*\]", text or "", re.DOTALL)
    try:
        rows = json.loads(m.group(0)) if m else []
    except ValueError:
        # JSON 坏了 (说明里抄了原文的半角引号之类) —— 逐条把编号和判断抠出来, 能救几条救几条
        rows = [{"i": i, "v": v, "e": re.findall(r"\d+", e or "")}
                for i, v, e in _LOOSE_RE.findall(text or "")]
    out: List[Optional[Tuple[str, List[int], str]]] = [None] * n
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        try:
            i = int(r.get("i")) - 1
        except (TypeError, ValueError):
            continue
        if 0 <= i < n:
            v = str(r.get("v") or "").strip()
            raw = r.get("e") if isinstance(r.get("e"), list) else []
            ev = [int(str(x).lstrip("段")) for x in raw if str(x).lstrip("段").isdigit()]
            out[i] = (v if v in ("支持", "部分", "不支持") else "不支持", ev, str(r.get("why") or "")[:80])
    return out


async def _post_judge(prompt: str, model: str) -> Optional[str]:
    token = _gateway_dev_token()
    if not token:
        return None
    try:
        import httpx  # noqa: PLC0415
    except ImportError:
        return None
    try:
        async with httpx.AsyncClient(timeout=_DISTILL_HTTP_TIMEOUT) as client:
            resp = await client.post(
                with_source(_gateway_url(), "plugin:memory-wiki-ground"),
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Catfish-Skip-Identity": "true",
                    "X-Catfish-Internal": "true",
                    "X-Catfish-Thinking": "off",
                    "Content-Type": "application/json",
                },
                json={"model": model, "messages": [{"role": "user", "content": prompt}],
                      "temperature": 0, "max_tokens": 3000, "stream": False},
            )
            if resp.status_code != 200:
                logger.warning("catfish-memory 核对原文: HTTP %d (%s)", resp.status_code, resp.text[:200])
                return None
            return resp.json().get("choices", [{}])[0].get("message", {}).get("content", "") or ""
    except Exception as e:  # noqa: BLE001
        logger.warning("catfish-memory 核对原文 异常 [%s]: %r", type(e).__name__, e)
        return None


#: 判一条事实的结果: (收不收, 不收的原因, 依据原文)
Verdict = Tuple[bool, str, List[str]]


async def judge_facts(facts: List[str], quotes: List[List[str]], source: str, model: str,
                      retriever: Optional[Retriever] = None, post=None) -> List[Optional[Verdict]]:
    """一批事实对一份原文。重试过还判不了的那几条给 None (调用方对它们退回 v1)。"""
    if not facts:
        return []
    post = post or _post_judge
    r = retriever or Retriever(source)
    cands = r.candidates(facts, quotes)
    results: List[Optional[Verdict]] = [None] * len(facts)
    todo: List[Tuple[int, str, List[int]]] = []
    for n, fact in enumerate(facts):
        fake, cand = r.number_check(fact, cands[n])
        if fake:
            results[n] = (False, fake, [])
        elif not cand:
            results[n] = (False, "原文里找不到相关的段", [])
        else:
            todo.append((n, fact, cand))
    sem = asyncio.Semaphore(CONCURRENCY)

    async def one(batch):
        for _ in range(1 + RETRIES):
            async with sem:
                text = await post(_judge_input([(f, c) for _, f, c in batch], r.passages), model)
            missed = []
            for item, got in zip(batch, parse_verdicts(text or "", len(batch))):
                if got is None:
                    missed.append(item)
                else:
                    settle(item, *got)
            batch = missed
            if not batch:
                return
        logger.info("catfish-memory 核对原文: %d 条模型判不了, 退回摘录比对", len(batch))
        try:  # 留最后一次的原样输出, 好查为什么解析不了
            home = Path(os.environ.get("CATFISH_HOME") or Path.home() / ".catfish").expanduser()
            (home / "last_wiki_ground_bad.txt").write_text(text or "", encoding="utf-8")
        except OSError:
            pass

    def settle(item, v, ev, why):
        n, _, cand = item
        ev = [i for i in ev if 0 <= i < len(r.passages)] or cand[:1]
        if v == "支持":
            results[n] = (True, "", [r.passages[i] for i in ev])
        else:
            results[n] = (False, f"原文对不上 ({v}{': ' + why if why else ''})", [])

    await asyncio.gather(*(one(todo[i:i + BATCH]) for i in range(0, len(todo), BATCH)))
    return results


# ---------------------------------------------------------------- 整张卡

def _fallback(line: str, source: str) -> Verdict:
    """模型判不了的那条: 用 v1 摘录比对 (单行卡跑一遍 verify_page)。"""
    out, st = verify.verify_page(f"---\ntitle: x\n---\n{line}\n", source)
    if st["kept"]:
        return True, "", _parse_frontmatter_lists(_split_frontmatter_body(out)[0]).get("evidence", [])
    why = (_parse_frontmatter_lists(_split_frontmatter_body(out)[0]).get("unverified") or [""])[0]
    return False, why.split(" —— ", 1)[-1] or "原文对不上", []


def _fact_lines(body: str) -> List[Tuple[int, str, List[str]]]:
    """(行号, 去掉标记的事实, 摘录) —— 旁白行行号给 -1 (直接丢)。"""
    out = []
    for n, line in enumerate(body.splitlines()):
        s = line.strip()
        if not verify._is_fact_line(s):
            continue
        quotes = verify.split_quotes(verify.MARK_RE.findall(line))
        text = verify.MARK_RE.sub("", line)
        if (not quotes and verify._COMMENTARY_RE.match(s)) or len(verify.norm(text)) < _MIN_FACT:
            out.append((-n - 1, "", []))  # 旁白 / 生成残渣 (```、单个标点) —— 不是事实, 直接丢
            continue
        out.append((n, verify.MARK_RE.sub("", line).rstrip(), quotes))
    return out


async def ground_files(files: Dict[str, str], source: str, model: str, post=None) -> Tuple[Dict[str, str], Dict[str, int]]:
    """一批卡一起核对 (一份原文只切段 / 算一次向量)。签名同 verify.verify_files, 多一个模型。"""
    if not verify.enabled():
        return files, {"kept": 0, "dropped": 0}
    if not enabled() or not model:
        return verify.verify_files(files, source)
    parsed = {}
    facts: List[str] = []
    quotes: List[List[str]] = []
    owner: List[Tuple[str, int]] = []
    for path, content in files.items():
        fm, body = _split_frontmatter_body(content)
        if not fm:
            continue
        lines = _fact_lines(body)
        parsed[path] = (fm, body, lines)
        for n, text, qs in lines:
            if n >= 0:
                facts.append(text.strip().lstrip("-*• ").strip())
                quotes.append(qs)
                owner.append((path, n))
    verdicts = await judge_facts(facts, quotes, source, model, post=post) if facts else []
    by_line = {owner[i]: verdicts[i] or _fallback(parsed[owner[i][0]][1].splitlines()[owner[i][1]], source)
               for i in range(len(owner))}
    out = dict(files)
    total = {"kept": 0, "dropped": 0}
    for path, (fm, body, lines) in parsed.items():
        drop_rows = {-n - 1 for n, _, _ in lines if n < 0}
        verdict_of = {n: by_line[(path, n)] for n, _, _ in lines if n >= 0}
        text_of = {n: t for n, t, _ in lines if n >= 0}
        kept_lines: List[str] = []
        evidence: List[str] = []
        dropped: List[str] = []
        for n, line in enumerate(body.splitlines()):
            if n in drop_rows:
                continue
            if n not in verdict_of:
                kept_lines.append(line)
                continue
            ok, why, ev = verdict_of[n]
            if ok:
                kept_lines.append(text_of[n])
                evidence.extend(verify._clean_item(e, 80) for e in ev[:2])
                total["kept"] += 1
            else:
                dropped.append(f"{verify._clean_item(text_of[n])} —— {why}")
                total["dropped"] += 1
        lists = _parse_frontmatter_lists(fm)
        fm = verify._set_list(fm, "evidence", lists.get("evidence", []) + evidence)
        fm = verify._set_list(fm, "unverified", lists.get("unverified", []) + dropped)
        new_body = "\n".join(kept_lines) + ("\n" if body.endswith("\n") else "")
        out[path] = f"---\n{fm}\n---\n{new_body}"
    return out, total
