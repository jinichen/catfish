#!/usr/bin/env python3
"""BL-CENTRAL-EDGE-TOOL-ARCHIVE Phase 5 (5/22 鸿波):
老 PG tool_archives 表数据一次性导出到员工本机.

# 用法

```bash
# 在 catfish-gateway 同 venv 里跑, 需要 CATFISH_DB_URL 环境变量
cd ~/person_task/catfish/central/llm-gateway
source venv/bin/activate
export CATFISH_DB_URL=postgresql://localhost/catfish  # 你真 PG URL
python3 ../../scripts/migrate_tool_archives_pg_to_local.py \\
    --user chenhongbo@ffcs.cn \\
    --catfish-home ~/.catfish \\
    --catfish-user "<your OIDC sub>"  # 跟 ~/.catfish/oauth/id_token sub 字段一致
```

# 行为

1. 读 PG `tool_archives` 表所有行 (可选 --user 过滤当前员工)
2. 每行写本机 `~/.catfish/tool_archives/<date>/<ref>.json` + sqlite 索引
3. 跳已存在的 ref (幂等, 可多次跑)
4. 跑完报 N rows imported / M skipped / K errors
5. **不动 PG** — 留作历史. 14 天 TTL 自动过期, 1-2 周后手工 DROP TABLE

# 安全

- 一次性脚本, 不进 cron / 不自动调
- PG 凭据从 env CATFISH_DB_URL 读, 不入参
- 失败行 log 出来给员工手工排查, 不 raise
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("migrate_tool_archives")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument(
        "--user",
        help="(可选) 只导这个 user_email 的行, 默认全导 (单员工 Mac 场景一般够)",
    )
    p.add_argument(
        "--catfish-home",
        default=str(Path.home() / ".catfish"),
        help="本机 catfish 目录, 默认 ~/.catfish",
    )
    p.add_argument(
        "--catfish-user",
        default=None,
        help="OIDC sub 写入新 catfish_user 字段做 portable. 不传 = None (read 时无权检查)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="只统计不写盘",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="(测试用) 最多导 N 条",
    )
    return p.parse_args()


def _load_env_files() -> None:
    """5/22 鸿波: db_url 一般在 .env, 自动 source.

    优先级 (后者覆盖前者, 让 export 的最高):
      1. ~/.hermes/.env (hermes 默认配置)
      2. catfish-gateway/.env (gateway-specific)
      3. cwd/.env
      4. 已 export 的 env (人工 export 不覆盖)
    """
    candidates = [
        Path.home() / ".hermes" / ".env",
        Path(__file__).parent.parent / "central" / "llm-gateway" / ".env",
        Path.cwd() / ".env",
    ]
    for env_path in candidates:
        if not env_path.exists():
            continue
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip().removeprefix("export ").strip()
                value = value.strip().strip("'").strip('"')
                # 已 export 的不覆盖 (人工 export 优先)
                if key not in os.environ:
                    os.environ[key] = value
            logger.info("loaded env from %s", env_path)
        except OSError as e:
            logger.debug("跳过 %s (%s)", env_path, e)


def main() -> int:
    args = parse_args()
    _load_env_files()
    db_url = os.environ.get("CATFISH_DB_URL", "").strip()
    if not db_url:
        logger.error("CATFISH_DB_URL 未设, 没法连 PG. 看 ~/.hermes/.env 或 catfish-gateway/.env 有没有.")
        return 2

    # 让 tool_archive_local 用我们指定的 catfish_home
    os.environ["CATFISH_HOME"] = args.catfish_home
    # 加 tool-bridge src 到 path
    repo_root = Path(__file__).parent.parent.resolve()
    sys.path.insert(0, str(repo_root / "edge" / "tool-bridge" / "src"))
    try:
        from catfish_tool_bridge import tool_archive_local as tal
    except ImportError as e:
        logger.error("导入 tool_archive_local 失败 (catfish-tool-bridge 路径错): %s", e)
        return 2

    try:
        import psycopg
    except ImportError:
        logger.error("psycopg 未装 — 在 gateway venv 里跑")
        return 2

    logger.info("连 PG: %s", db_url.split("@")[-1] if "@" in db_url else db_url)
    sql = """
        SELECT ref, session_id, user_email, tool_call_id, tool_name,
               content, content_bytes, lines, summary, summary_model,
               summary_at, created_at
        FROM tool_archives
        WHERE 1=1
    """
    params: list = []
    if args.user:
        sql += " AND user_email = %s"
        params.append(args.user)
    sql += " ORDER BY created_at"
    if args.limit:
        sql += " LIMIT %s"
        params.append(args.limit)

    n_total = 0
    n_skipped = 0
    n_written = 0
    n_errors = 0

    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            for row in cur:
                n_total += 1
                (ref, session_id, user_email, tool_call_id, tool_name,
                 content, content_bytes, lines, summary, summary_model,
                 summary_at, created_at) = row

                # 跳已存在 (幂等)
                existing = tal.read_archive(ref) if not args.dry_run else None
                if existing is not None:
                    n_skipped += 1
                    continue

                if args.dry_run:
                    n_written += 1
                    continue

                # 写本机 — 注意: tool_archive_local.upsert_archive 用 today
                # 做 date_dir, 这里要按老 created_at 做 date_dir.
                try:
                    d_str = created_at.strftime("%Y-%m-%d") if hasattr(created_at, "strftime") else str(created_at)[:10]
                    archives_dir = Path(args.catfish_home) / "tool_archives" / d_str
                    archives_dir.mkdir(parents=True, exist_ok=True)
                    file_path = archives_dir / f"{ref}.json"
                    rel_path = f"{d_str}/{ref}.json"

                    created_iso = (
                        created_at.isoformat() if hasattr(created_at, "isoformat")
                        else str(created_at)
                    )
                    summary_at_iso = (
                        summary_at.isoformat() if hasattr(summary_at, "isoformat")
                        else (str(summary_at) if summary_at else None)
                    )

                    payload = {
                        "ref": ref,
                        "tool_name": tool_name,
                        "tool_call_id": tool_call_id,
                        "session_id": session_id,  # 老 hermes session_id 留作历史
                        "catfish_user": args.catfish_user,
                        "content": content,
                        "content_bytes": content_bytes,
                        "lines": lines,
                        "summary": summary,
                        "summary_model": summary_model,
                        "summary_at": summary_at_iso,
                        "created_at": created_iso,
                        "_migrated_from_pg": True,
                        "_migrated_at": datetime.now(timezone.utc).isoformat(),
                        "_legacy_user_email": user_email,
                    }
                    file_path.write_text(
                        json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )

                    # 写 sqlite 索引
                    tal._ensure_db()
                    with tal._get_conn() as sqc:
                        sqc.execute(
                            """
                            INSERT INTO tool_archives_index (
                                ref, file_path, tool_name, content_bytes, lines,
                                has_summary, created_at, session_id, catfish_user, tool_call_id
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(ref) DO NOTHING
                            """,
                            (
                                ref, rel_path, tool_name, content_bytes, lines,
                                1 if summary else 0,
                                created_iso, session_id, args.catfish_user, tool_call_id,
                            ),
                        )
                    n_written += 1
                    if n_written % 100 == 0:
                        logger.info("已迁 %d 条 ...", n_written)
                except Exception as e:  # noqa: BLE001
                    logger.warning("迁 ref=%s 挂: %s", ref, e)
                    n_errors += 1

    logger.info("=" * 60)
    logger.info("迁移完成 (%s 模式)", "dry-run" if args.dry_run else "真写")
    logger.info("  PG 总行数:  %d", n_total)
    logger.info("  跳已存在:   %d", n_skipped)
    logger.info("  写本机:     %d", n_written)
    logger.info("  错误:       %d", n_errors)
    logger.info("=" * 60)
    if n_total > 0 and not args.dry_run:
        logger.info(
            "验证: ls %s/tool_archives/ | wc -l (应 ≥ %d 个 date dir)",
            args.catfish_home,
            len({row_d for row_d in [None]}) or 1,
        )
        logger.info(
            "之后 PG 14 天 TTL 自动过期, 期满后:"
        )
        logger.info(
            '  psql -c "DROP TABLE tool_archives" + alembic down revision'
        )
    return 0 if n_errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
