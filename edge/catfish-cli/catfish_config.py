"""catfish-cli 的基础层 — logger / 常量 / 路径与 URL 解析。

# 为什么这一层要单独一个文件 (8/15 拆分)

catfish.py 1875 行过了 CLAUDE.md §1 的 800 红线。拆的时候第一个要解决的
不是"哪些函数该走", 而是**依赖方向**: 几乎每个子模块都要用 logger、常量和
`_hermes_config_path()` 这些东西。如果它们留在 catfish.py 里, 子模块就得
`import catfish` 反向拿 —— 那会撞上两个坑:

  1. **循环导入**: catfish.py 顶部 import 子模块, 子模块又 import catfish。
  2. **双 module 对象**: catfish.py 直接跑的时候模块名是 `__main__`;
     子模块再 `import catfish` 会把同一份源码**再执行一遍**, 得到第二个
     module 对象。两份常量、两份 logger, 而且各自自洽 —— 测试看不出来。

所以基础层必须沉到最底下, 谁都能 import, 它谁都不 import。本文件只依赖
标准库, 这一点由 tests/test_split_layering.py 钉住。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger("catfish.cli")

# ─── 配置 ──────────────────────────────────────────────────

DEFAULT_IDENTITY_URL = "http://localhost:8998"
DEFAULT_GATEWAY_URL = "http://localhost:8999"
DEFAULT_CLIENT_ID = "hermes-cli"
DEFAULT_SCOPES = "openid email profile chat.completions audit.write tools.invoke skills.run"
HERMES_PROVIDER_NAME = "Local (localhost:8999)"  # 跟用户 hermes setup 时填的 display name 对齐

# BL-HERMES-SERVICE-TOKEN (5/24 鸿波"hermes 内存缓存过期 user token 撞 401"):
# 之前 _patch_hermes_config(store.access_token) 把用户 OAuth access_token (1h TTL)
# 写进 hermes config api_key. hermes 启动后读这个, 1 小时后过期 → gateway 验签
# 拒 → 401 storm 直到 `hermes gateway restart` 手动续.
#
# 真正的修法 (D 方案, identity-server 5/14 BL-RBAC P0 就为此预留了基础设施):
# hermes 用 client_credentials grant 拿"服务身份" token (sub=client:hermes-cli,
# TTL 30 天), 不依赖任何用户的 OAuth 周期. 0002 patch 已经实现 X-Catfish-User
# 转发, gateway 看 service token + header 一起识别真实员工身份做 audit/quota.
#
# client_secret 来源:
#   1. env CATFISH_HERMES_CLI_SECRET (生产推荐, 跟 identity-server clients.yaml
#      里 hermes-cli 的 bcrypt hash 对应的明文 secret)
#   2. 默认 dev demo secret (跟 identity-server/config/clients.yaml line 30 注释
#      里写的 "hermes-dev-secret-2026-please-change" 一致). 生产部署一定要改.
DEFAULT_HERMES_CLI_CLIENT_ID = "hermes-cli"
DEFAULT_HERMES_CLI_DEV_SECRET = "hermes-dev-secret-2026-please-change"
HERMES_SERVICE_SCOPES = "chat.completions tools.invoke skills.run audit.write"

#: token 过期前多少秒视为"快过期" (要重新拿)
EXPIRY_BUFFER_SECS = 60

#: 本地 callback 监听超时 (秒)
CALLBACK_TIMEOUT_SECS = 300

#: hermes-cli service token 剩余 < 这天数时, opportunistic 重新 mint (login/refresh 自动触发)
HERMES_TOKEN_MIN_REMAINING_DAYS = 7


def _auth_dir() -> Path:
    """token 存哪. env CATFISH_AUTH_DIR 覆盖 (测试用)."""
    if env := os.environ.get("CATFISH_AUTH_DIR"):
        return Path(env).expanduser()
    return Path.home() / ".catfish" / "auth"


def _token_path() -> Path:
    return _auth_dir() / "token.json"


def _hermes_config_path() -> Path:
    if env := os.environ.get("HERMES_CONFIG"):
        return Path(env).expanduser()
    return Path.home() / ".hermes" / "config.yaml"


def _hermes_env_path() -> Path:
    """~/.hermes/.env — hermes 0.14 读 backend API key 的文件 (TAVILY_API_KEY 等).

    hermes 启动时自动 load 这个文件. 我们 (catfish) 写 per-key update, 保留无关行.
    见 docs/DEPLOYMENT-RUNBOOK.md §15.
    """
    if env := os.environ.get("HERMES_DOTENV"):
        return Path(env).expanduser()
    return Path.home() / ".hermes" / ".env"


def _gateway_url() -> str:
    return os.environ.get("CATFISH_GATEWAY_URL", DEFAULT_GATEWAY_URL).rstrip("/")


def _identity_url() -> str:
    return os.environ.get("CATFISH_IDENTITY_URL", DEFAULT_IDENTITY_URL).rstrip("/")


def _client_id() -> str:
    return os.environ.get("CATFISH_CLIENT_ID", DEFAULT_CLIENT_ID)


def _scopes() -> str:
    return os.environ.get("CATFISH_SCOPES", DEFAULT_SCOPES)

