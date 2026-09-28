"""最近一次存进草稿箱的那封 —— Companion 据此在对话结束后跳到草稿箱 (9/29)。

# 为什么由这里记, 不让 Companion 去猜

9/27 的做法是 Companion 自己推断: 对话里看到 `catfish_email_create_draft`
工具完成 → 回合结束后连一次服务器列草稿箱 → 按 Date 头找一封"刚存的"。
9/29 鸿波: Windows 上草稿存进去了, 还是没跳, 得自己点「邮件 → 草稿箱」。

那条链上每一环都可能悄悄断, 而且断了都不报错, 只是不跳:
  · 小鲶不一定走那个工具 —— 邮件 skill 教的是在终端里跑 `catfish-email`,
    终端里直接 `catfish-email draft` 一样能存, 工具名对不上就不算
  · 工具事件要从 hermes 的流里按名字认
  · 回合结束再现连一次服务器列草稿箱, 慢的时候、连不上的时候就放弃
  · 最后按 Date 头比时间, 时区 / 时钟任何一点偏差都会判成"不是刚存的"

草稿是这里存的。存成功的那一刻, 这里确切知道是哪一封 (id) 、什么时候 ——
所以每次 `draft` 成功就记一笔, 不管是谁调的 (对话里的工具、终端、邮件页)。
Companion 回合结束读这一个小文件, 不连服务器、不比 Date 头。
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger("catfish_email.last_draft")

FILENAME = "email-last-draft.json"


def marker_path() -> Path:
    """~/.catfish/email-last-draft.json (CATFISH_HOME 优先, 跟 index_store 同一套约定)。

    Companion 那边 (commands/email_last_draft.rs) 按同一个规则找。
    """
    env = os.environ.get("CATFISH_HOME", "").strip()
    base = Path(env).expanduser() if env else Path.home() / ".catfish"
    return base / FILENAME


def record(draft_id: str, adapter: str, *, path: Path | None = None) -> None:
    """记下刚存好的草稿。写不进去只打日志 —— 草稿已经存好了, 不能因为这个报失败。"""
    target = path or marker_path()
    payload = {"id": draft_id, "adapter": adapter, "created_at": time.time()}
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, target)  # 整体换名: Companion 不会读到写了一半的文件
    except OSError as error:
        logger.warning("记不下刚存的草稿 (对话结束后不会自动跳到草稿箱): %s", error)
        try:
            tmp.unlink()
        except OSError:
            pass
