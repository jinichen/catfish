"""CLI 的输出与错误 helper —— 从 __main__.py 拆出 (原文件 913 行, 超 800 红线).

放这里而不是留在 __main__.py: cli_read / cli_action 都要用它俩, 而它们又被
__main__.py import。留在 __main__.py 会形成循环 import。

函数体从 __main__.py 原样搬过来, 一个字节没改。
"""
from __future__ import annotations

import sys
from dataclasses import asdict
from typing import Any


def _msg_to_dict(m, adapter_name: str | None = None) -> dict[str, Any]:
    """Message dataclass → dict, attachments 也展开.

    5/18 BL-EMAIL-LIST-ADAPTER-FIELD: 可选 adapter_name 注入到 dict 里 (放最前面),
    让 `jq group_by(.adapter)` / 调试 / 跨 adapter 联调能区分这条来自 Mail.app
    还是 Foxmail. 老调用方不传 adapter_name 时不带 key (向后兼容).
    """
    d: dict[str, Any] = {}
    if adapter_name is not None:
        d["adapter"] = adapter_name
    d.update(asdict(m))
    return d


def _err(msg: str) -> None:
    print(f"catfish-email: {msg}", file=sys.stderr)
