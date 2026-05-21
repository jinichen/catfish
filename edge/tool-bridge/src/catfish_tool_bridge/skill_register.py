"""skill_register — atomic 注册 ~/.catfish/skills/ 进 hermes config.yaml.

# 背景 (5/21 方案 1)

教学 freeze 产物默认落 `~/.catfish/skills/<namespace>/<name>/`, 不再自动 cp 进
`~/.hermes/skills/productivity/catfish-*/`. 但 hermes registry 默认只扫
`~/.hermes/skills/`, 我们的 skill 落到别处 hermes 看不到 → LLM 调不到.

# 解法

hermes 自带 `skills.external_dirs` 配置 (agent/skill_utils.py:279
`get_external_skills_dirs()`), 在 `~/.hermes/config.yaml` 写一段:

```yaml
skills:
  external_dirs:
    - ~/.catfish/skills/
```

→ hermes 启动后 (或 mtime 变化后) 自动把这条加进扫描路径, 跟自己 skill 一样
对待. **Curator 不扫 external_dirs**, 所以教学产物永不被 archive.

# 这模块只干一件事

`ensure_external_dir_registered(path)` 幂等检查 + 追加 path 进 config.yaml
external_dirs 列表. catfish 启动时调一次保证 hermes 看得到本机 skill.

# 失败处理

所有失败 silent log.warning, 不挂. 员工没装 hermes 也不挂 (config.yaml 不存
在跳过). 写盘失败 (权限 / 磁盘满) 跳过 — 下次启动还会重试.

# 设计要点

- atomic write (tmp + rename), 防半写坏 hermes config
- 幂等: external_dirs 已含此 path 不重复加
- 不动其它字段: 只读 ~/.hermes/config.yaml → 修改 skills.external_dirs →
  写回, 其它 yaml 字段原样保留 (yaml.safe_dump preserve insertion order)
- 不 force create ~/.hermes/config.yaml — 不存在就跳过, 让 hermes 自己 own 配置
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger("catfish.tool_bridge.skill_register")

#: catfish 教学产物本机路径 (5/21 方案 1)
LOCAL_SKILLS_ROOT = Path.home() / ".catfish" / "skills"

#: hermes config.yaml 标准位置
HERMES_CONFIG_PATH = Path.home() / ".hermes" / "config.yaml"


def ensure_local_skills_dir() -> Path:
    """保证 `~/.catfish/skills/` 存在. 返目录路径."""
    LOCAL_SKILLS_ROOT.mkdir(parents=True, exist_ok=True)
    return LOCAL_SKILLS_ROOT


def _atomic_write_yaml(path: Path, data: dict[str, Any]) -> None:
    """atomic write yaml: tmp 同目录 + rename."""
    import yaml  # noqa: PLC0415

    # tmp 必须同目录 (跨设备 rename 会失败)
    fd, tmp_path = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
        os.replace(tmp_path, path)
    except Exception:
        # 清掉残留 tmp
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise


def ensure_external_dir_registered(skill_dir: Path | None = None) -> dict[str, Any]:
    """幂等注册 skill_dir 进 hermes config.yaml skills.external_dirs.

    args:
      skill_dir: 要注册的目录. 默认 `~/.catfish/skills/`.

    返:
      {ok: bool, already_present: bool, action: 'noop' | 'appended' | 'created',
       error: str | None}

    幂等性:
      - external_dirs 已含此 path (字符串相等或 resolve 后相等) → action='noop'
      - external_dirs 不含 → 追加 → action='appended'
      - config.yaml 不存在 → 跳过 (action='skipped_no_config'), 不强造

    失败:
      - yaml import 失败 / 解析失败 / 写盘失败 → log.warning + 返 ok=False, 不 raise.
    """
    target = (skill_dir or LOCAL_SKILLS_ROOT).expanduser().resolve()

    if not HERMES_CONFIG_PATH.exists():
        logger.info(
            "skill_register: %s 不存在, 跳过注册 %s (hermes 没装?).",
            HERMES_CONFIG_PATH, target,
        )
        return {
            "ok": True,
            "already_present": False,
            "action": "skipped_no_config",
            "error": None,
        }

    try:
        import yaml  # noqa: PLC0415
    except ImportError as e:
        logger.warning("skill_register: yaml import 失败, 跳过: %s", e)
        return {"ok": False, "already_present": False, "action": "noop", "error": repr(e)}

    # 读
    try:
        with open(HERMES_CONFIG_PATH, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        if not isinstance(cfg, dict):
            logger.warning(
                "skill_register: %s 根不是 dict (%s), 跳过. 不动 hermes 配置.",
                HERMES_CONFIG_PATH, type(cfg).__name__,
            )
            return {
                "ok": False, "already_present": False, "action": "noop",
                "error": f"config.yaml root not dict: {type(cfg).__name__}",
            }
    except Exception as e:
        logger.warning("skill_register: 读 %s 失败: %s", HERMES_CONFIG_PATH, e)
        return {"ok": False, "already_present": False, "action": "noop", "error": repr(e)}

    skills_cfg = cfg.get("skills")
    if not isinstance(skills_cfg, dict):
        skills_cfg = {}
        cfg["skills"] = skills_cfg

    external_dirs = skills_cfg.get("external_dirs")
    if not isinstance(external_dirs, list):
        external_dirs = []
        skills_cfg["external_dirs"] = external_dirs

    # 幂等检查 — 字符串相等或 resolve 后相等
    target_str = str(target)
    for existing in external_dirs:
        if not isinstance(existing, str):
            continue
        try:
            if Path(existing).expanduser().resolve() == target:
                return {
                    "ok": True,
                    "already_present": True,
                    "action": "noop",
                    "error": None,
                }
        except Exception:
            # resolve 失败 (符号链坏了 / 权限) → 跳过这条, 继续看下一个
            continue
        if existing.strip() == target_str:
            return {
                "ok": True, "already_present": True, "action": "noop", "error": None,
            }

    # 追加
    external_dirs.append(target_str)
    skills_cfg["external_dirs"] = external_dirs
    cfg["skills"] = skills_cfg

    try:
        _atomic_write_yaml(HERMES_CONFIG_PATH, cfg)
    except Exception as e:
        logger.warning(
            "skill_register: 写 %s 失败 (%s), 没注册成功. 下次启动会重试.",
            HERMES_CONFIG_PATH, e,
        )
        return {"ok": False, "already_present": False, "action": "noop", "error": repr(e)}

    logger.info(
        "skill_register: 注册 %s 进 %s skills.external_dirs (mtime 变, hermes 自动重载缓存).",
        target, HERMES_CONFIG_PATH,
    )
    return {
        "ok": True,
        "already_present": False,
        "action": "appended",
        "error": None,
    }
