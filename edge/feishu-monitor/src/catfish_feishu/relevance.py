"""消息相关性判定。

三档结果：
    STRONG  → 弹桌面通知 + 写 inbox + 生成草稿（员工必须看到）
    SOFT    → 只写 inbox（员工打开 Hermes 时能看到，不实时打扰）
    NONE    → 丢弃，不处理

判定逻辑故意简单透明，避免"AI 拍脑袋说跟你有关"的不可解释性。
未来 V2 再加语义匹配（embedding 相似度），但第一版坚持关键词。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .config import RelevanceConfig


class Relevance(str, Enum):
    STRONG = "strong"
    SOFT = "soft"
    NONE = "none"


@dataclass
class RelevanceVerdict:
    level: Relevance
    matched: list[str]       # 命中的关键词
    reason: str              # 人类可读的解释，用于 log 和员工审计

    @property
    def should_notify(self) -> bool:
        return self.level == Relevance.STRONG

    @property
    def should_inbox(self) -> bool:
        return self.level in (Relevance.STRONG, Relevance.SOFT)

    @property
    def should_draft(self) -> bool:
        return self.level == Relevance.STRONG


def judge(
    message_text: str,
    sender: str,
    is_direct_message: bool,
    cfg: RelevanceConfig,
) -> RelevanceVerdict:
    """对一条消息判断相关性档位。

    参数：
        message_text: 消息原文
        sender: 发送者名（用来判断是不是领导/同事）
        is_direct_message: 是否 DM（而不是群）
        cfg: 关键词配置

    返回：RelevanceVerdict，含档位 + 命中原因。
    """
    if not message_text or not message_text.strip():
        return RelevanceVerdict(Relevance.NONE, [], "空消息")

    # DM 永远强相关（别人专门给你发的不可能不相关）
    if is_direct_message:
        return RelevanceVerdict(
            Relevance.STRONG,
            [f"DM from {sender}"],
            f"{sender} 私聊消息 (DM)",
        )

    text_lower = message_text.lower()

    # 1. 强相关：@ 了员工本人
    for mention in cfg.strong_mentions:
        if mention and mention in message_text:
            return RelevanceVerdict(
                Relevance.STRONG,
                [mention],
                f"消息中 @ 了你（{mention}）",
            )

    # 2. 强相关：提到员工姓名（裸名，不需要 @）
    name_hits = [n for n in cfg.strong_names if n and n in message_text]
    if name_hits:
        return RelevanceVerdict(
            Relevance.STRONG,
            name_hits,
            f"消息提到你的姓名：{', '.join(name_hits)}",
        )

    # 3. 弱相关：项目关键词
    project_hits = [p for p in cfg.soft_projects if p and p.lower() in text_lower]
    if project_hits:
        return RelevanceVerdict(
            Relevance.SOFT,
            project_hits,
            f"提到你负责的项目：{', '.join(project_hits)}",
        )

    # 4. 弱相关：系统/技术关键词
    system_hits = [s for s in cfg.soft_systems if s and s.lower() in text_lower]
    if system_hits:
        return RelevanceVerdict(
            Relevance.SOFT,
            system_hits,
            f"提到你负责的系统：{', '.join(system_hits)}",
        )

    # 5. 弱相关：同事名被提到
    people_hits = [p for p in cfg.soft_people if p and p in message_text]
    if people_hits:
        return RelevanceVerdict(
            Relevance.SOFT,
            people_hits,
            f"提到你关注的同事：{', '.join(people_hits)}",
        )

    return RelevanceVerdict(Relevance.NONE, [], "未命中任何关键词")


def judge_debounced(
    messages: list[dict],
    cfg: RelevanceConfig,
) -> list[tuple[dict, RelevanceVerdict]]:
    """批量判断（去抖窗口内）。

    保留原顺序，每条 message 附加 verdict。调用方决定只处理 STRONG+SOFT 还是全部。
    """
    out: list[tuple[dict, RelevanceVerdict]] = []
    for msg in messages:
        verdict = judge(
            message_text=msg.get("text", ""),
            sender=msg.get("sender", ""),
            is_direct_message=bool(msg.get("is_dm", False)),
            cfg=cfg,
        )
        out.append((msg, verdict))
    return out


def sanitize_for_log(text: str, max_chars: int = 40) -> str:
    """日志里记消息时用：截断 + 去换行，避免日志暴涨 + 泄露敏感信息。

    真实消息内容**永远不写进 log**，只在内存里处理。这个函数只用于 debug 辅助。
    """
    if not text:
        return ""
    t = text.replace("\n", " ").replace("\r", " ").strip()
    if len(t) <= max_chars:
        return t
    return t[:max_chars] + "..."
