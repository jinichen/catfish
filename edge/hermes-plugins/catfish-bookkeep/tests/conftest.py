"""pytest conftest — 让 `import bookkeep` 在单测里 work (绕过 dash 包名 relative import).

跟 catfish-memory tests/conftest.py 同套路 (C3 fake package alias). bookkeep.py
本身没 relative import (没 sibling helpers 文件), 所以简化版: 只 sys.path insert
plugin 目录, 让 `import bookkeep` 拿到 module.
"""
from __future__ import annotations

import sys
from pathlib import Path

_PLUGIN_DIR = Path(__file__).parent.parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))
