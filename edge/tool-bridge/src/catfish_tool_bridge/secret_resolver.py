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
  # 把密码存进 keychain
  security add-generic-password -a "$USER" -s "eis_password" -w "jiniaA1+"

  # 模型调 browser_fill 时:
  catfish_browser_fill(selector="input[name='password']", secret_ref="keychain://eis_password")

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

    使用 `security find-generic-password -s <name> -w` (-w 只输出 password).
    要求员工先存进:
        security add-generic-password -a "$USER" -s "<name>" -w "<password>"
    """
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
    """从 Windows Credential Manager 拉密码 (Phase 1 末尾批量做)."""
    raise SecretResolveError(
        f"wincred:// 还没实现 (Phase 1 末尾跟 Win 跨平台一起做). "
        f"当前用 env://{name.upper()} 替代."
    )


def is_secret_ref(value: Optional[str]) -> bool:
    """判定一个字符串是不是 secret_ref 形式.

    用法: 工具内部判断 'text' 字段是不是误填了 secret_ref (员工或模型写错位置).
    """
    if not value or not isinstance(value, str):
        return False
    return any(value.startswith(prefix) for prefix in ("env://", "keychain://", "wincred://"))
