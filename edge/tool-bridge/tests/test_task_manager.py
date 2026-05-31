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

    # BL-LONG-RUNNING-V1-FIX (5/31): tool-bridge sync call_tool 路径
    # 老代码 asyncio.create_task 在 sync caller 抛 RuntimeError → 标 failed.
    # 修复: detect 后 spawn daemon thread 跑 asyncio.run(). 这两个测试覆盖
    # 真实生产路径 (LLM → tool-bridge HTTP → sync call_tool → submit).
    def test_submit_from_sync_caller_spawns_thread(self):
        """sync caller 调 submit (无 running loop), task 应正常跑完, 不再 failed."""
        mgr = task_manager.manager()

        result_box = {}

        async def runner():
            await asyncio.sleep(0.05)
            result_box["v"] = "from-thread"
            return result_box["v"]

        # 直接 sync 调, 不在 async _run() 里
        task = mgr.submit("execute_code", "sync-caller", runner)

        # 立即查 — 不应该是 failed (我们的修复关键点)
        self.assertNotEqual(
            task.status, "failed",
            f"sync caller 不应直接失败, error={task.error}",
        )
        # 应该是 pending 或 running (asyncio.run 在另一线程内启)
        self.assertIn(task.status, ("pending", "running", "completed"))

        # 等 thread 跑完 (轮询, 不依赖 _async_task — sync 路径 _async_task=None)
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if task.status in ("completed", "failed"):
                break
            time.sleep(0.05)

        self.assertEqual(task.status, "completed", f"task error={task.error}")
        self.assertEqual(task.result, "from-thread")

    def test_submit_typed_task_from_sync_caller(self):
        """submit_typed_task (LLM 调 catfish_run_task 的真实入口) 应在 sync 也 work."""
        # 这跟 catfish_tools.py:call_tool sync 入口路径一致
        result = task_manager.submit_typed_task(
            kind="execute_code",
            payload={"code": "print('hello sync')", "lang": "python", "timeout_s": 10},
            label="sync-execute-test",
        )
        self.assertTrue(
            result["ok"],
            f"sync 入口不应失败, error={result.get('error')}",
        )
        self.assertTrue(result["task_id"].startswith("task_"))
        # status 此刻可能 pending/running, 等它完
        task_id = result["task_id"]
        mgr = task_manager.manager()
        deadline = time.time() + 5.0
        while time.time() < deadline:
            t = mgr.get(task_id)
            if t and t.status in ("completed", "failed"):
                break
            time.sleep(0.05)
        t = mgr.get(task_id)
        self.assertIsNotNone(t)
        self.assertEqual(
            t.status, "completed",
            f"sync-spawned task 应完成, error={t.error}",
        )


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

    def test_short_task_writes_bubble_but_no_macos_notify(self):
        """BL-E27.4 (5/8) 重写: 短任务也写桌宠 bubble (主通道), 但不发 macOS 通知.

        行为变化原因: 鸿波 5/8 凌晨拍板 — 桌宠状态着色是主通道, macOS 通知降级到辅
        (失败 / ≥30s 长任务才发系统通知). 桌宠通道全发, 颜色聚合多个通知到一个 dot.
        """
        async def _t():
            mgr = task_manager.manager()

            async def runner():
                return "fast"

            task = mgr.submit("test_short", "短任务", runner)
            await task._async_task

        _run(_t())
        # 桌宠 bubble: 即使短任务也写 (主通道)
        bubble_file = Path(self._tmphome) / ".catfish" / "pet_pending_bubbles.jsonl"
        self.assertTrue(bubble_file.exists(), "短任务也该写桌宠 bubble (BL-E27.4)")

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
        """env CATFISH_TASK_NOTIFY=0 关 macOS 通知通道, 但桌宠 bubble (主通道) 仍发.

        BL-E27.4 (5/8) 重写: env 只控 macOS 通知, 桌宠由 CATFISH_PET_BUBBLE 控.
        想全关请同时设两个 env, 或改用 BL-E15 专注模式 (临时屏蔽).
        """
        old = os.environ.get("CATFISH_TASK_NOTIFY")
        os.environ["CATFISH_TASK_NOTIFY"] = "0"
        try:
            async def _t():
                mgr = task_manager.manager()

                async def runner():
                    await asyncio.sleep(3.1)
                    return "x"

                task = mgr.submit("real_kind", "real label", runner)
                await task._async_task

            _run(_t())
            bubble_file = Path(self._tmphome) / ".catfish" / "pet_pending_bubbles.jsonl"
            # bubble 仍写 (主通道)
            self.assertTrue(bubble_file.exists(), "BL-E27.4: env 只关 macOS, bubble 主通道仍发")
        finally:
            if old is None:
                os.environ.pop("CATFISH_TASK_NOTIFY", None)
            else:
                os.environ["CATFISH_TASK_NOTIFY"] = old


