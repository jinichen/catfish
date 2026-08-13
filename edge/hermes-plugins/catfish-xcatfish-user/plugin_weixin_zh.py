"""P28 · 微信 outbound 英文→中文。

8/8 从 plugin.py 拆出来 —— 那个文件 4898 行, 军规红线是 800。

## 为什么先拆这一块

它跟 plugin.py 其余部分**只有 logger 一处耦合**: 区块里出现的
`_PATCH_TARGETS` / `_APPROVE_ALIASES` 都只在注释里, 不是真引用。拆一个
零代码依赖的块, 风险最低, 也最容易验证拆对了。

## logger 名字故意不变

仍然是 `catfish.xcatfish_user.plugin` —— 拆文件不该改日志的样子。现场排查
靠 grep 这个前缀, 换成新名字等于把已有的排查手法废掉一半。

## 谁在用

`plugin.py` 顶部按军规的 re-export 协议把这里的符号原样吐回去, 所以老代码
`from plugin import _P28_REPLACEMENTS` 照旧能拿到。
"""

# functools 是 L166 的 @functools.wraps 要的。
#
# 8/13 查出来: 这个 import 从 8/9 拆分出去时就漏了, P28 因此**每次启动都失败**,
# 日志里 19 次 `name 'functools' is not defined`。而收尾那行 "installed ✓
# (15 patches applied)" 是写死的常量, 把它整整盖了四天 —— 表现是微信那边一直
# 看到未翻译的 hermes 英文串, 没人知道为什么。
#
# 拆模块时最容易漏的就是这个: 原来在 plugin.py 模块作用域里的 import, 搬走的
# 那段代码用得到, 但搬的时候只盯着函数体。
import functools
import logging

logger = logging.getLogger("catfish.xcatfish_user.plugin")

_P28_REPLACEMENTS = [
    # 长真 reason — first 防被短真前缀打断
    (
        "execute_code script execution. The script can spawn subprocesses or "
        "mutate files without passing through terminal command approval; "
        "approval is one-shot for this run.",
        "execute_code 脚本执行 — 可能调子进程 / 改文件, 绕过终端命令审批. 本次审批仅 1 次有效.",
    ),
    # reply 段 (鸿波铁律: 砍 `/approve always`)
    # ⚠ 7/22 鸿波 catch (P3.5.79+): hermes v0.19 Quicksilver (7/20) 改了原文
    # `to execute,` → `to execute this one operation,` (gateway/run.py:370).
    # 老 v0.18 pattern silent miss → 英文全条泄漏到微信. 加 v0.19 pattern first
    # (长 first 匹配), 保留 v0.18 pattern 兜底 (客户装老版 hermes 时用).
    #
    # v0.19 pattern
    (
        "Reply `/approve` to execute this one operation, `/approve session` to approve this pattern "
        "for the session, `/approve always` to approve permanently, or `/deny` to cancel.",
        "回复 `/批准` 执行 (单次), 或 `/批准 本次会话` 本会话内同款命令免审批, 或 `/拒绝` 取消.\n"
        "（安全提示：永久免批已禁用，危险命令必须每次或每会话审批）",
    ),
    # v0.20 新增的两个变体 (8/8 升级 v2026.8.3 时补).
    #
    # 上面那条只盖住"四选项"这一种。看 v0.20 `gateway/run.py:508`
    # `_format_exec_approval_fallback` —— choices 是**按开关拼出来的**:
    #
    #     choices = ["Reply `/approve` to execute this one operation"]
    #     if not smart_denied and allow_session:
    #         choices.append("`/approve session` ...")
    #         if allow_permanent:
    #             choices.append("`/approve always` ...")
    #     choices.append("`/deny` to cancel")
    #
    # 所以一共 3 种成品, 我们原来只翻了 allow_session ∧ allow_permanent 那一种。
    # 另外两种会整条英文泄到微信 —— 正是 P28 要防的事。
    #
    # 三条互不为子串 (中间 "always" / "session" 段不同), 顺序不影响 str.replace,
    # 但仍按长→短排, 保持本表的既有约定。
    #
    # allow_permanent=False (铁律场景: 本来就该砍掉永久免批)
    (
        "Reply `/approve` to execute this one operation, `/approve session` to approve this pattern "
        "for the session, or `/deny` to cancel.",
        "回复 `/批准` 执行 (单次), 或 `/批准 本次会话` 本会话内同款命令免审批, 或 `/拒绝` 取消.",
    ),
    # allow_session=False 或 smart_denied=True — **只剩单次**, 不能提"本次会话",
    # 提了就是骗员工: hermes 那边根本不接受 `/approve session`。
    (
        "Reply `/approve` to execute this one operation, or `/deny` to cancel.",
        "回复 `/批准` 执行 (仅此一次), 或 `/拒绝` 取消.",
    ),
    # v0.18 及之前 pattern (兜底 · 客户老 hermes 装)
    (
        "Reply `/approve` to execute, `/approve session` to approve this pattern "
        "for the session, `/approve always` to approve permanently, or `/deny` to cancel.",
        "回复 `/批准` 执行 (单次), 或 `/批准 本次会话` 本会话内同款命令免审批, 或 `/拒绝` 取消.\n"
        "（安全提示：永久免批已禁用，危险命令必须每次或每会话审批）",
    ),
    # 真短单句
    ("⚠️ **Dangerous command requires approval:**", "⚠️ **危险命令需要审批:**"),
    # v0.20 新 heading (smart_denied 分支, gateway/run.py:505)
    (
        "⚠️ **Smart DENY — owner override for one operation:**",
        "⚠️ **智能拦截已拒绝 — 仅本次由管理员放行:**",
    ),
    ("⚡ Interrupting current task", "⚡ 中断当前任务"),
    (". I'll respond to your message shortly.", ", 马上回复你."),
    (". I'll respond once the current task finishes.", ", 当前任务完成后回复你."),
    ("⏳ Queued for the next turn", "⏳ 已排队下个回合"),
    ("Reason: ", "原因: "),
]

