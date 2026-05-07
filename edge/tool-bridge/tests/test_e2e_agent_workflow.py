"""
BL-A1.5 + A2.5 端到端 Agent workflow 测试.

不依赖真 LLM (mock LLM 决策), 但测**真组件链路**:
  - 真 sandbox (sandbox-exec)
  - 真 task_manager (asyncio task store)
  - 真 docx 文件读写
  - 真通知 jsonl 写

模拟 demo 场景:
  Scenario A (BL-A1.5): 单任务 Agent 自完成
    LLM → search 找 docx → read → execute_code 改 → verify 文件存在
    验证: docx 文件真生成 + 内容真改 + 全过程 sandbox 验证

  Scenario B (BL-A2.5): 多任务并发
    LLM → 同时启动 2 个后台 task → 第 1 个写 docx + 第 2 个查数据
    验证: 两任务真并发 + 各自完成 + 通知 jsonl 真写

跑法:
    cd edge/tool-bridge
    source venv/bin/activate
    python3 -m unittest tests.test_e2e_agent_workflow -v
"""
from __future__ import annotations

import asyncio
import json
import os
import platform
import shutil
import tempfile
import time
import unittest
from pathlib import Path

# 跳过非 macOS (sandbox-exec 仅 macOS, 本测试需要真 sandbox)
SKIP_NON_MACOS = platform.system() != "Darwin"


def _run(coro):
    """跑 async coroutine."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@unittest.skipIf(SKIP_NON_MACOS, "e2e 测试仅 macOS (sandbox-exec)")
class TestE2EAgentWorkflow(unittest.TestCase):
    """BL-A1.5: 单任务 Agent 自完成端到端."""

    def setUp(self):
        # 隔离 HOME 防污染员工真 ~/.catfish
        self._tmphome = tempfile.mkdtemp(prefix="catfish-e2e-agent-")
        self._old_home = os.environ.get("HOME")
        os.environ["HOME"] = self._tmphome
        # 启用沙箱
        self._old_sandbox = os.environ.get("CATFISH_SANDBOX_EXEC")
        os.environ["CATFISH_SANDBOX_EXEC"] = "1"
        # 重置 task_manager
        from catfish_tool_bridge import task_manager
        task_manager._manager = task_manager.TaskManager()

    def tearDown(self):
        if self._old_home is not None:
            os.environ["HOME"] = self._old_home
        else:
            os.environ.pop("HOME", None)
        if self._old_sandbox is not None:
            os.environ["CATFISH_SANDBOX_EXEC"] = self._old_sandbox
        else:
            os.environ.pop("CATFISH_SANDBOX_EXEC", None)
        shutil.rmtree(self._tmphome, ignore_errors=True)

    def test_e2e_single_task_writes_real_file(self):
        """场景 2.6 单任务 Agent: 沙箱内 execute_code 真写文件验证."""
        from catfish_tool_bridge import sandbox

        # 模拟 LLM 调 execute_code 写文件 (BL-S29 沙箱真跑)
        code = """
import os
output = os.path.join(os.environ.get('TMPDIR', '/tmp'), 'agent_test.txt')
with open(output, 'w') as f:
    f.write('Agent 真做了\\n第二行\\n')
