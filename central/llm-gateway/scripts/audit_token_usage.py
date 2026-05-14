#!/usr/bin/env python3
"""5/14 鸿波 token 用量 audit 脚本.

跑法 (在 catfish/central/llm-gateway/ 目录):

    source venv/bin/activate
    python scripts/audit_token_usage.py

    # 或换用户 / 换天数
    python scripts/audit_token_usage.py --user chenhongbo@ffcs.cn --days 1
    python scripts/audit_token_usage.py --days 7

输出三段:
1. 总量 + 平均
2. 按 model 分布 (谁烧得多)
3. top 20 大 prompt 请求 (找大头)

自动从 .env 读 CATFISH_DB_URL, 不需要 export 环境变量.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

# 自动加载 .env (跟 gateway 启动同源)
def _load_dotenv():
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.exists():
        print(f"⚠ .env 不在 {env_path}, 用当前 shell 环境", file=sys.stderr)
        return
    with env_path.open() as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and v and k not in os.environ:
                    os.environ[k] = v


_load_dotenv()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", default="chenhongbo@ffcs.cn", help="按 user_email 过滤. 空 = 全员")
    parser.add_argument("--days", type=int, default=1, help="看过去几天")
    args = parser.parse_args()

    db_url = os.environ.get("CATFISH_DB_URL", "").strip()
    if not db_url:
        print("❌ CATFISH_DB_URL 未设. 看 .env 或 gateway 启动配置.", file=sys.stderr)
        sys.exit(1)

    # 隐藏密码显示
    print(f"DB: {db_url.split('@')[-1] if '@' in db_url else db_url}")
    print(f"User filter: {args.user or '(全员)'}")
    print(f"Days: {args.days}")
    print()

    try:
        import psycopg
    except ImportError:
        print("❌ psycopg 没装. pip install psycopg[binary]", file=sys.stderr)
        sys.exit(1)

    cutoff_ms = int((time.time() - args.days * 86400) * 1000)
    user_clause = "AND user_email = %s" if args.user else ""
    user_params = [args.user] if args.user else []

    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:

            # ─── Q1 总量 ──────────────────────────────────────
            print("=" * 70)
            print(f"Q1: 过去 {args.days} 天 总量 + 平均")
            print("=" * 70)
            cur.execute(f"""
                SELECT
                    COUNT(*) AS total_requests,
                    COALESCE(SUM(tokens_in), 0) AS sum_prompt,
                    COALESCE(SUM(tokens_out), 0) AS sum_completion,
                    COALESCE(ROUND(AVG(tokens_in)), 0) AS avg_prompt,
                    COALESCE(MAX(tokens_in), 0) AS max_prompt,
                    COALESCE(ROUND(AVG(latency_ms)), 0) AS avg_latency_ms
                FROM gateway_audit
                WHERE ts_ms >= %s
                {user_clause}
            """, [cutoff_ms, *user_params])
            row = cur.fetchone()
            if row:
                total, sp, sc, ap, mp, lat = row
                print(f"  总请求数:     {total:>12,}")
                print(f"  prompt token: {sp:>12,}")
                print(f"  completion:   {sc:>12,}")
                print(f"  avg prompt:   {ap:>12,}  ← 这个 < 5K 健康, 30K+ 偏重, 60K+ 太重")
                print(f"  max prompt:   {mp:>12,}")
                print(f"  avg latency:  {lat:>12,} ms")
                print()
                if ap and ap > 30000:
                    print("  ⚠ 平均 prompt 超 30K, SOUL/journal 注入可能过重")
                elif ap and ap > 60000:
                    print("  ⚠⚠ 平均 prompt 超 60K, 几乎一定是注入问题")

            # ─── Q2 按 model ──────────────────────────────────
            print("=" * 70)
            print(f"Q2: 按 model 分布 (谁烧得多)")
            print("=" * 70)
            cur.execute(f"""
                SELECT
                    model,
                    COUNT(*) AS n,
                    COALESCE(SUM(tokens_in), 0) AS sum_in,
                    COALESCE(SUM(tokens_out), 0) AS sum_out,
                    COALESCE(ROUND(AVG(tokens_in)), 0) AS avg_in
                FROM gateway_audit
                WHERE ts_ms >= %s
                {user_clause}
                GROUP BY model
                ORDER BY sum_in DESC
            """, [cutoff_ms, *user_params])
            print(f"  {'model':<40} {'n':>6} {'sum_in':>12} {'sum_out':>10} {'avg_in':>10}")
            print(f"  {'-' * 40} {'-' * 6} {'-' * 12} {'-' * 10} {'-' * 10}")
            for r in cur.fetchall():
                m, n, si, so, ai = r
                tag = ""
                if "private" in (m or ""):
                    tag = " 🟢 内网"
                elif "public" in (m or ""):
                    tag = " 🔴 公网烧钱"
                print(f"  {m or '(unknown)':<40} {n:>6} {si:>12,} {so:>10,} {ai:>10,}{tag}")
            print()

            # ─── Q3 top 20 大请求 ────────────────────────────
            print("=" * 70)
            print(f"Q3: top 20 大 prompt 请求 (找大头)")
            print("=" * 70)
            cur.execute(f"""
                SELECT
                    to_char(to_timestamp(ts_ms / 1000), 'MM-DD HH24:MI:SS') AS ts,
                    model,
                    tokens_in,
                    tokens_out,
                    status,
                    LEFT(COALESCE(error_msg, ''), 50) AS err
                FROM gateway_audit
                WHERE ts_ms >= %s
                {user_clause}
                ORDER BY tokens_in DESC
                LIMIT 20
            """, [cutoff_ms, *user_params])
            print(f"  {'time':<15} {'model':<35} {'in':>10} {'out':>8} {'status':<10} {'err'}")
            print(f"  {'-' * 15} {'-' * 35} {'-' * 10} {'-' * 8} {'-' * 10} {'-' * 30}")
            for r in cur.fetchall():
                ts, m, ti, to, st, err = r
                print(f"  {ts:<15} {(m or '?')[:35]:<35} {ti:>10,} {to:>8,} {st or '?':<10} {err or ''}")
            print()

            # ─── Q4 status 分布 (看 retry / fallback) ───────
            print("=" * 70)
            print(f"Q4: status 分布 (看异常 / fallback 在哪)")
            print("=" * 70)
            cur.execute(f"""
                SELECT status, COUNT(*) AS n, COALESCE(SUM(tokens_in), 0) AS sum_in
                FROM gateway_audit
                WHERE ts_ms >= %s
                {user_clause}
                GROUP BY status
                ORDER BY n DESC
            """, [cutoff_ms, *user_params])
            for r in cur.fetchall():
                s, n, si = r
                print(f"  {(s or '?'):<25} n={n:>6} sum_in={si:>12,}")
            print()


if __name__ == "__main__":
    main()
