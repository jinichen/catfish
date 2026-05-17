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

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

from .employee_journal import read_journal

logger = logging.getLogger("catfish.gateway.memory_distill")

#: 触发阈值 (journal 累 N 条 ## 段后跑一次 distill).
#: demo 期 30 (~ 1-2 周员工正常使用), 6/15 PoC 1 个月时调到 100.
DISTILL_THRESHOLD = 30

#: BL-MEMORY-DISTILL-LIVE (5/16 鸿波 'memory_distill 真上线') — 蒸馏后落盘.
#: inject_employee_journal 注入时优先用这个 (老段精华版), 配合 journal 最近 5KB
#: 实现"老段蒸馏 + 新段全文" 两段式注入. 总预算 ~8KB, 不再丢 99% 老内容.
DISTILLED_FACTS_PATH = Path.home() / ".catfish" / "distilled_facts.md"

#: gateway loopback URL (memory_distill 走 gateway 自己路由享 fallback + quota + audit).
#: 默认 127.0.0.1:8999 跟 CATFISH_GATEWAY_HOST/PORT 联动.
_GATEWAY_LOOPBACK_URL_DEFAULT = "http://127.0.0.1:8999"

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
# BL-MEMORY-DISTILL-LIVE (5/16 鸿波) — 真 LLM 蒸馏
# ============================================================
#
# 设计:
#   走 gateway loopback (跟 session_summarizer / proactive 同套路), 享多模型
#   fallback + quota tracking + audit. 用轻模型 (catfish-public-qwen-flash) 抽
#   关键事实, 不抽红线 (健康/财务/感情/政治/宗教).
#
# 输出:
#   markdown 文本, bullet 形式, 写到 ~/.catfish/distilled_facts.md.
#   inject_employee_journal 注入时读这个 + journal 最近段 = 两段式注入.


_LLM_DISTILL_SYSTEM_PROMPT = """你是员工记忆蒸馏助手. 从下面员工 journal 段抽出**关键长期事实**.

**抽什么** (4 类, ≤ 20 条, 总字数 ≤ 600 字):
  - 重要的人: boss / 同事 / 家人, 含称呼 + 角色 + 简短背景, 不含个人隐私
  - 重要项目: 反复出现的项目名 / 任务流 / 关键时间节点
  - 工作偏好: 沟通风格 / 时间偏好 / 文档风格 (不是"用什么工具"!)
  - 决策 / 里程碑: 跨 session 仍重要的决定

**红线** (永不抽): 健康 / 病情 / 工资 / 贷款 / 债务 / 感情 / 婚姻 / 政治 / 宗教.

**严格禁止** (违反等于失败):
  - ❌ **不要列工具用法** — "使用 X 工具" / "使用 Y 命令" / "使用 Z 快捷键" 这类**全部不算长期事实**. 工具是 transient 实现细节.
  - ❌ **同一条事实只写一次** — 检测到自己重复就 STOP, 哪怕没写完
  - ❌ **不要把同事/项目展开成清单形式列工具调用** — 写本质偏好不是操作步骤

**格式**: markdown bullet, 一条一行, 不啰嗦. 例:

- 老板: 张总, 部门长, 偏好简短数字明确报告
- 同事: 周园 (本地网资质需求对接人), 戴明利 (高新申报合同对接)
- 项目: EIS 资质管理 (合规/资质/安全 4 段周报), 已迭代 18 版
- 项目: ISO 现场审核会议 5/18-5/22, 204/409 会议室, 7 位代表
- 偏好: 正式公文风格, 短句短段, 先事实后结论, 去 AI 味
- 决策: 投资走"需求确认先行 + 分步投入" (4/29)

如果 chunk 没值得抽的内容, 直接返 "(无显著事实)" 不啰嗦, **不要硬凑工具清单**."""


def _internal_loopback_url() -> str:
    """gateway loopback URL. 跟 session_summarizer / proactive 同套路."""
    host = os.environ.get("CATFISH_GATEWAY_HOST", "127.0.0.1")
    port = os.environ.get("CATFISH_GATEWAY_PORT", "8999")
    override = os.environ.get("CATFISH_GATEWAY_URL", "").strip()
    if override:
        return override
    return f"http://{host}:{port}"