# ============================================================
# BL-E27.4 (5/8) 通知收紧规则单测
# ============================================================


class TestBLE274NotifyRules(unittest.TestCase):
    """直接测 _should_send_macos_notify / _is_test_task 函数级行为."""

    def setUp(self):
        # 每个 test 重置全局 dedup state
        task_manager._RECENT_NOTIFY_BY_LABEL.clear()

    @staticmethod
    def _mk_task(kind: str, label: str | None, status: str, error: str | None = None):
        """造一个 Task 对象 (跳 mgr.submit, 直接构造测 _notify_task_done 调路径)."""
        t = task_manager.Task(
            task_id="test-id",
            kind=kind,
            label=label,
            status=status,
        )
        t.started_at = 0.0
        t.finished_at = 0.0
        t.error = error
        return t

    def test_is_test_task_recognizes_kind_test(self):
        cases_yes = ["test_short", "_test_long", "test", "fake_test_kind"]
        for k in cases_yes:
            t = self._mk_task(k, "label", "completed")
            self.assertTrue(task_manager._is_test_task(t), f"kind={k} 应判测试")

    def test_is_test_task_recognizes_label_test(self):
        cases_yes = ["测试任务", "Test something", "test foobar"]
        for label in cases_yes:
            t = self._mk_task("real_kind", label, "completed")
            self.assertTrue(task_manager._is_test_task(t), f"label={label!r} 应判测试")

    def test_is_test_task_real_work_passes(self):
        cases_no = [
            ("write_docx", "周报"),
            ("parse_pdf", "解析合同"),
            ("modify", "修订《资质管理办法》"),
        ]
        for kind, label in cases_no:
            t = self._mk_task(kind, label, "completed")
            self.assertFalse(task_manager._is_test_task(t), f"{kind}/{label} 不该判测试")

    def test_test_task_blocks_macos_notify_even_for_failure(self):
        """测试任务 + failed → 仍不发 macOS (避免 demo 反复跑刷屏)"""
        t = self._mk_task("test_long", "长任务测试", "failed", error="boom")
        self.assertFalse(task_manager._should_send_macos_notify(t, elapsed=10.0))

    def test_real_failed_task_always_notifies(self):
        """真业务 failed → 始终发 macOS"""
        t = self._mk_task("write_docx", "周报", "failed", error="模板找不到")
        self.assertTrue(task_manager._should_send_macos_notify(t, elapsed=5.0))

    def test_real_success_short_no_macos_notify(self):
        """真业务 completed 但 < 30s → 不发 macOS (桌宠 bubble 通道走)"""
        t = self._mk_task("write_docx", "周报", "completed")
        self.assertFalse(task_manager._should_send_macos_notify(t, elapsed=10.0))

    def test_real_success_long_macos_notify(self):
        """真业务 completed ≥ 30s → 发 macOS"""
        t = self._mk_task("modify", "修订办法", "completed")
        self.assertTrue(task_manager._should_send_macos_notify(t, elapsed=35.0))

    def test_dedup_same_label_within_1h(self):
        """同 label 1h 内 → 第二次失败也不发 macOS"""
        t1 = self._mk_task("write_docx", "周报", "failed", error="x")
        self.assertTrue(task_manager._should_send_macos_notify(t1, elapsed=5.0))
        # 再来同 label
        t2 = self._mk_task("write_docx", "周报", "failed", error="y")
        self.assertFalse(task_manager._should_send_macos_notify(t2, elapsed=5.0),
                         "同 label 1h 内不重复发")

    def test_dedup_different_label_independent(self):
        """不同 label 互不影响"""
        t1 = self._mk_task("write_docx", "周报", "failed", error="x")
        t2 = self._mk_task("write_docx", "立项材料", "failed", error="y")
        self.assertTrue(task_manager._should_send_macos_notify(t1, elapsed=5.0))
        self.assertTrue(task_manager._should_send_macos_notify(t2, elapsed=5.0))

    def test_dedup_window_resets_after_1h(self):
        """超 1h 后同 label 又能发"""
        import time as _time
        t1 = self._mk_task("write_docx", "周报", "failed", error="x")
        self.assertTrue(task_manager._should_send_macos_notify(t1, elapsed=5.0))
        # 模拟 1h+ 过去 — 直接改 _RECENT_NOTIFY_BY_LABEL
        task_manager._RECENT_NOTIFY_BY_LABEL["周报"] = _time.time() - 3700
        t2 = self._mk_task("write_docx", "周报", "failed", error="y")
        self.assertTrue(task_manager._should_send_macos_notify(t2, elapsed=5.0))


