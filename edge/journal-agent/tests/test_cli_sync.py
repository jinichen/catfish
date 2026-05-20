"""catfish-journal sync --stdin (batch) 单测.

BL-CATFISH-TODO-SYNC v0.1.10 (5/20): catfish-todo-sync plugin 一次性传 N 个 op,
catfish-journal 单进程内部 read → process all → write 一次.

测点:
- jsonl ops 解析 (含错误处理)
- pending → add (含幂等)
- completed (journal 已有) → done
- completed (journal 没有) → add_done (v0.1.9 同语义)
- cancelled → delete
- 多 op 顺序处理 (后面 op 看得到前面 op 改的 journal)
- 空 stdin / 空 op
- 非 JSON 输入返 exit 2
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from catfish_journal import cli, core


class CmdSyncFakeJournal(unittest.TestCase):
    """sync 子命令端到端测试 — 用 tempfile 模拟 ~/.catfish/employee_journal.md."""

    def setUp(self) -> None:
        # 临时 journal 路径
        self._tmpdir = tempfile.TemporaryDirectory()
        self.journal_path = Path(self._tmpdir.name) / "employee_journal.md"
        # patch cli.JOURNAL_PATH 指向临时文件
        self._patcher = patch.object(cli, "JOURNAL_PATH", self.journal_path)
        self._patcher.start()

    def tearDown(self) -> None:
        self._patcher.stop()
        self._tmpdir.cleanup()

    def _run_sync(self, ops: list[dict]) -> tuple[int, dict]:
        """跑 catfish-journal sync --stdin <ops jsonl>. 返 (exit_code, stats)."""
        stdin_data = "\n".join(
            json.dumps(op, ensure_ascii=False) for op in ops
        )
        stdout_buf = io.StringIO()
        with (
            patch.object(sys, "stdin", io.StringIO(stdin_data)),
            patch.object(sys, "stdout", stdout_buf),
        ):
            rc = cli.main(["sync", "--stdin"])
        stats_line = stdout_buf.getvalue().strip()
        stats = json.loads(stats_line) if stats_line else {}
        return rc, stats

    def _journal(self) -> str:
        if not self.journal_path.exists():
            return ""
        return self.journal_path.read_text(encoding="utf-8")

    # ── pending → add ──────────────────────────────────────────────

    def test_sync_pending_adds_unchecked(self) -> None:
        rc, stats = self._run_sync([
            {"status": "pending", "content": "新任务 A"},
        ])
        self.assertEqual(rc, 0)
        self.assertEqual(stats["add"], 1)
        self.assertIn("- [ ] 新任务 A", self._journal())

    def test_sync_pending_idempotent(self) -> None:
        """pending 已存在 → add 调用但 core.add_todo 幂等返原 content. 仍计 add+1."""
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        self.journal_path.write_text("- [ ] 已有\n", encoding="utf-8")
        rc, stats = self._run_sync([
            {"status": "pending", "content": "已有"},
        ])
        self.assertEqual(rc, 0)
        # journal 仍只一行
        self.assertEqual(self._journal().count("- [ ] 已有"), 1)

    # ── completed (有 line) → done ─────────────────────────────────

    def test_sync_completed_in_journal_marks_done(self) -> None:
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        self.journal_path.write_text(
            "- [ ] 任务 A\n- [ ] 任务 B\n", encoding="utf-8"
        )
        rc, stats = self._run_sync([
            {"status": "completed", "content": "任务 A"},
        ])
        self.assertEqual(rc, 0)
        self.assertEqual(stats["done"], 1)
        j = self._journal()
        self.assertIn("- [x] 任务 A", j)
        self.assertIn("- [ ] 任务 B", j)  # 别误伤

    # ── completed (没 line) → add_done (v0.1.9 语义) ───────────────

    def test_sync_completed_not_in_journal_adds_done_history(self) -> None:
        rc, stats = self._run_sync([
            {"status": "completed", "content": "直接完成的事"},
        ])
        self.assertEqual(rc, 0)
        self.assertEqual(stats["add_done"], 1)
        self.assertIn("- [x] 直接完成的事", self._journal())

    # ── cancelled → delete ────────────────────────────────────────

    def test_sync_cancelled_deletes_line(self) -> None:
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        self.journal_path.write_text(
            "- [ ] 取消的\n- [ ] 留着的\n", encoding="utf-8"
        )
        rc, stats = self._run_sync([
            {"status": "cancelled", "content": "取消的"},
        ])
        self.assertEqual(rc, 0)
        self.assertEqual(stats["delete"], 1)
        j = self._journal()
        self.assertNotIn("取消的", j)
        self.assertIn("- [ ] 留着的", j)

    def test_sync_cancelled_not_in_journal_skipped(self) -> None:
        rc, stats = self._run_sync([
            {"status": "cancelled", "content": "不存在的"},
        ])
        self.assertEqual(rc, 0)
        self.assertEqual(stats["skip"], 1)

    # ── 多 op 顺序处理 ─────────────────────────────────────────────

    def test_sync_multiple_ops_in_order(self) -> None:
        """3 pending → 3 add; 1 add 后面跟 completed 应找到 line 调 done.

        关键: 后面 op 看得到前面 op 改的 journal in-memory state.
        """
        rc, stats = self._run_sync([
            {"status": "pending", "content": "任务 1"},
            {"status": "pending", "content": "任务 2"},
            {"status": "completed", "content": "任务 1"},  # 应能 done (前面刚 add)
            {"status": "pending", "content": "任务 3"},
        ])
        self.assertEqual(rc, 0)
        self.assertEqual(stats["add"], 3)
        self.assertEqual(stats["done"], 1)  # 任务 1 done
        j = self._journal()
        self.assertIn("- [x] 任务 1", j)
        self.assertIn("- [ ] 任务 2", j)
        self.assertIn("- [ ] 任务 3", j)

    # ── 空输入 / 错误 ──────────────────────────────────────────────

    def test_sync_empty_stdin(self) -> None:
        rc, stats = self._run_sync([])
        self.assertEqual(rc, 0)
        # 空 op → 全 0
        self.assertEqual(sum(stats.values()), 0)

    def test_sync_empty_content_skipped(self) -> None:
        rc, stats = self._run_sync([
            {"status": "pending", "content": "  "},  # 全空白
        ])
        self.assertEqual(rc, 0)
        self.assertEqual(stats["skip"], 1)

    def test_sync_unknown_status_skipped(self) -> None:
        rc, stats = self._run_sync([
            {"status": "deleted_or_what", "content": "未知"},
        ])
        self.assertEqual(rc, 0)
        self.assertEqual(stats["skip"], 1)

    def test_sync_invalid_json_returns_2(self) -> None:
        """非 JSON 输入 → exit 2 + stderr 错误."""
        stdin_data = "not a json line"
        stderr_buf = io.StringIO()
        with (
            patch.object(sys, "stdin", io.StringIO(stdin_data)),
            patch.object(sys, "stderr", stderr_buf),
        ):
            rc = cli.main(["sync", "--stdin"])
        self.assertEqual(rc, 2)
        self.assertIn("非 JSON", stderr_buf.getvalue())


if __name__ == "__main__":
    unittest.main()
