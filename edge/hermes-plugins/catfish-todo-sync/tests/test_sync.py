"""catfish-todo-sync 单测.

测点: monkey-patch 逻辑 + _sync_to_journal 状态过滤 + subprocess 调用安全.

不真启动 hermes — 模拟 TodoStore 类来跑 patch 验证.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# 加 plugin 到 path
PLUGIN_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_DIR))

import catfish_todo_sync  # noqa: E402


class TestSyncToJournal(unittest.TestCase):
    """_sync_to_journal 函数 — 单调 fallback 路径测试.

    v0.1.10 起 _sync_to_journal 默认走 batch 路径. 本 class 测的是 batch 不可用
    时的单调 fallback (老 catfish-journal CLI). 用 setUp patch _sync_to_journal_batch
    强制返 False 让测试走单调路径.

    Batch 路径自身的测试在 TestBatchSync.
    """

    def setUp(self) -> None:
        # 每次重置 _PATCHED 状态防测试间污染
        catfish_todo_sync._PATCHED = False
        # 强制 batch 返 False 走单调 fallback (老路径)
        self._batch_patcher = patch.object(
            catfish_todo_sync, "_sync_to_journal_batch", return_value=False
        )
        self._batch_patcher.start()

    def tearDown(self) -> None:
        self._batch_patcher.stop()

    @patch("catfish_todo_sync._find_catfish_journal_bin")
    @patch("catfish_todo_sync.subprocess.run")
    def test_sync_pending_calls_cli(self, mock_run: MagicMock, mock_find: MagicMock) -> None:
        """pending 状态的 TODO 应该调 catfish-journal add."""
        mock_find.return_value = "/fake/path/catfish-journal"
        catfish_todo_sync._sync_to_journal(
            [{"content": "测试任务", "status": "pending"}]
        )
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        self.assertEqual(args[0], "/fake/path/catfish-journal")
        self.assertEqual(args[1], "add")
        self.assertEqual(args[2], "测试任务")

    @patch("catfish_todo_sync._find_catfish_journal_bin")
    @patch("catfish_todo_sync.subprocess.run")
    def test_sync_completed_calls_done(self, mock_run: MagicMock, mock_find: MagicMock) -> None:
        """v0.1.8: completed 状态应调 catfish-journal done (查 line 后)."""
        mock_find.return_value = "/fake/path/catfish-journal"
        # 第一次调 list 返 [{"text": "已完成", "line": 5}]; 第二次调 done
        list_output = '[{"text": "已完成", "line": 5, "source": "checkbox", "section": ""}]'.encode("utf-8")
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=list_output),
            MagicMock(returncode=0, stdout=b"", stderr=b""),
        ]
        catfish_todo_sync._sync_to_journal(
            [{"content": "已完成", "status": "completed"}]
        )
        assert mock_run.call_count == 2
        # 第二条调用是 done
        done_call = mock_run.call_args_list[1]
        assert done_call[0][0][1] == "done"
        assert "--line" in done_call[0][0]
        assert "5" in done_call[0][0]

    @patch("catfish_todo_sync._find_catfish_journal_bin")
    @patch("catfish_todo_sync.subprocess.run")
    def test_sync_completed_not_in_journal_adds_done_history(
        self, mock_run: MagicMock, mock_find: MagicMock
    ) -> None:
        """v0.1.9: completed 但 journal 里没该 TODO → 调 add --done 补 [x] 历史.

        v0.1.8 之前是 silent skip, v0.1.9 改成补一条 [x] 让 BriefingCard 看得到
        历史完成事项 (LLM 直接标完成没经 add 的场景).
        """
        mock_find.return_value = "/fake/path"
        # list 返空, 找不到; add --done 调用也 mock 成功
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=b'[]'),  # list 找不到
            MagicMock(returncode=0, stdout=b"", stderr=b""),  # add --done
        ]
        catfish_todo_sync._sync_to_journal(
            [{"content": "不存在的事", "status": "completed"}]
        )
        # v0.1.9: 调 list + add --done 共 2 次
        assert mock_run.call_count == 2
        add_call = mock_run.call_args_list[1]
        assert add_call[0][0][1] == "add"
        assert add_call[0][0][2] == "不存在的事"
        assert "--done" in add_call[0][0]

    @patch("catfish_todo_sync._find_catfish_journal_bin")
    @patch("catfish_todo_sync.subprocess.run")
    def test_sync_cancelled_calls_delete(self, mock_run: MagicMock, mock_find: MagicMock) -> None:
        """v0.1.8: cancelled 状态应调 catfish-journal delete."""
        mock_find.return_value = "/fake/path"
        list_output = '[{"text": "取消", "line": 3, "source": "checkbox", "section": ""}]'.encode("utf-8")
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=list_output),
            MagicMock(returncode=0, stdout=b"", stderr=b""),
        ]
        catfish_todo_sync._sync_to_journal(
            [{"content": "取消", "status": "cancelled"}]
        )
        assert mock_run.call_count == 2
        delete_call = mock_run.call_args_list[1]
        assert delete_call[0][0][1] == "delete"

    @patch("catfish_todo_sync._find_catfish_journal_bin")
    @patch("catfish_todo_sync.subprocess.run")
    def test_sync_in_progress_writes(self, mock_run: MagicMock, mock_find: MagicMock) -> None:
        """in_progress 状态应该写 journal (相当于未完成)."""
        mock_find.return_value = "/fake/path/catfish-journal"
        catfish_todo_sync._sync_to_journal(
            [{"content": "进行中", "status": "in_progress"}]
        )
        mock_run.assert_called_once()

    @patch("catfish_todo_sync._find_catfish_journal_bin")
    @patch("catfish_todo_sync.subprocess.run")
    def test_sync_empty_content_skipped(self, mock_run: MagicMock, mock_find: MagicMock) -> None:
        """空 content 不调 CLI."""
        mock_find.return_value = "/fake/path/catfish-journal"
        catfish_todo_sync._sync_to_journal(
            [{"content": "", "status": "pending"}]
        )
        mock_run.assert_not_called()

    @patch("catfish_todo_sync._find_catfish_journal_bin")
    @patch("catfish_todo_sync.subprocess.run")
    def test_sync_no_cli_silent(self, mock_run: MagicMock, mock_find: MagicMock) -> None:
        """catfish-journal CLI 没装时静默, 不抛."""
        mock_find.return_value = None
        # 不应抛
        catfish_todo_sync._sync_to_journal(
            [{"content": "X", "status": "pending"}]
        )
        mock_run.assert_not_called()

    @patch("catfish_todo_sync._find_catfish_journal_bin")
    @patch("catfish_todo_sync.subprocess.run")
    def test_sync_subprocess_failure_silent(
        self, mock_run: MagicMock, mock_find: MagicMock
    ) -> None:
        """subprocess 调用失败不抛, 静默 log.debug."""
        mock_find.return_value = "/fake/path"
        mock_run.side_effect = Exception("subprocess crashed")
        # 不应抛, 主流程继续
        catfish_todo_sync._sync_to_journal(
            [{"content": "X", "status": "pending"}]
        )

    @patch("catfish_todo_sync._find_catfish_journal_bin")
    @patch("catfish_todo_sync.subprocess.run")
    def test_sync_batch_multiple_todos(
        self, mock_run: MagicMock, mock_find: MagicMock
    ) -> None:
        """v0.1.9: 多条 TODO 逐条 sync.
        3 pending → 3 add; 1 completed → 1 list (mock 返空) + 1 add --done 补历史
        → 共 5 次 CLI 调用."""
        mock_find.return_value = "/fake/path"
        # mock_run 默认返 MagicMock, list 调用时 stdout 是 mock 不是 bytes,
        # json 解析失败 → completed 路径在 list 后走 add --done. 这里 mock 返空 list.
        mock_run.return_value = MagicMock(returncode=0, stdout=b"[]")
        catfish_todo_sync._sync_to_journal([
            {"content": "任务 1", "status": "pending"},
            {"content": "任务 2", "status": "pending"},
            {"content": "完成", "status": "completed"},  # list 查不到, v0.1.9 走 add --done
            {"content": "任务 3", "status": "pending"},
        ])
        # 3 pending add + 1 completed list + 1 completed add --done = 5 次 CLI 调用
        self.assertEqual(mock_run.call_count, 5)


class TestBatchSync(unittest.TestCase):
    """v0.1.10 (5/20) batch path _sync_to_journal_batch 单测."""

    def setUp(self) -> None:
        catfish_todo_sync._PATCHED = False

    @patch("catfish_todo_sync.subprocess.run")
    def test_batch_calls_sync_stdin(self, mock_run: MagicMock) -> None:
        """batch 路径走 catfish-journal sync --stdin, 一次 subprocess 处理 N op."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout=b'{"add": 3}', stderr=b""
        )
        ok = catfish_todo_sync._sync_to_journal_batch(
            "/fake/path",
            [
                {"content": "X", "status": "pending"},
                {"content": "Y", "status": "completed"},
                {"content": "Z", "status": "cancelled"},
            ],
        )
        assert ok is True
        # 只调一次
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "/fake/path"
        assert args[1] == "sync"
        assert "--stdin" in args
        # stdin 含 3 行 jsonl
        stdin_data = mock_run.call_args[1]["input"]
        lines = stdin_data.decode("utf-8").splitlines()
        assert len(lines) == 3

    @patch("catfish_todo_sync.subprocess.run")
    def test_batch_old_cli_fallback(self, mock_run: MagicMock) -> None:
        """老 catfish-journal (< v0.1.10) 没 sync 子命令 → exit 2 + invalid choice → False."""
        mock_run.return_value = MagicMock(
            returncode=2,
            stdout=b"",
            stderr=b"usage: catfish-journal ... invalid choice: 'sync'",
        )
        ok = catfish_todo_sync._sync_to_journal_batch(
            "/fake/path",
            [{"content": "X", "status": "pending"}],
        )
        assert ok is False

    @patch("catfish_todo_sync.subprocess.run")
    def test_batch_timeout_fallback(self, mock_run: MagicMock) -> None:
        """batch timeout → False (caller fallback 单调)."""
        import subprocess as _subprocess
        mock_run.side_effect = _subprocess.TimeoutExpired("cmd", 5)
        ok = catfish_todo_sync._sync_to_journal_batch(
            "/fake/path",
            [{"content": "X", "status": "pending"}],
        )
        assert ok is False

    @patch("catfish_todo_sync.subprocess.run")
    def test_batch_empty_todos_no_call(self, mock_run: MagicMock) -> None:
        """空 todos → 不调 CLI, 直接返 True."""
        ok = catfish_todo_sync._sync_to_journal_batch("/fake/path", [])
        assert ok is True
        mock_run.assert_not_called()

    @patch("catfish_todo_sync.subprocess.run")
    def test_batch_all_empty_content_no_call(self, mock_run: MagicMock) -> None:
        """所有 todos content 都空 → 不调 CLI."""
        ok = catfish_todo_sync._sync_to_journal_batch(
            "/fake/path",
            [
                {"content": "", "status": "pending"},
                {"content": "  ", "status": "completed"},
            ],
        )
        assert ok is True
        mock_run.assert_not_called()

    @patch("catfish_todo_sync._find_catfish_journal_bin")
    @patch("catfish_todo_sync.subprocess.run")
    def test_sync_to_journal_uses_batch_first(
        self, mock_run: MagicMock, mock_find: MagicMock
    ) -> None:
        """v0.1.10: _sync_to_journal 默认走 batch, batch 成功不再走单调."""
        mock_find.return_value = "/fake/path"
        mock_run.return_value = MagicMock(
            returncode=0, stdout=b'{"add": 2}', stderr=b""
        )
        catfish_todo_sync._sync_to_journal([
            {"content": "X", "status": "pending"},
            {"content": "Y", "status": "pending"},
        ])
        # batch 成功 → 只调一次 (而非单调 2 次)
        assert mock_run.call_count == 1
        assert mock_run.call_args[0][0][1] == "sync"


class TestApplyPatch(unittest.TestCase):
    """_apply_patch 函数测试 (mock hermes 模块)."""

    def setUp(self) -> None:
        catfish_todo_sync._PATCHED = False

    def test_no_hermes_returns_false(self) -> None:
        """没装 hermes tools.todo_tool 时, _apply_patch 返 False, 不抛."""
        # tools.todo_tool 在 sandbox 里没装
        result = catfish_todo_sync._apply_patch()
        self.assertFalse(result)

    def test_double_patch_idempotent(self) -> None:
        """重复 _apply_patch 不应重复 patch (幂等)."""
        catfish_todo_sync._PATCHED = True  # 模拟已 patched
        result = catfish_todo_sync._apply_patch()
        self.assertFalse(result)  # 第二次调返 False


if __name__ == "__main__":
    unittest.main()
