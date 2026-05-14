#!/usr/bin/env python3
"""5/14 audit: SOUL / journal / facts / 等注入到底多大.

跑法:
    cd central/llm-gateway && source venv/bin/activate
    python scripts/audit_inject_size.py

输出: gateway 注入的各段长度 (字符 + 估 token), 找大头.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# load .env
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and v and k not in os.environ:
                os.environ[k] = v


def fmt(n: int) -> str:
    """char 估 token (中英混 / 2 保守, 跟 fallback.py estimate_prompt_tokens 同口径)."""
    return f"{n:>8,} chars / ~{n // 2:>6,} tokens"


def main():
    # ─── SOUL.md 主文件 ────────────────────────────────────
    soul_paths = [
        Path.home() / ".catfish" / "SOUL.md",
        Path("/etc/catfish/SOUL.md"),
        Path(__file__).parent.parent.parent.parent / "edge" / "identity" / "SOUL.md",
    ]
    print("=" * 70)
    print("SOUL.md 注入 (每次请求都注入)")
    print("=" * 70)
    soul_size = 0
    for p in soul_paths:
        if p.exists():
            size = len(p.read_text(encoding="utf-8"))
            print(f"  {p}")
            print(f"    {fmt(size)}")
            soul_size = size
            break
    else:
        print("  (找不到 SOUL.md, 检查 ~/.catfish/SOUL.md 或 edge/identity/SOUL.md)")

    # ─── 场景 SOUL (BL-SOUL-SCENARIO P2 5/13) ─────────────
    print()
    print("=" * 70)
    print("场景 SOUL (按 tools 触发注入, 5/13 BL-SOUL-SCENARIO P2)")
    print("=" * 70)
    scenario_paths = [
        Path.home() / ".catfish" / "SOUL_BROWSER.md",
        Path.home() / ".catfish" / "SOUL_EXECUTE_CODE.md",
        Path.home() / ".catfish" / "SOUL_FFCS.md",
    ]
    for p in scenario_paths:
        if p.exists():
            size = len(p.read_text(encoding="utf-8"))
            print(f"  {p.name:<25} {fmt(size)}")
        else:
            print(f"  {p.name:<25} (未注入, 可能是触发条件没命中)")

    # ─── employee_journal ──────────────────────────────────
    print()
    print("=" * 70)
    print("employee_journal (鸿波自己的, 5/13 cap 5K)")
    print("=" * 70)
    journal_dir = Path.home() / ".catfish" / "employee_journal"
    user_jsonl = journal_dir / "chenhongbo@ffcs.cn.jsonl"
    if user_jsonl.exists():
        full_size = user_jsonl.stat().st_size
        print(f"  全文件: {fmt(full_size)} (cap 前)")
        # 注入时是 last-N 行 + cap, 这里粗看
    else:
        print(f"  ({user_jsonl} 不存在 — 没 journal)")

    # ─── session_facts ─────────────────────────────────────
    print()
    print("=" * 70)
    print("session_facts / fact_patch")
    print("=" * 70)
    facts_dir = Path.home() / ".catfish" / "session_facts"
    if facts_dir.exists():
        total = 0
        n = 0
        for f in facts_dir.glob("*.json"):
            total += f.stat().st_size
            n += 1
        print(f"  session_facts: {n} 个文件, 总 {fmt(total)}")
    else:
        print(f"  ({facts_dir} 不存在)")

    # ─── 估算单次请求总注入 ────────────────────────────────
    print()
    print("=" * 70)
    print("估算: 单次请求 prompt 中 inject 部分 (不含用户对话)")
    print("=" * 70)
    print(f"  SOUL.md:           ~{soul_size // 2:>6,} tokens (每次都注)")
    print(f"  + 场景 SOUL:        ~视触发, BROWSER ~3K / EXECUTE_CODE ~3K / FFCS ~2K")
    print(f"  + journal (cap 5K): ~5,000 tokens (BL-FIX21 5/13)")
    print(f"  + session_facts:   ~3,000 tokens (BL-MM5 估)")
    print(f"  + tool schemas:     ~视 tools 数量, 30 工具 * 200 tokens = 6,000")
    print()
    print(f"  典型最小 (chat 无 tool, 无场景):  ~{soul_size // 2 + 5000:>6,} tokens 起步")
    print(f"  典型完整 (tool + 1 场景):         ~{soul_size // 2 + 5000 + 3000 + 6000:>6,} tokens")
    print()
    print("跟 audit 数据 avg_prompt 41,105 比对:")
    expect = soul_size // 2 + 5000 + 3000 + 6000
    print(f"  期望注入 ~{expect:,}, 实际 avg 41,105 → 差 {41105 - expect:,} 是真用户对话累积")
    print(f"  说明: 大头确实是 SOUL ({soul_size // 2:,} tokens 占 ~{(soul_size // 2) * 100 // 41105}%)")


if __name__ == "__main__":
    main()
