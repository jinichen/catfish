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

    # ── 五一 sprint Day 2: Skill 全生命周期 4 步 ──

    version: str = "0.1.0"
    """SemVer 版本字符串, 例: '1.2.3'. 没填默认 '0.1.0'.

    用途:
    - audit log 记录调用时的版本, 便于追溯 (鸿波改 SKILL.md 后看哪个 session 用了旧版)
    - 客户 IT 审 skill 时按版本对照
    - Phase 2 Skills Hub 拉新 skill 时按版本兼容性检查
    """

    deprecated: bool = False
    """是否下线. 设 true 后:
    - format_skills_block 在 LLM 看到时显示 ⚠️ 警告 + 不推荐调用
    - catfish_run_skill 调用时 result 加 'deprecated_warning' 字段提示员工
    """

    deprecated_reason: str = ""
    """下线原因, 例: '改用 v2 的 leadership-briefing-strict' 或 '客户合规变更, 不再使用'.
    deprecated=true 时建议填, 让员工/LLM 知道为啥下线 + 替代方案."""

    # ── BL-SKILL-METADATA-DYNAMIC (5/15 鸿波 '半半的工作造成更大困恼') ──
    # skill 自己声明触发词 / 类型, skill_guard 不再硬编码每个 skill 的关键词.

    triggers: tuple[str, ...] = ()
    """触发关键词. 员工 user message 含任一 trigger → skill_guard 强制 agent 调本 skill.

    例: leadership-briefing 的 triggers = ("汇报", "请示", "立项", "上报", "呈报", ...)
        guizang-ppt-magazine 的 triggers = ("PPT", "幻灯片", "slides", "deck", "杂志风", ...)

    设计约束:
    - 至少 3 个词, 否则太容易漏判
    - 不要写"做" / "帮" 这种泛词, 会跟所有 skill 撞
    - 中英都列 (员工有时打英文)
    """

    kind: str = "procedural"
    """skill 类型, 影响 skill_guard 注入的铁律强度:

    - "procedural"   — script.py 自己跑出文件 (leadership-briefing / weekly-report).
                       agent 调 catfish_run_skill 就完事, 拿 files 字段给员工.
    - "instructional" — script.py 只返指令 + 模板路径, 真活由 agent 接力
                        (guizang-ppt-magazine 杂志风 PPT). agent 必须按返回的
                        post_steps 真发 read_file / write_file, 不能嘴炮.
    """


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


def _parse_skill_md(skill_md: Path) -> dict[str, Any] | None:
    """从 SKILL.md frontmatter 拿 {name, description, version, deprecated, deprecated_reason}.

    拿不到 (没 frontmatter / 缺 name / yaml 失败) 返 None.

    五一 sprint Day 2: 加 version/deprecated/deprecated_reason 字段解析,
    老 skill 没填的字段走 SkillMeta 默认值 (version=0.1.0, deprecated=False).
    """
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

    name = str(front.get("name", "")).strip()
    description = str(front.get("description", "")).strip()
    if not name or not description:
        logger.debug("%s frontmatter 缺 name 或 description", skill_md)
        return None

    # version: SemVer 字符串. 老 skill 没填默认 "0.1.0".
    version = str(front.get("version", "0.1.0")).strip() or "0.1.0"

    # deprecated: bool. 没填默认 False.
    deprecated_raw = front.get("deprecated", False)
    deprecated = bool(deprecated_raw) if deprecated_raw is not None else False

    deprecated_reason = str(front.get("deprecated_reason", "")).strip()

    # BL-SKILL-METADATA-DYNAMIC: triggers + kind
    # triggers 可以是 list 或 csv 字符串. 全部归一成 tuple[str, ...]
    raw_triggers = front.get("triggers", [])
    if isinstance(raw_triggers, str):
        # "PPT, 杂志风, slides" — 容错
        triggers = tuple(t.strip() for t in raw_triggers.split(",") if t.strip())
    elif isinstance(raw_triggers, list):
        triggers = tuple(str(t).strip() for t in raw_triggers if str(t).strip())
    else:
        triggers = ()

    kind = str(front.get("kind", "procedural")).strip().lower()
    if kind not in ("procedural", "instructional"):
        logger.warning(
            "%s frontmatter kind=%r 不合法, 回落 'procedural'", skill_md, kind,
        )
        kind = "procedural"

    return {
        "name": name,
        "description": description,
        "version": version,
        "deprecated": deprecated,
        "deprecated_reason": deprecated_reason,
        "triggers": triggers,
        "kind": kind,
    }


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

        script_py = skill_md.parent / "script.py"
        results.append(
            SkillMeta(
                skill_path=skill_path,
                name=parsed["name"],
                description=parsed["description"],
                skill_md_path=skill_md,
                script_py_path=script_py if script_py.exists() else None,
                version=parsed["version"],
                deprecated=parsed["deprecated"],
                deprecated_reason=parsed["deprecated_reason"],
                triggers=parsed["triggers"],
                kind=parsed["kind"],
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
        # 五一 sprint Day 2: 显示版本 + 下线警告
        version_tag = f" `v{s.version}`" if s.version != "0.1.0" else ""
        if s.deprecated:
            lines.append(f"### `{s.skill_path}` — {s.name}{version_tag}  ⚠️ DEPRECATED")
            lines.append("")
            if s.deprecated_reason:
                lines.append(f"**⚠️ 此 skill 已下线**: {s.deprecated_reason}")
                lines.append("")
                lines.append("除非员工明确要求, 否则不要调此 skill.")
            else:
                lines.append("**⚠️ 此 skill 已下线, 不推荐调用.**")
        else:
            lines.append(f"### `{s.skill_path}` — {s.name}{version_tag}")
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