async def _llm_distill_chunk(
    chunk: str, *, model_name: str, timeout: float = 60.0,
) -> str | None:
    """走 gateway loopback 调 LLM 抽 chunk 关键事实.

    返 markdown bullet 文本 (或 None 失败). 失败不抛 — distill 后台异步, 不阻塞 chat.

    BL-INTERNAL-MODEL-FOLLOW-USER-DISTILL (5/17 鸿波 'memory_distill 用 qwen-flash
    不是员工 nemotron'): model_name 必须由 caller 解析员工最近 session 的 model
    传进来. 严格 1 candidate, 不 fallback (跟 summarizer / proactive / a2a / facts
    同套路). 通过 internal token 走 X-Catfish-Internal header, 跳 quota check.
    """
    if not chunk.strip():
        return None
    if not model_name:
        # 调用方应已解析过, 防御性 guard
        logger.debug("memory_distill _llm_distill_chunk: 没拿到 model_name, 跳过")
        return None
    try:
        # lazy import 避免循环 + 测试 mock 容易
        import httpx  # noqa: PLC0415

        from .auth.dev_token import ensure_internal_dev_token  # noqa: PLC0415
    except ImportError as e:
        logger.warning("memory_distill _llm_distill_chunk import 失败: %s", e)
        return None

    token = ensure_internal_dev_token()
    url = _internal_loopback_url() + "/v1/chat/completions"
    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": _LLM_DISTILL_SYSTEM_PROMPT},
            {"role": "user", "content": chunk},
        ],
        # BL-MEMORY-DISTILL-DEDUP (5/16 实盘 Chunk 2 死循环 200+ 重复行):
        # 降 1000 → 400 (~ 30 条 bullet 够用), 防 worst-case 死循环还能撞 max_tokens.
        # 加 frequency_penalty=0.5 惩罚重复 token, presence_penalty=0.3 鼓励新概念.
        # Qwen 公网 API 支持这两参数 (deepseek/gemini 也支持, fallback 不抖).
        "max_tokens": 400,
        "temperature": 0.3,
        "frequency_penalty": 0.5,
        "presence_penalty": 0.3,
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        # 跳后台 inject 链路防递归 (memory_distill 不需要看 journal 再 distill 自己)
        "X-Catfish-Skip-Identity": "true",
        # internal call 标记, gateway 跳 quota 检查 + 跳 inject_session_history / journal
        # BL-MEMORY-POLISH (5/16 实盘 bug): app.py is_internal_call 判 lower() in
        # ("true","1","yes"), 之前写 "memory-distill" 不匹配 → 被当 user 请求 →
        # 反复触发 inject pipeline. 必须传 "true". 区分来源走 X-Catfish-Internal-Source.
        "X-Catfish-Internal": "true",
        "X-Catfish-Internal-Source": "memory-distill",
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(url, json=payload, headers=headers)
    except (httpx.HTTPError, OSError) as e:
        logger.warning("memory_distill loopback 调用失败 (跳过此 chunk): %s", e)
        return None
    if r.status_code != 200:
        logger.warning(
            "memory_distill LLM 返 %d (跳过): %s", r.status_code, r.text[:200]
        )
        return None
    try:
        data = r.json()
        text = data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, ValueError) as e:
        logger.warning("memory_distill 解析响应失败: %s", e)
        return None
    if not text or text == "(无显著事实)":
        return None
    return _dedup_distilled_lines(text)


