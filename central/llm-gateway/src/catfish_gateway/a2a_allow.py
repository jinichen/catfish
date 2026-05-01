"""Plan D · ALLOW.md 解析 + 隐私拦截 — 五一 sprint Day 5 (BL-M5.1).

# 设计 (PLAN-D-PROTOCOL.md § 4)

`~/.catfish/ALLOW.md` 员工自己写, 用关键词 / 正则匹配, **不用 LLM 判断** (稳定 + 可审 + 快).

## 格式

```markdown
# 我允许其他鲶鱼问我什么

## 工作公开内容
- 项目 X 进展
- 周报

## 工作偏好 (限同部门)
allow_to: department=研发部
- 我对什么技术感兴趣
- 工作时段

## 限定 purpose
allow_purpose: 周报
- 上周做了什么

## 显式拒绝
deny:
- 薪资
- 个人财务
- 私人日程
```

## 匹配逻辑

1. **deny 段优先** (高强度拒绝, allow 段绕不过)
2. **allow 段** 看 from_sub / purpose 限制是否符合 + 关键词命中
3. 默认 **DENY** (没匹配到 allow)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("catfish.gateway.a2a_allow")


def _allow_md_path() -> Path:
    """ALLOW.md 路径. 默认 ~/.catfish/ALLOW.md.

    可通过 env CATFISH_ALLOW_PATH override (单机 mock 跑 alice/bob 各自 ALLOW.md).
    """
    import os
    custom = os.environ.get("CATFISH_ALLOW_PATH")
    if custom:
        return Path(custom).expanduser()
    return Path.home() / ".catfish" / "ALLOW.md"


@dataclass
class AllowSection:
    """ALLOW.md 一个段 (## 标题 + 限制 + 规则)."""

    title: str  # 段标题, 例 "工作公开内容"
    allow_to: dict[str, str] = field(default_factory=dict)  # 例 {"department": "研发部"}
    allow_purpose: str = ""  # 例 "周报", 空 = 任意 purpose
    rules: list[str] = field(default_factory=list)  # 关键词列表, 例 ["项目 X 进展", "周报"]


@dataclass
class DenySection:
    """显式拒绝 (deny: 段)."""

    rules: list[str] = field(default_factory=list)


@dataclass
class AllowConfig:
    """完整解析后的 ALLOW.md 配置."""

    allow_sections: list[AllowSection] = field(default_factory=list)
    deny_section: DenySection = field(default_factory=DenySection)
    raw_text: str = ""


def parse_allow_md(text: str) -> AllowConfig:
    """解析 ALLOW.md 文本.

    极简语法 (不用 yaml / markdown-parser, 防依赖):
    - `## <段标题>` 开新段
    - `allow_to: key=value[, key2=value2]` 限制 sub 属性
    - `allow_purpose: <purpose>` 限制 purpose
    - `- <关键词>` 规则一行一个 (该段)
    - `deny:` 开 deny 段, 后面 `- <关键词>` 是显式拒绝
    """
    config = AllowConfig(raw_text=text)
    current: AllowSection | DenySection | None = None
    in_deny = False

    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line or line.startswith("#") and not line.startswith("##"):
            # 顶级 # (#1 个) 标题跳过, ## 开头进入 section
            if line.startswith("#") and not line.startswith("##"):
                continue
            continue

        # ## <标题>
        if line.startswith("##"):
            title = line.lstrip("#").strip()
            if title.lower().startswith("显式拒绝") or title.lower().startswith("deny"):
                # 这一段是 deny
                in_deny = True
                current = config.deny_section
            else:
                in_deny = False
                section = AllowSection(title=title)
                config.allow_sections.append(section)
                current = section
            continue

        # `allow_to: department=研发部, role=manager`
        if line.lower().startswith("allow_to:") and isinstance(current, AllowSection):
            constraint = line[len("allow_to:"):].strip()
            for pair in constraint.split(","):
                pair = pair.strip()
                if "=" in pair:
                    k, _, v = pair.partition("=")
                    current.allow_to[k.strip()] = v.strip()
            continue

        # `allow_purpose: 周报`
        if line.lower().startswith("allow_purpose:") and isinstance(current, AllowSection):
            current.allow_purpose = line[len("allow_purpose:"):].strip()
            continue

        # `deny:` 开 deny 段
        if line.lower() == "deny:":
            in_deny = True
            current = config.deny_section
            continue

        # `- <关键词>`
        if line.startswith("-") or line.startswith("•"):
            keyword = line.lstrip("-•").strip()
            if not keyword:
                continue
            if isinstance(current, DenySection):
                current.rules.append(keyword)
            elif isinstance(current, AllowSection):
                current.rules.append(keyword)

    return config


def _matches_keyword(question: str, keyword: str) -> bool:
    """检查 question 是否包含 keyword. 简单子串匹配, 大小写不敏感."""
    return keyword.lower() in question.lower()


def _matches_constraint(constraint: dict[str, str], from_attrs: dict[str, str]) -> bool:
    """检查 from 端属性是否满足 allow_to 限制."""
    for k, v in constraint.items():
        if from_attrs.get(k, "").strip() != v.strip():
            return False
    return True


def check_allow(
    question: str,
    config: AllowConfig,
    from_sub: str = "",
    from_attrs: dict[str, str] | None = None,
    purpose: str = "",
) -> tuple[bool, str]:
    """检查 question 是否被 B 端 ALLOW.md 允许.

    返回 (allowed, reason).

    流程:
    1. deny 段优先扫, 命中拒
    2. allow 段扫, 看 allow_to / allow_purpose 限制 + 关键词
    3. 默认拒
    """
    from_attrs = from_attrs or {}

    # 1. deny 优先
    for kw in config.deny_section.rules:
        if _matches_keyword(question, kw):
            return False, f"matched deny: {kw}"

    # 2. allow 段
    for section in config.allow_sections:
        # allow_to 限制不符 → 跳
        if section.allow_to and not _matches_constraint(section.allow_to, from_attrs):
            continue
        # allow_purpose 限制不符 → 跳
        if section.allow_purpose and section.allow_purpose != purpose:
            continue
        # 关键词匹配
        for kw in section.rules:
            if _matches_keyword(question, kw):
                return True, f"matched allow: {section.title} / {kw}"

    # 3. 默认拒
    return False, "no allow rule matched (default DENY)"


def load_allow_config() -> AllowConfig:
    """从 ~/.catfish/ALLOW.md 加载. 文件不存在返空 config (默认全拒)."""
    path = _allow_md_path()
    if not path.exists():
        logger.info("ALLOW.md 不存在 (%s), Plan D A2A 默认全拒", path)
        return AllowConfig()
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("ALLOW.md 读取失败 (%s): %s", path, e)
        return AllowConfig()
    return parse_allow_md(text)


__all__ = [
    "AllowConfig",
    "AllowSection",
    "DenySection",
    "parse_allow_md",
    "check_allow",
    "load_allow_config",
]
