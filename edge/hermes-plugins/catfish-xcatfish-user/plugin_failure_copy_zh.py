"""P62 (10/22): 模型调用重试用尽时, 聊天框里那句话用员工看得懂的中文。

# 病

10/22 Windows 现场: 中央新装, 供应商 API key 填错。员工发消息后聊天框里是 hermes 的英文模板:

    custom didn't answer after 3 attempts — it looks temporarily unavailable. ...
    To avoid this in future, add a backup provider with `hermes fallback add`.
    Provider said: litellm.BadGatewayError: ... Authorization 鏃犳晥

三处都在误导: 不是「暂时不可用」(是配置错了, 等多久都一样); `hermes fallback add` 是开发者
命令; 唯一有用的那句还是乱码。现场翻了 errors.log 才找到原因。

网关那边 (central/llm-gateway errors.py) 已经把上游原话翻成中文、指明哪个模型、去门户哪一页改。
这里只负责外面那层: `agent.turn_failure_copy.exhausted_copy` 是写死的英文 (不走 t(), 语言包盖不到),
display.language 是 zh 时换成中文, 原因原样放最后 —— 原因怎么说由网关决定, 这里不再猜。

# 只换 zh

员工机 display.language 是 zh (Companion 写)。开发机切英文看到的还是上游原文。
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("catfish.xcatfish_user.plugin")

#: 网关的中文说明里有这些词 = 配置问题, 重试 / 等待都没用, 要管理员改
_NEEDS_ADMIN = ("管理员", "控制台", "供应商")


def failure_copy_zh(attempts: int, summary: str, reset_text: Optional[str]) -> str:
    """中文版的重试用尽提示。`reset_text` 是「~2h」这类额度恢复时间 (没有就 None)。"""
    if reset_text:
        nxt = f"额度大约 {reset_text.lstrip('~')} 后恢复, 到时再发一次, 或者换个模型。"
    elif any(w in summary for w in _NEEDS_ADMIN):
        nxt = "这个重试解决不了, 需要管理员处理, 见下面的原因。"
    else:
        nxt = "可以过一会儿再发一次, 或者换个模型。"
    # ⚠ 开头这句是暗号: Companion upstreamErrorGuard.ts 和网关 app.py 靠它认出「hermes 把失败当正文返了」
    #   (拟稿 / 早安卡片不能把它当成模型写的)。改字要三处一起改。
    return f"这次没能从模型拿到回复 (试了 {attempts} 次)。{nxt}\n\n原因: {summary}"


def _patch_p62_failure_copy_zh() -> None:
    try:
        from agent import i18n, turn_recovery
        from agent.retry_utils import format_reset_window
    except ImportError as e:
        logger.warning("P62: hermes 里没有 turn_recovery / i18n (版本不对?), skip: %s", e)
        return
    original = getattr(turn_recovery, "exhausted_copy", None)
    if original is None:
        logger.warning("P62: turn_recovery.exhausted_copy 不在 (上游改名?), skip")
        return
    if getattr(original, "_catfish_p62", False):
        return  # 插件重载

    def exhausted_copy_zh(reason, *, label, attempts, summary, reset_seconds=None):
        try:
            if i18n.get_language() != "zh":
                return original(reason, label=label, attempts=attempts, summary=summary, reset_seconds=reset_seconds)
            reset_text = format_reset_window(reset_seconds) if reset_seconds and reset_seconds >= 120 else None
            return failure_copy_zh(attempts, summary, reset_text)
        except Exception:  # noqa: BLE001 —— 出错退回原文, 绝不让失败提示本身再失败
            logger.exception("P62: 中文失败提示生成出错, 用原文")
            return original(reason, label=label, attempts=attempts, summary=summary, reset_seconds=reset_seconds)

    exhausted_copy_zh._catfish_p62 = True  # type: ignore[attr-defined]
    turn_recovery.exhausted_copy = exhausted_copy_zh
    logger.info("P62 模型重试用尽的聊天提示改成中文 (原因原样附后)")
