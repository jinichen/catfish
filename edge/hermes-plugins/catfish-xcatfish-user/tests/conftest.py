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
    except ImportError:
        return False


# CI 没装 hermes-agent → 跳 test_patches_present.py (它必须 hermes module)
collect_ignore: list[str] = []
if not _hermes_agent_available():
    collect_ignore.append("test_patches_present.py")