def _cross_chunk_dedup(parts: list[str]) -> str:
    """跨 chunk 全文 bullet 级去重 + 滤 LLM 寒暄.

    BL-MEMORY-POLISH (5/16 鸿波实盘 Chunk 2/3 重复"同事: 周园" 等):
      老 journal 段被切多个 chunk, 同一员工事实可能在多 chunk 都被蒸出. 这里收
      全部 chunk 后做最终去重:
        - 滤掉 "好的, 鸿波" / "根据 session" 这种 LLM 寒暄开场白 (不是 bullet)
        - 同一 bullet 关键词前缀 (": " 前的部分) 重复 → 保留首次出现, 后面跳过
        - 保留段标题 (### ...)

    例子:
      Chunk 2 蒸馏:
        - 同事: 周园 (本地网资质需求对接人)
        - 项目: ISO 现场审核会议 5/18-5/22
      Chunk 3 蒸馏:
        好的，鸿波。根据 session 历史:
        - 同事: 周园 (本地网资质需求对接人)
        - 项目: ISO 现场审核会议 5/18-5/22, 204/409 会议室
        - 决策: 投资策略
      去重后:
        Chunk 2: 全留
        Chunk 3: 去寒暄 + 同事/项目 bullet 重复跳过, 只留"决策: 投资策略"
    """
    if not parts:
        return ""

    # 第 1 步: 拼接所有 chunk
    raw_lines: list[str] = []
    for part in parts:
        for line in part.splitlines():
            raw_lines.append(line)

    # 第 2 步: 维护 "已见 bullet 关键词前缀" set
    seen_keys: set[str] = set()
    out_lines: list[str] = []
    # 寒暄/无关开场词正则: LLM 容易写"好的, X。根据 Y..." / "根据您提供的"
    _CHATTY_PREFIXES = (
        "好的", "根据", "以下是", "我为", "整理如下", "总结如下",
        "通读", "我看到", "看到您", "您提供", "鸿波，",
    )

    for line in raw_lines:
        stripped = line.strip()
        # 空行保留 (段落分隔)
        if not stripped:
            out_lines.append(line)
            continue
        # 段标题 ### 保留
        if stripped.startswith("#"):
            out_lines.append(line)
            continue
        # bullet — 抽 "关键词前缀" (前 12 字符, 跳掉 "- " 前缀符)
        if stripped.startswith(("-", "*", "•")):
            content = stripped.lstrip("-*• ").strip()
            key = content[:12]  # 前 12 字符当 dedup key
            if key in seen_keys:
                continue  # 重复 bullet, skip
            seen_keys.add(key)
            out_lines.append(line)
            continue
        # 非 bullet 非段标题: 检查是不是寒暄
        if any(stripped.startswith(prefix) for prefix in _CHATTY_PREFIXES):
            continue  # LLM 寒暄, skip
        # 其它内容 (例 LLM 自由发挥的段落) 保留
        out_lines.append(line)

    return "\n".join(out_lines).strip()


def _dedup_distilled_lines(text: str) -> str:
    """后处理: 检测连续重复模式截断, 防 LLM degenerate repetition 漏过 max_tokens.

    BL-MEMORY-DISTILL-DEDUP (5/16 实盘 Qwen3.6-flash 死循环 200+ 重复行):
      LLM 进入 degenerate repetition 时, 同一 bullet 前缀 (例 "- 偏好: 使用 X")
      反复出现. 检测 **同一行内容连续出现 ≥ 2 次** 或 **3 行 ngram 重复** 就截断
      到第一次出现点.

    算法:
      1. 按行扫
      2. 维护 seen_lines: 行 → 首次出现 index
      3. 一行再次出现 → 截断到首次 index (后面全是重复, 丢)
      4. 也检测"同前缀连续 ≥ 3 行" — 例如所有行都以 "- 偏好: 使用" 开头 → 截到
         首次该前缀以外的行

    保留行 — 不是 dedup 整个文件, 是检测到**疑似进入死循环**才截.
    """
    if not text:
        return text
    lines = text.splitlines()
    seen: dict[str, int] = {}
    same_prefix_streak = 0
    last_prefix: str | None = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            same_prefix_streak = 0
            last_prefix = None
            continue
        # 完全相同行重复出现 → 截断
        if stripped in seen:
            logger.info(
                "memory_distill dedup: 行重复检测, 截断到 index=%d (重复行: %r)",
                seen[stripped] + 1, stripped[:50],
            )
            return "\n".join(lines[: seen[stripped] + 1]).strip()
        seen[stripped] = i
        # 同前缀 (前 8 字符) 连续 ≥ 6 行 → 截到第一次该前缀行
        # 选 8: 能匹配 "- 偏好: 使用" 这种死循环模式但不至于太宽误判
        # (例 "- 项目: A" vs "- 项目: B" 前 7 字符相同但第 8 差 — 不被误判 streak)
        prefix = stripped[:8]
        if prefix == last_prefix:
            same_prefix_streak += 1
            if same_prefix_streak >= 6:
                # 找出 streak 起点
                start = i - same_prefix_streak
                logger.info(
                    "memory_distill dedup: 同前缀 %d 连续, 截到 index=%d (前缀: %r)",
                    same_prefix_streak, start, prefix,
                )
                return "\n".join(lines[:start + 1]).strip()
        else:
            same_prefix_streak = 0
            last_prefix = prefix
    return "\n".join(lines).strip()


