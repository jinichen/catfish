"""插件的每个文件都必须烘进 Companion 安装包 (hermes_plugin_baked.rs 的 BAKED_FILES)。

Rust 那边有同样的测试, 但 CI 的 cargo test 运行步骤是 continue-on-error, 挂了也是绿勾。
9/26 windows_chat_credentials.py 漏烘, Windows 每句聊天都报「文件不存在」, 这里用必过的
Python 测试再守一道。
"""
from __future__ import annotations

import re
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
BAKED_RS = PLUGIN.parents[1] / "companion-app" / "src-tauri" / "src" / "commands" / "hermes_plugin_baked.rs"


def test_every_plugin_file_is_baked():
    text = BAKED_RS.read_text(encoding="utf-8")
    listed = set(re.findall(r'\(\s*"([^"]+)",\s*BAKED_', text))
    shipped = {p.name for p in PLUGIN.iterdir() if p.suffix in (".py", ".yaml") and p.is_file()}
    missing = sorted(shipped - listed)
    assert not missing, f"这些文件没进 BAKED_FILES, 安装包里会缺: {missing}"
