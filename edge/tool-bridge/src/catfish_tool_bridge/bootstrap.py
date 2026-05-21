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
    """import 完所有 tool 后返回 hermes 的 tools.registry 模块。

    BL-HERMES-014-LAZY (5/17 #77): hermes 0.14 #24515 [all] extras 缩水 +
    lazy-deps. import 错误信息加 0.14 诊断, 客户机升级失败时给清晰指引.
    """
    hermes_root = find_hermes_agent_path()
    logger.info("hermes-agent path: %s", hermes_root)

    if str(hermes_root) not in sys.path:
        sys.path.insert(0, str(hermes_root))

    # 触发所有 tool 自注册
    # noqa: F401 —— 故意只 import 不引用,模块级副作用是关键
    try:
        import model_tools  # noqa: F401, PLC0415
    except ImportError as e:
        raise RuntimeError(
            f"BL-HERMES-014-LAZY: import model_tools 失败 (hermes-agent root={hermes_root}). "
            f"诊断步骤:\n"
            f"  1. cd {hermes_root} && ls model_tools.py    # 应在根目录\n"
            f"  2. cd {hermes_root} && git log --oneline -1  # 当前 hermes 版本\n"
            f"  3. cd {hermes_root} && pip install -e .     # 重装 (0.14 lazy-deps)\n"
            f"原错: {e}"
        ) from e

    try:
        from tools import registry as registry_module  # noqa: PLC0415
    except ImportError as e:
        raise RuntimeError(
            f"BL-HERMES-014-LAZY: import tools.registry 失败 "
            f"(hermes-agent root={hermes_root}). 先 `pip install -e .` 重装. 原错: {e}"
        ) from e

    tool_count = len(registry_module.registry.get_all_tool_names())
    logger.info("loaded %d tools from hermes-agent", tool_count)

    # 5/21 方案 1: 启动时幂等注册 ~/.catfish/skills/ 进 hermes external_dirs.
    # 让 hermes registry 自动扫到教学产物路径, 不需要 freeze 后 cp 到 hermes 目录.
    # 失败 silent log, 不阻塞 tool-bridge 启动 (hermes 没装 / config 损坏 / 写盘失败).
    try:
        from . import skill_register  # noqa: PLC0415
        result = skill_register.ensure_external_dir_registered()
        if result.get("action") == "appended":
            logger.info(
                "skill_register: 首次注册 %s 进 hermes config.yaml external_dirs.",
                skill_register.LOCAL_SKILLS_ROOT,
            )
        elif result.get("action") == "noop" and result.get("already_present"):
            logger.debug("skill_register: external_dirs 已含 %s, noop.", skill_register.LOCAL_SKILLS_ROOT)
        elif result.get("action") == "skipped_no_config":
            logger.info("skill_register: ~/.hermes/config.yaml 不存在, 跳过 external_dirs 注册.")
        # 同时保证目录存在 (即使没注册成功, 教学 freeze 第一次也不挂)
        skill_register.ensure_local_skills_dir()
    except Exception as e:
        logger.warning("skill_register: 启动时注册失败, 不阻塞 tool-bridge: %s", e)

    return registry_module
