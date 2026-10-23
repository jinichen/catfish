"""蒸馏出来的知识卡, 每条事实写盘前用代码对一遍原文 (10/23)。

# 为什么

精度原来只靠提示词 (「原文没写的不写」「禁推测句」), 写盘时只校验格式和关系, **没有任何一步
核对卡片上这句话原文里有没有**。模型把 7:3 写成 3:7、把人名张冠李戴、凭常识补一句, 都会
原样进知识库。提示词是要求, 不是检查。

# 做法 (不靠模型自查, 是确定性的字符串比对)

  · 分析那步给每条事实附一句原文里一字不差的摘录; 写卡那步把它带上:
        - 事实内容 〔原文:「一字不差的摘录」〕
  · 写盘前逐行查:
      1. 摘录在本轮原文里找得到 (忽略空白 / 标点 / 全半角差异);
      2. 这一行里的数字 (两位以上: 年份、编号、金额、比例里的大数) 在它的摘录附近 (前后 150 字) 出现过。
    过了 → 去掉标记留在正文, 摘录进 frontmatter `evidence` (卡上能看到依据);
    没过 → 从正文拿掉, 连同原因进 frontmatter `unverified`, 在「关系整理」里给人决定加回还是丢掉。
  · 一条都没过的卡标 pending (见 catfish_memory_wiki._classify_ontology_status)。
  · 同名合并 (LLM merge) 之后再查一遍数字: 合并后的正文里的数字, 要么在旧卡里、要么在新卡里、
    要么在原文里 —— 合并那步不许凭空多出数字。

CATFISH_WIKI_VERIFY=0 关掉 (只给评测脚本做对照用)。

10/23 晚: 写卡后的逐条核对换成 v2 (catfish_memory_wiki_ground: 检索原文段 + 模型判断)。这里的
摘录比对只在模型调不通时兜底; 合并后的数字检查 (check_merged_numbers) 和 carry_lists 照旧用。
scripts/eval_verify.py 同一批题: v1 错杀 5% / 漏放 50%, v2 见那个模块的说明。
"""
from __future__ import annotations

import os
import re
import unicodedata
from datetime import date
from typing import Dict, List, Tuple

try:
    from .catfish_memory_fm import _parse_frontmatter_lists, _split_frontmatter_body
except ImportError:  # 独立脚本模式 (无父包)
    from catfish_memory_fm import _parse_frontmatter_lists, _split_frontmatter_body

MARK_RE = re.compile(r"〔\s*原文\s*[:：]\s*[「“\"](.+?)[」”\"]\s*〕")
_INT_RE = re.compile(r"\d+")
#: 摘录至少这么长 (规范化后) 才算数 —— 两三个字的「摘录」到处都能撞上
_MIN_QUOTE = 6
#: 事实里的数字要在摘录附近这么多字以内出现过。全文范围太宽: 「增长 12%」(原文 10%)
#: 会因为同一份文件别处有个「12号」而放过去
_NUM_WINDOW = 150


_ELLIPSIS_RE = re.compile(r"\.{2,}|…+|⋯+")
#: 省略号连起来的几段摘录, 相邻两段在原文里最多隔这么远 (规范化后的字数)
_ELLIPSIS_GAP = 300
#: 模型把自己的说明写进了正文 (「(注: …)」「修正思考: …」) —— 不是事实, 直接丢, 不进待审
_COMMENTARY_RE = re.compile(r"^[\s*_>(（\[]*(注|备注|说明|修正|思考|提示|note|self-correction|correction)[^\s:：]{0,4}[\s:：]", re.IGNORECASE)


_MULTI_QUOTE_RE = re.compile(r"[」”\"]\s*[「“\"]")


def split_quotes(raw: List[str]) -> List[str]:
    """一个标记里塞了两段摘录 (〔原文:「A」「B」〕) —— 拆开分别核对, 不能当成一整句去找。"""
    return [p for q in raw for p in _MULTI_QUOTE_RE.split(q) if p.strip()]


#: 摘录比原文少字时, 原文那一段最长可以是摘录的几倍 (跳过的字不能太多, 不然什么都能"凑"出来)
_SKIP_RATIO = 1.8


def _subsequence_span(src_norm: str, nq: str) -> Tuple[int, int]:
    """摘录只是比原文**少了几个字** (中间跳过), 没有多字、没有改字 → 原文那一段的 (起, 止); 否则 (-1, -1)。

    10/23 鸿波 catch: 原文「中电福富公司时任信息安全BG高级总监」, 模型摘成
    「中电福富信息安全BG高级总监」(省了「公司时任」) —— 一字不差的规矩判它找不到。
    放宽成「摘录的每个字按顺序都在原文里」: 省字可以, 加字改字不行 ——
    「时任副主任」(原文「时任主任」) 多一个「副」, 照样拦。
    """
    if len(nq) < _MIN_QUOTE:
        return -1, -1
    limit = int(len(nq) * _SKIP_RATIO)
    head = nq[:2]
    start = src_norm.find(head)
    while start >= 0:
        i, j = 0, start
        end_cap = min(len(src_norm), start + limit)
        while i < len(nq) and j < end_cap:
            if src_norm[j] == nq[i]:
                i += 1
            j += 1
        if i == len(nq):
            return start, j
        start = src_norm.find(head, start + 1)
    return -1, -1


