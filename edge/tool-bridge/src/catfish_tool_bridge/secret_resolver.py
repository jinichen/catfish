"""Secret reference resolver.

# 为啥需要
==========
让 catfish 工具 (browser_fill 等) 接受 `secret_ref` 字段 (例 'keychain://eis_password'),
从安全源拉值, 而不是让员工把明文密码写进 LLM prompt.

LLM 上下文里出现明文密码 = 进 SOUL middleware / gateway audit / Companion 历史会话 db,
最终落到多个地方, 撤销难. secret_ref 让密码**永远不进 LLM 上下文**.

# 支持的 scheme
================
  env://VAR_NAME              # 从环境变量读, 0 依赖, 跨平台
  keychain://service_name     # macOS Keychain, 走 `security find-generic-password`
  wincred://target_name       # Windows Credential Manager (Phase 1 末尾批量做, 现 stub)

# 员工怎么用
============
macOS:
  # 推荐在 Companion 的「教学入口 → 保存登录密码」里保存，
  # 不要把密码写进终端命令。界面会返回类似：
  # keychain://catfish-teaching:教学登录

  # 模型调 browser_fill 时:
  catfish_browser_fill(selector="input[name='password']", secret_ref="keychain://catfish-teaching:教学登录")

Windows:
  # 在 Companion 的同一界面保存，返回 wincred://catfish-teaching:教学登录
  catfish_browser_fill(selector="input[name='password']", secret_ref="wincred://catfish-teaching:教学登录")

  # tool-bridge 自动 resolve, 拉到密码喂 page.fill, 密码不进 LLM 上下文.

env (跨平台):
  export EIS_PASSWORD=jiniaA1+
  catfish_browser_fill(selector="input[name='password']", secret_ref="env://EIS_PASSWORD")

# 安全保证
==========
- LLM 上下文只看到 secret_ref (引用), 不看到密码值
- audit log 只记 secret_ref (例 "keychain://eis_password"), 不记密码值
- 失败时只报 "secret 'eis_password' 不存在", 不漏值
- Companion / hermes 历史会话 db 也只存 secret_ref

# 风险 (员工要清楚)
===================
- env://: 进程能看到 environ. catfish-tool-bridge 进程内能看到, 别的进程也能 (ps aux). 适合开发不适合生产.
- keychain://: macOS 系统级安全, 加锁. 推荐.
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
from typing import Optional

logger = logging.getLogger("catfish.tool_bridge.secret_resolver")


class SecretResolveError(Exception):
    """secret_ref 解析失败 — 找不到 secret / 不支持的 scheme / 平台不支持."""


def resolve_secret(ref: str) -> str:
    """根据 secret_ref 拉真实值. 失败抛 SecretResolveError.

    Args:
        ref: 例 'keychain://eis_password' / 'env://EIS_PASSWORD'

    Returns:
        真实密码字符串 (不打印, 不入 audit log)

    Raises:
        SecretResolveError: scheme 不识别 / secret 不存在 / 平台不支持
    """
    if not ref or not isinstance(ref, str):
        raise SecretResolveError("secret_ref 不能为空")

    # 解析 scheme
    if "://" not in ref:
        raise SecretResolveError(
            f"secret_ref 必须是 '<scheme>://<name>' 形式, 例如 'env://EIS_PASSWORD'. "
            f"你给的: '{ref}'"
        )

    scheme, _, name = ref.partition("://")
    name = name.strip()
    if not name:
        raise SecretResolveError(f"secret_ref '{ref}' name 部分为空")

    if scheme == "env":
        return _resolve_env(name)
    if scheme == "keychain":
        return _resolve_keychain(name)
    if scheme == "wincred":
        return _resolve_wincred(name)

    raise SecretResolveError(
        f"不支持的 scheme '{scheme}'. 支持: env / keychain (macOS) / wincred (Windows)"
    )


def _resolve_env(name: str) -> str:
    """从 env var 拉密码."""
    val = os.environ.get(name)
    if val is None:
        raise SecretResolveError(
            f"env var '{name}' 没设. 先 export {name}=xxx 再调用"
        )
    if not val:
        raise SecretResolveError(f"env var '{name}' 是空字符串")
    return val


def _resolve_keychain(name: str) -> str:
    """从 macOS Keychain 拉密码.

    分两条路, 按 service 名的命名空间分:

    **catfish-teaching:\\***  (Companion 界面存的, 8/19 起)
        问 Companion 要, 自己不碰钥匙串。因为钥匙串的授权是按二进制记的, 那条
        目只授权给 Companion —— 本进程 exec `security` 去读会弹一个员工看不见
        的确认框, 然后超时。详见 companion_secrets.py 文件头。

    **其它** (例 `eis_password`, TEACHING-SOP.md:107 手工建的, 冻结的老 skill 在用)
        照旧 `security find-generic-password -s <name> -w`。这些本来就是
        `security` 建的, ACL 里就是它, 一直读得到, 没有要改的。
    """
    # ⚠ 顺序: 先分流再判平台。handles() 内部已经卡了 Darwin, 非 macOS 直接 False,
    #   落到下面那句原来的平台报错上 —— 行为不变。
    from . import companion_secrets  # noqa: PLC0415

    if companion_secrets.handles(name):
        try:
            return companion_secrets.fetch(name)
        except companion_secrets.CompanionSecretError as e:
            # 不回退到 `security`。回退看着"更稳", 实际是把这次改动悄悄撤销:
            # 老条目的 ACL 里还留着 `security` (8/18 那版加的), 回退会碰巧成功,
            # 于是没人发现通道断了; 新条目则会撞上看不见的授权框卡满 5 秒。
            # 两种都比"直说 Companion 没连上"糟。
            raise SecretResolveError(str(e)) from e

    if platform.system() != "Darwin":
        raise SecretResolveError(
            f"keychain:// 只在 macOS 支持. 当前系统: {platform.system()}. "
            "Win 用 wincred:// (Phase 1 末尾上线), 其他平台用 env://"
        )
    if not shutil.which("security"):
        raise SecretResolveError(
            "找不到 'security' 命令 (macOS 系统残缺?)"
        )

    try:
        # -s service_name, -w 只输出 password (不加这个会输出整个 entry)
        result = subprocess.run(
            ["security", "find-generic-password", "-s", name, "-w"],
            timeout=5,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired:
        raise SecretResolveError(f"keychain 查询超时 (security 命令卡住)")
    except OSError as e:
        raise SecretResolveError(f"启动 security 命令失败: {e}")

    if result.returncode != 0:
        # 常见: 44 = 找不到 entry, 51 = 用户拒绝授权
        stderr = (result.stderr or "").strip()
        if "could not be found" in stderr.lower() or result.returncode == 44:
            raise SecretResolveError(
                f"keychain 里没有 service '{name}'. 先存:\n"
                f"  security add-generic-password -a \"$USER\" -s \"{name}\" -w \"<密码>\""
            )
        raise SecretResolveError(
            f"security 命令失败 (returncode={result.returncode}): {stderr[:200]}"
        )

    pwd = result.stdout.rstrip("\n")  # security 输出尾部有换行, strip
    if not pwd:
        raise SecretResolveError(f"keychain 里 '{name}' 的密码是空的")
    return pwd


def _resolve_wincred(name: str) -> str:
    """从 Windows Credential Manager 拉密码 (BL-WIN2, 5/8).

    优先用 ``keyring`` Python 包 (跨平台抽象, Windows 上走 wincred backend).
    keyring 装不上时 fallback 走 PowerShell ``Get-Secret`` (PowerShell 7
    + Microsoft.PowerShell.SecretManagement). 都失败抛 friendly error.

    Companion 会自动写入 Credential Manager。不要把密码放进 cmdkey 命令。
    或 PowerShell:
      $cred = Get-Credential
      cmdkey /generic:<name> /user:$cred.UserName /pass:$cred.GetNetworkCredential().Password
    """
    if platform.system() != "Windows":
        raise SecretResolveError(
            f"wincred:// 只在 Windows 支持. 当前系统: {platform.system()}. "
            "macOS 用 keychain://, 其他平台用 env://"
        )

    # 路径 1: 优先用 Python keyring 包 (官方推荐, win32cred 包装)
    try:
        import keyring  # noqa: PLC0415

        pwd = keyring.get_password("catfish", name)
        if pwd:
            return pwd
        # service='catfish' 找不到, 试 service=name (不带 catfish prefix 也接受)
        pwd = keyring.get_password(name, os.environ.get("USERNAME", "user"))
        if pwd:
            return pwd
        raise SecretResolveError(
            f"Windows Credential Manager 里没找到 service='catfish' target='{name}'. "
            f"先存:\n"
            f"  cmdkey /generic:catfish:{name} /user:%USERNAME% /pass:<密码>\n"
            f"或 PowerShell:\n"
            f"  python -c \"import keyring; keyring.set_password('catfish','{name}','<密码>')\""
        )
    except ImportError:
        pass  # 没装 keyring, 走 fallback 路径

    # 路径 2 (fallback): PowerShell + Microsoft.PowerShell.SecretManagement
    if not shutil.which("powershell.exe"):
        raise SecretResolveError(
            "Windows Credential Manager 读取失败: 既没装 Python `keyring` 包, "
            "也找不到 powershell.exe.\n"
            "推荐: pip install keyring (跨平台 secret backend, 自动用 wincred)"
        )

    ps_script = (
        f"$ErrorActionPreference='Stop';"
        f"try {{ "
        f"  $s = Get-Secret -Name 'catfish:{name}' -AsPlainText -ErrorAction Stop; "
        f"  Write-Output $s "
        f"}} catch {{ "
        f"  Write-Error $_.Exception.Message; exit 44 "
        f"}}"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", ps_script],
            timeout=5,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired:
        raise SecretResolveError("PowerShell Get-Secret 超时")
    except OSError as e:
        raise SecretResolveError(f"启动 powershell 失败: {e}")

    if result.returncode != 0:
        raise SecretResolveError(
            f"Get-Secret 'catfish:{name}' 失败. "
            f"先装 SecretManagement 模块 + 存密码:\n"
            f"  Install-Module Microsoft.PowerShell.SecretManagement -Scope CurrentUser\n"
            f"  Install-Module Microsoft.PowerShell.SecretStore -Scope CurrentUser\n"
            f"  Register-SecretVault -Name catfish -ModuleName Microsoft.PowerShell.SecretStore -DefaultVault\n"
            f"  Set-Secret -Name 'catfish:{name}' -Secret '<密码>'"
        )

    pwd = result.stdout.rstrip("\r\n")
    if not pwd:
        raise SecretResolveError(f"Credential Manager 里 'catfish:{name}' 是空的")
    return pwd


def is_secret_ref(value: Optional[str]) -> bool:
    """判定一个字符串是不是 secret_ref 形式.

    用法: 工具内部判断 'text' 字段是不是误填了 secret_ref (员工或模型写错位置).
    """
    if not value or not isinstance(value, str):
        return False
    return any(value.startswith(prefix) for prefix in ("env://", "keychain://", "wincred://"))
