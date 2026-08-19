"""教学凭据的密码**向 Companion 要**, tool-bridge 自己不读钥匙串 (8/19)。

# 为什么要绕这一圈

macOS 钥匙串的授权是**按二进制**记的: 每个条目自带一份"哪几个程序可以读我"
的名单 (ACL)。Companion 存密码时, 名单里只有 Companion 自己。

而教学时读密码的是 tool-bridge —— hermes venv 里的另一个 python 进程, 它 exec
`/usr/bin/security` 去读。不在名单里, 于是系统弹确认框。tool-bridge 是 launchd
起的后台进程, **那个框员工根本看不见**, 5 秒后 `security` 超时, 教学就卡在
"填密码"这一步不动了。

8/18 试过两条"让 tool-bridge 也读得到"的路, 都不对:

  · 存完之后拿 `SecKeychainItemSetAccess` 把 `/usr/bin/security` 加进名单
    → 改**已存在**条目的名单, macOS 每次都要员工输开机密码 (鸿波连输三次)。
      等于把"读的时候卡住"搬成了"存的时候拦人"。

  · 创建时就把 `/usr/bin/security` 写进名单 (`SecKeychainItemCreateFromContent`)
    → 不弹框了, 但 `security` 是**谁都能 exec 的通用工具**。把它放进信任名单,
      跟 `add-generic-password -A`(对所有程序开放) 没有实质区别 —— 任何进程
      exec 一下就把密码拿走了, 而且悄无声息。那道墙等于自己拆了。

治本的做法是**不让第二个二进制去读**: 密码只有 Companion 读得到, 别人要就问
它。钥匙串那份名单保持"只有 Companion", 是 macOS 在替我们挡, 不是我们自己写
一个 if 挡。这条 socket 就是"问它"的通道。

# 只管 catfish-teaching: 这一类

`keychain://eis_password` 那种手工建的条目 (TEACHING-SOP.md:107, 4/28 那条,
冻结的老 skill 还在用) **不走这里** —— 它本来就是 `security` 建的, ACL 里就是
`security`, 一直读得到, 没有要修的东西。

前缀白名单不是拿来防黑客的 (同一个用户下的进程有的是别的办法), 是拿来**防我们
自己**的: 把这条通道的能力钉死在"教学凭据"这一类上, 免得将来哪个调用点顺手拿
它去读钥匙串里的 SSO token / API key。Companion 那边也会再卡一次同样的前缀。

# 只有 macOS 需要

Windows 凭据管理器没有按程序的 ACL —— 同一个用户下的任何进程都读得到, 所以
`wincred://` 那条路照旧, 一行都不用改。见 secret_resolver._resolve_wincred。

# 红线

**响应里带着密码。** 任何时候都不要把响应原文放进异常消息、日志或返回值 ——
tool_bridge_rpc.rs:117 那种 `format!("响应非合法 JSON: {e}\\n原文: {line}")`
在这条通道上是泄漏。下面每一处 raise 都只说"解析失败", 不带原文。
"""
from __future__ import annotations

import json
import logging
import platform
import socket
from pathlib import Path

logger = logging.getLogger("catfish.tool_bridge.companion_secrets")

#: 只有这个命名空间下的条目走 Companion。见文件头。
SERVICE_PREFIX = "catfish-teaching:"

#: 连不上 = Companion 没起。要**快**失败, 否则模型会以为工具卡住了。
CONNECT_TIMEOUT = 2.0

#: 读响应给足时间。Companion 那边可能正弹着钥匙串授权框 (重装 / 重新签名后
#: 第一次读会弹一次), 那个框要等人点。5 秒会把人正在点的那一下掐掉 ——
#: 这条通道的全部意义就是让弹框出现在**员工看得见的前台 app** 上, 再掐掉就白搭了。
READ_TIMEOUT = 60.0

#: 响应最大长度。防对端疯掉时把内存撑爆。密码再长也不会到这个量级。
MAX_RESPONSE_BYTES = 1 << 20


class CompanionSecretError(Exception):
    """向 Companion 取密码失败。"""


class CompanionUnavailable(CompanionSecretError):
    """Companion 没在跑 —— 跟"没这条密码"要分开, 员工的动作不一样。"""


def socket_path() -> Path:
    """`~/.catfish/companion-secrets.sock`。

    跟 `~/.catfish/tool-bridge.sock` 并排 —— 那条是**反方向**的 (Companion →
    tool-bridge, 见 services/tool_bridge_rpc.rs)。两条不复用: 方向反的, server
    在两头。

    ⚠ 这里用 `Path.home()` 而**不是** CATFISH_HOME, 是照着 tool-bridge.sock 那条
      来的 (__main__.py:18 用 Path.home(), catfish_paths.rs:143 用 $HOME)。
      socket 两端必须指同一个文件, 而 Rust 那边没有 CATFISH_HOME 这个概念 ——
      这边多认一个 env 只会让两端在设了它的机器上错开, 表现成"Companion 明明开着
      却说连不上"。
    """
    return Path.home() / ".catfish" / "companion-secrets.sock"


