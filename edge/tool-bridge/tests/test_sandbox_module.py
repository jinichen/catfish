"""
BL-S29.2 sandbox 模块单测.

验证:
- is_sandbox_supported() 在 macOS 返 True (有 sandbox-exec)
- is_sandbox_enabled() 跟 env 关联
- detect_lang_from_tool_name 映射正确
- run_in_sandbox: 沙箱内合法操作 OK / 越权操作 DENY (跟 sh 测试同步)
- adapter._do_dispatch wiring 正确 (env 启用时不走 hermes registry)

跑法: cd edge/tool-bridge && python -m pytest tests/test_sandbox_module.py -v
注: 仅 macOS 跑 (skip 非 macOS 平台).
"""
from __future__ import annotations

import asyncio
import os
import platform
import unittest
from unittest.mock import patch

# 不在 macOS 上整个跳, sandbox-exec 是 Apple 私有
SKIP_NON_MACOS = platform.system() != "Darwin"


@unittest.skipIf(SKIP_NON_MACOS, "sandbox-exec 仅 macOS")
class TestSandboxModule(unittest.TestCase):
    def test_is_sandbox_supported_on_macos(self):
        from catfish_tool_bridge import sandbox
        self.assertTrue(sandbox.is_sandbox_supported())

    def test_is_sandbox_enabled_via_env(self):
        from catfish_tool_bridge import sandbox
        with patch.dict(os.environ, {"CATFISH_SANDBOX_EXEC": "1"}, clear=False):
            self.assertTrue(sandbox.is_sandbox_enabled())
        with patch.dict(os.environ, {"CATFISH_SANDBOX_EXEC": "0"}, clear=False):
            self.assertFalse(sandbox.is_sandbox_enabled())
        # env 不存在时默认 False
        env_no = {k: v for k, v in os.environ.items() if k != "CATFISH_SANDBOX_EXEC"}
        with patch.dict(os.environ, env_no, clear=True):
            self.assertFalse(sandbox.is_sandbox_enabled())

    def test_detect_lang_from_tool_name(self):
        from catfish_tool_bridge import sandbox
        self.assertEqual(sandbox.detect_lang_from_tool_name("execute_code"), "python")
        self.assertEqual(sandbox.detect_lang_from_tool_name("python"), "python")
        self.assertEqual(sandbox.detect_lang_from_tool_name("PYTHON"), "python")
        self.assertEqual(sandbox.detect_lang_from_tool_name("bash"), "bash")
        self.assertEqual(sandbox.detect_lang_from_tool_name("shell_exec"), "sh")
        self.assertEqual(sandbox.detect_lang_from_tool_name("sh"), "sh")
        self.assertIsNone(sandbox.detect_lang_from_tool_name("read_file"))
        self.assertIsNone(sandbox.detect_lang_from_tool_name("browser_click"))

    def test_run_in_sandbox_legit_python(self):
        """沙箱内合法 python 计算 OK."""
        from catfish_tool_bridge import sandbox
        result = sandbox.run_in_sandbox(
            "import json,math; print(json.dumps({'pi': math.pi}))",
            lang="python",
            timeout_s=10,
        )
        self.assertTrue(result["ok"], f"沙箱内合法操作误伤: {result}")
        self.assertEqual(result["rc"], 0)
        self.assertIn("3.14", result["stdout"])
        self.assertTrue(result["sandbox_used"])
        self.assertEqual(result["sandbox_kind"], "sandbox-exec")
        self.assertFalse(result["timed_out"])

    def test_run_in_sandbox_blocks_etc_passwd_read(self):
        """沙箱拦读 /etc/passwd (file-read* deny 真生效).

        用 /etc/passwd 而不是 ~/.ssh/id_rsa, 因为前者一定存在,
        后者很多员工 mac 没创过 (ENOENT 会比 deny 检查更早返).
        profile 里 `(literal "/etc/passwd")` 显式 deny.
        """
        from catfish_tool_bridge import sandbox
        # 先确认 /etc/passwd 真存在 (沙箱外能看到)
        self.assertTrue(os.path.isfile("/etc/passwd"))
        result = sandbox.run_in_sandbox(
            "open('/etc/passwd','rb').read()",
            lang="python",
            timeout_s=10,
        )
        self.assertFalse(result["ok"], "沙箱漏了 /etc/passwd 读!")
        self.assertNotEqual(result["rc"], 0)
        # SBPL deny 在 python 层抛 PermissionError, errno 1 (EPERM)
        self.assertIn("Permission", result["stderr"] + result["stdout"])

    def test_run_in_sandbox_home_redirect(self):
        """验第一层防御: 沙箱内 HOME 被重定向到 TASK_DIR, ~/ 看不到员工真文件."""
        from catfish_tool_bridge import sandbox
        result = sandbox.run_in_sandbox(
            "import os; print(os.environ['HOME']); print(os.path.expanduser('~'))",
            lang="python",
            timeout_s=10,
        )
        self.assertTrue(result["ok"], f"沙箱跑失败: {result}")
        # 沙箱内 HOME 应该是 /var/folders/... (TASK_DIR), 不是 /Users/<name>/
        self.assertIn("catfish-sandbox-", result["stdout"])
        self.assertNotIn(os.path.expanduser("~"), result["stdout"])

    def test_run_in_sandbox_blocks_network(self):
        """沙箱拦网络外联 (urllib)."""
        from catfish_tool_bridge import sandbox
        result = sandbox.run_in_sandbox(
            "import urllib.request; urllib.request.urlopen('http://1.2.3.4', timeout=2)",
            lang="python",
            timeout_s=10,
        )
        self.assertFalse(result["ok"], "沙箱漏了网络外联!")
        self.assertNotEqual(result["rc"], 0)

    def test_run_in_sandbox_blocks_etc_hosts_write(self):
        """沙箱拦写 /etc/hosts."""
        from catfish_tool_bridge import sandbox
        result = sandbox.run_in_sandbox(
            "open('/etc/hosts','a').write('1.2.3.4 evil\\n')",
            lang="python",
            timeout_s=10,
        )
        self.assertFalse(result["ok"], "沙箱漏了 /etc/hosts 写!")
        self.assertNotEqual(result["rc"], 0)

    def test_run_in_sandbox_timeout(self):
        """超时被 SIGKILL, timed_out=True."""
        from catfish_tool_bridge import sandbox
        result = sandbox.run_in_sandbox(
            "import time; time.sleep(60)",
            lang="python",
            timeout_s=2,  # 2 秒超时, 远小于 sleep 60
        )
        self.assertTrue(result["timed_out"])
        self.assertFalse(result["ok"])

    def test_run_in_sandbox_clean_env(self):
        """沙箱内 env 干净, 看不到 GITHUB_TOKEN 之类员工 mac secret."""
        from catfish_tool_bridge import sandbox
        with patch.dict(os.environ, {"GITHUB_TOKEN": "ghp_secret_xxx"}, clear=False):
            result = sandbox.run_in_sandbox(
                "import os; print(os.environ.get('GITHUB_TOKEN', 'NOT_SET'))",
                lang="python",
                timeout_s=10,
            )
        self.assertTrue(result["ok"], f"出错: {result}")
        self.assertIn("NOT_SET", result["stdout"])
        self.assertNotIn("ghp_secret_xxx", result["stdout"])

    def test_run_in_sandbox_unsupported_lang(self):
        from catfish_tool_bridge import sandbox
        result = sandbox.run_in_sandbox("dummy", lang="ruby")
        self.assertFalse(result["ok"])
        self.assertEqual(result["rc"], -1)
        self.assertIn("不支持", result["stderr"])


