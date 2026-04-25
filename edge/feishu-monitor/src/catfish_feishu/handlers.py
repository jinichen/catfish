"""三种处理策略：macOS 通知 / Hermes inbox / 自动草稿。

设计原则：
    - 每个 handler 独立函数，失败只影响自己不影响别的
    - 所有 handler 都是幂等可重入的（同一消息 ID 触发两次不会重复动作）
    - 消息内容不写到 log / pid 文件等持久化位置，只进 inbox/drafts 目录（员工视角）
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

import httpx

from .config import DRAFTS_DIR, INBOX_DIR, DraftConfig, HandlerConfig
from .relevance import RelevanceVerdict, sanitize_for_log

logger = logging.getLogger("catfish.feishu.handlers")


# ------------------------------------------------------------------
# 工具
# ------------------------------------------------------------------


def _message_id(msg: dict[str, Any]) -> str:
    """从消息内容派生一个稳定 ID，用于去重。"""
    raw = "|".join([
        str(msg.get("conversation") or ""),
        str(msg.get("sender") or ""),
        str(msg.get("timestamp") or ""),
        (msg.get("text") or "")[:200],
    ])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]  # noqa: S324


def _already_handled(msg_id: str, marker_dir: Path) -> bool:
    """用 marker 文件做去重，同一个消息只处理一次。"""
    marker = marker_dir / f".handled-{msg_id}"
    return marker.exists()


def _mark_handled(msg_id: str, marker_dir: Path) -> None:
    marker_dir.mkdir(parents=True, exist_ok=True)
    marker = marker_dir / f".handled-{msg_id}"
    try:
        marker.touch(exist_ok=True)
    except OSError as e:
        logger.debug("mark_handled failed: %s", e)


# ------------------------------------------------------------------
# 1. macOS 通知
# ------------------------------------------------------------------


def notify_macos(msg: dict[str, Any], verdict: RelevanceVerdict) -> bool:
    """用 osascript 弹桌面通知。通知内容刻意简短，避免领导名字/原文在通知中心截图里泄漏。"""
    sender = msg.get("sender") or "某人"
    conversation = msg.get("conversation") or "飞书"
    title = f"🐟 {conversation}"
    subtitle = f"{sender} 可能在找你"
    # 通知文本用 verdict.reason 而不是原文，避免敏感内容出现在通知中心
    body = verdict.reason

    # osascript display notification 的字符串要转义
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')

    script = (
        f'display notification "{esc(body)}" '
        f'with title "{esc(title)}" '
        f'subtitle "{esc(subtitle)}" '
        f'sound name "Ping"'
    )
    try:
        subprocess.run(  # noqa: S603
            ["osascript", "-e", script],
            check=False,
            capture_output=True,
            timeout=5,
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning("osascript 通知失败：%s", e)
        return False


# ------------------------------------------------------------------
# 2. Hermes inbox（写到 ~/.catfish/feishu-inbox/）
# ------------------------------------------------------------------


def write_to_inbox(msg: dict[str, Any], verdict: RelevanceVerdict) -> Optional[Path]:
    """把相关消息结构化写到 inbox 目录。

    文件名：YYYYMMDD-HHMMSS-msgid.json
    格式：JSON 单条记录，便于 Hermes skill 后续读取聚合。
    """
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    msg_id = _message_id(msg)
    if _already_handled(msg_id, INBOX_DIR):
        return None

    ts = int(msg.get("timestamp") or time.time())
    fname = time.strftime("%Y%m%d-%H%M%S", time.localtime(ts)) + f"-{msg_id}.json"
    path = INBOX_DIR / fname

    record = {
        "id": msg_id,
        "received_at": int(time.time()),
        "conversation": msg.get("conversation"),
        "sender": msg.get("sender"),
        "is_dm": bool(msg.get("is_dm", False)),
        "timestamp": ts,
        "text": msg.get("text") or "",
        "relevance": {
            "level": verdict.level.value,
            "matched": verdict.matched,
            "reason": verdict.reason,
        },
        "status": "new",  # new | read | archived
    }
    try:
        path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _mark_handled(msg_id, INBOX_DIR)
        logger.info(
            "inbox 写入：%s  sender=%s  level=%s",
            fname, msg.get("sender"), verdict.level.value,
        )
        return path
    except OSError as e:
        logger.warning("inbox 写入失败：%s", e)
        return None


# ------------------------------------------------------------------
# 3. 自动草稿（调 gateway 生成回复建议）
# ------------------------------------------------------------------


_DRAFT_SYSTEM_PROMPT = """你是员工本人的飞书回复助手，不是 Bot。根据下面这条别人发来的消息，
给员工起草一条回复。要求：

