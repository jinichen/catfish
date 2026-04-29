"""扫描 catfish/skills/ 目录, 收集所有 SKILL.md 元数据.

# 为啥需要

鲶鱼里有越来越多的 skill (leadership-briefing / weekly-report / eis-export ...),
但**LLM 不知道这些 skill 存在** — 不会主动调. 表现是: 员工说"写汇报", LLM 退化
到"我给你写 Python 脚本你跑", 因为它看不到 catfish 暴露什么 skill.

修复: 启动时扫所有 SKILL.md, 把 skill 列表 (name + description + 路径) 注入到
LLM 的 system prompt + 暴露 `catfish_run_skill` 工具让 LLM 调.

# 跟 hermes-skill 的关系

hermes-skill 在 ~/.hermes/skills/ — 那是 LLM 自己写的运行时学习. 这个
catfish/skills/ 是**我们工程团队**写的合规 skill, 必跟客户 demo / 合同绑定的.

两套并存:
  - catfish/skills/         # 工程审定 skill, 客户跑必须合规 (公文 / 合同模板)
  - ~/.hermes/skills/       # LLM 自学 skill, 员工本机灵活 (员工自己 dogfood)

LLM 同时看到两套, 优先级 catfish/skills > ~/.hermes/skills (公文必须用合规版).

# 找 skills 目录的策略

  1. 如果 CATFISH_SKILLS_DIR env 设了 → 用它
  2. 否则从本文件位置向上找, 找含 'skills/' 的 catfish 项目 root
  3. 都失败 → 返回空列表 + warning, 不 fail (兼容老部署)
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("catfish.gateway.skills_loader")


@dataclass
class SkillMeta:
    """单个 skill 的元数据."""

    skill_path: str
    """相对路径, 例: 'department/leadership-briefing'. 用作 LLM 调 run_skill 的 key."""

    name: str
    """skill 名, 来自 SKILL.md frontmatter, 例: 'leadership-briefing'."""

    description: str
    """skill 描述, 来自 frontmatter description 字段."""

    skill_md_path: Path
    """绝对路径到 SKILL.md, 给 LLM 想看完整规范时回看."""

    script_py_path: Path | None
    """同目录下的 script.py (如果存在). tool-bridge run_skill 会执行它."""


# ── 找 skills root ──────────────────────────────────────────────


def _find_skills_root() -> Path | None:
    """
    1. 环境变量 CATFISH_SKILLS_DIR
    2. 从本文件向上搜 catfish 项目 root (含 skills/ 目录的)
    3. 失败返 None
    """
    env_path = os.environ.get("CATFISH_SKILLS_DIR")
    if env_path:
        p = Path(env_path).expanduser()
        if p.exists() and p.is_dir():
            return p
        logger.warning("CATFISH_SKILLS_DIR=%s 不存在或不是目录", env_path)

    # 从 __file__ 向上找 — gateway 装在 catfish/central/llm-gateway/src/
    # 上溯找含 catfish/skills/department/ 的位置
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "skills"
        if candidate.is_dir() and (candidate / "department").is_dir():
            return candidate
        # 也兼容 parent 自己就叫 catfish (开发模式)
        if (parent / "skills" / "department").is_dir():
            return parent / "skills"

    return None


# ── 解析 SKILL.md ───────────────────────────────────────────────

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.+?)\n---\s*\n?", re.DOTALL)


def _parse_skill_md(skill_md: Path) -> tuple[str, str] | None:
    """从 SKILL.md frontmatter 拿 (name, description). 拿不到返 None."""
    try:
        text = skill_md.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("无法读取 %s: %s", skill_md, e)
        return None

    match = _FRONTMATTER_RE.match(text)
    if not match:
        logger.debug("%s 没有 yaml frontmatter, 跳过", skill_md)
        return None

    try:
        front = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as e:
        logger.warning("%s frontmatter yaml 解析失败: %s", skill_md, e)
        return None

    name = front.get("name", "").strip()
    description = front.get("description", "").strip()
    if not name or not description:
        logger.debug("%s frontmatter 缺 name 或 description", skill_md)
        return None

    return name, description


# ── 主入口 ──────────────────────────────────────────────────────


def discover_skills() -> list[SkillMeta]:
    """扫描 skills 目录, 返回所有合法 skill 的元数据列表.

    每次调用重新扫 — skills 改了不需要重启 gateway, 客户 IT / 我们改 SKILL.md
    立即生效. 一次扫描成本: <50ms (skill 数量量级 10s).

    返回: 按 skill_path 字母序排序.
    """
    root = _find_skills_root()
    if root is None:
        logger.info(
            "skills_loader: 没找到 catfish skills 目录 (CATFISH_SKILLS_DIR 没设, "
            "也没扫到默认路径). LLM 不会看到 catfish skill 列表."
        )
        return []

    results: list[SkillMeta] = []
    # 递归扫所有 SKILL.md (排除 ~/.hermes/skills 风格)
    for skill_md in sorted(root.rglob("SKILL.md")):
        # 相对路径 (去掉 root prefix + SKILL.md 文件名)
        try:
            rel = skill_md.relative_to(root).parent
        except ValueError:
            continue
        skill_path = str(rel).replace(os.sep, "/")

        parsed = _parse_skill_md(skill_md)
        if parsed is None:
            continue
        name, description = parsed

        script_py = skill_md.parent / "script.py"
        results.append(
            SkillMeta(
                skill_path=skill_path,
                name=name,
                description=description,
                skill_md_path=skill_md,
                script_py_path=script_py if script_py.exists() else None,
            )
        )

    logger.info(
        "skills_loader: 发现 %d 个 skill (%s)",
        len(results),
        [s.skill_path for s in results],
    )
    return results


def format_skills_block(skills: list[SkillMeta]) -> str:
    """把 skill 列表渲染成 system prompt 用的 markdown 块.

    设计:
      - 每个 skill 一段, 标题 = skill_path, 正文 = description
      - 顶部点出"用 catfish_run_skill 调用"
      - 底部告诉 LLM 想看具体参数请看 SKILL.md

    输出长度: 单 skill ~200-500 字, 总长跟 skill 数量线性. 5 个 skill 内可控.
    """
    if not skills:
        return ""

    lines = [
        "",
        "## 🔧 catfish 工程审定 skill (铁律!! 用 catfish_run_skill 工具调用)",
        "",
        "### ⚠ 不可违反的硬规则",
        "",
        "1. **员工要求生成 .docx / .xlsx / .pptx / .pdf 文档**, 你**必须**先扫下方 "
        "skill 列表 — 任何一个 skill 的描述能 cover 该需求, **必须**调用 "
        "`catfish_run_skill`.",
        "2. **绝对禁止**回答\"我无法访问您的文件系统 / 我给您写个 Python 脚本 / "
        "请您复制并运行\". 你**有** `catfish_run_skill` 工具, 它就是用来生成文件的.",
        "3. **关键词模糊匹配**: 员工说\"汇报\" / \"汇报材料\" / \"请示\" / \"立项\" / "
        "\"决策事项\" / \"上报\" / \"呈报\" / \"工作总结\"等任何向**上级**提交的 .docx, "
        "都走 leadership-briefing skill — 即使员工没说\"用 skill\" / \"用模板\".",
        "4. **不会的话先 _help**: 不知道某 skill 参数怎么传时, "
        "`catfish_run_skill(skill_path='...', params={'_help': True})` 会返回完整 schema.",
        "5. **跟员工对话补全参数**: skill 需要的字段 (例: 5 段汇报的 background / problems "
        "/ solutions / next_steps) 员工没全部说? 主动**反问**他, 一次问 1-2 个, "
        "不要等员工一次性给齐.",
        "",
        "### 调用方式",
        "",
        "```",
        "catfish_run_skill(skill_path='<path>', params={...})",
        "```",
        "",
        "返回 `{ok, files, summary}`. files 字段里的路径 Companion 会自动渲染成 "
        "可点击 pill 给员工.",
        "",
        "### 当前可用 skill",
        "",
    ]
    for s in skills:
        lines.append(f"### `{s.skill_path}` — {s.name}")
        lines.append("")
        lines.append(s.description)
        lines.append("")
        lines.append(
            f"💡 不知道 params 时: 调用 `catfish_run_skill(skill_path='{s.skill_path}', "
            "params={'_help': True})` 拿到参数 schema."
        )
        lines.append("")
    return "\n".join(lines)


# ── 单元测试 hook ───────────────────────────────────────────────


def _reset_for_tests() -> None:
    """让测试可以替换 skills_root 环境."""
    pass


__all__ = [
    "SkillMeta",
    "discover_skills",
    "format_skills_block",
]
