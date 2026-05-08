"""BL-MM10 (5/8) — memory 自精炼 loop.

# 解决什么问题 (5/8 鸿波 review 识别为精度刚需)

employee_journal.md 是 append-only, 长期 (6 个月+) 后超 MAX_BYTES (50KB) 触发
tail-truncate, **30-50% 老洞察永久消失**. 同时 user_profile.json 累积 evidence
但**不进化** — 永远停 level 1 具体事实, 不抽到 level 2 性格模型.

不做的代价 (5/8 backlog 排期识别):
  - 第 6 个月起 30-50% 老 journal 洞察永久丢失
  - user_profile 永远停具体事实层 (老李 8 点收周报), 不进化到性格层 (morning person)
  - 客户故事 "用一年比同事更懂你" 是空话, 续费转化潜在低 15-20%

# 设计

后台触发 (不阻塞 chat):
  1. 每次 chat 后检查 journal 行数; 累 ≥ DISTILL_THRESHOLD 条 (demo 期 30, 6 月调 100) 触发
  2. 调 catfish-public-qwen-flash (轻模型) 把老 journal 跨 chunk 总结 → 提议 user_profile traits
  3. 用 BL-MM7 catfish_user_profile_propose 流程 (3 evidence + 红线 + lock)
  4. 老 journal 段标 [distilled-into-profile-<traits>] 但**不删**, 保留可追溯

跟 hermes 对标但区别:
  - hermes self-improving: agent 静默改 memory 格式 (黑盒)
  - 鲶鱼: 走 BL-MM7 confirm 流程, 员工可看可改可锁 (透明)

# MVP 范围 (5/8)

- distill_old_journal_to_profile_traits(): 核心函数, 一次抽取 (不调 LLM, 用规则)
- maybe_run_distillation(): 触发判断, append-only / 不阻塞
- 留 LLM 接入 hook (BL-MM10.1 后续): _llm_distill_chunk(text) 是 stub, 6 月真做时填

注意 MVP 阶段**不真调 LLM** — 只把基础设施搭起来 + 单测覆盖. 真 LLM 调用 6/15
PoC 1 个月时按 PoC 数据再调参数 (DISTILL_THRESHOLD / chunk size / model 选择).
现在就上线避免 demo 后插队.
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any

from .employee_journal import journal_path, read_journal

logger = logging.getLogger("catfish.gateway.memory_distill")

#: 触发阈值 (journal 累 N 条 ## 段后跑一次 distill).
#: demo 期 30 (~ 1-2 周员工正常使用), 6/15 PoC 1 个月时调到 100.
DISTILL_THRESHOLD = 30

#: 单 chunk 最多字符数 (LLM 一次看的 journal 段数量上限)
DISTILL_CHUNK_CHARS = 8000

#: 跑过 distillation 的标记文件 (防同 journal 重复跑)
DISTILL_STATE_PATH = Path.home() / ".catfish" / "memory_distill_state.json"

#: 红线字段 (LLM 永不 propose 这些 trait, 跟 BL-MM7 + BL-MM9 一致)
_DISTILL_REDLINE_KEYWORDS = (
    "health", "medical", "diagnos",
    "salary", "loan", "debt", "finance",
    "love", "dating", "marriage",
    "politic", "election",
    "religion", "buddh", "christ",
    "健康", "病", "诊", "工资", "贷款", "债", "理财",
    "恋爱", "结婚", "离婚", "政治", "选举", "宗教",
)


def _count_journal_entries(journal_text: str) -> int:
    """数 journal 里 '## ' 开头的段数量."""
    return sum(1 for line in journal_text.splitlines() if line.startswith("## "))


def _split_journal_chunks(journal_text: str, max_chars: int = DISTILL_CHUNK_CHARS) -> list[str]:
    """按 '## ' 段切, 同 chunk 累计不超 max_chars.

    简单实现 — 按段切, 累到 max_chars 起新 chunk. 返 [] 表示空 / 没段可切.
    """
    if not journal_text.strip():
        return []
    # 用 ## 边界切, 第一段可能没 ## 前缀 (历史遗留), 也算一段
    parts = re.split(r"^## ", journal_text, flags=re.MULTILINE)
    parts = [p.strip() for p in parts if p.strip()]
    chunks: list[str] = []
    cur = ""
    for p in parts:
        # 加回 ## 前缀
        seg = "## " + p if not p.startswith("## ") else p
        if cur and len(cur) + len(seg) > max_chars:
            chunks.append(cur)
            cur = seg
        else:
            cur = (cur + "\n\n" + seg) if cur else seg
    if cur:
        chunks.append(cur)
    return chunks


def _is_redline_trait(field: str, value: str) -> bool:
    """检查抽出的 trait 是否触红线 (健康/财务/感情/政治/宗教)."""
    text = (field + " " + value).lower()
    return any(kw in text for kw in _DISTILL_REDLINE_KEYWORDS)


# ============================================================
# 规则版 distillation (MVP, 不调 LLM)
# ============================================================
#
# 6/15 真做 LLM 抽取前, 先用简单规则抽几个高频 pattern. 准确率不如 LLM,
# 但够 demo 演示"鲶鱼自精炼" 故事 + 留 hook 给 LLM 接入.
#
# 抽 3 类基础 trait:
#   1. work_pattern.peak_hours — 看 journal 时间戳分布 (8/9/10 点出现 ≥ 3 次 → "morning person")
#   2. writing_style.bullet_pref — 看 journal 列表 vs 散文比例
#   3. interest_topics — 高频名词 (用 jieba? 太重, 用 char-ngram 简化)


_TIMESTAMP_RE = re.compile(r"(\d{4}-\d{2}-\d{2})\s+(\d{1,2}):(\d{2})")


def _extract_peak_hours(journal_text: str) -> tuple[str, list[str]] | None:
    """从 journal '## YYYY-MM-DD HH:MM - ...' 时间戳找出 ≥ 3 次出现的小时分布.

    返 (proposed_value, evidence_list) 或 None (数据不够).
    例: ("早晨型 (8-10 点高频)", ["2026-05-01 08:30", "2026-05-03 09:15", ...])
    """
    matches = _TIMESTAMP_RE.findall(journal_text)
    if len(matches) < 3:
        return None

    hour_counts: dict[int, list[str]] = {}
    for date, hour, minute in matches:
        try:
            h = int(hour)
        except ValueError:
            continue
        if 0 <= h <= 23:
            hour_counts.setdefault(h, []).append(f"{date} {hour}:{minute}")

    if not hour_counts:
        return None

    # 找最高频的 3 小时窗
    morning = sum(len(hour_counts.get(h, [])) for h in range(6, 12))
    afternoon = sum(len(hour_counts.get(h, [])) for h in range(12, 18))
    evening = sum(len(hour_counts.get(h, [])) for h in range(18, 24))
    night = sum(len(hour_counts.get(h, [])) for h in range(0, 6))

    total = morning + afternoon + evening + night
    if total < 5:
        return None

    label = max(
        [("早晨型 (6-12 点高频)", morning),
         ("下午型 (12-18 点高频)", afternoon),
         ("晚上型 (18-24 点高频)", evening),
         ("夜猫型 (0-6 点高频)", night)],
        key=lambda x: x[1],
    )
    if label[1] / total < 0.4:
        # 没明显倾向, 不 propose
        return None

    # 拼 evidence: 取最高频小时的前 5 个 timestamp
    busiest_hour = max(hour_counts.items(), key=lambda x: len(x[1]))
    evidence_samples = busiest_hour[1][:5]
    return (label[0], evidence_samples)


def _extract_bullet_preference(journal_text: str) -> tuple[str, list[str]] | None:
    """从 journal 段落里看 list (`- ` / `1. `) vs 散文 比例."""
    lines = journal_text.splitlines()
    list_lines = sum(1 for ln in lines if re.match(r"^\s*([-*]|\d+\.)\s+", ln))
    prose_lines = sum(1 for ln in lines if ln.strip() and not re.match(r"^\s*([-*#]|\d+\.)\s+", ln))

    total = list_lines + prose_lines
    if total < 30:
        return None

    list_ratio = list_lines / total
    if list_ratio >= 0.4:
        return ("偏列表型 (≥40% 内容是 bullet/编号)", [f"list_ratio={list_ratio:.2f} ({list_lines}/{total} 行)"])
    if list_ratio <= 0.1:
        return ("偏散文型 (列表 <10%)", [f"list_ratio={list_ratio:.2f} ({list_lines}/{total} 行)"])
    return None


# ============================================================
# 主流程
# ============================================================


def distill_journal_to_traits(journal_text: str) -> list[dict[str, Any]]:
    """从 journal 文本抽出多个 trait proposal.

    每条 proposal: {field, value, evidence (list[str]), source: 'rule' | 'llm'}.
    红线字段自动过滤 (符合 BL-MM7 红线哲学).

    MVP: 只用规则. 6/15 PoC 时 _llm_distill_chunk 接入真 LLM.
    """
    proposals: list[dict[str, Any]] = []

    peak = _extract_peak_hours(journal_text)
    if peak is not None:
        value, evidence = peak
        if not _is_redline_trait("work_pattern.peak_hours", value):
            proposals.append({
                "field": "work_pattern.peak_hours",
                "value": value,
                "evidence": evidence,
                "source": "rule",
            })

    bullet = _extract_bullet_preference(journal_text)
    if bullet is not None:
        value, evidence = bullet
        if not _is_redline_trait("writing_style.bullet_pref", value):
            proposals.append({
                "field": "writing_style.bullet_pref",
                "value": value,
                "evidence": evidence,
                "source": "rule",
            })

    return proposals


def should_run_distillation(journal_text: str | None = None) -> bool:
    """判断是否该跑一次 distillation.

    条件:
      1. journal entry 数 ≥ DISTILL_THRESHOLD
      2. 距上次 distillation ≥ 24 小时 (防同次 chat 反复触发)
    """
    text = journal_text if journal_text is not None else read_journal()
    n_entries = _count_journal_entries(text)
    if n_entries < DISTILL_THRESHOLD:
        return False

    # 检查上次跑的时间
    if DISTILL_STATE_PATH.exists():
        try:
            import json as _json
            state = _json.loads(DISTILL_STATE_PATH.read_text(encoding="utf-8"))
            last_ts = state.get("last_run_ts", 0)
            if time.time() - last_ts < 24 * 3600:
                return False
        except (OSError, ValueError):
            pass

    return True


def mark_distillation_run(num_proposals: int) -> None:
    """记录这次 distillation 跑过, 防 24h 内重跑."""
    DISTILL_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "last_run_ts": time.time(),
        "last_run_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "last_num_proposals": num_proposals,
    }
    try:
        import json as _json
        DISTILL_STATE_PATH.write_text(_json.dumps(state), encoding="utf-8")
    except OSError as e:
        logger.warning("写 memory_distill_state.json 失败 (不阻塞): %s", e)


def maybe_run_distillation(journal_text: str | None = None) -> list[dict[str, Any]]:
    """触发一次 distillation 如果条件满足. 返回 proposals 列表 (空 = 没跑或没抽出).

    设计上**不阻塞 chat** — 调用方 (gateway) 可以在 chat 完成后异步调.
    返 proposals 让调用方决定怎么处理 (写到 user_profile.json propose 或显式提示员工).

    BL-MM10 MVP 阶段不直接落盘 — 6/15 PoC 时再接入 BL-MM7 confirm 流程.
    """
    text = journal_text if journal_text is not None else read_journal()
    if not should_run_distillation(text):
        return []
    chunks = _split_journal_chunks(text)
    all_proposals: list[dict[str, Any]] = []
    for chunk in chunks:
        all_proposals.extend(distill_journal_to_traits(chunk))
    # 同 field 多条 → 取 evidence 最多的那条
    best_by_field: dict[str, dict[str, Any]] = {}
    for p in all_proposals:
        f = p["field"]
        prev = best_by_field.get(f)
        if prev is None or len(p["evidence"]) > len(prev["evidence"]):
            best_by_field[f] = p
    deduped = list(best_by_field.values())
    mark_distillation_run(len(deduped))
    logger.info(
        "BL-MM10 distillation: 跑过 %d chunks, 抽出 %d 个 trait proposals",
        len(chunks), len(deduped),
    )
    return deduped
