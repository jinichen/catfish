"""P57 — /new、/undo 的确认在微信里也回 1 / 2 (10/14 鸿波「怎么还是这么麻烦的回复？」)。

hermes 的 destructive_slash_confirm (默认开) 在 /new /reset /undo 之前问一句, 文字渠道给三个选项:
「确认 · 总是确认 · 取消」+「回复 /approve 确认, /always 以后不问, /cancel 取消」。P28 只翻了
措辞, 员工还是要背斜杠口令, 而且 _斜体_ 在微信里显示成两根下划线。跟 P51 执行审批同一套:
一句话说清会发生什么, 回 1 / 2, 不给「以后不问」(10/13 定的「只给单次批准」)。

· 提示: 包 GatewayRunner._request_slash_confirm, 按 command 换掉 message。Telegram / Discord
  的按钮不受影响 (按钮回调直接 slash_confirm.resolve)。取消时的回执也换成大白话。
· 回答: 包 GatewayRunner._handle_message —— 有确认在等、且没有执行审批在等 (那个归 P52) 时,
  parse_approval_reply 认出的 1 / 2 / 好 / 不 … 改写成 /approve 或 /cancel, 交给 hermes 原逻辑
  (run.py「Intercept messages that are responses to a pending … slash-confirm prompt」那段)。
  认不出的照旧当普通消息 —— hermes 自己会清掉过期的确认。
· 跟 P56 配合: 记下提示时间 / 回答时间。不然 5 分钟内刚给执行审批回过「1」, 这次的「1」
  会被微信内容去重当重投吞掉。

ux 模块 (plugin_approval_ux) 由 plugin.py 传进来, 保证改的是 P56 读的那份全局状态。
"""
from __future__ import annotations

import logging
import re
import time

logger = logging.getLogger("catfish.xcatfish_user.plugin")

_UNDO_N = re.compile(r"removes the last (\d+) user turns")


def confirm_prompt(command: str, detail: str = "") -> str:
    """给员工看的确认提示。"""
    if command == "new":
        what = "🆕 要开新对话吗？\n现在这段聊天记录不再带着 (长期记忆还在)。"
        yes = "开新对话"
    elif command == "undo":
        m = _UNDO_N.search(detail or "")
        what = f"↩️ 要撤回吗？\n会把最近 {m.group(1)} 轮对话从记录里删掉。" if m else \
            "↩️ 要撤回吗？\n会把最近一轮问答从记录里删掉。"
        yes = "撤回"
    else:
        what = f"⚠️ 要执行 /{command} 吗？"
        yes = "执行"
    return f"{what}\n\n回复 1 {yes} · 2 不要"


def cancel_reply(command: str) -> str:
    return {
        "new": "好, 不开新对话, 接着聊。",
        "undo": "好, 不撤回。",
    }.get(command, f"好, 不执行 /{command}。")


def _clean_detail(message: str) -> str:
    # hermes 的 message 是「⚠️ **Confirm /x**\n\n{detail}\n\nChoose: …」, detail 在第二段
    parts = (message or "").split("\n\n")
    return parts[1] if len(parts) > 1 else ""


def wrap_request(orig, ux):
    async def patched(self, *, event, command, title, message, handler):
        async def _handler(choice):
            ux._LAST_RESOLVED_AT = time.time()
            if choice == "cancel":
                return cancel_reply(command)
            return await handler(choice)

        new_message, new_handler = message, _handler
        try:
            new_message = confirm_prompt(command, _clean_detail(message))
            ux._PROMPT_SENT_AT = time.time()
        except Exception as e:  # noqa: BLE001 — 出错就用原提示
            logger.debug("P57 改提示异常 (用原提示): %s", e)
            new_message, new_handler = message, handler
        return await orig(self, event=event, command=command, title=title,
                          message=new_message, handler=new_handler)

    patched._catfish_p57 = True
    return patched


def wrap_handle_message(orig, ux, get_pending, has_blocking_approval):
    async def patched(self, event):
        try:
            raw = (event.text or "").strip()
            if raw and not raw.startswith("/"):
                key = self._session_key_for_source(event.source)
                if get_pending(key) and not has_blocking_approval(key):
                    intent = ux.parse_approval_reply(raw)
                    if not intent and "\n" in raw:
                        intent = ux.parse_approval_reply(raw.splitlines()[-1])
                    if intent:
                        event.text = "/approve" if intent == "approve" else "/cancel"
                        logger.info("P57 确认回复: %r → %s", raw[:20], event.text)
        except Exception as e:  # noqa: BLE001
            logger.debug("P57 认回复异常 (走原逻辑): %s", e)
        return await orig(self, event)

    patched._catfish_p57 = True
    return patched


def patch(ux) -> None:
    try:
        from gateway.run import GatewayRunner
        from tools import slash_confirm
        from tools.approval import has_blocking_approval
    except ImportError as e:
        logger.warning("P57: hermes import 失败 (%s), skip", e)
        return
    req = getattr(GatewayRunner, "_request_slash_confirm", None)
    if req is None:
        logger.warning("P57: GatewayRunner._request_slash_confirm 不在 (hermes 改了?), skip")
        return
    if getattr(req, "_catfish_p57", False):
        return
    GatewayRunner._request_slash_confirm = wrap_request(req, ux)
    GatewayRunner._handle_message = wrap_handle_message(
        GatewayRunner._handle_message, ux, slash_confirm.get_pending, has_blocking_approval)
    logger.info("P57 /new /undo 确认回 1/2 patched")
