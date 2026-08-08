"""pytest conftest: 把 plugin 根目录加 sys.path, 让 tests 能 `import plugin` 等.

dash 包名 (catfish-xcatfish-user) 让 `from .. import x` 在 pytest 加载时找不到
parent package. 用 sys.path 注入 + 绝对 import 绕过.

C6 (6/6 鸿波 CI matrix audit): test_patches_present.py 必须在 hermes-agent
环境跑 (PYTHONPATH 含 agent / run_agent / gateway), CI 装不了完整 hermes 代码 →
ImportError. 当 hermes module 不可用时, collect_ignore 跳过这条 test
(注释明确说 "跑法: cd ~/.hermes/hermes-agent, python -m pytest ..."). 鸿波本机
hermes 已装, 跑这条仍 work. CI / 没 hermes 的环境跳过.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_plugin_dir = str(Path(__file__).parent.parent.resolve())
if _plugin_dir not in sys.path:
    sys.path.insert(0, _plugin_dir)


def _hermes_agent_available() -> bool:
    """检测 hermes-agent module 是否在 PYTHONPATH (本地真 hermes 装好就 True)."""
    hermes_root = os.environ.get("HERMES_ROOT") or os.path.expanduser("~/.hermes/hermes-agent")
    # 也加到 sys.path 让 hermes 装好的本机能跑
    if os.path.exists(hermes_root) and hermes_root not in sys.path:
        sys.path.insert(0, hermes_root)
    # 真 import 试一下
    try:
        import agent  # noqa: F401
        import run_agent  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        # 8/9: 原来只 catch ImportError。但这个探测的意图是"hermes 能不能用",
        # 而"装了但**跑不起来**"照样是不能用 —— 而且它抛的不是 ImportError。
        #
        # 实撞: PYTHONPATH 指向 hermes、解释器却是 3.10 (hermes 要 3.11+),
        # `import run_agent` 抛 `re.error: multiple repeat` (3.10 的 sre 解析不了
        # 新版正则)。ImportError 接不住 → conftest 自己炸 → **整个测试目录
        # 收集失败**, 连不需要 hermes 的用例一起挂。
        #
        # 探测函数的失败方向只该有一个: 拿不准就返 False (跳过), 绝不把调用方
        # 带崩。
        return False


# CI 没装 hermes-agent → 跳 test_patches_present.py (它必须 hermes module)
collect_ignore: list[str] = []
if not _hermes_agent_available():
    collect_ignore.append("test_patches_present.py")
