#!/usr/bin/env python3
"""BL-FIX36-B: 把 employee_journal.md 里的"员工偏好 X" 反向迁移到 user_profile.json.

# 背景

5/10 鸿波诊断: dashboard 画像卡 0 项. 5/4 SOUL BL-MM5 教 LLM 用 memory_save 落盘
偏好, 5/6 BL-MM7 ship catfish_user_profile_* 真后端, 但 BL-MM5 段没改写示例,
LLM 8 天**两个都没用** — 偏好只在 session_summarizer 后台跑的 employee_journal.md
里以"员工偏好 X" 形式被记录(总结性观察), 没进结构化画像.

A 修了 SOUL.md 教 LLM 用新接口 (BL-FIX36-A); B 是这个脚本, 把历史 journal 里
8 天积累的偏好观察一次性反向 propose 到画像系统, dashboard 立刻有真画像可看.

# 用法

```bash
cd ~/person_task/catfish/edge/tool-bridge
python -m scripts.migrate_journal_to_profile [--dry-run] [--journal PATH]
```

dry-run 模式: 只打印映射结果, 不真写 user_profile.json. 推荐先 dry-run 看看
分类对不对再真跑.

# 工作流程

1. 读 ~/.catfish/employee_journal.md
2. 找含"员工偏好 / 偏好" 的行
3. 用关键词规则映射到 9 个 user_profile 字段(纯规则不调 LLM, 可审计)
4. 同 (field, value) 出现 ≥ 3 次 → 调 user_profile_confirm 直接落盘 (历史已观察够多)
5. 出现 < 3 次 → 调 user_profile_propose 累 evidence (等 LLM 后续观察补到 3 再问)
6. 不能映射的行 → 列在 unmapped 里, 让你 review

# 字段映射规则 (关键词 → field/value)

写 keyword 时优先 quote 完整短语, 避免误匹配 ("简洁" 容易乱触发, 用"简洁直接"等组合).
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

# 让脚本能从 catfish_tool_bridge 包 import (走仓库内 src/)
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from catfish_tool_bridge import user_profile  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("migrate")

JOURNAL_PATH = Path.home() / ".catfish" / "employee_journal.md"


# ── 映射规则 ─────────────────────────────────────────────────────────
#
# 每条规则: (正则模式, field, value)
# 正则在含"偏好" / "员工" 的行内 search, 命中即 propose 一次 evidence.
# 排序很重要: 更精确的规则在前 (例: "简洁直接" 命中前先排除"复杂").

MAPPING_RULES: List[Tuple[re.Pattern, str, str]] = [
    # 文风 · 语气 ─────────────────────────────────────
    (re.compile(r"简洁直接|直接[、，]?简洁|偏好直接|沟通方式.*直接|直接.*沟通"),
     "writing_style.tone", "直接"),
    (re.compile(r"避免冗余|不要套话|不绕弯|不要绕弯|不绕圈|偏好直接.*不要"),
     "writing_style.tone", "直接"),
    (re.compile(r"风格.*正式|formal|公文风格"),
     "writing_style.tone", "formal"),
    (re.compile(r"风格.*随意|风格.*轻松|casual|偏好.*闲聊"),
     "writing_style.tone", "casual"),
    (re.compile(r"幽默|搞笑|风格.*幽默"),
     "writing_style.tone", "幽默"),
    (re.compile(r"委婉|偏好.*温和"),
     "writing_style.tone", "委婉"),

    # 文风 · 长度 ─────────────────────────────────────
    # BL-FIX36-B v2 (5/10): "长" 规则之前太宽, "偏好.*展开/详细" 把"周报详细展开"
    # 这种非偏好句也命中. 收紧成必须含"长篇/详尽" 等 explicit 表达.
    (re.compile(r"偏好.*简短|偏好.*简洁|偏好.*短|不要.*冗长|避免冗长|长篇.*删除"),
     "writing_style.length_pref", "短"),
    (re.compile(r"偏好.*长篇|偏好.*详尽|偏好.*完整列出|偏好.*面面俱到"),
     "writing_style.length_pref", "长"),

    # 文风 · 列表 vs 段落 ─────────────────────────────
    (re.compile(r"偏好.*列表|偏好.*bullet|偏好.*列清单"),
     "writing_style.bullet_pref", "列表"),
    (re.compile(r"偏好.*段落|偏好.*纯文字|不要.*列表"),
     "writing_style.bullet_pref", "段落"),

    # 工作 · 任务呈现 ─────────────────────────────────
    (re.compile(r"偏好.*列清单|清单形式|偏好.*列出"),
     "work_pattern.task_pref", "列清单"),
    (re.compile(r"偏好.*图表|图表展示|偏好.*可视化"),
     "work_pattern.task_pref", "看图表"),
    (re.compile(r"偏好.*纯文字|偏好.*文字"),
     "work_pattern.task_pref", "纯文字"),
    (re.compile(r"偏好.*对照表|对照表.*偏好|偏好.*表格"),
     "work_pattern.task_pref", "对照表"),

    # 工作 · 看材料 ───────────────────────────────────
    (re.compile(r"偏好.*摘要|先看.*摘要|只看.*摘要"),
     "work_pattern.review_pref", "先看摘要"),
    (re.compile(r"偏好.*全量|全量.*查看|偏好.*完整查看"),
     "work_pattern.review_pref", "全量看"),
    (re.compile(r"偏好.*异常|只看.*异常|关注.*异常"),
     "work_pattern.review_pref", "只看异常"),

    # 性格 · 节奏 ─────────────────────────────────────
    (re.compile(r"偏好.*快|节奏.*快|偏好.*立即|偏好.*马上|跳过.*确认|拒绝.*重复"),
     "personality.pace", "急"),
    (re.compile(r"偏好.*慢|偏好.*稳|偏好.*仔细"),
     "personality.pace", "缓"),

    # 性格 · 反馈风格 ────────────────────────────────
    (re.compile(r"偏好.*结果|结果导向|偏好.*直接执行|跳过.*草稿|跳过.*审查|直接执行.*修改"),
     "personality.feedback_style", "结果导向"),
    (re.compile(r"偏好.*细节|细节确认|逐一.*确认"),
     "personality.feedback_style", "细节确认"),
    (re.compile(r"偏好.*大方向|大点拨|大局.*偏好"),
     "personality.feedback_style", "大点拨"),

    # 性格 · 称呼正式度 ──────────────────────────────
    (re.compile(r"偏好.*平等|对等.*交流|偏好.*平视"),
     "personality.deference", "平等"),
    (re.compile(r"偏好.*尊敬|偏好.*正式|偏好.*礼貌"),
     "personality.deference", "尊重正式"),
    (re.compile(r"偏好.*随意|偏好.*随便|偏好.*放松"),
     "personality.deference", "随意"),
]


def extract_preference_lines(journal_path: Path) -> List[str]:
    """从 journal 抽含"偏好"关键字的句子."""
    if not journal_path.exists():
        return []
    text = journal_path.read_text(encoding="utf-8", errors="ignore")
    # 按行抽 (journal 一行一段总结), 含偏好的留下
    lines = [
        ln.strip() for ln in text.splitlines()
        if ln.strip() and ("偏好" in ln or "员工" in ln and any(
            kw in ln for kw in ("简洁", "直接", "简短", "立即", "跳过", "拒绝重复",
                                "结果导向", "幽默", "正式", "随意", "列清单",
                                "看图表", "全量", "异常", "细节", "急", "缓")
        ))
    ]
    return lines


def classify_line(line: str) -> List[Tuple[str, str]]:
    """把一行 journal 句子 classify 到 (field, value) 列表. 一行可能命中多个规则."""
    hits: List[Tuple[str, str]] = []
    for pattern, field, value in MAPPING_RULES:
        if pattern.search(line):
            hits.append((field, value))
    # 去重 (一个规则的 field/value 组合只算一次, 多规则命中同 (f,v) 也合并)
    return list(set(hits))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印, 不真改 user_profile.json")
    parser.add_argument("--journal", type=Path, default=JOURNAL_PATH,
                        help=f"employee_journal.md 路径 (默认 {JOURNAL_PATH})")
    parser.add_argument("--auto-confirm", action="store_true",
                        help="(field, value) 累 ≥ 3 次直接 confirm 落盘 "
                             "(历史已观察够, 跳过员工再次确认). 默认开. 关掉只 propose 不 confirm.")
    parser.add_argument("--no-auto-confirm", action="store_true",
                        help="关掉 auto-confirm, 只累 evidence 不 confirm.")
    args = parser.parse_args()

    auto_confirm = not args.no_auto_confirm  # 默认开 auto-confirm

    logger.info("=" * 60)
    logger.info("BL-FIX36-B 历史偏好迁移 (journal → user_profile)")
    logger.info("journal: %s", args.journal)
    logger.info("dry-run: %s", args.dry_run)
    logger.info("auto-confirm: %s", auto_confirm)
    logger.info("=" * 60)

    lines = extract_preference_lines(args.journal)
    if not lines:
        logger.warning("journal 里没找到偏好相关条目, 退出")
        return 1
    logger.info("抽到 %d 行含'偏好'的 journal 条目", len(lines))

    # 累 evidence: (field, value) → [evidence_quotes]
    accumulator: Dict[Tuple[str, str], List[str]] = defaultdict(list)
    unmapped: List[str] = []

    for line in lines:
        hits = classify_line(line)
        if not hits:
            unmapped.append(line)
            continue
        for (field, value) in hits:
            # evidence 取这行的前 200 字符
            evidence = line[:200]
            accumulator[(field, value)].append(evidence)

    # ── 总结 + 落盘 ────────────────────────────────────
    logger.info("")
    logger.info("分类结果:")
    logger.info("-" * 60)
    sorted_kv = sorted(accumulator.items(), key=lambda kv: -len(kv[1]))
    for (field, value), evidences in sorted_kv:
        logger.info("  %-35s = %-15s × %d 次",
                    field, value, len(evidences))

    logger.info("")
    logger.info("未能映射的句子: %d 行", len(unmapped))
    if unmapped:
        logger.info("(前 5 行 sample, 看是不是规则缺了)")
        for ln in unmapped[:5]:
            logger.info("  - %s", ln[:120])

    if args.dry_run:
        logger.info("")
        logger.info("== dry-run 结束, 没真写 user_profile.json ==")
        return 0

    # ── 真写 ────────────────────────────────────────────
    # BL-FIX36-B v2 (5/10): 同 field 多 value 冲突时, 只 confirm 最高 count value,
    # 其他低 count value 只累 evidence 不 confirm (保留 LLM 后续观察空间).
    # 老 v1 bug: 把 (急 42 次 + 缓 3 次) 都 confirm, 后写的 "缓" 覆盖了 "急", 反了.

    # 1. 找每个 field 的"赢家" value (count 最高的)
    field_winners: Dict[str, Tuple[str, int]] = {}  # field → (best_value, best_count)
    for (field, value), evidences in accumulator.items():
        cnt = len(evidences)
        if field not in field_winners or cnt > field_winners[field][1]:
            field_winners[field] = (value, cnt)

    logger.info("")
    logger.info("每 field 取 count 最高的 value (BL-FIX36-B v2):")
    for field, (value, cnt) in sorted(field_winners.items()):
        logger.info("  %-35s = %-15s × %d", field, value, cnt)

    logger.info("")
    logger.info("开始真写 user_profile.json...")

    proposed = 0
    confirmed = 0
    for (field, value), evidences in sorted_kv:
        is_winner = field_winners.get(field, ("", 0)) == (value, len(evidences))

        # 跑前 10 条 evidence 累 (覆盖工具内部 MAX_EVIDENCE_PER_FIELD=10)
        for ev in evidences[:user_profile.MAX_EVIDENCE_PER_FIELD]:
            r = user_profile.user_profile_propose({
                "field": field,
                "value": value,
                "evidence": ev,
            })
            if r.get("type") == "error":
                logger.warning("propose %s=%s 失败: %s",
                               field, value, r.get("error"))
                break
            proposed += 1

        # auto-confirm: 累够 3 次 + 是该 field 的 winner 才落盘
        # 非 winner 只累 evidence 不 confirm, 保留 LLM 后续观察空间
        if (auto_confirm
                and len(evidences) >= user_profile.MIN_EVIDENCE_TO_PROPOSE
                and is_winner):
            r = user_profile.user_profile_confirm({
                "field": field,
                "value": value,
                "locked": False,  # 不锁, 让 LLM 后续还能 propose 改
            })
            if r.get("type") == "error":
                logger.warning("confirm %s=%s 失败: %s",
                               field, value, r.get("error"))
            else:
                confirmed += 1
                logger.info("  ✓ confirmed %s = %s (%d evidence, winner)",
                            field, value, len(evidences))
        elif (auto_confirm
              and len(evidences) >= user_profile.MIN_EVIDENCE_TO_PROPOSE
              and not is_winner):
            logger.info("  ↳ skipped confirm %s = %s (%d evidence, 非 winner, 留给 LLM 后续观察)",
                        field, value, len(evidences))

    logger.info("")
    logger.info("=" * 60)
    logger.info("迁移完成. propose %d 次, confirm %d 个字段.", proposed, confirmed)
    logger.info("user_profile.json: %s", user_profile.USER_PROFILE_PATH)
    logger.info("打开 Companion Dashboard 看 UserProfileCard, 30s 内自动刷新.")
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