@unittest.skipIf(SKIP_NON_MACOS, "BL-S29.5 三层 fallback 测试跑在 macOS")
class TestSandboxFallbackChain(unittest.TestCase):
    """BL-S29.5: 验证 detect_sandbox_kind 三层 fallback 顺序.

    macOS: sandbox-exec → docker → None
    Linux: nsjail → docker → None
    """

    def test_macos_prefers_sandbox_exec(self):
        from catfish_tool_bridge import sandbox
        # macOS 上 sandbox-exec 一定在, 应该返这个 (不会走 docker fallback)
        self.assertEqual(sandbox.detect_sandbox_kind(), "sandbox-exec")

    def test_docker_detection_doesnt_block_when_unavailable(self):
        """_is_docker_available 即使 docker 不在 daemon 没起也快速返 False, 不抛."""
        from catfish_tool_bridge import sandbox
        # 这只是验函数能跑不抛 — 真值看 caller 环境
        result = sandbox._is_docker_available()
        self.assertIsInstance(result, bool)

    def test_fallback_when_no_sandbox_returns_friendly_error(self):
        """模拟"啥都没有"环境, run_in_sandbox 应该友好报错, 不抛."""
        from catfish_tool_bridge import sandbox

        with patch("catfish_tool_bridge.sandbox.detect_sandbox_kind",
                   return_value=None):
            result = sandbox.run_in_sandbox("print(1)", lang="python")
        self.assertFalse(result["ok"])
        self.assertFalse(result["sandbox_used"])
        self.assertIsNone(result["sandbox_kind"])
        self.assertIn("沙箱不可用", result["stderr"])


