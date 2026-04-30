"""employee_journal — 跨 session 上下文档 2.

# 为啥需要 (跟档 1 配套)

档 1 (`inject_session_history`) 解决"模型知道有历史 session"的问题, 但模型只看到
session 元信息 (id / 时间 / 首条 message), **不知道每次具体讲了啥**. 真要"鲶鱼像
真实助手", 模型需要看到**总结过的关键决策、偏好、里程碑**.

# 设计

`~/.catfish/employee_journal.md` 是一个**追加式日记**, 由 session_summarizer 模块在
每次 session 结束/有重大进展时, 用 LLM 生成 1-2 段总结追加进去.

模型每次 chat 时, gateway 把整个 journal (受 MAX_BYTES 限制) 注入 system prompt
顶部. 模型每次推理都看到员工最近的工作总结、决策、偏好.

例:
```markdown
## 2026-04-30 13:45 - 资质管理周报
鸿波让我写本周周报. 协商 4 段格式 (合规/资质/安全/其它). 偏好简短 + 数字明确.
落款"陈鸿波".

## 2026-04-29 20:00 - 资质汇报第 18 版
鸿波要把 17 项缺失资质的请示事项改成"对标头部企业拉平"措辞.
最终决定: 投资决策走"需求确认先行 + 分步投入"策略.
```

# Token 限制

journal 文件最多 50KB ≈ 12K token. 超过自动从尾部截断 (保留最新). 长期看应该用
向量检索召回相关片段, 不是全文注入. 短期全文注入足够 demo.

# 跟档 1 关系

  identity → session_facts → stats_guard → skills_catalog → skill_guard →
  session_history (档 1, 元信息) → **employee_journal** (档 2, 内容总结) → multimodal → tool_capability

档 2 在档 1 之后 — 档 1 给"index", 档 2 给"content". 模型先看 index 知道有哪些
session, 再看 content 知道每个讲啥.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from pathlib import Path
from typing import Any

logger = logging.getLogger("catfish.gateway.employee_journal")

#: journal 文件路径
JOURNAL_PATH = Path.home() / ".catfish" / "employee_journal.md"

#: 文件最大字节数 (50KB ≈ 12K 中文 token, 全注入 system prompt 可控)
MAX_BYTES = 50_000


def journal_path() -> Path:
    """允许测试 monkeypatch."""
    return JOURNAL_PATH


def read_journal() -> str:
    """读 journal, 超过 MAX_BYTES 从**尾部**截断 (保留最新).

    异常 (文件不存在 / 读失败) → 返空字符串.
    """
    p = journal_path()
    if not p.exists():
        return ""
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("读 employee_journal.md 失败: %s", e)
        return ""

    # 从尾部截断保留最新 (按字符近似, 不完美但够用)
    encoded = text.encode("utf-8")
    if len(encoded) > MAX_BYTES:
        # 回退到字符级截断 (避免半个 utf-8 字符)
        text = text[-MAX_BYTES:]
        # 找下一个 ## 段开始, 避免半段
        idx = text.find("\n## ")
        if idx > 0:
            text = text[idx + 1:]
    return text


def append_to_journal(entry: str) -> None:
    """追加一段到 journal. entry 应该是完整的 markdown 段 (含 ## 标题).

    自动加换行分隔. 创建父目录.
    """
    p = journal_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    # 确保前后有换行
    entry = entry.strip() + "\n\n"
    with p.open("a", encoding="utf-8") as f:
        f.write(entry)
    logger.info("append_to_journal: 写入 %d 字节", len(entry.encode("utf-8")))


def inject_employee_journal(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """在最后 system message 末尾追加 employee_journal.

    journal 空 → 原样返. 幂等.
    """
    if not messages:
        return messages

    journal = read_journal()
    if not journal.strip():
        return messages

    block = (
        "\n\n## 📝 员工长期日记 (gateway 自动注入, ~/.catfish/employee_journal.md)\n\n"
        "下面是员工最近的工作总结、决策、偏好 (按时间倒序). 你**必须**通读这些, "
        "理解员工当前在做什么、偏好什么风格、做过哪些重要决策. 当员工提到\""
        "上次 / 之前 / 那个 X / 我们讨论过的\"时, 优先在这里找上下文. "
        "不要假装不知道. 不要让员工感觉你是 100 个素不相识的人轮流帮他.\n\n"
        f"{journal.strip()}\n"
    )

    last_system_idx = -1
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "system":
            last_system_idx = i
            break
    if last_system_idx < 0:
        return messages

    cur = messages[last_system_idx].get("content", "")
    if not isinstance(cur, str):
        return messages
    # 幂等
    if "员工长期日记" in cur:
        return messages

    out = deepcopy(messages)
    out[last_system_idx]["content"] = (
        out[last_system_idx]["content"].rstrip() + block
    )
    if logger.isEnabledFor(logging.INFO):
        logger.info(
            "inject_employee_journal: 注入 %d 字节", len(journal)
        )
    return out


__all__ = [
    "JOURNAL_PATH",
    "journal_path",
    "read_journal",
    "append_to_journal",
    "inject_employee_journal",
]
