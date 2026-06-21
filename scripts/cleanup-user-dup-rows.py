#!/usr/bin/env python3
"""
P3.5.54 — 一次性清洗 ~/.hermes/state.db 历史 user msg 双行.

# 真因 (state.db 实测验证, 不是猜)

Companion 老路径在 useChat.ts:619 IIFE 里 persistMessage(userMsg) 写一行,
hermes v0.17 gateway/run.py:9786 _flush_messages_to_session_db 350ms 后又写
一行 (#860 skip_db 标记证实). Rust idempotent guard session_write.rs:278 是
assistant-only (user 不查), 两侧 SQLite connection 各走各路, dedup 覆不到.

P3.5.54 已撤回 Companion 端 user msg 写入 (hermes 是唯一 writer), 新 session
不再出双行. 但历史 session 已经有 32+ 对 dup, ChatTab.tsx polling 5s 后 reload
仍会看到 2 个 user bubble. 这个脚本一次性清洗历史 dup, 保留每对 dup 的较早
那行 (Companion 写的), 删较晚那行 (hermes 写的) — 因为较早的是先看见的,
更可能挂在 attachments.db messageId 上.

# 安全

- 先 dry-run (--dry-run, 默认), 列将删的 rowid 列表
- 显式 --apply 才真删
- 自动 backup state.db 到 .bak.{timestamp} 再删
- 同时清 session.message_count 让计数对得上 (-1 每删一行)

# 用法

  python3 scripts/cleanup-user-dup-rows.py            # dry-run
  python3 scripts/cleanup-user-dup-rows.py --apply    # 真删
  python3 scripts/cleanup-user-dup-rows.py --db /path/state.db --apply
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import time
from pathlib import Path


DEFAULT_DB = Path.home() / ".hermes" / "state.db"


def find_dup_pairs(conn: sqlite3.Connection) -> list[tuple[str, int, float, int, float, str]]:
    """找所有相邻 user dup (同 session, 同 trim content, < 5s 间隔).

    返回 (session_id, earlier_id, earlier_ts, later_id, later_ts, content_preview) 列表.
    """
    cur = conn.cursor()
    cur.execute(
        """
        SELECT m1.session_id, m1.id, m1.timestamp, m2.id, m2.timestamp,
               substr(m1.content, 1, 50)
        FROM messages m1
        JOIN messages m2 ON m1.session_id = m2.session_id
            AND m2.id > m1.id
            AND m2.role = 'user'
            AND m1.role = 'user'
            AND trim(m1.content) = trim(m2.content)
            AND (m2.timestamp - m1.timestamp) < 5.0
            AND (m2.timestamp - m1.timestamp) > 0
        ORDER BY m1.session_id, m1.id
        """
    )
    return cur.fetchall()


def cleanup(db_path: Path, apply: bool) -> None:
    if not db_path.exists():
        print(f"ERROR: state.db 不存在: {db_path}", file=sys.stderr)
        sys.exit(2)

    conn = sqlite3.connect(str(db_path))
    try:
        pairs = find_dup_pairs(conn)
    finally:
        conn.close()

    if not pairs:
        print("没找到 user msg 双行, db 干净.")
        return

    print(f"找到 {len(pairs)} 对相邻 user msg 双行 (3s 内 content trim 后相同):")
    print()
    for sid, eid, ets, lid, lts, content in pairs:
        gap_ms = int((lts - ets) * 1000)
        print(
            f"  session={sid} 留={eid} 删={lid} 间隔={gap_ms}ms  '{content}'"
        )
    print()

    if not apply:
        print("Dry-run. 真删加 --apply.")
        return

    # backup
    ts = int(time.time())
    backup_path = db_path.with_suffix(f".db.bak.{ts}")
    print(f"备份 state.db → {backup_path}")
    shutil.copy2(db_path, backup_path)

    # delete the later-written rows (hermes side) — 但保留较早的 (Companion 写,
    # attachments.db messageId 挂在它身上). 同时 session.message_count -1 每行.
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        # session_id → number of deletions
        session_decrements: dict[str, int] = {}
        for sid, _eid, _ets, lid, _lts, _content in pairs:
            cur.execute(
                "DELETE FROM messages WHERE id = ? AND session_id = ?",
                (lid, sid),
            )
            session_decrements[sid] = session_decrements.get(sid, 0) + 1
        for sid, n in session_decrements.items():
            cur.execute(
                "UPDATE sessions SET message_count = MAX(message_count - ?, 0) WHERE id = ?",
                (n, sid),
            )
        conn.commit()
    finally:
        conn.close()

    print()
    print(f"✓ 删了 {len(pairs)} 行, 涉及 {len(session_decrements)} 个 session.")
    print(f"  session.message_count 已 -{len(pairs)}.")
    print(f"  backup 在 {backup_path}, 出问题 cp 回去.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help=f"state.db 路径 (默认 {DEFAULT_DB})",
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="真删 (默认 dry-run 只列)",
    )
    args = ap.parse_args()
    cleanup(args.db, args.apply)


if __name__ == "__main__":
    main()