1. 用员工第一人称（"我"），不要说"您好，我是小鲶"之类暴露 AI 身份
2. 语气自然，避免套话和客气话泛滥
3. 不编造事实。如果不确定答案就写"这个我核实一下"或"等我查下回你"
4. 长度控制在 {max_length} 字内
5. 风格：{tone}

只输出草稿文本，不要加引号、不要加"以下是草稿"之类的前缀。
"""


def _render_draft_prompt(msg: dict[str, Any], draft_cfg: DraftConfig) -> tuple[str, str]:
    """构造 system + user prompt。"""
    system = _DRAFT_SYSTEM_PROMPT.format(
        max_length=draft_cfg.max_length,
        tone=draft_cfg.tone,
    )
    conv = msg.get("conversation") or "飞书"
    sender = msg.get("sender") or "某人"
    is_dm = msg.get("is_dm", False)
    user = (
        f"场景：{'DM' if is_dm else '群'} - {conv}\n"
        f"发送者：{sender}\n"
        f"消息原文：\n{msg.get('text') or ''}"
    )
    return system, user


def generate_draft(
    msg: dict[str, Any],
    verdict: RelevanceVerdict,  # noqa: ARG001 -- 接口对称，未来可能用到
    draft_cfg: DraftConfig,
    gateway_token: Optional[str] = None,
) -> Optional[Path]:
    """调 catfish gateway 生成回复草稿，存到 ~/.catfish/feishu-drafts/。"""
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    msg_id = _message_id(msg)
    if _already_handled(msg_id, DRAFTS_DIR):
        return DRAFTS_DIR / f"{msg_id}.txt"

    system, user = _render_draft_prompt(msg, draft_cfg)

    headers = {"Content-Type": "application/json"}
    token = gateway_token or os.environ.get("CATFISH_DEV_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    payload = {
        "model": draft_cfg.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": draft_cfg.max_length * 2,  # 给点 buffer，token != 字
        "temperature": 0.7,
    }

    try:
        resp = httpx.post(
            f"{draft_cfg.gateway_url}/v1/chat/completions",
            json=payload,
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        draft_text = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            .strip()
        )
    except (httpx.HTTPError, ValueError, KeyError) as e:
        logger.warning(
            "草稿生成失败 msg_id=%s preview=%r err=%s",
            msg_id, sanitize_for_log(msg.get("text") or ""), e,
        )
        return None

    if not draft_text:
        return None

    path = DRAFTS_DIR / f"{msg_id}.txt"
    meta_path = DRAFTS_DIR / f"{msg_id}.meta.json"
    try:
        path.write_text(draft_text, encoding="utf-8")
        meta_path.write_text(
            json.dumps(
                {
                    "id": msg_id,
                    "generated_at": int(time.time()),
                    "conversation": msg.get("conversation"),
                    "sender": msg.get("sender"),
                    "original_preview": sanitize_for_log(msg.get("text") or "", 80),
                    "model": draft_cfg.model,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        _mark_handled(msg_id, DRAFTS_DIR)
        logger.info(
            "草稿已生成：%s  sender=%s  len=%d",
            path.name, msg.get("sender"), len(draft_text),
        )
        return path
    except OSError as e:
        logger.warning("草稿落盘失败：%s", e)
        return None


# ------------------------------------------------------------------
# 总分发
# ------------------------------------------------------------------


def dispatch(
    msg: dict[str, Any],
    verdict: RelevanceVerdict,
    handler_cfg: HandlerConfig,
    draft_cfg: DraftConfig,
) -> dict[str, Any]:
    """按 verdict 和配置调度三个 handler，返回每个 handler 的结果。"""
    results: dict[str, Any] = {
        "level": verdict.level.value,
        "notified": False,
        "inbox_path": None,
        "draft_path": None,
    }

    if verdict.level.value == "none":
        return results

    if verdict.should_notify and handler_cfg.desktop_notification:
        results["notified"] = notify_macos(msg, verdict)

    if verdict.should_inbox and handler_cfg.hermes_inbox:
        p = write_to_inbox(msg, verdict)
        results["inbox_path"] = str(p) if p else None

    if verdict.should_draft and handler_cfg.auto_draft:
        p = generate_draft(msg, verdict, draft_cfg)
        results["draft_path"] = str(p) if p else None

    return results
