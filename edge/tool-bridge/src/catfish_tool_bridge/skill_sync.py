"""P3.5.43 — sync ~/.catfish/skills/<ns>/<name>/ → ~/.hermes/skills/<name>/.

# 为啥要这个

hermes plugin loader 启动时扫 ~/.hermes/skills/ 装载 SKILL. 自动生成的 skill 落
~/.catfish/skills/<ns>/<name>/, 当年这个目录 hermes **不扫** (P3.5.43 audit 4 个
BLOCKER 之一). 现在用户是 catfish_install_proposal (员工同意小鲶的提议后落地)。
10/20: 原来还有录屏学习 (RecMode) 一路, 已整体拿掉; 文件从 recmode/ 挪出来。

# 修法

落档后调 sync_to_hermes: rsync 模式拷过去, 覆盖旧文件,
保留 hermes 仓库内手写 skill (按 slug 区分).

冲突处理 — slug 同名时:
  - hermes 仓库内有同名 skill (e.g. weekly-report 是仓库内手写的) → **拒绝覆盖**
    返错让 caller 知道, 让员工改名或手动 review
  - hermes 仓库内同名但 metadata 显示是鲶鱼自动生成的 (frontmatter author 含「鲶鱼」;
    老的录屏产物是「鲶鱼 RecMode」) → 覆盖 (重新生成的迭代)

不用 symlink — symlink 在某些 hermes 加载路径里 mtime 算错可能 cache 老内容,
直接 copy 简单可控.
"""
from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from .hermes_paths import hermes_home as _hermes_home

logger = logging.getLogger("catfish.tool_bridge.skill_sync")


HERMES_SKILLS_ROOT = _hermes_home() / "skills"


def _is_auto_generated(skill_md_path: Path) -> bool:
    """看 SKILL.md frontmatter 的 author 字段是不是鲶鱼自动生成的.

    跟 skill_format.SkillManifest 默认 author='鲶鱼' 对齐; 老录屏产物写的是
    '鲶鱼 RecMode', 员工机器上还有, 照样认。
    """
    if not skill_md_path.is_file():
        return False
    try:
        text = skill_md_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    # 抽 frontmatter
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        return False
    fm = m.group(1)
    # author 含「鲶鱼」(老录屏产物还有「RecMode」)
    for line in fm.splitlines():
        line = line.strip()
        if line.startswith("author:"):
            return "RecMode" in line or "鲶鱼" in line
    return False


class SkillSlugCollision(Exception):
    """hermes 仓库内有非自动生成的同名 skill, 拒绝覆盖."""


def sync_to_hermes(src_skill_dir: Path, slug: str,
                   hermes_root: Path | None = None) -> Path:
    """把 src_skill_dir (e.g. ~/.catfish/skills/personal/weekly-report/)
    内全部内容拷到 ~/.hermes/skills/<slug>/.

    Args:
      src_skill_dir: 源目录, 必须含 SKILL.md
      slug: 装到 hermes 的目录名 (一般 == skill name kebab-case)
      hermes_root: 默认 ~/.hermes/skills/

    Returns:
      目标目录路径 (~/.hermes/skills/<slug>/)

    Raises:
      FileNotFoundError: 源目录 / SKILL.md 不存在
      SkillSlugCollision: hermes 仓库内有同名 skill 但**不是**鲶鱼自动生成的
        (避免覆盖员工手写 skill / hermes 自带 skill)
    """
    if not src_skill_dir.is_dir():
        raise FileNotFoundError(f"源 skill 目录不存在: {src_skill_dir}")
    src_skill_md = src_skill_dir / "SKILL.md"
    if not src_skill_md.is_file():
        raise FileNotFoundError(f"源缺 SKILL.md: {src_skill_md}")

    if hermes_root is None:
        hermes_root = HERMES_SKILLS_ROOT

    target = hermes_root / slug

    # 冲突检测
    if target.exists():
        target_skill_md = target / "SKILL.md"
        if not _is_auto_generated(target_skill_md):
            raise SkillSlugCollision(
                f"~/.hermes/skills/{slug}/ 已存在且不是鲶鱼自动生成的 "
                f"(手写 skill / hermes 自带). 拒绝覆盖. 请换个 skill 名字."
            )
        # 是自动生成的 → 安全覆盖 (重新生成的迭代)
        logger.info("覆盖 hermes 内同 slug 的自动生成 skill: %s", target)
        shutil.rmtree(target)

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src_skill_dir, target)
    logger.info("sync_to_hermes: %s → %s", src_skill_dir, target)
    return target


__all__ = [
    "HERMES_SKILLS_ROOT",
    "SkillSlugCollision",
    "sync_to_hermes",
]
