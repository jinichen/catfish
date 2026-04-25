"""把 hermes-agent 的 tools 全部 import 进来,触发 registry 自注册。

设计依赖 hermes-agent 的 tools/registry.py docstring:
    每个 tool 文件 import 时通过 module-level registry.register() 注册自己。
    `model_tools.py` 是聚合点 —— import 它就触发所有 tool 模块加载。

启动顺序:
    1. sys.path 加 hermes-agent 根目录
    2. import model_tools (触发链式 import + 注册)
    3. 现在 tools.registry.registry singleton 装满了所有 tool
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger("catfish.tool_bridge.bootstrap")


def find_hermes_agent_path() -> Path:
    """优先级:
    1. 环境变量 HERMES_AGENT_PATH 显式指向
    2. ~/.hermes/hermes-agent (默认 hermes 安装位置)
    3. 沿当前 cwd 向上找带 model_tools.py 的目录(开发场景)
    """
    if env := os.environ.get("HERMES_AGENT_PATH"):
        p = Path(env).expanduser().resolve()
        if (p / "model_tools.py").exists():
            return p
        raise RuntimeError(
            f"HERMES_AGENT_PATH 设置了 {p} 但 model_tools.py 不在那 — 路径错了？"
        )

    default = Path.home() / ".hermes" / "hermes-agent"
    if (default / "model_tools.py").exists():
        return default

    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if (parent / "model_tools.py").exists():
            return parent

    raise RuntimeError(
        "找不到 hermes-agent —— 设 HERMES_AGENT_PATH 或确保 "
        "~/.hermes/hermes-agent 存在"
    )


def bootstrap() -> "registry_module":  # type: ignore[name-defined]
    """import 完所有 tool 后返回 hermes 的 tools.registry 模块。"""
    hermes_root = find_hermes_agent_path()
    logger.info("hermes-agent path: %s", hermes_root)

    if str(hermes_root) not in sys.path:
        sys.path.insert(0, str(hermes_root))

    # 触发所有 tool 自注册
    # noqa: F401 —— 故意只 import 不引用,模块级副作用是关键
    import model_tools  # noqa: F401, PLC0415

    from tools import registry as registry_module  # noqa: PLC0415

    tool_count = len(registry_module.registry.get_all_tool_names())
    logger.info("loaded %d tools from hermes-agent", tool_count)
    return registry_module
