"""
BL-A2.1 task_manager 单测.

跑法:
    cd edge/tool-bridge
    source venv/bin/activate
    python3 -m unittest tests.test_task_manager -v
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import unittest
from pathlib import Path

from catfish_tool_bridge import task_manager


def _run(coro):
    """跑 async coroutine 在测试里, 用新 event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestTaskManager(unittest.TestCase):
    """BL-A2.1: in-memory task store + asyncio runner."""

    def setUp(self):
        # 每个测试用新 manager 防互相污染
        task_manager._manager = task_manager.TaskManager()

    def test_submit_returns_task_id(self):
        """submit 立即返 task_id, 不等任务."""
        async def _t():
            mgr = task_manager.manager()

            async def runner():
                return "done"

            task = mgr.submit("test_kind", "测试任务", runner)
            self.assertTrue(task.task_id.startswith("task_"))
            self.assertEqual(task.kind, "test_kind")
            self.assertEqual(task.label, "测试任务")
            # 立即查应该是 pending 或 running
            self.assertIn(task.status, ("pending", "running"))
            # 等任务完成
            await task._async_task
            self.assertEqual(task.status, "completed")
            self.assertEqual(task.result, "done")

        _run(_t())

    def test_simple_task_completes(self):
        """简单任务跑完 status=completed + result."""
        async def _t():
            mgr = task_manager.manager()

            async def runner():
                await asyncio.sleep(0.01)
                return {"data": [1, 2, 3]}

            task = mgr.submit("test", "hi", runner)
            await task._async_task
            self.assertEqual(task.status, "completed")
            self.assertEqual(task.result, {"data": [1, 2, 3]})
            self.assertIsNone(task.error)

        _run(_t())

    def test_failing_task_marked_failed(self):
        """task 抛异常 status=failed + error."""
        async def _t():
            mgr = task_manager.manager()

            async def runner():
                raise ValueError("boom")

            task = mgr.submit("test", "x", runner)
            await task._async_task
            self.assertEqual(task.status, "failed")
            self.assertIn("ValueError", task.error or "")
            self.assertIn("boom", task.error or "")

        _run(_t())

    def test_unknown_task_id_returns_not_found(self):
        """查不存在的 task_id → not_found."""
        mgr = task_manager.TaskManager()
        d = mgr.status_dict("task_doesnotexist")
        self.assertEqual(d["status"], "not_found")

    def test_status_dict_excludes_internal_async_task(self):
        """status_dict 不能 leak _async_task 给 LLM (那是 internal asyncio.Task obj)."""
        async def _t():
            mgr = task_manager.manager()

            async def runner():
                return "x"

            task = mgr.submit("test", "x", runner)
            d = mgr.status_dict(task.task_id)
            self.assertNotIn("_async_task", d)
            await task._async_task

        _run(_t())

    def test_result_dict_includes_result_when_completed(self):
        """result_dict 完成后含 result 字段."""
        async def _t():
            mgr = task_manager.manager()

            async def runner():
                return "RESULT_VALUE"

            task = mgr.submit("test", "x", runner)
            await task._async_task
            d = mgr.result_dict(task.task_id)
            self.assertEqual(d["status"], "completed")
            self.assertEqual(d["result"], "RESULT_VALUE")

        _run(_t())

    def test_result_dict_includes_error_when_failed(self):
        """failed task result_dict 含 error."""
        async def _t():
            mgr = task_manager.manager()

            async def runner():
                raise RuntimeError("crash")

            task = mgr.submit("test", "x", runner)
            await task._async_task
            d = mgr.result_dict(task.task_id)
            self.assertEqual(d["status"], "failed")
            self.assertIn("crash", d["error"])

        _run(_t())

    def test_concurrent_tasks_run_in_parallel(self):
        """10 个任务并发跑, 总时间应远小于 串行."""
        async def _t():
            mgr = task_manager.manager()

            async def runner():
                await asyncio.sleep(0.05)
                return "ok"

            start = time.time()
            tasks = [mgr.submit("test", f"t{i}", runner) for i in range(10)]
            for t in tasks:
                await t._async_task
            elapsed = time.time() - start
            # 串行 = 10 * 0.05 = 0.5s, 并发应 < 0.2s
            self.assertLess(elapsed, 0.3, f"任务没并发, elapsed={elapsed}")
            for t in tasks:
                self.assertEqual(t.status, "completed")

        _run(_t())

    def test_truncate_huge_string_result(self):
        """超长 string result 截断防 LLM 拿到天量."""
        big = "X" * (task_manager._MAX_RESULT_CHARS + 1000)
        truncated = task_manager.TaskManager._truncate_result(big)
        self.assertLess(len(truncated), len(big))
        self.assertIn("截断", truncated)

    def test_truncate_dict_with_huge_stdout(self):
        """dict result 里 stdout/stderr 超长也截断."""
        big = "Y" * (task_manager._MAX_RESULT_CHARS + 500)
        result = {"ok": True, "stdout": big, "stderr": "short"}
        truncated = task_manager.TaskManager._truncate_result(result)
        self.assertTrue(truncated["ok"])
        self.assertLess(len(truncated["stdout"]), len(big))
        self.assertEqual(truncated["stderr"], "short")  # 短的不动

    def test_submit_typed_task_unknown_kind(self):
        """unknown kind 返 ok=False."""
        async def _t():
            result = task_manager.submit_typed_task(
                kind="not_a_real_kind",
                payload={},
                label="x",
            )
            self.assertFalse(result["ok"])
            self.assertIn("未知", result["error"])

        _run(_t())

    def test_list_active_returns_all_tasks(self):
        """list_active 返当前 manager 里所有任务."""
        async def _t():
            mgr = task_manager.manager()

            async def runner():
                await asyncio.sleep(0.01)
                return "x"

            t1 = mgr.submit("a", "task1", runner)
            t2 = mgr.submit("b", "task2", runner)
            actives = mgr.list_active()
            self.assertEqual(len(actives), 2)
            ids = {a["task_id"] for a in actives}
            self.assertEqual(ids, {t1.task_id, t2.task_id})
            await t1._async_task
            await t2._async_task

        _run(_t())


