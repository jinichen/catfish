"""日志 / 长期记忆文件只许追加, 不许改写 (11/10)。

# 为什么

11/09 16:59 小鲶记一条「纠正」时写了这么一行代码:

    open(p, "w", encoding="utf-8").write(open(p, encoding="utf-8").read().rstrip() + ...)

"w" 先把文件清空, 再去读 —— 读到的是空的, 整本 employee_journal.md (6 月以来 4000 来条) 只剩它刚写的那一条。
19:53 蒸馏发现日志「换过了」, 按设计全量重建, distilled_facts.md (长期记忆) 也只剩 1.3KB。
它这么写不是乱来: 自己写的技能 catfish-memory-ops 就教它「execute_code 追加写 employee_journal.md」、
「写坏了 s[:i] 截掉再重写」; 9/27 以来它直接改这个文件 30 多次, 那次只是写法错了。

# 做什么 (hermes pre_tool_call 钩子)

execute_code / terminal / write_file / patch 碰到下面这几个文件, 且是**改写**
(open 用 w / w+ / r+ / x、write_text、删除 / 改名 / 覆盖、shell 的单个 > 重定向、sed -i、tee 不带 -a …) → 拦下,
告诉它: 记日志调 memory(target='memory', kind='journal', content=…), 插件代写 (memory_enforce.append_journal);
记错了再记一条更正, 不改旧的。**追加** (open "a"、>>、tee -a) 和读取照常放行。
"""
from __future__ import annotations

import re
from typing import Any

PROTECTED = ("employee_journal.md", "distilled_facts.md", "memory_distill_state.json")

_PY_WRITE = re.compile(
    r"""open\([^)]*?,\s*(?:mode\s*=\s*)?['"](?:w|wb|w\+|r\+|x|wt)['"]"""   # 覆盖式打开
    r"""|\.write_text\(|\.write_bytes\("""
    r"""|os\.(?:remove|unlink|rename|replace|truncate)\(|\.unlink\(|\.truncate\(|\.rename\("""
    r"""|shutil\.(?:move|copy|copyfile|copy2)\(""",
)
_SH_WRITE = re.compile(
    r"""(?<![>&\d])>(?!>)\s*\S*(?:%s)"""            # 单个 > 重定向到受保护文件
    r"""|\bsed\s+(?:-[a-zA-Z]*i|--in-place)"""
    r"""|\b(?:rm|mv|cp|truncate|dd)\s"""
    r"""|\btee\s+(?!-a\b|--append\b)""" % "|".join(re.escape(p) for p in PROTECTED),
)

BLOCK_MESSAGE = (
    "⛔ employee_journal.md / distilled_facts.md 是小鲶的日志和长期记忆, **只能追加, 不能改写、删除或覆盖** "
    "(11/09 一次改写把整本日志清空了)。\n"
    "· 要记日志: 调 memory(action='add', target='memory', kind='journal', content='第一行写标题\\n后面写正文'), "
    "插件会替你追加进日志, 不用自己写文件。\n"
    "· 记错了: 再记一条, 标题以「更正:」开头, 说明哪条错了、正确的是什么; 不要删改旧条目。\n"
    "· 只是读日志 (open 默认的 'r'、grep、tail) 不受影响。"
)


def _texts(tool_name: str, args: Any) -> list[str]:
    if not isinstance(args, dict):
        return []
    keys = {"execute_code": ("code",), "terminal": ("command",), "write_file": ("path",), "patch": ("path", "file_path")}
    return [str(args.get(k) or "") for k in keys.get(tool_name, ())]


def touches_protected_destructively(tool_name: str, args: Any) -> bool:
    if tool_name in ("write_file", "patch"):
        return any(t.rstrip("/").endswith(PROTECTED) for t in _texts(tool_name, args))
    for text in _texts(tool_name, args):
        if not any(p in text for p in PROTECTED):
            continue
        if tool_name == "execute_code" and _PY_WRITE.search(text):
            return True
        if tool_name == "terminal" and _SH_WRITE.search(text):
            return True
        # shell 里内嵌 python -c "open(...,'w')" 也算
        if tool_name == "terminal" and _PY_WRITE.search(text):
            return True
    return False


def journal_guard_hook(*, tool_name: str = "", args: Any = None, task_id: str = "", **_: Any) -> dict[str, str] | None:
    """hermes pre_tool_call 钩子。出错放行 (不能因为钩子挂了让小鲶干不了活)。"""
    try:
        if tool_name not in ("execute_code", "terminal", "write_file", "patch"):
            return None
        if touches_protected_destructively(tool_name, args):
            return {"action": "block", "message": BLOCK_MESSAGE}
    except Exception:  # noqa: BLE001
        return None
    return None


__all__ = ["journal_guard_hook", "touches_protected_destructively", "PROTECTED", "BLOCK_MESSAGE"]
