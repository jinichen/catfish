"""数据外发记录模块两份 (hermes 插件正本 + tool-bridge) 必须一字不差 —— 改了一份忘了另一份,
hermes 和 tool-bridge 的记录标准就不一样了。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_egress_log_两份一致():
    plugin = ROOT / "hermes-plugins" / "catfish-xcatfish-user" / "egress_log.py"
    bridge = ROOT / "tool-bridge" / "src" / "catfish_tool_bridge" / "egress_log.py"
    assert plugin.read_text(encoding="utf-8") == bridge.read_text(encoding="utf-8"), \
        "改了 egress_log.py 的一份, 另一份也要改 (cp hermes-plugins/catfish-xcatfish-user/egress_log.py tool-bridge/src/catfish_tool_bridge/)"
