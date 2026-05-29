"""pytest conftest: 把 plugin 根目录加 sys.path, 让 tests 能 `import plugin` 等.

dash 包名 (catfish-xcatfish-user) 让 `from .. import x` 在 pytest 加载时找不到
parent package. 用 sys.path 注入 + 绝对 import 绕过.
"""
from __future__ import annotations

import sys
from pathlib import Path

_plugin_dir = str(Path(__file__).parent.parent.resolve())
if _plugin_dir not in sys.path:
    sys.path.insert(0, _plugin_dir)
