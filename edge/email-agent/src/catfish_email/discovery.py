"""Windows 邮件客户端发现。

这个模块只报告客户端和账号元数据，不返回邮件正文、密码或 token。发现与
实际 list 命令分开，避免 Outlook COM 不可用时把 Foxmail 的可用结果污染成
“没有账号”。
"""
from __future__ import annotations

import platform
import os
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
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


def discover_sources(force_scan: bool = False) -> list[EmailSource]:
    """探测这台机器上可用的邮件来源。

    每个客户端都隔离异常。一个客户端不可用是正常状态，不应阻塞其它客户端。

    Args:
        force_scan: 无视"值不值得探"的判断, 三个来源全探一遍。界面上
            「扫描一次」走这条。默认 False —— 见 _windows_clients 里
            9/21 那个把安装器搞挂的 bug。
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

    # 9/18: imap 排第一 —— 它是唯一不依赖邮件客户端的来源, 配了就该优先用。
    clients, skipped = _windows_clients(force_scan)
    if os.name == "nt":
        # COM may hang inside native code: a thread timeout cannot stop it.
        # Independent hidden processes keep the .eml source usable when Outlook hangs.
        with ThreadPoolExecutor(max_workers=max(1, len(clients))) as executor:
            found = list(executor.map(_discover_isolated, clients))
    else:
        found = [_discover_client(client) for client in clients]
    return found + skipped


#: 跳过探测时用的 status。前端只认 "ready", 所以它自然落进折叠的诊断区 ——
#: 但**必须出现**, 不能从列表里消失。"我们没去试" 和 "试了不行" 是两回事,
#: 后者员工无能为力, 前者他点一下就能试。静默消失的话, 装着经典 Outlook 的
#: 人会以为这软件不支持 Outlook。
SKIPPED = "skipped"


def _windows_clients(force_scan: bool) -> tuple[tuple[str, ...], list[EmailSource]]:
    """Windows 上这一轮真正要去探的有哪些, 以及跳过了哪些。

    # 为什么不再无条件探三个

    原来是 ("imap", "outlook-win", "eml-dir") 三个各起一个子进程, 每次邮件页
    挂载/重扫都跑。9/21 真机日志证明这不只是浪费:

        error: failed to remove file `...pywin32_system32/pythoncom311.dll`:
               拒绝访问。 (os error 5)
        uv 安装 catfish-email 失败: 2

    outlook-win 那个子进程 import pywin32 就把 COM DLL 加载了, 而新版 Outlook
    根本不提供 COM —— 这次探测注定失败。与此同时 bootstrap 在升级
    catfish-email, uv 替换不了被占用的 pywin32, 整个邮件功能装不上。

    一次注定失败的探测, 代价是把安装器搞挂。

    # 判据都是"纯本地、零成本"的

      outlook-win  注册表里有没有 Outlook.Application 的 COM 注册 (winreg,
                   标准库, 不加载任何 COM DLL)
      eml-dir      CATFISH_EML_DIR 有没有指向一个真实存在的目录 (env + stat)

    两个都不起子进程、不碰客户端。判断为否就连子进程都不 spawn。

    # force_scan 是给少数派留的门

    装着经典 Outlook、或者刚导出完 .eml 还没设环境变量的人, 点界面上那个
    「扫描一次」就走 force_scan=True, 三个照探。默认不探不等于不能探。
    """
    clients = ["imap"]
    skipped: list[EmailSource] = []

    from .adapters.outlook_win import classic_outlook_registered  # noqa: PLC0415

    if force_scan or classic_outlook_registered():
        clients.append("outlook-win")
    else:
        skipped.append(EmailSource(
            client="outlook-win", status=SKIPPED, accounts=[],
            reason="没有检测到经典桌面版 Outlook 的 COM 注册, 已跳过探测。"
                   "新版 Outlook (Microsoft.OutlookForWindows) 不提供 COM 自动化接口, "
                   "读不到邮件。装了经典版的话点「扫描一次」。",
        ))

    if force_scan or _eml_dir_configured():
        clients.append("eml-dir")
    else:
        skipped.append(EmailSource(
            client="eml-dir", status=SKIPPED, accounts=[],
            reason="还没有选过邮件导出目录, 已跳过探测。"
                   "在邮件客户端里把邮件导出为 .eml 之后, 点「选择邮件目录」。",
        ))

    return tuple(clients), skipped


def _eml_dir_configured() -> bool:
    """导出目录配过没有。纯 env + stat, 不扫盘。"""
    from .adapters.eml_dir import _detect_root  # noqa: PLC0415

    try:
        root = _detect_root()
    except Exception:  # noqa: BLE001 — 探测的判据出错只意味着"当成没配"
        return False
    return root is not None and root.is_dir()


def _discover_isolated(client: str) -> EmailSource:
    try:
        output = subprocess.run(
            [sys.executable, "-m", "catfish_email.discovery", client],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=15, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        if output.returncode != 0:
            raise ValueError(f"发现子进程退出码 {output.returncode}: {_safe_reason(output.stderr)}")
        data = json.loads(output.stdout)
        if not isinstance(data, dict) or data.get("client") != client or not isinstance(data.get("accounts"), list):
            raise ValueError("发现结果格式无效")
        return EmailSource(**data)
    except subprocess.TimeoutExpired:
        return EmailSource(client, "unavailable", [], reason="客户端探测超过 15 秒；不影响其他邮箱的发现，请稍后重试")
    except (OSError, ValueError, TypeError) as error:
        return EmailSource(client, "unavailable", [], reason=_safe_reason(str(error)))


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
    except Exception as error:  # noqa: BLE001
        # 9/17: 截图实锤 —— Foxmail 探测抛了一个不在上面清单里的异常, 子进程直接
        # 吐 traceback 退出码 1, 前端把整段 traceback 当"原因"显示。发现是探测,
        # 任何失败都只是"这个客户端不可用", 不该让员工看 Python 栈。异常类型留在
        # reason 里, 方便定位真因 (下一步再按类型收窄)。
        return EmailSource(
            client=client,
            status="unavailable",
            accounts=[],
            reason=_safe_reason(f"{type(error).__name__}: {error}"),
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


def discover_payload(force_scan: bool = False) -> dict[str, Any]:
    sources = discover_sources(force_scan)
    ready = [source for source in sources if source.status == "ready"]
    return {
        "platform": platform.system(),
        "sources": [source.as_json() for source in sources],
        "ready_client": ready[0].client if len(ready) == 1 else None,
    }


def discover_human(force_scan: bool = False) -> str:
    payload = discover_payload(force_scan)
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


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("imap", "outlook-win", "eml-dir"):
        raise SystemExit(2)
    try:
        source = _discover_client(sys.argv[1])
    except BaseException as error:  # noqa: BLE001 — 子进程的最后一道: 永远给父进程一个 JSON
        source = EmailSource(sys.argv[1], "unavailable", [], reason=_safe_reason(f"{type(error).__name__}: {error}"))
    print(json.dumps(source.as_json(), ensure_ascii=True))
