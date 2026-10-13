"""P51 / P52 — 审批在微信里好用 (10/13 鸿波: "这种方式是不是很违反人性")。

不放松任何审批: 每段代码 / 危险命令仍要员工点头, 这里只改"怎么问"和"怎么听懂回答"。

P51 · 审批消息 (wrap gateway.run._format_exec_approval_fallback)
    原来: 英文模板 → P28 翻成「回复 `/批准` 执行 (单次), 或 `/批准 本次会话` ...」+ 截断的
    Python 代码。员工要背斜杠口令, 也看不懂在批什么。
    现在: 先一句大白话 (代码第一行注释, SOUL_EXECUTE_CODE 要求模型写), 再「回 1 执行 / 2 不要」,
    代码放最后给想看的人。smart_denied (管理员单次放行) 场景不动, 走原文。

    **只给单次批准 (10/13 鸿波定 A)**: 微信会话是长寿的 (实测 7/31 开的会话 10/13 还在用),
    「这次对话里都允许」实际等于长期放行, 文案在误导人。所以文字审批渠道一律不提供会话级 /
    永久批准; 员工说「都允许」「本次会话」也只算这一次。Companion 走按钮审批 (P15), 不受影响。

P52 · 听懂回答 (wrap GatewayRunner._handle_active_session_busy_message)
    真因 (10/13 实证): 审批在等时 agent 处于"忙", 员工的回复先进 busy handler。hermes 原版
    那里只认 approve / yes / ok / session / deny 等几个英文词; 中文、带斜杠的「/批准」、打错
    一个字的「/审批本次会话」都不认 → 当新消息 **打断任务** (「⚡ 中断当前任务」) → 模型重来
    → 又弹审批。P14 的中文别名挂在 _handle_message 上, 这条路径根本轮不到它。
    现在 (只在 has_blocking_approval 时):
      · 按意思认: 数字 1 (执行) / 2、3 (不要), 口语 (好 / 行 / 同意 / 不要 ...), 带不带斜杠都行。
        先判"不/拒/别", 防「不同意」被当成「同意」。「都允许」「/批准 本次会话」也只算单次。
      · 认出来 → 改写成原版认得的 approve / deny, 交回原 handler (复用它的 resolve + 回执)。
      · 短回复认不出 → 回一句"回 1 或 2 就行", **不打断任务**, 审批继续等。
      · 长回复 (> 16 字) 视为员工在交代新事 → 走原路径 (打断), 跟以前一样。
"""
from __future__ import annotations

import logging
import re
import unicodedata
from typing import Optional

logger = logging.getLogger("catfish.xcatfish_user.plugin")

# ── 回答解析 ─────────────────────────────────────────────────────────
_MAX_REPLY_LEN = 16          # 超过就当员工在说别的事, 不硬解析
_STRIP = re.compile(r"[\s/／。.!！,，、~～?？…]+")

_DENY_WORDS = ("不同意", "不批", "不要", "不行", "不用", "不执行", "拒绝", "驳回", "取消",
               "算了", "别", "否", "no", "deny", "reject", "cancel", "stop", "👎")
# 以否定字开头却是同意的口语; 其余「不 / 别 / 没」开头一律当拒绝 (「不对」「不可以」「没必要」)
_POSITIVE_DESPITE_NEGATION = {"没问题", "没事", "不错", "没毛病"}
# 「都允许」「本次会话」「always」: 员工想放行 → 只算这一次 (文字渠道不给会话级 / 永久批准)
_APPROVE_WORDS = ("批准", "同意", "审批", "允许", "可以", "执行", "确认", "通过", "好", "行",
                  "嗯", "对", "没问题", "ok", "okay", "yes", "approve", "👍",
                  "会话", "都允许", "都可以", "都行", "全部", "总是", "始终", "always", "session")


def parse_approval_reply(text: Optional[str]) -> Optional[str]:
    """员工的一句回复 → "approve" (只此一次) / "deny" / None (认不出)。"""
    t = _STRIP.sub("", unicodedata.normalize("NFKC", text or "").strip().lower())
    if not t or len(t) > _MAX_REPLY_LEN:
        return None
    if t in _POSITIVE_DESPITE_NEGATION:
        return "approve"
    if t in {"2", "二", "3", "三", "n"} or t.startswith(("不", "别", "没")) \
            or any(w in t for w in _DENY_WORDS):
        return "deny"
    if t in {"1", "一", "y"} or any(w in t for w in _APPROVE_WORDS):
        return "approve"
    return None


# ── 审批消息 ─────────────────────────────────────────────────────────
_EXEC_PREFIX = "execute_code <<'PY'\n"


def _first_comment(code: str) -> Optional[str]:
    for line in code.splitlines():
        s = line.strip()
        if s.startswith("#") and not s.startswith("#!"):
            text = s.lstrip("#").strip()
            if len(text) >= 2:
                return text[:80]
    return None


