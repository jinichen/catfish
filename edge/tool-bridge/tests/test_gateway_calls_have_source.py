"""11/06 鸿波「怎么这么多未标来源」: tool-bridge 直接调网关的地方都得带 catfish_source,
不然中央门户配额日志只显示「未标来源」(当天那几条是整理记忆, 输入 1 万 / 输出 8 千)。"""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "catfish_tool_bridge"


def test_直接调网关都带来源():
    bad = []
    for f in sorted(SRC.glob("*.py")):
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            for m in re.finditer(r'}/v1/chat/completions([^"\'\s]*)["\']', line):
                if "catfish_source=" not in m.group(1):
                    bad.append(f"{f.name}:{n}")
    assert not bad, f"这些请求没带 catfish_source: {bad}"


def test_来源都有页面上的叫法():
    from catfish_tool_bridge import egress_log
    used = set()
    for f in SRC.glob("*.py"):
        used |= set(re.findall(r"catfish_source=(plugin:toolbridge-[a-z-]+)", f.read_text(encoding="utf-8")))
    assert used and not (used - set(egress_log._SOURCE_PURPOSE)), used - set(egress_log._SOURCE_PURPOSE)