# BL-P14-P28-DEDUPE (7/19 鸿波 catch): _P28_CMD_ALIASES 老死代码 — **定义但从未使用**.
# grep 全项目 zero use. 老 P28 只做 outbound (WeixinAdapter.send 英文→中文), inbound
# 命令翻译 (`/批准`→`/approve`) 事实上没接. 员工按 Bot 提示 `/批准 本次会话` 发, 走
# hermes 原 slash dispatcher, 不认 `/批准` → 当 message 触发 LLM → LLM 又调
# execute_code → 又弹审批. 死循环.
#
# 修法 · 死代码删 + 中文 slash alias dedupe 到 **_APPROVE_ALIASES** (line 1833) ·
# 由 P14 patch 统一处理 (P14 wrap GatewayRunner._handle_message · 覆盖所有平台
# inbound · 不只 WeChat). P14 patched 逻辑放宽 · 支持带 / 前缀. 见 line 1870+ 修.
# _P28_CMD_ALIASES = {} — 死代码删净.


def _translate_hermes_zh(text):
    """str.replace 英文 → 中文 — P28 outbound 中文化main entry真.

    0 raise — : input 异常 → 返原文 (不阻塞 send).

    ⚠ 7/22 军规 fail-loud (P3.5.79+): 翻译完仍含英文 slash prompt (`/approve`,
    `/deny`) → warn log. hermes 升级会改原文 (v0.18→v0.19 就改过), 老 pattern
    silent miss = 员工看到英文丑. warn 让下次一发现就修 _P28_REPLACEMENTS, 别
    等员工投诉.
    """
    if not text or not isinstance(text, str):
        return text
    try:
        for en, zh in _P28_REPLACEMENTS:
            text = text.replace(en, zh)
    except Exception as e:  # noqa: BLE001
        logger.warning("P28 translate fail (return original): %s", e)
        return text

    # fail-loud 检测: 只在明显 approval prompt (含 `/approve`) 场景下检查 · 且
    # 中文替换未生效 (没 "回复" / "批准" 关键字). 防误报 (员工正常聊到 /approve).
    if isinstance(text, str) and "/approve" in text and "批准" not in text and "回复" not in text:
        logger.warning(
            "P28 miss: outbound 含未翻译 `/approve` — hermes 上游可能改了原文, "
            "需 update _P28_REPLACEMENTS. text preview: %r",
            text[:200],
        )
    return text