class JsonlPersistenceTests(unittest.TestCase):
    """BL-HERMES013-RED-2 (5/13): tasks.jsonl 持久化给 catfish-web Kanban 用."""

    def setUp(self):
        # 隔离 CATFISH_HOME 防污染真用户的 ~/.catfish/tasks.jsonl
        self._tmp = Path(f"/tmp/catfish-test-jsonl-{os.getpid()}-{time.time_ns()}")
        self._tmp.mkdir(parents=True, exist_ok=True)
        self._old_home = os.environ.get("CATFISH_HOME")
        os.environ["CATFISH_HOME"] = str(self._tmp)

    def tearDown(self):
        # 还原 CATFISH_HOME + 清 tmp
        if self._old_home is None:
            os.environ.pop("CATFISH_HOME", None)
        else:
            os.environ["CATFISH_HOME"] = self._old_home
        try:
            for f in self._tmp.glob("*"):
                f.unlink()
            self._tmp.rmdir()
        except OSError:
            pass

    def _mk_task(self, **kw):
        defaults = dict(
            task_id="task_test1", kind="execute_code", label="测试", status="completed",
            started_at=time.time() - 10, finished_at=time.time(), result=None, error=None,
        )
        defaults.update(kw)
        return task_manager.Task(**defaults)

    def test_persist_completed_task_writes_one_line(self):
        t = self._mk_task(result={"stdout": "hi", "returncode": 0})
        task_manager._persist_task_to_jsonl(t)
        path = task_manager._tasks_jsonl_path()
        self.assertTrue(path.exists(), f"{path} 应被创建")
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 1)
        rec = json.loads(lines[0])
        self.assertEqual(rec["task_id"], "task_test1")
        self.assertEqual(rec["status"], "completed")
        self.assertIn("rc=0", rec["result_preview"])
        self.assertIn("hi", rec["result_preview"])

    def test_persist_failed_task_records_error(self):
        t = self._mk_task(status="failed", error="OSError: boom")
        task_manager._persist_task_to_jsonl(t)
        rows = task_manager.read_tasks_from_jsonl(hours_back=None)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "failed")
        self.assertEqual(rows[0]["error"], "OSError: boom")

    def test_read_filters_by_hours_back(self):
        """超 hours_back 的老任务应被过滤掉."""
        t_new = self._mk_task(task_id="task_new1")
        t_old = self._mk_task(task_id="task_old1", started_at=time.time() - 100 * 3600)
        task_manager._persist_task_to_jsonl(t_new)
        task_manager._persist_task_to_jsonl(t_old)
        rows = task_manager.read_tasks_from_jsonl(hours_back=24)
        ids = [r["task_id"] for r in rows]
        self.assertIn("task_new1", ids)
        self.assertNotIn("task_old1", ids)

    def test_read_returns_newest_first(self):
        """倒序 — 最新在前."""
        for i, ts in enumerate([100, 200, 300]):
            task_manager._persist_task_to_jsonl(
                self._mk_task(task_id=f"task_{i}", started_at=time.time() - ts)
            )
        rows = task_manager.read_tasks_from_jsonl(hours_back=None)
        # task_2 (started_at=time-300s) 写在最后, read 倒序后应该在第一
        self.assertEqual(rows[0]["task_id"], "task_2")
        self.assertEqual(rows[-1]["task_id"], "task_0")

    def test_read_handles_missing_file(self):
        """文件不存在不挂."""
        # tearDown 会删, 这里手动验"还没创建" 状态
        path = task_manager._tasks_jsonl_path()
        if path.exists():
            path.unlink()
        rows = task_manager.read_tasks_from_jsonl()
        self.assertEqual(rows, [])

    def test_read_skips_corrupt_lines(self):
        """坏行跳过, 不致命整个 read 挂."""
        path = task_manager._tasks_jsonl_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            '{"task_id":"task_ok1","status":"completed","started_at":' + str(time.time()) + '}\n'
            'not valid json at all\n'
            '{"task_id":"task_ok2","status":"completed","started_at":' + str(time.time()) + '}\n',
            encoding="utf-8",
        )
        rows = task_manager.read_tasks_from_jsonl()
        ids = [r["task_id"] for r in rows]
        self.assertEqual(set(ids), {"task_ok1", "task_ok2"})

    def test_persist_string_result(self):
        """result 是字符串也能 preview."""
        t = self._mk_task(result="纯文字结果 " + "x" * 500)
        task_manager._persist_task_to_jsonl(t)
        rec = task_manager.read_tasks_from_jsonl()[0]
        self.assertTrue(rec["result_preview"].startswith("纯文字结果"))
        self.assertLessEqual(len(rec["result_preview"]), 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