class TestTaskNotification(unittest.TestCase):
    """BL-A2.3: 任务完成通知 (macOS + 桌宠 bubble)."""

    def setUp(self):
        task_manager._manager = task_manager.TaskManager()
        # 测试用临时 HOME 防污染员工真 ~/.catfish
        import tempfile
        self._tmphome = tempfile.mkdtemp(prefix="catfish-task-notify-test-")
        self._old_home = os.environ.get("HOME")
        os.environ["HOME"] = self._tmphome

    def tearDown(self):
        import shutil
        if self._old_home is not None:
            os.environ["HOME"] = self._old_home
        else:
            os.environ.pop("HOME", None)
        shutil.rmtree(self._tmphome, ignore_errors=True)

    def test_short_task_no_notification(self):
        """任务 < 3 秒不通知 (员工还在等, 不打扰)."""
        async def _t():
            mgr = task_manager.manager()

            async def runner():
                return "fast"

            task = mgr.submit("test", "短任务", runner)
            await task._async_task

        _run(_t())
        # bubble jsonl 应该没创建 (fast task 不通知)
        bubble_file = Path(self._tmphome) / ".catfish" / "pet_pending_bubbles.jsonl"
        self.assertFalse(bubble_file.exists(), "短任务不该写 bubble")

    def test_long_task_writes_bubble(self):
        """任务 >= 3 秒 + completed 写 bubble."""
        async def _t():
            mgr = task_manager.manager()

            async def runner():
                await asyncio.sleep(3.1)
                return "done"

            task = mgr.submit("test", "长任务测试", runner)
            await task._async_task

        _run(_t())
        bubble_file = Path(self._tmphome) / ".catfish" / "pet_pending_bubbles.jsonl"
        self.assertTrue(bubble_file.exists())
        content = bubble_file.read_text(encoding="utf-8")
        # bubble 含任务 label + "做完了"
        self.assertIn("长任务测试", content)
        self.assertIn("做完了", content)
        # JSON 格式正确
        line = content.strip().split("\n")[0]
        d = json.loads(line)
        self.assertEqual(d["kind"], "task_done")
        self.assertEqual(d["task_status"], "completed")

    def test_failed_task_writes_failure_bubble(self):
        """failed task 也通知, 文案区分."""
        async def _t():
            mgr = task_manager.manager()

            async def runner():
                await asyncio.sleep(3.1)
                raise RuntimeError("boom")

            task = mgr.submit("test", "失败任务", runner)
            await task._async_task

        _run(_t())
        bubble_file = Path(self._tmphome) / ".catfish" / "pet_pending_bubbles.jsonl"
        self.assertTrue(bubble_file.exists())
        content = bubble_file.read_text(encoding="utf-8")
        self.assertIn("失败任务", content)
        self.assertIn("没做成", content)

    def test_notify_disabled_via_env(self):
        """env CATFISH_TASK_NOTIFY=0 整个通道关."""
        old = os.environ.get("CATFISH_TASK_NOTIFY")
        os.environ["CATFISH_TASK_NOTIFY"] = "0"
        try:
            async def _t():
                mgr = task_manager.manager()

                async def runner():
                    await asyncio.sleep(3.1)
                    return "x"

                task = mgr.submit("test", "test", runner)
                await task._async_task

            _run(_t())
            bubble_file = Path(self._tmphome) / ".catfish" / "pet_pending_bubbles.jsonl"
            self.assertFalse(bubble_file.exists())
        finally:
            if old is None:
                os.environ.pop("CATFISH_TASK_NOTIFY", None)
            else:
                os.environ["CATFISH_TASK_NOTIFY"] = old


if __name__ == "__main__":
    unittest.main(verbosity=2)
