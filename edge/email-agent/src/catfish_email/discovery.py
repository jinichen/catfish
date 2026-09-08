"""Windows 邮件客户端发现。

这个模块只报告客户端和账号元数据，不返回邮件正文、密码或 token。发现与
实际 list 命令分开，避免 Outlook COM 不可用时把 Foxmail 的可用结果污染成
“没有账号”。
"""
from __future__ import annotations

import platform
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .adapters.base import DataNotFoundError, EmailAdapterError
from .inbox import _get_adapter_explicit


@dataclass(frozen=True)
class EmailSource:
    client: str
    status: str
    accounts: list[dict[str, Any]]
    root: str | None = None
    reason: str | None = None

    def as_json(self) -> dict[str, Any]:
        return asdict(self)


def discover_sources() -> list[EmailSource]:
    """独立探测 Windows 上的 Outlook 和 Foxmail。

    每个客户端都隔离异常。一个客户端不可用是正常状态，不应阻塞其它客户端。
    """
    if platform.system() != "Windows":
        return [
            EmailSource(
                client="unsupported",
                status="unsupported",
                accounts=[],
                reason=f"当前平台 {platform.system()} 不是 Windows",
            )
        ]

    return [_discover_client(client) for client in ("outlook-win", "foxmail-win")]


def _discover_client(client: str) -> EmailSource:
    try:
        adapter = _get_adapter_explicit(client)
        accounts = [
            {
                "name": account.name,
                "address": account.address,
                "is_default": account.is_default,
                "client": adapter.name,
            }
            for account in adapter.list_accounts()
        ]
    except (DataNotFoundError, EmailAdapterError, ImportError, NotImplementedError, OSError) as error:
        return EmailSource(
            client=client,
            status="unavailable",
            accounts=[],
            reason=_safe_reason(str(error)),
        )

    root = getattr(adapter, "profiles_dir", None)
    return EmailSource(
        client=client,
        status="ready" if accounts else "unavailable",
        accounts=accounts,
        root=_safe_root(root),
        reason=None if accounts else "客户端已找到，但没有可用邮箱账号",
    )


def _safe_root(root: object) -> str | None:
    if not isinstance(root, (str, Path)):
        return None
    return str(root)


def _safe_reason(reason: str) -> str:
    # COM 错误通常包含可诊断的 HRESULT；其它错误只保留一行，避免把 traceback
    # 或意外的正文内容暴露给前端。
    return " ".join(reason.split())[:500]


def discover_payload() -> dict[str, Any]:
    sources = discover_sources()
    ready = [source for source in sources if source.status == "ready"]
    return {
        "platform": platform.system(),
        "sources": [source.as_json() for source in sources],
        "ready_client": ready[0].client if len(ready) == 1 else None,
    }


def discover_human() -> str:
    payload = discover_payload()
    lines = [f"平台: {payload['platform']}"]
    for source in payload["sources"]:
        label = "可用" if source["status"] == "ready" else "不可用"
        lines.append(f"- {source['client']}: {label}")
        if source.get("root"):
            lines.append(f"  目录: {source['root']}")
        if source.get("accounts"):
            lines.append(f"  账号: {len(source['accounts'])}")
        if source.get("reason"):
            lines.append(f"  原因: {source['reason']}")
    return "\n".join(lines)
