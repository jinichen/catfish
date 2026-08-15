"""网关地址 + 开发 token —— 这个插件怎么找到 catfish-gateway。

8/15 从 catfish_memory_helpers.py 搬出来。

# _GATEWAY_URL_DEPRECATION_LOGGED 跟 _gateway_url 放一起

那是个用 `global` 写的模块级布尔, 作用是"废弃告警只打一次"。写它的只有
`_gateway_url` 一个函数。

⚠ 这里记一条**我拆分当天先写错、后来实测纠正**的东西, 免得下一个人重复推错:

我原本在这段写的是「分到两个文件的话, global 改的是自己模块那份, 另一个文件
看到的永远是 False, 于是**告警每次都打**」。做变异验证时把标志挪去
catfish_memory_base.py, 结果**三次调用仍然只打一条**, 跟预测相反。

查清楚了。`global X` 绑的是**执行 global 那个函数所在模块**的命名空间。
`from base import FLAG` 在本模块建了一份绑定, 之后 `global FLAG; FLAG = True`
改的是本模块这份, 而 `if not FLAG` 读的也是本模块这份 —— **读写在同一个地方,
所以"只打一次"照常生效**。最小复现:

    base.py:  FLAG = False
    user.py:  from base import FLAG
              def f():
                  global FLAG
                  if not FLAG: print("warn"); FLAG = True

    调三次 f() → 只打一次;  user.FLAG=True 而 base.FLAG 永远是 False

所以真正的后果不是"变吵", 是**同名标志有了两份, 各自飘**。今天没有症状,
因为没人读 base 那份。但哪天有人写个测试断言"我们警告过了"、去读 base 那份,
它永远是 False —— 而那种失效在现场看不出来。

结论没变 (这两个东西该放一起), 但理由是**内聚**: 一个只服务于某个函数的
once-only 标志, 就该跟那个函数在一起。不是"拆了会立刻坏"。

(同一天在 plugin_cors / plugin_approval 上确实栽过"拆了立刻坏"的 —— 那次是
审批中间件静默不进链。但那是**读**跨模块的可变全局, 跟这里的 global 写不是
一回事。当时我把两件事混成了一条。)

# token 的取法有优先级, 别改顺序

`_gateway_dev_token` 依次试: 插件 yaml → ~/.hermes/.env → 环境变量。
拿不到时走 `_log_token_missing` 打一次提示, **不抛** —— 记忆功能降级,
但不能因此阻塞员工聊天。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from .catfish_memory_base import _DEFAULT_GATEWAY_URL, _catfish_home, logger
except ImportError:  # 独立脚本模式 (无父包)
    from catfish_memory_base import _DEFAULT_GATEWAY_URL, _catfish_home, logger


_GATEWAY_URL_DEPRECATION_LOGGED = False


def _gateway_url() -> str:
    """gateway loopback URL.

    P23 (6/5 鸿波): 统一 env 名 `CATFISH_GATEWAY_URL` (base URL, 不带 path) 跟
    Companion / catfish-xcatfish-user plugin 对齐. 老名 `CATFISH_GATEWAY_INTERNAL_URL`
    保留兼容 (含 full path), 设了 → 打 deprecation warning 用一次提醒.

    P24 (6/5 鸿波): yaml 接入 — ~/.catfish/memory_plugin.yaml 加 `gateway.url`
    字段 (跟 env 同义, 但客户端友好 — 不用 launchctl 改).

    优先级 (高→低):
      1. CATFISH_GATEWAY_INTERNAL_URL env (老, full URL e.g. http://x/v1/chat/completions)
      2. CATFISH_GATEWAY_URL env (新统一名, base URL), 自动拼 /v1/chat/completions
      3. yaml gateway.url (base URL), 自动拼 /v1/chat/completions
      4. _DEFAULT_GATEWAY_URL (http://127.0.0.1:8999/v1/chat/completions)
    """
    global _GATEWAY_URL_DEPRECATION_LOGGED
    full = os.environ.get("CATFISH_GATEWAY_INTERNAL_URL", "").strip()
    if full:
        if not _GATEWAY_URL_DEPRECATION_LOGGED:
            logger.warning(
                "P23 deprecation: CATFISH_GATEWAY_INTERNAL_URL 老 env 名, "
                "改用 CATFISH_GATEWAY_URL (base URL, 不带 path). 这次先兼容."
            )
            _GATEWAY_URL_DEPRECATION_LOGGED = True
        return full
    base = os.environ.get("CATFISH_GATEWAY_URL", "").strip().rstrip("/")
    if base:
        return f"{base}/v1/chat/completions"
    # P24 yaml 兜底
    cfg = _load_plugin_config()
    yaml_base = ""
    if isinstance(cfg, dict):
        gw = cfg.get("gateway", {})
        if isinstance(gw, dict):
            yaml_base = str(gw.get("url", "")).strip().rstrip("/")
    if yaml_base:
        return f"{yaml_base}/v1/chat/completions"
    return _DEFAULT_GATEWAY_URL


# ── plugin 配置 yaml (BL-PLUGIN-CONFIG-YAML 5/20 Day 2.5) ─────────
#
# 鸿波拍板: 配置不硬编码 / env, 走 yaml 参数文件, 跟 ~/.catfish/companion.yaml
# 同套路 (catfish 全栈共享配置位置).
#
# 文件路径: ~/.catfish/memory_plugin.yaml
#
# 优先级: yaml > env > hardcoded default. env 兜底兼容老部署.
#
# 内容示例:
#   enabled: true
#   summarize:
#     model: catfish-private-vision
#     every_n_turns: 5
#     min_interval_seconds: 1800

_PLUGIN_CONFIG_FILENAME = "memory_plugin.yaml"


def _plugin_config_path(home: Optional[Path] = None) -> Path:
    """yaml 配置文件位置. 默认 ~/.catfish/memory_plugin.yaml.

    home 参数让单测可指定 fake home; 没传时用 _catfish_home() (env CATFISH_HOME aware).
    """
    return (home or _catfish_home()) / _PLUGIN_CONFIG_FILENAME


def _load_plugin_config(home: Optional[Path] = None) -> Dict[str, Any]:
    """读 yaml 配置. 不存在 / 解析失败返空 dict (走 env / default 兜底).

    PyYAML 不可用时也返空 (优雅降级) — env 仍 work.
    """
    path = _plugin_config_path(home)
    if not path.exists():
        return {}
    try:
        import yaml  # 懒 import, PyYAML 是 hermes 自带依赖
    except ImportError:
        logger.debug("PyYAML 不可用, plugin yaml config 不加载")
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, yaml.YAMLError) as e:
        logger.warning("catfish-memory plugin yaml 配置加载失败 (%s): %s", path, e)
        return {}


def _read_hermes_env_key(key: str) -> str:
    """读 hermes 管理的 env key. 双查 os.environ + ~/.hermes/.env 文件.

    BL-PLUGIN-AUTH-FIX (7/27 鸿波): 为什么必须双查 —
      hermes `hermes_cli/config.py:load_env()` 只**返回 dict 不写 os.environ**.
      写 os.environ 只发生在 `/reload` 命令 (reload_env():8163) 或
      `set_env_value():8046`. 所以 plugin 光 os.environ.get() 可能拿不到.
      hermes 自己的 `get_env_value():8186` 就是双查 (先 os.environ 后 .env 文件),
      本函数语义跟它对齐.

      不 import hermes_cli.config — plugin 不该耦合 hermes 内部模块 (跨版本易断).
      Companion Rust 侧 `dream.rs:read_hermes_dev_env()` 也是直读 .env 文件, 同思路.

    parse 规则跟 dream.rs:262-278 对齐: 跳空行/注释, strip 引号.
    """
    val = os.environ.get(key, "").strip()
    if val:
        return val
    try:
        env_path = Path(os.path.expanduser("~")) / ".hermes" / ".env"
        if not env_path.exists():
            return ""
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if not line.startswith(f"{key}="):
                continue
            raw = line[len(key) + 1:].strip()
            # strip 成对引号 (跟 dream.rs strip_env_quotes 对齐)
            if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
                raw = raw[1:-1]
            return raw.strip()
    except OSError as e:
        logger.debug("catfish-memory 读 ~/.hermes/.env 失败 (%s): %s", key, e)
    return ""


_TOKEN_MISSING_MSG = (
    "catfish-memory: 拿不到 gateway 鉴权 token, %s skip. "
    "查顺序 ① ~/.hermes/.env OPENAI_API_KEY (hermes→gateway service token, "
    "跑 central/llm-gateway/refresh-jwt-hermes-env.sh 刷新) "
    "② env/. env CATFISH_INTERNAL_DEV_TOKEN ③ ~/.catfish/memory_plugin.yaml gateway.token"
)


def _log_token_missing(what: str) -> None:
    """BL-PLUGIN-AUTH-FIX (7/27): 统一 fail-loud. 老代码 4 处静默 return None,
    员工永远不知道 distill/wiki 从没跑过 (Dream Engine 9.7 天没跑就是这么来的).
    只记日志不弹 UI (后台任务失败不该打扰员工 · 鸿波 7/27 拍)."""
    logger.warning(_TOKEN_MISSING_MSG, what)


def _gateway_dev_token() -> str:
    """拿调 gateway 的鉴权 token. 没拿到返空 (caller skip + fail-loud log).

    BL-PLUGIN-AUTH-FIX (7/27 鸿波 catch "Dream Engine 9.7 天没跑"):

    ── 真因 ──
    老实现只读 CATFISH_INTERNAL_DEV_TOKEN. 但那是 **gateway 进程内 loopback 专用**
    (central/llm-gateway/.../auth/dev_token.py:11-15 明写 "启动时随机生成, 进程内存,
    重启即变, 不写 .env 文件"), 真 caller 只有 gateway 自己的 proactive.py /
    conversation_compressor.py. plugin 跑在 **hermes 进程** (另一进程 · 生产还跨机),
    永远拿不到 → _call_distill_llm 静默 return None → Dream Engine 自动蒸馏从没跑过.

    ── 正解 ──
    plugin 在 hermes 进程内, 调的又是 gateway, 就该复用 **hermes → gateway 这一跳**
    的凭证 = .env `OPENAI_API_KEY`:
      - aud=catfish-gateway · token_use=service · scope 含 chat.completions
      - 由 hermes-cli client_credentials 派发 (scope 经 identity 白名单校验, 可信)
      - refresh-jwt-hermes-env.sh 30 天刷新 (已有维护机制)
      - 生产分离时 OPENAI_BASE_URL 指中央 · 这 token 也是中央派发 · **天然跨机**

    链路: Companion ─[API_SERVER_KEY]→ hermes:8642 ─[OPENAI_API_KEY]→ gateway:8999 → LLM
                                          └── 本 plugin (in-process, 复用第 2 跳凭证)

    优先级:
      1. OPENAI_API_KEY (hermes→gateway service token · 生产正路)
      2. CATFISH_INTERNAL_DEV_TOKEN (本机 dev · gateway 同机且手工预设过时用)
      3. yaml gateway.token (P24 6/5 手工预设兜底)
    """
    token = _read_hermes_env_key("OPENAI_API_KEY")
    if token:
        return token
    token = _read_hermes_env_key("CATFISH_INTERNAL_DEV_TOKEN")
    if token:
        return token
    cfg = _load_plugin_config()
    if isinstance(cfg, dict):
        gw = cfg.get("gateway", {})
        if isinstance(gw, dict):
            return str(gw.get("token", "")).strip()
    return ""
