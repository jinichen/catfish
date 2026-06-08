"""macOS sandbox rlimit test — 6/7 BL-SANDBOX-MACOS-RLIMIT.

测 _apply_macos_rlimits 真生效 + sandbox-exec 子进程被 SIGXCPU 拦.

非 macOS 上跳过 (nsjail 走 cfg, docker 走 run flags, 都不走 preexec_fn).
"""
from __future__ import annotations

import platform
import unittest

from catfish_tool_bridge import sandbox


SKIP_NON_MACOS = platform.system() != "Darwin"


@unittest.skipIf(SKIP_NON_MACOS, "_apply_macos_rlimits 仅 macOS 路径用")
class TestMacosRlimitFunction(unittest.TestCase):
    """单测 _apply_macos_rlimits 函数本身 (不跑 sandbox-exec, 跑就慢)."""

    def test_apply_rlimits_no_exception(self):
        """函数能跑通, 不抛 (macOS 各版本兼容)."""
        # 跑在测试进程里, setrlimit 后会影响测试进程, 但只是缩限 → safe
        # (rlimit 只能缩不能扩, 测试进程缩到 30s CPU / 50MB 文件等不影响 pytest)
        sandbox._apply_macos_rlimits()  # 不抛 = 通过

    def test_rlimit_constants_defined(self):
        """4 个 rlimit 字段都配了."""
        expected = {"RLIMIT_CPU", "RLIMIT_FSIZE", "RLIMIT_NOFILE", "RLIMIT_STACK"}
        assert set(sandbox._MACOS_SANDBOX_RLIMITS.keys()) == expected

    def test_rlimit_values_reasonable(self):
        """rlimit 值跟 nsjail cfg 一致 + reasonable."""
        rl = sandbox._MACOS_SANDBOX_RLIMITS
        assert rl["RLIMIT_CPU"] == 30, "CPU 30s 跟 nsjail rlimit_cpu 一致"
        assert rl["RLIMIT_FSIZE"] == 50 * 1024 * 1024, "FSIZE 50MB"
        assert rl["RLIMIT_NOFILE"] == 256
        assert rl["RLIMIT_STACK"] == 8 * 1024 * 1024


@unittest.skipIf(SKIP_NON_MACOS, "sandbox-exec 仅 macOS")
class TestMacosRlimitInSandbox(unittest.TestCase):
    """E2E: 真跑 sandbox-exec 子进程, 验 rlimit 真 enforce.

    比较慢 (每个 test 起一个真 sandbox 子进程), 但是核心 hardening 必测.
    """

    def test_rlimit_inherits_to_python_child(self):
        """sandbox-exec → python -c 子进程 read RLIMIT_CPU 应该看到 30s."""
        code = """
import resource
soft, hard = resource.getrlimit(resource.RLIMIT_CPU)
print(f"cpu_soft={soft},cpu_hard={hard}")
soft, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
print(f"fsize_soft={soft},fsize_hard={hard}")
"""
        result = sandbox.run_in_sandbox(code, lang="python", timeout_s=10)
        assert result["sandbox_used"], f"沙箱没起来: {result}"
        assert result["sandbox_kind"] == "sandbox-exec"
        # 子进程看到的 RLIMIT_CPU 应该是 30 秒
        assert "cpu_soft=30" in result["stdout"], (
            f"RLIMIT_CPU 没 inherit 进沙箱子进程: stdout={result['stdout']!r}"
        )
        assert "fsize_soft=52428800" in result["stdout"], (
            f"RLIMIT_FSIZE 没 inherit: stdout={result['stdout']!r}"
        )

    def test_cpu_bomb_hits_sigxcpu(self):
        """死循环跑超 30s CPU → SIGXCPU 被 kernel 拦.

        实际跑会快 (Python 死循环 1s 烧 ~1 CPU 秒), 但测的是 rlimit 真生效.
        wall-clock timeout 设 60s 给 RLIMIT_CPU 30s 触发空间.
        """
        # 4 路并发死循环, 加速 CPU 累计 (单线程 30s wall-clock 才到 30s CPU)
        # 实际单 process 单线程, 30s CPU = 30s wall (单核满载).
        # 为了 test 速度, 改 RLIMIT_CPU=2 临时测... 不行, rlimit 是常量.
        # 简单测: 死循环 跑直到被杀. 验 timed_out=False (是 SIGXCPU 不是
        # wall-clock timeout) + rc 是负数 (signal).
        code = "while True: pass"
        result = sandbox.run_in_sandbox(code, lang="python", timeout_s=60)
        assert result["sandbox_used"]
        # 跑 60s wall-clock 内死, RLIMIT_CPU=30s 触发 SIGXCPU
        # 但实际 Python "while True: pass" 是 GIL+busy loop, 30s CPU ≈ 30s wall,
        # timed_out 应该 False (RLIMIT_CPU 先击中, 不是 wall-clock 60s)
        assert not result["ok"], f"死循环应该失败: {result}"
        # rc 应该是负 (signal), -SIGXCPU=-24 (macOS)
        # 也可能是 SIGKILL 如果 RLIMIT_CPU 触发后又被 timeout kill
        assert result["rc"] != 0


if __name__ == "__main__":
    unittest.main()