def read_distilled_facts() -> str:
    """读 ~/.catfish/distilled_facts.md (不存在/读失败返空字符串)."""
    if not DISTILLED_FACTS_PATH.exists():
        return ""
    try:
        return DISTILLED_FACTS_PATH.read_text(encoding="utf-8").strip()
    except OSError as e:
        logger.warning("读 distilled_facts.md 失败 (返空): %s", e)
        return ""


def write_distilled_facts(text: str) -> None:
    """全量写 ~/.catfish/distilled_facts.md (覆盖式, 不 append).

    每次 distillation 跑完, 拿最新蒸馏结果替换. 老版本不保留, 因为蒸馏是幂等的
    (同一份 journal 蒸馏俩次结果应该一致). 文件 ≤ 5KB (LLM 1000 max_tokens × N chunks
    + dedup). 上限不强制 — 信任 LLM prompt 里的 800 字约束.
    """
    DISTILLED_FACTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        DISTILLED_FACTS_PATH.write_text(text.strip() + "\n", encoding="utf-8")
        logger.info(
            "memory_distill: 写 distilled_facts.md %d 字节",
            len(text.encode("utf-8")),
        )
    except OSError as e:
        logger.warning("写 distilled_facts.md 失败 (跳过): %s", e)


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

    注意: 这是**规则版** (peak_hours / bullet_pref 等). 真 LLM 抽用
    maybe_run_distillation_with_llm() — 它能抽**实质性内容** (人/项目/偏好/决策).
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


# ============================================================
# BL-MEMORY-DISTILL-LIVE — 真 LLM 蒸馏主流程
# ============================================================


def should_run_llm_distillation(journal_text: str | None = None) -> bool:
    """跟 should_run_distillation 同条件 (阈值 + 24h 间隔).

    单独函数, 是因为 LLM 蒸馏跟规则蒸馏可能用不同 state 文件 (未来扩展用).
    现在共享 DISTILL_STATE_PATH, 24h 内只触发一种.
    """
    return should_run_distillation(journal_text)


def _format_distilled_header() -> str:
    """蒸馏文件顶部加生成时间戳 + 提示."""
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    return (
        f"<!-- Generated by memory_distill at {now} -->\n"
        f"<!-- 这是老 journal 段的 LLM 蒸馏精华, 注入时跟最近 journal 段配合 -->\n"
    )


