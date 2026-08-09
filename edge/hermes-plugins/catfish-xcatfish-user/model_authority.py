"""哪个模型说了算 —— **picker 是唯一真源** (P46, 8/9 鸿波拍板).

# 为什么要有这个模块

8/9 实撞: 员工在工作台选了 deepseek, 同一次早安页刷新里, 一部分请求走 deepseek
(成功), 另一部分走 qwen (配额已耗尽 → 429 → 重试三次 → 早安页出不来)。

查到根因在 hermes 的 **会话级模型 override**:

    ~/.hermes/state.db  sessions.model
      api-b6bcbf8a419068fa   catfish-public-qwen-flash   ← 就它一个
      其余 11 个 api-* 会话   catfish-public-deepseek-flash

`api_server.py:2410` 的 docstring 写着这是有意的:

    a user-issued ``/model`` always wins over static config

语义本身讲得通 —— 员工临时切模型不该被全局配置冲掉。但在 catfish 这边有两个
前提不成立:

  1. **员工不知道这个会话存在。** session_key 是
     `sha256(system_prompt + 第一条 user message)[:16]` (api_server.py:1231),
     早安页每次刷新命中同一行。员工看不到、也无从清除。
  2. **override 没有失效条件。** 模型停用、配额耗尽、员工换 picker —— 都不解除。
     一次写入, 永久生效。

结果就是"同一个员工、同一个界面、同一次刷新, 不同请求用不同模型, 而且没有任何
地方显示这件事"。鸿波的判断: **在要求确定性的环境里这不可接受**, 直接砍掉。

# 规则 (就三条, 按顺序)

  1. 请求体里显式带的 model  —— Companion picker 就是这么传的, 也是员工当下的
     明确意图, 最高优先级
  2. `~/.catfish/picker_state.json` 的 chat_model —— 员工上一次的选择, 唯一真源
  3. 都没有 → 不动 (让 hermes 自己那套决定; 我们不猜)

**会话持久化的 model 一律不参与。** 它仍然会被 hermes 写进 state.db, 我们不去
拦写入 (那要动 hermes 的持久化路径, 面大且没必要) —— 而是在 agent 建好之后
无条件覆盖 `agent.model`, 让那个值**变成惰性的**。

# 为什么单独一个文件

判据本身要能单测 (纯函数, 不碰 hermes), 而 plugin.py 已经 4300+ 行。这里只放
"谁说了算"这一个决定; 什么时候调、怎么接进 hermes, 归 plugin.py 的 P11。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger("catfish.xcatfish_user.model_authority")

#: picker 状态文件 —— Companion 写, 我们只读。
PICKER_STATE_PATH = Path.home() / ".catfish" / "picker_state.json"

#: hermes config.yaml 里 `model.default` 的静态占位符。
#:
#: 它不是真模型名 —— gateway 收到后从 roles.yaml 的 chat_default 动态解析
#: (app.py:2864 BL-CATFISH-AUTO-ROUTE)。所以它**不能**算"员工显式选了模型",
#: 否则微信那条路 (员工没 picker, hermes 恒发 catfish-auto) 会把 picker 挡住。
AUTO_SENTINEL = "catfish-auto"


def read_picker_model(path: Path | None = None) -> str:
    """读 picker_state.json 的 chat_model; 读不到返空串。**永不抛。**

    跟 plugin.py 的 `_read_catfish_picker_model` 同款 ABI (catfish-memory plugin
    也有一份 —— 三个 plugin 独立装载不能 cross import, 只能各抄一份最简版)。
    """
    p = path or PICKER_STATE_PATH
    try:
        if not p.exists():
            return ""
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as e:
        logger.debug("读 picker_state 失败, 当作没配: %s", e)
        return ""
    if not isinstance(data, dict):
        return ""
    model = data.get("chat_model", "")
    return model.strip() if isinstance(model, str) else ""


def decide_model(
    *,
    request_model: str | None,
    picker_model: str | None = None,
) -> str:
    """返回这次调用**应该**用的模型名; 返空串表示"我们不表态, 按 hermes 的来"。

    Args:
        request_model: 请求体里显式带的 model (Companion picker 传的那个)。
            `catfish-auto` 不算显式 —— 它是 hermes config.yaml 里的静态占位符
            (gateway 收到后从 roles.yaml chat_default 解析), 不是员工的选择。
        picker_model: 覆盖 picker 读取结果, 给测试用; None = 现读文件。

    **不接受 session_model 参数** —— 这是这个模块存在的全部意义。想传都传不进来。
    """
    explicit = (request_model or "").strip()
    if explicit and explicit.lower() != AUTO_SENTINEL:
        return explicit

    picked = picker_model if picker_model is not None else read_picker_model()
    return (picked or "").strip()
