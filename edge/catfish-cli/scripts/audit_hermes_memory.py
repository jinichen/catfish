#!/usr/bin/env python3
"""BL-HERMES-MEMORY-AUDIT — 每日 audit hermes 跟 catfish 两边记忆系统的字节增长.

# 为啥

5/16 鸿波修通 hermes memory 工具暴露 (之前 19 天没动) + 抽 MemoryProvider 接口.
半切方案 spec (docs/MEMORY-HALFCUT-PLAN.md) Step 3-5 (砍 catfish compressor /
distill / summarizer 换 hermes) 的**前置条件** 是: hermes 那边 (USER.md /
memories/) 真在写, 不是空表演.

每天跑这 script 看 6 个数:
  1. hermes ~/.hermes/USER.md (hermes 写, 长期画像)
  2. hermes ~/.hermes/memories/ 总字节 + 文件数
  3. hermes ~/.hermes/SOUL.md (catfish symlink, 跟踪 mtime 不动是问题)
  4. catfish ~/.catfish/employee_journal.md (catfish session_summarizer 写)
  5. catfish ~/.catfish/distilled_facts.md (catfish memory_distill 写, 5/16 上线)
  6. catfish ~/.catfish/session_facts.json (员工 catfish_remember 工具记)

# KPI (BL-MEMORY-FULL-HERMES 2026-05-16 鸿波拍板 V2)

5/16 实盘发现 LLM 选 catfish_remember 不调 hermes memory. 鸿波诊断:
catfish_remember 简陋 LLM 选简单的, 真方向是充分用 hermes memory 能力 (C 方向).

新 KPI 分 2 维:

  KPI A — hermes memory 是否真活 (跨 session 增长):
    delta(hermes_user_md + hermes_memories) 月增 > 0
    ⇒ memory.add 真被 LLM 调过

  KPI B — catfish_remember 是否正确收窄到 session-only:
    catfish_facts 月增 → **应该接近 0** (session 结束员工清, 跨 session 不持久)
    catfish_facts 如果持续涨 → LLM 仍误用 catfish_remember 当长期存储, SOUL 纪律失效

  理想数据 (BL-MEMORY-FULL-HERMES 1 个月后):
    hermes_user_md_bytes: ▲ 4KB → 30KB+   (员工长期画像真攒)
    hermes_memories_files: ▲ 4 → 20+       (分主题 memory 文件出现)
    catfish_facts_bytes: ≈ 0-2KB (session-only 速记, 不持久)
    catfish_journal_bytes: 维持 (本来就跨 session, 但希望 hermes 接管后能砍)

# 输出

CSV 一行一天, 追加写到 ~/Library/Logs/catfish/hermes-memory-audit.csv:

  date,hermes_user_md_bytes,hermes_memories_bytes,hermes_memories_files,...

后续用 pandas 1 行 plot:
  df.plot(x='date', y=['hermes_user_md_bytes', 'catfish_journal_bytes'])

# 装法

  cp this_script ~/.local/bin/audit-hermes-memory  &&  chmod +x ~/.local/bin/audit-hermes-memory
  + launchd plist (ai.catfish.hermes-audit.plist) — 每天 18:00 跑
"""

from __future__ import annotations

import csv
import os
import sys
from datetime import datetime
from pathlib import Path


def _safe_bytes(path: Path) -> int:
    """文件字节数, 不存在 / 不可读返 0."""
    try:
        return path.stat().st_size if path.exists() else 0
    except OSError:
        return 0


def _safe_mtime(path: Path) -> int:
    """文件 mtime epoch, 不存在 / 不可读返 0."""
    try:
        return int(path.stat().st_mtime) if path.exists() else 0
    except OSError:
        return 0


def _dir_total_bytes(dir_path: Path) -> tuple[int, int]:
    """目录总字节 + 文件数 (含子目录递归). 跳 .symlink.bak / .lock / .tmp 等."""
    if not dir_path.exists() or not dir_path.is_dir():
        return 0, 0
    total_bytes = 0
    file_count = 0
    skip_suffixes = (".lock", ".tmp", ".bak", ".swp")
    try:
        for p in dir_path.rglob("*"):
            if p.is_file() and not p.name.endswith(skip_suffixes):
                try:
                    total_bytes += p.stat().st_size
                    file_count += 1
                except OSError:
                    pass
    except OSError:
        pass
    return total_bytes, file_count


def _count_jsonl_lines(path: Path) -> int:
    """jsonl 文件行数 (空文件 / 不存在返 0). 用于 feedback.jsonl."""
    if not path.exists():
        return 0
    try:
        with open(path, encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
    except OSError:
        return 0


def audit_snapshot(home: Path) -> dict[str, int | str]:
    """采 6 大指标 + 时间戳, 返 dict (CSV 一行)."""
    hermes = home / ".hermes"
    catfish = home / ".catfish"

    hermes_user_md = hermes / "USER.md"
    hermes_soul_md = hermes / "SOUL.md"  # symlink, 跟踪 mtime 看 catfish 改没
    hermes_memories_dir = hermes / "memories"

    catfish_journal = catfish / "employee_journal.md"
    catfish_distilled = catfish / "distilled_facts.md"
    catfish_facts = catfish / "session_facts.json"
    catfish_feedback = catfish / "feedback.jsonl"

    memories_bytes, memories_files = _dir_total_bytes(hermes_memories_dir)

    return {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        # hermes 这边 (KPI 1 分子)
        "hermes_user_md_bytes": _safe_bytes(hermes_user_md),
        "hermes_memories_bytes": memories_bytes,
        "hermes_memories_files": memories_files,
        "hermes_soul_mtime": _safe_mtime(hermes_soul_md),
        # catfish 这边 (KPI 1 分母)
        "catfish_journal_bytes": _safe_bytes(catfish_journal),
        "catfish_distilled_bytes": _safe_bytes(catfish_distilled),
        "catfish_facts_bytes": _safe_bytes(catfish_facts),
        "catfish_feedback_lines": _count_jsonl_lines(catfish_feedback),
    }


def append_csv(csv_path: Path, snapshot: dict) -> None:
    """追加到 CSV. 首次写 header, 后续只 append 行. 失败抛 (launchd 会 log)."""
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not csv_path.exists()
    with open(csv_path, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(snapshot.keys()))
        if is_new:
            writer.writeheader()
        writer.writerow(snapshot)


def main() -> int:
    home = Path(os.environ.get("HOME") or Path.home())
    csv_path = home / "Library" / "Logs" / "catfish" / "hermes-memory-audit.csv"

    snapshot = audit_snapshot(home)
    append_csv(csv_path, snapshot)

    # stdout 打一行让 launchd log + 人 cat 时一眼看到
    print(
        f"[{snapshot['timestamp']}] "
        f"hermes USER.md={snapshot['hermes_user_md_bytes']}B "
        f"memories={snapshot['hermes_memories_bytes']}B/{snapshot['hermes_memories_files']}f | "
        f"catfish journal={snapshot['catfish_journal_bytes']}B "
        f"distilled={snapshot['catfish_distilled_bytes']}B "
        f"facts={snapshot['catfish_facts_bytes']}B "
        f"feedback={snapshot['catfish_feedback_lines']}l"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