def find_quote(src_norm: str, quote: str) -> int:
    """摘录在原文里的位置 (规范化后); 找不到 -1。

    模型不听话用「…」拼了两处时: 每段都要找得到、按顺序、彼此离得近, 才算。
    """
    parts = [norm(p) for p in _ELLIPSIS_RE.split(quote)]
    parts = [p for p in parts if p]
    if not parts or sum(len(p) for p in parts) < _MIN_QUOTE:
        return -1
    if len(parts) == 1:
        exact = src_norm.find(parts[0])
        return exact if exact >= 0 else _subsequence_span(src_norm, parts[0])[0]
    if any(len(p) < 4 for p in parts):
        return -1
    start = src_norm.find(parts[0])
    while start >= 0:
        pos, ok = start + len(parts[0]), True
        for p in parts[1:]:
            j = src_norm.find(p, pos)
            if j < 0 or j - pos > _ELLIPSIS_GAP:
                ok = False
                break
            pos = j + len(p)
        if ok:
            return start
        start = src_norm.find(parts[0], start + 1)
    return -1


def numbers_in_context(text: str, src_norm: str, missing: set) -> set:
    """不在摘录附近的数字, 再看它在事实里的上下文 (前后各 3 个字) 原文里有没有原样出现。

    10/23 鸿波 catch:「《…成立105周年…讲话》于2026年7月3日印发」—— 摘录是文末的落款日期,
    105 在文首标题里, 离得远就被判「数字原文里没有」。数字是名称 / 标题的一部分时, 连着
    上下文一起能在原文里找到; 「增长 12%」(原文 10%) 的上下文「增长12」找不到, 照样拦。
    返回仍然找不到的那些。
    """
    t = norm(text)
    still = set()
    for n in missing:
        ok = False
        start = t.find(n)
        while start >= 0:
            ctx = t[max(0, start - 3): start + len(n) + 3]
            if len(ctx) > len(n) and ctx in src_norm:
                ok = True
                break
            start = t.find(n, start + 1)
        if not ok:
            still.add(n)
    return still


def _norm_with_map(text: str) -> Tuple[str, List[int]]:
    """norm() 之后每个字符对应原文第几个字符 —— 找到摘录后回原文取附近那段。

    不能直接在规范化后的串里取数字: 去掉标点会把相邻数字粘起来 (「〔2026〕12号」→ 202612)。
    """
    chars: List[str] = []
    pos: List[int] = []
    for i, ch in enumerate(text):
        for c in unicodedata.normalize("NFKC", ch).lower():
            if c.isalnum():
                chars.append(c)
                pos.append(i)
    return "".join(chars), pos


def source_span(source: str, src_norm: str, src_pos: List[int], quote: str) -> str:
    """摘录在原文里对应的那一段原话 (证据存原话, 不存模型的转述; 模型省了哪些字一眼看得出)。"""
    nq = norm(quote)
    i = src_norm.find(nq)
    if i >= 0:
        a, b = i, i + len(nq)
    else:
        a, b = _subsequence_span(src_norm, nq)
        if a < 0:
            return ""
    return source[src_pos[a]: src_pos[b - 1] + 1].replace("\n", " ")


def _nearby_ints(source: str, src_norm: str, src_pos: List[int], quotes: List[str]) -> set:
    out: set = set()
    for q in quotes:
        i = find_quote(src_norm, q)
        if i >= 0:
            span = min(len(norm(q)) + _ELLIPSIS_GAP, len(src_pos) - i)
            start, end = src_pos[i], src_pos[i + span - 1]
            out |= ints(source[max(0, start - _NUM_WINDOW): end + 1 + _NUM_WINDOW])
    return out
_MAX_LIST = 30


def enabled() -> bool:
    return os.environ.get("CATFISH_WIKI_VERIFY", "1") != "0"


def norm(text: str) -> str:
    """比对用: 全半角统一、去空白和标点、英文小写。"""
    t = unicodedata.normalize("NFKC", text).lower()
    return "".join(c for c in t if c.isalnum())


def ints(text: str) -> set:
    """两位以上的整数 (去前导零)。一位数太常见 (第1、3个), 不查。"""
    t = unicodedata.normalize("NFKC", text)
    out = set()
    for m in _INT_RE.findall(t):
        v = m.lstrip("0") or "0"
        if len(v) >= 2:
            out.add(v)
    return out


def _strip_today(text: str) -> str:
    """提示词要求进度句写「截至 今天」—— 今天的日期原文里当然没有, 不算编的。"""
    d = date.today()
    for pat in (f"{d.year}-{d.month:02d}-{d.day:02d}", f"{d.year}-{d.month}-{d.day}",
                f"{d.year}年{d.month}月{d.day}日"):
        text = text.replace(pat, "")
    return text


def _clean_item(s: str, limit: int = 120) -> str:
    s = s.strip().lstrip("-*• ").strip().replace('"', "”").replace("\n", " ")
    return s[:limit]