def handles(service: str) -> bool:
    """这个 service 该不该走 Companion。

    非 macOS 一律 False —— 别的平台没有按程序的 ACL, 没有要绕的东西 (文件头)。
    """
    if platform.system() != "Darwin":
        return False
    return bool(service) and service.startswith(SERVICE_PREFIX)


def _read_line(sock: socket.socket) -> bytes:
    """读到第一个换行为止。NDJSON, 跟 tool-bridge 那条 socket 同款帧。"""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = sock.recv(65536)
        if not chunk:
            raise CompanionSecretError("Companion 没回话就关掉了连接")
        nl = chunk.find(b"\n")
        if nl >= 0:
            chunks.append(chunk[:nl])
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES:
            raise CompanionSecretError("Companion 的响应过长, 已放弃")


def fetch(service: str) -> str:
    """问 Companion 要 `service` 这条的密码。

    Args:
        service: 钥匙串 service 名, 例 'catfish-teaching:neis.ffcs.cn'。
                 调用前该先过 `handles()`。

    Returns:
        密码明文。**不打印, 不进日志, 不进任何异常消息。**

    Raises:
        CompanionUnavailable: Companion 没在跑
        CompanionSecretError:  其它 (钥匙串里没有 / 前缀被拒 / 协议异常)
    """
    if not handles(service):
        # 走到这儿说明调用方没先问 handles() —— 是代码错, 不是员工的错。
        raise CompanionSecretError(
            f"'{service}' 不该走 Companion 通道 (只服务 {SERVICE_PREFIX}*)"
        )

    path = socket_path()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(CONNECT_TIMEOUT)
        try:
            sock.connect(str(path))
        except FileNotFoundError:
            # sock 文件不在 = Companion 从没起过 (或者是没带这个功能的老版本)
            raise CompanionUnavailable(_not_running_hint(service, path, "socket 文件不存在"))
        except ConnectionRefusedError:
            # 文件在但没人 listen = 上次崩溃留下的死文件
            raise CompanionUnavailable(_not_running_hint(service, path, "没有进程在监听"))
        except (socket.timeout, TimeoutError):
            raise CompanionUnavailable(_not_running_hint(service, path, "连接超时"))
        except OSError as e:
            raise CompanionUnavailable(_not_running_hint(service, path, f"{type(e).__name__}"))

        req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "secret/get",
            "params": {"service": service},
        }
        sock.settimeout(READ_TIMEOUT)
        try:
            sock.sendall((json.dumps(req, ensure_ascii=False) + "\n").encode("utf-8"))
            raw = _read_line(sock)
        except (socket.timeout, TimeoutError):
            raise CompanionSecretError(
                f"等 Companion 返回密码超时 ({READ_TIMEOUT:.0f}s)。"
                "如果 Companion 弹了钥匙串授权框, 点一下「始终允许」再重试这一步。"
            )
        except OSError as e:
            raise CompanionSecretError(f"跟 Companion 通信失败: {type(e).__name__}")
    finally:
        try:
            sock.close()
        except OSError:
            pass

    try:
        resp = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        # ⚠ 绝不把 raw 放进消息 —— 那一行里就是密码。见文件头「红线」。
        raise CompanionSecretError("Companion 的响应解析不了 (不是合法 JSON)")

    if not isinstance(resp, dict):
        raise CompanionSecretError("Companion 的响应格式不对")

    err = resp.get("error")
    if isinstance(err, dict):
        msg = str(err.get("message") or "未说明原因")
        raise CompanionSecretError(f"Companion 拒绝了这次取值: {msg}")

    result = resp.get("result")
    if not isinstance(result, dict):
        raise CompanionSecretError("Companion 的响应里没有 result")

    pwd = result.get("password")
    if not isinstance(pwd, str) or not pwd:
        # 空密码当失败 —— 拿一个空串去 page.fill 只会让登录失败得更难查。
        raise CompanionSecretError(f"Companion 说 '{service}' 的密码是空的")
    return pwd


def _not_running_hint(service: str, path: Path, why: str) -> str:
    """连不上时给员工的话。**不 fallback 回 `security`** —— 那会把这次改动
    悄悄退回老路: 要么撞上看不见的授权框卡 5 秒, 要么(更糟)在 ACL 还留着
    `security` 的老条目上碰巧成功, 于是没人发现这条通道其实断了。
    """
    return (
        f"读 '{service}' 的密码要经过鲶鱼 Companion, 但连不上它 ({why})。\n"
        f"密码只有 Companion 读得到 —— 钥匙串里那条只授权给它, 这是有意的。\n"
        f"打开 Companion 再重试这一步。(通道: {path})"
    )