@unittest.skipIf(SKIP_NON_MACOS, "sandbox-exec 仅 macOS")
class TestAdapterSandboxWiring(unittest.TestCase):
    """验证 adapter._do_dispatch 拦截 execute_code 走沙箱, 不去 hermes registry."""

    def setUp(self):
        # 给 install_registry 一个 mock 的 registry, 防止 _do_dispatch 调 _r()
        # 走 hermes 路径 (我们要验的就是它**不走**)
        from catfish_tool_bridge import adapter

        class _MockRegistryModule:
            class registry:
                @staticmethod
                def get_all_tool_names():
                    # read_file 加进去, 让"非沙箱工具走 hermes" 测试能命中 dispatch
                    # (不在列表里会被 _do_dispatch 提前 "unknown tool" 拦)
                    return ["execute_code", "python", "bash", "read_file"]

                @staticmethod
                def get_toolset_for_tool(_):
                    return None

                @staticmethod
                def is_toolset_available(_):
                    return True

                @staticmethod
                def dispatch(name, args):
                    raise AssertionError(
                        f"沙箱启用时, hermes registry.dispatch 不应被调到! "
                        f"调到了说明拦截路径有 bug. name={name}"
                    )

        adapter.install_registry(_MockRegistryModule())

    def test_dispatch_execute_code_routes_to_sandbox_when_enabled(self):
        """env CATFISH_SANDBOX_EXEC=1 时 execute_code 走沙箱, 不走 hermes."""
        from catfish_tool_bridge import adapter

        with patch.dict(os.environ, {"CATFISH_SANDBOX_EXEC": "1"}, clear=False):
            result = asyncio.run(
                adapter._do_dispatch("execute_code", {"code": "print(2 + 3)"})
            )
        self.assertTrue(result["ok"], f"沙箱跑合法代码失败: {result}")
        self.assertEqual(result["tool"], "execute_code")
        self.assertIn("5", result["result"]["stdout"])
        self.assertTrue(result["result"]["sandbox_used"])

    def test_dispatch_python_routes_to_sandbox_when_enabled(self):
        """python 工具同样走沙箱."""
        from catfish_tool_bridge import adapter

        with patch.dict(os.environ, {"CATFISH_SANDBOX_EXEC": "1"}, clear=False):
            result = asyncio.run(
                adapter._do_dispatch("python", {"code": "print('hello-from-bls29')"})
            )
        self.assertTrue(result["ok"])
        self.assertIn("hello-from-bls29", result["result"]["stdout"])

    def test_dispatch_execute_code_missing_code_field(self):
        """沙箱模式但 args 没 code 字段 → 错误返回, 不调 sandbox-exec."""
        from catfish_tool_bridge import adapter

        with patch.dict(os.environ, {"CATFISH_SANDBOX_EXEC": "1"}, clear=False):
            result = asyncio.run(adapter._do_dispatch("execute_code", {}))
        self.assertFalse(result["ok"])
        self.assertIn("未提供", result["error"])

    def test_dispatch_non_code_tool_unaffected(self):
        """不在 detect_lang 范围的工具不走沙箱 (会走 hermes registry).

        mock registry.dispatch 设计为抛 AssertionError, _do_dispatch 会 catch
        并返 {"ok": False, "error": "AssertionError: ..."}. 看到这个 error 就证明
        请求没走沙箱拦截分支, 走了原 hermes 路径.
        """
        from catfish_tool_bridge import adapter

        with patch.dict(os.environ, {"CATFISH_SANDBOX_EXEC": "1"}, clear=False):
            result = asyncio.run(
                adapter._do_dispatch("read_file", {"path": "/tmp/x"})
            )
        self.assertFalse(result["ok"])
        self.assertIn("AssertionError", result.get("error", ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)


# ─── BL-SANDBOX-PPTX (5/15): _resolve_python_executable ──────────


class TestResolvePythonExecutable(unittest.TestCase):
    """python 解释器选择策略 — 5/15 鸿波撞 PPT 退化, 真因 sandbox.py 写死
    /usr/bin/python3. 改成默认 sys.executable (= tool-bridge venv), 让 venv
    装的 python-pptx / pandas / matplotlib 等 LLM execute_code 直接可用.
    """

    def test_default_is_sys_executable(self):
        """没设 CATFISH_SANDBOX_PYTHON 时, 默认用 sys.executable"""
        import sys
        from catfish_tool_bridge import sandbox
        env_no = {
            k: v for k, v in os.environ.items()
            if k != "CATFISH_SANDBOX_PYTHON"
        }
        with patch.dict(os.environ, env_no, clear=True):
            result = sandbox._resolve_python_executable()
            self.assertEqual(result, sys.executable)

    def test_env_override(self):
        """CATFISH_SANDBOX_PYTHON 显式覆盖时, 用 env (IT 部署定制场景)"""
        from catfish_tool_bridge import sandbox
        # 用一个真实存在的 path (sys.executable 自己就行)
        import sys
        with patch.dict(
            os.environ,
            {"CATFISH_SANDBOX_PYTHON": sys.executable},
            clear=False,
        ):
            self.assertEqual(
                sandbox._resolve_python_executable(),
                sys.executable,
            )

    def test_env_override_nonexistent_falls_back(self):
        """env 指了一个不存在的 path → fallback sys.executable + warn"""
        import sys
        from catfish_tool_bridge import sandbox
        with patch.dict(
            os.environ,
            {"CATFISH_SANDBOX_PYTHON": "/nonexistent/python_definitely_not_here"},
            clear=False,
        ):
            result = sandbox._resolve_python_executable()
            # fallback 到 sys.executable
            self.assertEqual(result, sys.executable)

    def test_falls_back_to_usr_bin_if_no_sys_executable(self):
        """极端: sys.executable 也没 — fallback /usr/bin/python3"""
        from catfish_tool_bridge import sandbox
        env_no = {
            k: v for k, v in os.environ.items()
            if k != "CATFISH_SANDBOX_PYTHON"
        }
        with patch.dict(os.environ, env_no, clear=True):
            with patch("sys.executable", ""):
                result = sandbox._resolve_python_executable()
                self.assertEqual(result, "/usr/bin/python3")


if __name__ == "__main__":
    unittest.main()