def _patch_p28_weixin_zh() -> None:
    """wrap WeixinAdapter.send — : 真:** : 真:** : : : : : : : : : : :

    真: : : : : : : : : : : : : : : : : : : : : : : : : : : : :

    真:** : : : : : : : : : : : : : : : : : : : : : : :
    """
    try:
        from gateway.platforms import weixin as _wx_mod  # noqa: PLC0415
    except ImportError as e:
        logger.warning("P28: hermes gateway.platforms.weixin 没导, skip (%s)", e)
        return

    _WxCls = getattr(_wx_mod, "WeixinAdapter", None)
    if _WxCls is None:
        logger.warning("P28: WeixinAdapter 没真 attr (hermes 改名?), skip")
        return

    _orig_send = getattr(_WxCls, "send", None)
    if _orig_send is None:
        logger.warning("P28: WeixinAdapter.send 没真 method, skip")
        return
    if getattr(_orig_send, "_p28_patched", False):
        logger.info("P28 already patched, skip (dev hot-reload)")
        return

    @functools.wraps(_orig_send)
    async def patched_send(self, chat_id, content, reply_to=None, metadata=None):
        content = _translate_hermes_zh(content)
        return await _orig_send(
            self, chat_id, content, reply_to=reply_to, metadata=metadata
        )

    patched_send._p28_patched = True  # type: ignore[attr-defined]
    _WxCls.send = patched_send  # type: ignore[method-assign]
    logger.info(
        "P28 wrap WeixinAdapter.send 完成 — 中文化 hermes 英文 outbound "
        "(approval / 中断提示 / /approve 命令说明), 鸿波铁律: 砍永久免批入口"
    )


# ── P29 (P3.5.168, 7/3 鸿波): /learn slash command 前置翻译 ────────────────
#
# 真因 (P3.5.164 严格 audit):
#   hermes v0.18 /learn 只在 GatewayRunner._handle_message 处理
#   (gateway/run.py:9263-9289): 检测 canonical == "learn" → 调
#   agent.learn_prompt.build_learn_prompt → 替换 event.text → fall through.
#   /v1/chat/completions (APIServerAdapter._handle_chat_completions api_server.py:1833+)
#   严格不过 slash command dispatcher — 提取 messages → 直接 _run_agent, 无 canonical
#   command 检测. Companion 员工输 "/learn xxx" → LLM 只当 prompt 释义.
#   catfish P15 patch comment 明确 confirm (plugin.py:1807-1808):
#     "chat completions 没 slash command hook → LLM 直接看 '/approve' 编释义"
#
# 修法 (不 fork hermes, 不改 Companion 前端):
#   wrap APIServerAdapter._run_agent (跟 P15 同挂点, 但 P29 wrap 是外层).
#   前置检测 message.startswith("/learn") → 调 hermes agent/learn_prompt.
#   build_learn_prompt(arg) 翻译 → 替换 args[0] 或 kwargs["message"] →
#   继续 P15 approval 闭包 → hermes original _run_agent.
#
# wrap 顺序: 注册顺序 P1..P15..P28..P29, runtime call chain: P29 (外, 先跑翻译)
# → P15 (中, approval 闭包) → hermes original. P29 前置翻译不影响 P15 approval.
#
# 风险评估:
#   - hermes v0.18 _run_agent signature 第 1 位置参 = message (P3.5.159 Phase A audit
#     confirm): _run_agent(self, message, context_prompt, history, source, session_id, ...).
#     P29 拿 args[0] 或 kwargs["message"], 兼容 caller.
#   - build_learn_prompt 抛异常 → fall through as normal message (原行为), warn log.
#   - hermes v0.19 若改 _run_agent 参数顺序或 build_learn_prompt module 位置 →
#     P29 fail-safe: import 失败 skip patch, runtime 反射失败 warn + fall through.
#   - 只处理 /learn, 其他 slash (/goal /journey /steer /fast /verbose /memory /skills)
#     未来员工反馈驱动再加 P30+.
#