async def maybe_run_llm_distillation(
    journal_text: str | None = None,
    *,
    user_email: str | None = None,
    only_old_segments: bool = True,
) -> str | None:
    """真 LLM 蒸馏主流程.

    流程:
      1. 检查触发条件 (阈值 + 24h 间隔)
      2. 解析员工最近 session model (BL-INTERNAL-MODEL-FOLLOW-USER-DISTILL)
      3. 读 journal, 切 chunk (DISTILL_CHUNK_CHARS = 8000)
      4. 每 chunk 走 gateway loopback 调 LLM 抽事实
      5. 合并所有 chunk 结果 → 写 ~/.catfish/distilled_facts.md
      6. 标记 distillation 跑过 (DISTILL_STATE_PATH)

    user_email: 必传 (caller 应传 user.sub). BL-INTERNAL-MODEL-FOLLOW-USER (5/17
      鸿波 '选哪个 model, 所有 LLM 都用同款'). 拿不到 model → 直接 skip, 不
      fallback (跟 summarizer / proactive / a2a / facts 同套路).

    only_old_segments=True (默认): 跳过最后 5KB (那部分 inject_employee_journal 全文
      注入, 不需要蒸馏). 防 distilled + journal 重复.

    返:
      - 蒸馏后 markdown 文本 (写盘成功)
      - None: 没跑 (条件不满足 / 拿不到员工 model / 失败)

    设计:
      - 不抛异常 — 失败静默, distill 是 background task 不该影响 chat
      - 不阻塞 — 调用方该用 asyncio.create_task() 异步丢出
      - 幂等 — 24h 内重跑直接 skip
    """
    text = journal_text if journal_text is not None else read_journal(for_injection=False)
    if not should_run_llm_distillation(text):
        return None

    # BL-INTERNAL-MODEL-FOLLOW-USER-DISTILL (5/17 鸿波 'memory_distill 用 qwen-flash
    # 不是员工 nemotron'): 严格用员工最近 session 的 model. 拿不到 → skip.
    # 之前硬编码 catfish-public-qwen-flash → dashscope 403 (员工没绑公网 qwen 配额),
    # 且违反"所有 LLM 用员工选的 model" 总规则.
    try:
        from .config import load_config  # noqa: PLC0415
        from .user_model_resolver import (  # noqa: PLC0415
            get_user_last_session_model,
            resolve_model_obj,
        )

        config = load_config()
        model_name = get_user_last_session_model(user_email or "")
        origin_obj = resolve_model_obj(model_name, config)
        if origin_obj is None:
            logger.info(
                "memory_distill skip: 没拿到 user=%s 最近 session model (新员工 / "
                "老 schema / model 不可达), 不 fallback",
                user_email or "<none>",
            )
            return None
        chosen_model_name = origin_obj.name
    except Exception as e:  # noqa: BLE001
        logger.warning("memory_distill 解析 model 失败 (skip): %s", e)
        return None

    # 跳过最后 ~5KB (这段 inject_employee_journal 会直接 inject 全文)
    if only_old_segments:
        from .employee_journal import INJECT_MAX_BYTES  # noqa: PLC0415
        encoded = text.encode("utf-8")
        if len(encoded) > INJECT_MAX_BYTES:
            # 留前面段去 distill, 后面 INJECT_MAX_BYTES 留给全文 inject
            old_bytes = encoded[:-INJECT_MAX_BYTES]
            try:
                text = old_bytes.decode("utf-8", errors="ignore")
            except Exception:  # noqa: BLE001
                pass
            # 对齐 ## 段边界 (从首个 ## 开始, 防半段开头)
            idx = text.find("## ")
            if idx > 0:
                text = text[idx:]
        else:
            # journal 全文 < 5KB, 没"老段"可蒸馏, skip
            logger.info(
                "memory_distill: journal 仅 %d 字节 < INJECT_MAX_BYTES, 无老段需蒸馏, skip",
                len(encoded),
            )
            return None

    chunks = _split_journal_chunks(text)
    if not chunks:
        return None

    distilled_parts: list[str] = []
    fail_count = 0
    for chunk in chunks:
        result = await _llm_distill_chunk(chunk, model_name=chosen_model_name)
        if result:
            # BL-MEMORY-POLISH (5/16): 用 len 当编号, 跳过失败的 chunk 不留空号.
            # 之前用 i+1, 失败 chunk 在 output 编号上跳号 (见过"Chunk 2/3 没 1").
            distilled_parts.append(
                f"### 蒸馏段 {len(distilled_parts) + 1}\n\n{result}"
            )
        else:
            fail_count += 1

    if not distilled_parts:
        logger.warning(
            "memory_distill: %d chunks 全部蒸馏失败, 不写 distilled_facts.md",
            len(chunks),
        )
        return None

    # BL-MEMORY-POLISH (5/16 鸿波实盘 Chunk 2/3 内容重复):
    # cross-chunk dedup — 同一 bullet 内容在多 chunk 蒸馏结果中重复 (因为老 journal
    # 段被多次扫到, 例 "同事: 周园 (本地网资质需求对接人)" 在 Chunk 2 和 Chunk 3
    # 都出现). 全文 bullet 级去重 (按 bullet 内容子串近似匹配).
    full_text = _cross_chunk_dedup(distilled_parts)
    full_text = _format_distilled_header() + "\n" + full_text
    write_distilled_facts(full_text)
    mark_distillation_run(len(distilled_parts))
    logger.info(
        "BL-MEMORY-DISTILL-LIVE: model=%s user=%s %d chunks 蒸馏成功 %d 失败 %d, "
        "写 distilled_facts.md %d 字节",
        chosen_model_name, user_email or "<none>",
        len(chunks), len(distilled_parts), fail_count,
        len(full_text.encode("utf-8")),
    )
    return full_text