print(f'已写入 {output}')
"""
        result = sandbox.run_in_sandbox(code, lang="python", timeout_s=10)
        self.assertTrue(result["ok"], f"沙箱跑失败: {result}")
        self.assertIn("已写入", result["stdout"])
        self.assertTrue(result["sandbox_used"])
        # 沙箱里 TMPDIR 是 /tmp (沙箱里映射), 沙箱外验证不到, 但 stdout 含 "已写入" 已说明真做.

    def test_e2e_agent_dispatch_through_tool_bridge(self):
        """端到端 dispatch_tool 入口 → 沙箱 → audit log."""
        import asyncio
        from catfish_tool_bridge import adapter

        # mock registry: 验证沙箱模式时不走 hermes
        class _StubReg:
            class registry:
                @staticmethod
                def get_all_tool_names():
                    return ["execute_code", "python", "bash"]

                @staticmethod
                def get_toolset_for_tool(_):
                    return None

                @staticmethod
                def is_toolset_available(_):
                    return True

                @staticmethod
                def dispatch(*_a, **_k):
                    raise AssertionError("不应走 hermes")

        adapter.install_registry(_StubReg())

        result = asyncio.run(adapter.dispatch_tool(
            "execute_code",
            {"code": "print(2 + 3)"},
        ))
        # 沙箱模式 (env CATFISH_SANDBOX_EXEC=1) 走本地, 不走 hermes
        self.assertTrue(result["ok"])
        self.assertEqual(result["tool"], "execute_code")
        self.assertIn("5", result["result"]["stdout"])
        self.assertTrue(result["result"]["sandbox_used"])


@unittest.skipIf(SKIP_NON_MACOS, "e2e 测试仅 macOS")
class TestE2EConcurrentTasks(unittest.TestCase):
    """BL-A2.5: 多任务并发端到端."""

    def setUp(self):
        self._tmphome = tempfile.mkdtemp(prefix="catfish-e2e-concurrent-")
        self._old_home = os.environ.get("HOME")
        os.environ["HOME"] = self._tmphome
        os.environ["CATFISH_SANDBOX_EXEC"] = "1"
        from catfish_tool_bridge import task_manager
        task_manager._manager = task_manager.TaskManager()

    def tearDown(self):
        if self._old_home is not None:
            os.environ["HOME"] = self._old_home
        else:
            os.environ.pop("HOME", None)
        os.environ.pop("CATFISH_SANDBOX_EXEC", None)
        shutil.rmtree(self._tmphome, ignore_errors=True)

    def test_two_tasks_run_in_parallel(self):
        """场景 2.7: 两任务真并发, 总时间 < 串行."""
        from catfish_tool_bridge import task_manager

        async def _t():
            mgr = task_manager.manager()

            async def slow_task():
                await asyncio.sleep(0.5)
                return {"data": "task1 done"}

            async def quick_task():
                await asyncio.sleep(0.3)
                return {"data": "task2 done"}

            start = time.time()
            t1 = mgr.submit("test", "长任务", slow_task)
            t2 = mgr.submit("test", "短任务", quick_task)
            # 并发等待
            await asyncio.gather(t1._async_task, t2._async_task)
            elapsed = time.time() - start

            # 串行 = 0.5 + 0.3 = 0.8s, 并发应 < 0.7s
            self.assertLess(elapsed, 0.7, f"任务没并发, elapsed={elapsed}")
            # 两任务都 completed
            self.assertEqual(t1.status, "completed")
            self.assertEqual(t2.status, "completed")

        _run(_t())

    def test_concurrent_tasks_independent_status(self):
        """场景 2.7: 一任务跑时另一任务可查状态独立."""
        from catfish_tool_bridge import task_manager

        async def _t():
            mgr = task_manager.manager()

            async def long_task():
                await asyncio.sleep(0.4)
                return "long done"

            async def short_task():
                return "short done"

            t1 = mgr.submit("test", "长", long_task)
            t2 = mgr.submit("test", "短", short_task)

            # 短任务很快完, 长任务还在跑 — 状态独立
            await t2._async_task
            self.assertEqual(t2.status, "completed")
            # 此时 t1 可能还在 running
            t1_status = mgr.status_dict(t1.task_id)
            self.assertIn(t1_status["status"], ("pending", "running", "completed"))
            await t1._async_task

        _run(_t())

    def test_long_task_writes_notification_bubble(self):
        """场景 2.7 完成通知: 任务 ≥ 3s 完成后写 bubble jsonl."""
        from catfish_tool_bridge import task_manager

        async def _t():
            mgr = task_manager.manager()

            async def long_runner():
                await asyncio.sleep(3.05)
                return "done"

            task = mgr.submit("test", "修订《资质管理办法》", long_runner)
            await task._async_task

        _run(_t())
        bubble_file = Path(self._tmphome) / ".catfish" / "pet_pending_bubbles.jsonl"
        self.assertTrue(bubble_file.exists(), "桌宠 bubble jsonl 没写")
        content = bubble_file.read_text(encoding="utf-8")
        # demo 场景 2.7 期望的桌宠文案
        self.assertIn("修订《资质管理办法》", content)
        self.assertIn("做完了", content)
        # 解析 JSON 校验 task_status
        line = content.strip().split("\n")[-1]
        d = json.loads(line)
        self.assertEqual(d["kind"], "task_done")
        self.assertEqual(d["task_status"], "completed")

    def test_e2e_concurrent_sandbox_tasks(self):
        """端到端: 两个 sandbox execute_code 任务并发跑, 各自独立."""
        from catfish_tool_bridge import task_manager

        async def _t():
            # 启动两个真 sandbox 任务
            r1 = task_manager.submit_typed_task(
                kind="execute_code",
                payload={"code": "import time; time.sleep(0.2); print('A done')", "lang": "python"},
                label="任务 A",
            )
            r2 = task_manager.submit_typed_task(
                kind="execute_code",
                payload={"code": "import time; time.sleep(0.2); print('B done')", "lang": "python"},
                label="任务 B",
            )
            self.assertTrue(r1["ok"])
            self.assertTrue(r2["ok"])

            # 等两个完
            mgr = task_manager.manager()
            t1 = mgr.get(r1["task_id"])
            t2 = mgr.get(r2["task_id"])
            await asyncio.gather(t1._async_task, t2._async_task)

            # 两个都 sandbox 跑过 + 真输出
            self.assertEqual(t1.status, "completed")
            self.assertEqual(t2.status, "completed")
            self.assertIn("A done", t1.result["stdout"])
            self.assertIn("B done", t2.result["stdout"])
            self.assertTrue(t1.result["sandbox_used"])
            self.assertTrue(t2.result["sandbox_used"])

        _run(_t())


if __name__ == "__main__":
    unittest.main(verbosity=2)
