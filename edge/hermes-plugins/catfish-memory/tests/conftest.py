"""pytest conftest — 让测试能直接 `import catfish_memory` (绕过 __init__.py
的 relative import, 单测不走 hermes plugin discovery).
"""
from __future__ import annotations

import sys
from pathlib import Path

# 加 plugin 目录到 sys.path 头部, 让 `import catfish_memory` work
_PLUGIN_DIR = Path(__file__).parent.parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))