def format_approval_prompt(command: str, description: str) -> str:
    """中文审批提示: 一句话说要干什么 → 回 1 执行 / 2 不要 → 代码 (给想看的人)。只给单次批准。"""
    is_code = command.startswith(_EXEC_PREFIX)
    if is_code:
        body = command[len(_EXEC_PREFIX):]
        if body.endswith("\nPY"):
            body = body[:-3]
        what = _first_comment(body)
        head = "🔐 小鲶想在你电脑上运行一段代码"
        line_what = f"要做的事: {what}" if what else "要做的事: (代码里没写说明, 可以看下面的代码)"
        preview = body.strip()
        lines = [head, "", line_what]
    else:
        preview = command.strip()
        lines = ["🔐 小鲶想运行一条可能有风险的命令", "", f"原因: {description}"]
    if len(preview) > 300:
        preview = preview[:300] + "\n…"
    lines += ["", "回复数字就行:", "1 · 执行", "2 · 不要执行"]
    lines += ["", "(代码如下, 不看也行)" if is_code else "(命令如下)", "```", preview, "```"]
    return "\n".join(lines)


_HINT = "没看懂这句 🙂 现在有一段操作在等你同意, 回复数字就行: 1 执行 · 2 不要"


def wrap_format(orig):
    """包 gateway.run._format_exec_approval_fallback。smart_denied / 出错 → 原文。"""
    def patched(command, description, command_prefix, *, allow_permanent=True,
                allow_session=True, smart_denied=False):
        kw = dict(allow_permanent=allow_permanent, allow_session=allow_session,
                  smart_denied=smart_denied)
        if smart_denied:
            return orig(command, description, command_prefix, **kw)
        try:
            return format_approval_prompt(str(command or ""), str(description or ""))
        except Exception as e:  # noqa: BLE001 — 提示文字坏了也不能让审批发不出去
            logger.warning("P51: 生成中文审批提示失败, 用原文 (%s)", e)
            return orig(command, description, command_prefix, **kw)

    patched._catfish_p51 = True
    return patched


def _patch_p51_approval_prompt() -> None:
    try:
        import gateway.run as _run
    except ImportError as e:
        logger.warning("P51: gateway.run import 失败 (%s), skip", e)
        return
    orig = getattr(_run, "_format_exec_approval_fallback", None)
    if orig is None:
        logger.warning("P51: gateway.run._format_exec_approval_fallback 不在 (hermes 改了?), skip")
        return
    if getattr(orig, "_catfish_p51", False):
        return
    _run._format_exec_approval_fallback = wrap_format(orig)
    logger.info("P51 审批提示中文化 (回 1 执行 / 2 不要, 只给单次批准) patched")


def wrap_busy_handler(orig, has_blocking_approval):
    """包 GatewayRunner._handle_active_session_busy_message (self, event, session_key) -> bool。"""
    async def patched(self, event, session_key):
        raw = getattr(event, "text", None) or ""
        try:
            # 空文本 (图片 / 文件) 不归这里管, 走原路径
            pending = bool(raw.strip()) and bool(getattr(event, "allow_gateway_control", False)) \
                and has_blocking_approval(session_key)
        except Exception:  # noqa: BLE001
            pending = False
        if pending:
            intent = parse_approval_reply(raw)
            if intent:
                logger.info("P52 审批回复: %r → %s (session=%s)", raw[:20], intent, str(session_key)[:12])
                # 改写成 hermes 原版认得的 approve / deny, 交回原 handler: 它负责 resolve + 回执。
                # 只有单次 approve —— 不改写成 session / always (见文件头「只给单次批准」)。
                event.text = intent
            elif len(_STRIP.sub("", raw.strip())) <= _MAX_REPLY_LEN:
                logger.info("P52 审批在等, 回复认不出 %r → 提示 1 / 2, 不打断任务", raw[:20])
                try:
                    adapter = self._adapter_for_source(event.source)
                    if adapter:
                        anchor = self._reply_anchor_for_event(event)
                        await adapter._send_with_retry(
                            chat_id=event.source.chat_id, content=_HINT, reply_to=anchor,
                            metadata=self._thread_metadata_for_source(event.source, anchor),
                        )
                except Exception as e:  # noqa: BLE001
                    logger.warning("P52: 发提示失败 (%s)", e)
                return True
        return await orig(self, event, session_key)

    patched._catfish_p52 = True
    return patched


def _patch_p52_busy_approval_reply() -> None:
    try:
        from gateway.run import GatewayRunner
        from tools.approval import has_blocking_approval
    except ImportError as e:
        logger.warning("P52: GatewayRunner / has_blocking_approval import 失败 (%s), skip", e)
        return
    orig = GatewayRunner._handle_active_session_busy_message
    if getattr(orig, "_catfish_p52", False):
        return
    GatewayRunner._handle_active_session_busy_message = wrap_busy_handler(orig, has_blocking_approval)
    logger.info("P52 审批回复按意思认 / 认不出不打断 patched")
