"""journal CRUD 纯函数 — 跟 src-tauri/src/commands/journal.rs 同 regex / 同算法.

不依赖 IO. 单测用 in-memory string 跑全套.
Rust 端是 BL-JOURNAL-TODO-EDIT-CHAT Stage 1 (5/20) ship, 这里是 Stage 2 hermes 边路径,
两边算法对齐, 任一端改 regex 另一端要跟.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# ── 跟 Rust 端 regex 完全对齐 ────────────────────────────────────────────

# 未完成 checkbox: `- [ ] xxx`, 缩进 + bullet 任意
CHECKBOX_UNCHECKED_RE = re.compile(r"^(\s*[-*+]\s*)\[\s\](\s+.+)$")
# 已完成 / 任何 checkbox (mark / delete 时识别)
CHECKBOX_ANY_RE = re.compile(r"^\s*[-*+]\s*\[[ xX]\]\s+.+$")
# 行内 TODO 标记: TODO: / 待办: / TODO - / 待办 -
INLINE_TODO_RE = re.compile(r"(?i)(?:^|\s)(?:todo|待办)\s*[:：\-]\s*(.+?)\s*$")
# 段标题
SECTION_RE = re.compile(r"^##\s+(.+?)\s*$")


@dataclass
class JournalTodo:
    """journal_todos_fetch 返的单条 TODO. 跟 Rust JournalTodo struct 对齐."""

    text: str
    line: int  # 1-based
    source: str  # "checkbox" | "inline"
    section: str  # 所在段标题, 没有返空


# ── 抽 TODO ──────────────────────────────────────────────────────────────


def extract_todos(text: str) -> list[JournalTodo]:
    """扫 journal 文本抽未完成 TODO. 跟 Rust extract_todos 同行为.

    - `- [ ] xxx` 抽出来标 source=checkbox
    - 行内 `TODO: xxx` / `待办: xxx` 抽出来标 source=inline
    - 已完成 `- [x] xxx` 跳过
    - `- [x] TODO: xxx` 跳过 (避免双抽)
    """
    todos: list[JournalTodo] = []
    current_section = ""

    for idx, line in enumerate(text.splitlines()):
        lineno = idx + 1

        # 段标题
        m = SECTION_RE.match(line)
        if m:
            current_section = m.group(1).strip()
            continue

        # checkbox 未完成
        m = CHECKBOX_UNCHECKED_RE.match(line)
        if m:
            # m.group(2) 是 "[ ]" 之后那段, 去掉前导空白
            content = line[line.index("[ ]") + 3 :].strip()
            todos.append(
                JournalTodo(
                    text=content,
                    line=lineno,
                    source="checkbox",
                    section=current_section,
                )
            )
            continue

        # 行内 TODO — 排除已完成 checkbox 行 (避免 `- [x] TODO: xxx` 被抽)
        if "[x]" in line or "[X]" in line:
            continue
        m = INLINE_TODO_RE.search(line)
        if m:
            todos.append(
                JournalTodo(
                    text=m.group(1).strip(),
                    line=lineno,
                    source="inline",
                    section=current_section,
                )
            )

    return todos


# ── mark done ────────────────────────────────────────────────────────────


class JournalEditError(ValueError):
    """journal 编辑业务错误 (越界 / text_hint 不匹配 / 非 TODO 行).

    CLI 端 catch 这个出 exit code 2 + 友好错误信息.
    """


def mark_todo_done(content: str, line: int, text_hint: str) -> str:
    """改 `- [ ]` → `- [x]`. line 1-based.

    双重定位: line + text_hint 必须 substring 匹配该行, 不一致拒绝 (员工自己改过
    journal 行号偏移防误伤).

    Raises:
        JournalEditError: line 越界 / text_hint 不匹配 / 非未完成 checkbox 行
    """
    if line < 1:
        raise JournalEditError("line 必须 >= 1")

    lines = content.split("\n")
    if line > len(lines):
        raise JournalEditError(
            f"journal 只有 {len(lines)} 行, line {line} 越界"
        )

    line_str = lines[line - 1]
    if text_hint not in line_str:
        raise JournalEditError(
            f"text_hint 跟 line {line} 不匹配 "
            f"(journal 可能被改了, 重拉 list 再试). "
            f"该行内容: {line_str[:60]!r}"
        )

    m = CHECKBOX_UNCHECKED_RE.match(line_str)
    if not m:
        raise JournalEditError(
            f"line {line} 不是未完成 checkbox 行 "
            f"(要求 '- [ ] xxx'). 内容: {line_str[:60]!r}"
        )

    # group(1) = "[ ]" 前的 bullet 部分, group(2) = "[ ]" 后的内容部分
    new_line = f"{m.group(1)}[x]{m.group(2)}"
    lines[line - 1] = new_line
    return "\n".join(lines)


# ── delete ──────────────────────────────────────────────────────────────


def delete_todo(content: str, line: int, text_hint: str) -> tuple[str, str]:
    """删 TODO 整行. 返 (新内容, 被删的那行内容).

    白名单: 只允许删 checkbox 行或行内 TODO 标记行, 防 LLM 误删段标题 / 正文.

    Raises:
        JournalEditError: 越界 / text_hint 不匹配 / 非 TODO 行
    """
    if line < 1:
        raise JournalEditError("line 必须 >= 1")

    lines = content.split("\n")
    if line > len(lines):
        raise JournalEditError(
            f"journal 只有 {len(lines)} 行, line {line} 越界"
        )

    line_str = lines[line - 1]
    if text_hint not in line_str:
        raise JournalEditError(
            f"text_hint 跟 line {line} 不匹配, 重拉 list 再试. "
            f"该行: {line_str[:60]!r}"
        )

    is_checkbox = bool(CHECKBOX_ANY_RE.match(line_str))
    is_inline = bool(INLINE_TODO_RE.search(line_str))
    if not (is_checkbox or is_inline):
        raise JournalEditError(
            f"line {line} 不是 TODO 行 (不许删非 TODO 内容防误伤). "
            f"内容: {line_str[:60]!r}"
        )

    deleted = lines.pop(line - 1)
    return "\n".join(lines), deleted


# ── add ────────────────────────────────────────────────────────────────


def add_todo(
    content: str,
    text: str,
    section: Optional[str] = None,
    done: bool = False,
) -> str:
    """追加新 TODO 到 journal. section 给定就在该段尾插, 没给追加文末.

    格式: `- [ ] {text}` (未完成) 或 `- [x] {text}` (done=True, BL-CATFISH-TODO-SYNC
    v0.1.9, 5/20). 后者用于历史记录场景: catfish-todo-sync 收到 completed status
    但 journal 里没该 TODO 时 (LLM 没经 add 直接 complete), 补一条 `- [x]` 历史.

    section 不存在 → 文末新建该 section.

    **幂等性 (BL-CATFISH-TODO-SYNC v0.1.7, 5/20)**: 如果 journal 已有完全相同
    text 的 `- [ ] {text}` 或 `- [x] {text}` 行, 跳过不重复添加, 返原 content.
    这避免 catfish-todo-sync plugin 多次 sync 同一 todos array 导致重复.

    Args:
        content: 当前 journal 文本
        text: 要 add 的 TODO 文本
        section: 段标题, 不给追加文末
        done: True 写 `- [x] {text}`, False 写 `- [ ] {text}` (默认未完成)

    Returns:
        新 content (含新 todo). 已存在则返原 content.
    """
    text = text.strip()
    if not text:
        raise JournalEditError("text 不能为空")
    marker = "[x]" if done else "[ ]"
    new_todo = f"- {marker} {text}"

    # 幂等性检查: 如果 journal 已含同 text 的 - [ ] / - [x] 行, 跳过.
    # 兼容缩进 (\s* 在前) + 兼容 - / * / + 三种 markdown bullet.
    # 用 re 而非 in 是为了精确匹配整个 text 而非子串.
    # done=True 路径也用同一 pattern — 已存在 [ ] 或 [x] 都跳过, 防 v0.1.9 重复写历史.
    existing_pattern = re.compile(
        r"^\s*[-*+]\s*\[[ xX]\]\s+" + re.escape(text) + r"\s*$",
        re.MULTILINE,
    )
    if existing_pattern.search(content):
        # 已存在, 不重复添加, 返原 content
        return content

    if section and section.strip():
        section = section.strip()
        section_marker = f"## {section}"
        idx = content.find(section_marker)
        if idx >= 0:
            # 找下一个 ## 段或文件末尾作 section 结束
            after = idx + len(section_marker)
            next_section = content.find("\n## ", after)
            sec_end = next_section if next_section >= 0 else len(content)
            before = content[:sec_end]
            after_content = content[sec_end:]
            sep = "" if before.endswith("\n") else "\n"
            return f"{before}{sep}{new_todo}\n{after_content}"
        else:
            # section 不存在 → 文末新建
            sep = "" if (content.endswith("\n") or not content) else "\n"
            return f"{content}{sep}\n## {section}\n{new_todo}\n"

    # 没指定 section → 直接追加
    sep = "" if (content.endswith("\n") or not content) else "\n"
    return f"{content}{sep}{new_todo}\n"
