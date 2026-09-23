"""hermes 数据目录 —— tool-bridge 里所有 `~/.hermes/...` 都从这里取 (9/23).

原来 13 处写死 `Path.home() / ".hermes"`。Windows 上 hermes 装在
`%LOCALAPPDATA%\\hermes` (hermes_constants._get_platform_default_hermes_home),
于是 tool-bridge 在 Windows 上读的 config.yaml / state.db / memories / skills
全部指向一个不存在的目录 —— 不报错, 只是功能静默失效。

优先级跟 hermes 自己一致: HERMES_HOME > 平台默认。不 import hermes_constants:
tool-bridge 可能跑在 hermes venv 之外。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def hermes_home() -> Path:
    env = os.environ.get("HERMES_HOME", "").strip()
    if env:
        return Path(env).expanduser()
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "hermes"
    return Path.home() / ".hermes"