def _set_list(fm: str, field: str, items: List[str]) -> str:
    items = list(dict.fromkeys(i for i in items if i))[:_MAX_LIST]
    pat = re.compile(rf"^{field}:\s*\[.*?\]\s*$", re.MULTILINE)
    if not items:
        return pat.sub("", fm).replace("\n\n\n", "\n\n")
    line = f"{field}: [" + ", ".join(f'"{i}"' for i in items) + "]"
    return pat.sub(line, fm) if pat.search(fm) else fm.rstrip("\n") + "\n" + line


def _is_fact_line(s: str) -> bool:
    return bool(s) and not s.startswith("#") and not s.startswith("<!--") and s not in ("---",)


def verify_page(content: str, source: str) -> Tuple[str, Dict[str, int]]:
    """查一张卡。返回 (改过的内容, {kept, dropped})。"""
    fm, body = _split_frontmatter_body(content)
    if not fm:
        return content, {"kept": 0, "dropped": 0}
    src_norm, src_pos = _norm_with_map(source)
    kept_lines: List[str] = []
    evidence: List[str] = []
    dropped: List[str] = []
    n_kept = 0
    for line in body.splitlines():
        s = line.strip()
        if not _is_fact_line(s):
            kept_lines.append(line)
            continue
        quotes = split_quotes(MARK_RE.findall(line))
        text = MARK_RE.sub("", line).rstrip()
        if not quotes and _COMMENTARY_RE.match(s):
            continue  # 模型的旁白, 不是事实
        reason = None
        if not quotes:
            reason = "没给原文摘录"
        else:
            bad = [q for q in quotes if find_quote(src_norm, q) < 0]
            if bad:
                reason = f"摘录原文里找不到「{bad[0][:60]}」"
        if reason is None:
            missing = ints(_strip_today(text)) - _nearby_ints(source, src_norm, src_pos, quotes)
            missing = numbers_in_context(_strip_today(text), src_norm, missing)
            if missing:
                reason = "数字原文里没有: " + "、".join(sorted(missing)[:3])
        if reason:
            dropped.append(f"{_clean_item(text)} —— {reason}")
        else:
            kept_lines.append(text)
            evidence.extend(_clean_item(source_span(source, src_norm, src_pos, q) or q, 80) for q in quotes)
            n_kept += 1
    lists = _parse_frontmatter_lists(fm)
    fm = _set_list(fm, "evidence", lists.get("evidence", []) + evidence)
    fm = _set_list(fm, "unverified", lists.get("unverified", []) + dropped)
    new_body = "\n".join(kept_lines)
    if body.endswith("\n"):
        new_body += "\n"
    return f"---\n{fm}\n---\n{new_body}", {"kept": n_kept, "dropped": len(dropped)}


def verify_files(files: Dict[str, str], source: str) -> Tuple[Dict[str, str], Dict[str, int]]:
    """一批卡 (路径 → 内容) 都查一遍。"""
    if not enabled():
        return files, {"kept": 0, "dropped": 0}
    out: Dict[str, str] = {}
    total = {"kept": 0, "dropped": 0}
    for path, content in files.items():
        new, st = verify_page(content, source)
        out[path] = new
        total["kept"] += st["kept"]
        total["dropped"] += st["dropped"]
    return out, total


def check_merged_numbers(merged: str, before: List[str], source: str) -> str:
    """同名合并之后: 正文里冒出来的数字, 必须在合并前的旧卡 / 新卡 / 原文里出现过。

    不在的那一行拿掉进 unverified —— 合并那步只许重新组织, 不许新增事实。
    """
    if not enabled():
        return merged
    fm, body = _split_frontmatter_body(merged)
    if not fm:
        return merged
    allowed = ints(source)
    for b in before:
        allowed |= ints(b)
    kept: List[str] = []
    dropped: List[str] = []
    for line in body.splitlines():
        s = line.strip()
        missing = ints(_strip_today(s)) - allowed if _is_fact_line(s) else set()
        if missing:
            dropped.append(f"{_clean_item(s)} —— 合并时多出的数字: " + "、".join(sorted(missing)[:3]))
        else:
            kept.append(line)
    if not dropped:
        return merged
    lists = _parse_frontmatter_lists(fm)
    fm = _set_list(fm, "unverified", lists.get("unverified", []) + dropped)
    return f"---\n{fm}\n---\n" + "\n".join(kept) + ("\n" if body.endswith("\n") else "")


def carry_lists(merged: str, before: List[str], fields=("evidence", "unverified")) -> str:
    """LLM 合并时可能把 evidence / unverified 丢了 —— 从合并前的版本补回 (取并集)。"""
    fm, body = _split_frontmatter_body(merged)
    if not fm:
        return merged
    have = _parse_frontmatter_lists(fm)
    for field in fields:
        items = list(have.get(field, []))
        for b in before:
            bfm, _ = _split_frontmatter_body(b)
            items += _parse_frontmatter_lists(bfm).get(field, []) if bfm else []
        fm = _set_list(fm, field, items)
    return f"---\n{fm}\n---\n{body}"
