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
    # 10/13 重启 / 改道提示 (gateway/run.py:11522-11528, 11243-11250), 微信实测漏翻。
    # 长的在前: 带结尾空格的那句先替, 免得被短的截断。
    ("Send any message after restart and I'll try to resume where you left off.",
     "重启好后随便发句话, 我接着做。"),
    ("Your current task will be interrupted. ", "手上的事会先中断, "),
    ("Your current task will be interrupted.", "手上的事会先中断, 稍后接着做。"),
    ("⚠️ Gateway shutting down — ", "⚠️ 小鲶要重启一下 — "),
    ("⚠️ Gateway restarting — ", "⚠️ 小鲶正在重启 — "),
    ("↪ Redirected current run", "↪ 收到, 按你说的改"),
    ("⏩ Steered into current run", "⏩ 收到, 插进当前任务"),
    (". I'll adjust using your correction.", "。"),
    (". Your message arrives after the next tool call.", ", 下一步就用上你这句。"),
    # 10/13 审批回执 (hermes locales/en.yaml approve./deny.*): 员工回 1/2/3 后看到的那句。
    # 原来漏翻, 微信里回一句「✅ Command approved. The agent is resuming...」。
    ("(pattern approved for this session)", "(同类的这次对话里都允许了)"),
    ("(pattern approved permanently)", "(同类的以后都允许了)"),
    ("✅ Commands approved", "✅ 好的, 已同意"),
    ("✅ Command approved", "✅ 好的, 已同意"),
    (". The agent is resuming...", ", 小鲶继续干活…"),
    (" commands)", " 条)"),
    ("❌ Command denied.", "❌ 好的, 不执行了。"),
    ("❌ Commands denied", "❌ 好的, 不执行了"),
    ("❌ Command denied", "❌ 好的, 不执行了"),
    (" (approval was stale)", " (这条审批已经过期)"),
    (". Reason relayed to the agent:", ", 已把原因转告小鲶:"),
    ("No pending command to approve.", "现在没有在等你同意的操作。"),
    ("No pending command to deny.", "现在没有在等你同意的操作。"),
    ("⚠️ Approval expired (agent is no longer waiting). Ask the agent to try again.",
     "⚠️ 这条审批已经过期了, 需要的话让小鲶重新做一次。"),
    # ── 10/13 hermes 切中文 (display.language: zh) 后它自带的说法 → 我们的说法 ─────────────
    # locales/zh.yaml 的审批回执 / 重置 / 停止等写的是「代理」「命令已批准」, 对员工生硬。
    ("✅ 命令已批准（本次会话内允许该模式）", "✅ 好的, 已同意 (同类的这次对话里都允许了)"),
    ("✅ 命令已批准（永久允许该模式）", "✅ 好的, 已同意 (同类的以后都允许了)"),
    ("✅ 命令已批准", "✅ 好的, 已同意"),
    ("。代理正在恢复…", ", 小鲶继续干活…"),
    ("❌ 命令已拒绝（批准已过期）。", "❌ 这条审批已经过期了。"),
    ("❌ 命令已拒绝。 已将原因转达给代理: ", "❌ 好的, 不执行了, 已把原因转告小鲶: "),
    ("❌ 命令已拒绝。", "❌ 好的, 不执行了。"),
    ("❌ 命令已拒绝", "❌ 好的, 不执行了"),
    ("。 已将原因转达给代理: ", ", 已把原因转告小鲶: "),
    ("没有待批准的命令。", "现在没有在等你同意的操作。"),
    ("没有待拒绝的命令。", "现在没有在等你同意的操作。"),
    ("⚠️ 批准已过期（代理不再等待）。请让代理重试。", "⚠️ 这条审批已经过期了, 需要的话让小鲶重新做一次。"),
    ("⚡ 已停止。代理尚未启动 — 你可以继续此会话。", "⚡ 停了 (还没开始做), 可以接着聊。"),
    ("⚡ 已停止。你可以继续此会话。", "⚡ 停了, 可以接着聊。"),
    ("没有可停止的活跃任务。", "现在没有在做的事。"),
    ("✨ 会话已重置！重新开始。", "✨ 已开新对话。"),
    ("✨ 新会话已启动！", "✨ 新对话开始了。"),
    ("✨ 新会话已启动：", "✨ 新对话开始了: "),
    ("⏳ 网关重启已在进行中……", "⏳ 已经在重启了…"),
    ("♻ 正在重启网关。如果 60 秒内没有收到通知，请在控制台运行 `hermes gateway restart` 重启。",
     "♻ 小鲶正在重启, 一分钟后还没回音的话, 请在电脑上重启鲶鱼。"),
    # ── 10/13 全量盘点 (gateway/run.py / agent/onboarding.py / base.py) ──────────────
    # 忙碌 / 排队 / 第一次提示
    ("⏳ Subagent working", "⏳ 子任务还在跑"),
    ("⏳ Compressing context", "⏳ 正在整理对话记录"),
    (" — your message is queued for when it finishes (use /stop to cancel everything).",
     " — 你的消息排上了, 这步做完就处理 (发 /stop 可以全部取消)。"),
    ("💡 First-time tip — I queued your message instead of interrupting. "
     "Send `/busy interrupt` to make new messages stop the current task "
     "immediately, or `/busy status` to check. This notice won't appear again.",
     "💡 小提示: 你的消息排在当前任务后面了, 不会打断它。这条提示只出现一次。"),
    ("💡 First-time tip — I steered your message into the current run; "
     "it will arrive after the next tool call instead of interrupting. "
     "Send `/busy interrupt` or `/busy queue` to change this, or "
     "`/busy status` to check. This notice won't appear again.",
     "💡 小提示: 你的消息插进了当前任务, 下一步就会用上。这条提示只出现一次。"),
    ("💡 First-time tip — I redirected the current run using your message. "
     "Completed work stays in context, and `/stop` still cancels the task. "
     "Send `/busy queue` to wait for a separate turn, or `/busy status` "
     "to check. This notice won't appear again.",
     "💡 小提示: 我按你的新消息调整了正在做的事, 已做完的部分还在; 发 /stop 可以取消。这条提示只出现一次。"),
    ("💡 First-time tip — I just interrupted my current task to answer you. "
     "Send `/busy queue` to queue follow-ups for after the current task instead, "
     "`/busy steer` to inject them mid-run without interrupting, or "
     "`/busy status` to check. This notice won't appear again.",
     "💡 小提示: 我先停下手上的事来回你。这条提示只出现一次。"),
    # 重启 / 维护
    ("⏳ Gateway restarting — queued for the next turn after it comes back.",
     "⏳ 小鲶正在重启 — 你的消息排上了, 回来就处理。"),
    ("⏳ Gateway shutting down — queued for the next turn after it comes back.",
     "⏳ 小鲶正在关闭 — 你的消息排上了, 回来就处理。"),
    ("⏳ Gateway is restarting and is not accepting another turn right now.",
     "⏳ 小鲶正在重启, 暂时接不了新消息, 过一会儿再发。"),
    ("⏳ Gateway is shutting down and is not accepting another turn right now.",
     "⏳ 小鲶正在关闭, 暂时接不了新消息, 过一会儿再发。"),
    ("⏳ Gateway is restarting and is not accepting new work right now.",
     "⏳ 小鲶正在重启, 暂时接不了新消息, 过一会儿再发。"),
    ("⏳ Gateway is shutting down and is not accepting new work right now.",
     "⏳ 小鲶正在关闭, 暂时接不了新消息, 过一会儿再发。"),
    ("⏳ This agent is draining for a maintenance action and isn't accepting new turns right now. "
     "It'll be back in a moment — please resend shortly.",
     "⏳ 小鲶在做维护, 马上回来, 请过一会儿再发一次。"),
    ("⏳ Another turn is still running on this session. To protect the transcript, this message "
     "was not processed. Wait for the active turn to finish, then resend it.",
     "⏳ 上一件事还没做完, 这条消息没处理。等它做完再发一次。"),
    ("⏳ Gateway restart already in progress...", "⏳ 已经在重启了…"),
    ("♻ Restarting gateway. If you aren't notified within 60 seconds, restart from the console "
     "with `hermes gateway restart`.", "♻ 小鲶正在重启, 一分钟后还没回音的话, 请在电脑上重启鲶鱼。"),
    ("♻ Gateway restarted successfully. Your session continues.", "♻ 小鲶重启好了, 接着聊。"),
    ("♻️ Gateway online — Hermes is back and ready.", "♻️ 小鲶回来了, 随时可以用。"),
    # /stop /queue /steer /pause
    ("⚡ Stopped. The agent hadn't started yet — you can continue this session.",
     "⚡ 停了 (还没开始做), 可以接着聊。"),
    ("⚡ Stopped. You can continue this session.", "⚡ 停了, 可以接着聊。"),
    ("⚡ Force-stopped. The agent was still starting — session unlocked.", "⚡ 强制停了, 可以接着聊。"),
    ("No active task to stop.", "现在没有在做的事。"),
    ("Agent still starting — /steer queued for the next turn.", "小鲶还没开始做, 你这句排到下一轮。"),
    ("No active agent — /steer queued for the next turn.", "现在没有在做的事, 你这句排到下一轮。"),
    ("Steer rejected (empty payload).", "内容是空的, 没插进去。"),
    ("Agent is running — wait or /stop first, then switch models.", "小鲶正在做事, 等做完或发 /stop 后再换模型。"),
    ("▶️ Resumed — new work is accepted again.", "▶️ 恢复了, 可以接新消息了。"),
    ("Hermes wasn't paused.", "小鲶没有暂停。"),
    # /new 确认 (destructive_slash_confirm 默认开)
    ("⚠️ **Confirm /new**", "⚠️ **确认开新对话**"),
    ("This starts a fresh session and discards the current conversation history.",
     "会开一个新对话, 现在这段对话的记录不再带着。"),
    ("Choose:", "选一个:"),
    ("• **Approve Once** — proceed this time only", "• **确认** — 就这一次"),
    ("• **Always Approve** — proceed and silence this prompt permanently", "• **总是确认** — 以后不再问"),
    ("• **Cancel** — keep current conversation", "• **取消** — 保留现在的对话"),
    ("_Text fallback: reply `/approve`, `/always`, or `/cancel`._",
     "_回复 `/approve` 确认, `/always` 以后不问, `/cancel` 取消。_"),
    ("✨ Session reset! Starting fresh.", "✨ 已开新对话。"),
    ("✨ New session started!", "✨ 新对话开始了。"),
    # 出错
    ("⚠️ Provider authentication failed. Check the configured credentials; raw provider details "
     "are in the gateway logs.", "⚠️ 模型服务登录失败, 请联系管理员检查模型配置。"),
    ("⚠️ The model provider rejected the request. I kept the raw provider error out of chat; "
     "check gateway logs for details or try rephrasing.", "⚠️ 模型服务拒绝了这次请求, 换个说法再试一次。"),
    ("⏱️ The model provider is rate-limiting requests. Please wait a moment and try again.",
     "⏱️ 模型服务现在太忙, 稍等一会儿再试。"),
    ("⚠️ The model server is not responding — it looks like the configured model endpoint is not "
     "running or is unreachable.", "⚠️ 模型服务没有响应, 请联系管理员检查。"),
    ("⚠️ The model provider failed after retries. I kept raw provider details out of chat; check "
     "gateway logs for diagnostics.", "⚠️ 模型服务重试几次都失败了, 稍后再试。"),
    ("⚠️ Session storage was temporarily unavailable, so this turn was stopped to protect your "
     "conversation history. Please check available disk space, then send your message again.",
     "⚠️ 对话记录暂时存不了 (可能磁盘满了), 这次先停下。清理一下磁盘后再发一次。"),
    ("⚠️ Session storage was temporarily unavailable, so this turn was stopped to protect your "
     "conversation history. Your message should already be saved — please send it again in a moment.",
     "⚠️ 对话记录暂时存不了, 这次先停下, 过一会儿再发一次。"),
    ("⚠️ Session too large for the model's context window.\nUse /compact to compress the "
     "conversation, or /reset to start fresh.", "⚠️ 对话太长了。发 /compact 整理一下, 或发 /reset 开新对话。"),
    ("⚠️ Your message was interrupted before processing started (likely by a recent /stop). "
     "Please send it again.", "⚠️ 你的消息还没开始处理就被中断了, 再发一次。"),
    ("⚠️ Processing completed but no response was generated. This may be a transient error — "
     "try sending your message again.", "⚠️ 处理完了但没生成回复, 再发一次试试。"),
    ("⚠️ Your message wasn't processed (the previous turn was still being cleaned up). "
     "Please send it again.", "⚠️ 上一轮还在收尾, 你的消息没处理, 再发一次。"),
    ("⚠️ The model returned no response after processing tool results. This can happen with some "
     "models — try again or rephrase your question.", "⚠️ 模型这次没给出回复, 再试一次或换个说法。"),
    ("⚠️ The model declined to respond to this request (safety refusal).", "⚠️ 模型拒绝回答这个请求。"),
    ("\nTry again or use /reset to start a fresh session.", "\n再试一次, 或发 /reset 开新对话。"),
    ("Sorry, I encountered an unexpected error.", "抱歉, 出了个意外的错误。"),
    ("The request failed: ", "请求失败: "),
    ("⏳ Another Hermes process is using this session; waiting…", "⏳ 这段对话正被另一处使用, 稍等…"),
    ("↻ Stream interrupted — using delivered content as final response", "↻ 连接中断, 先用已收到的内容作答"),
    ("💾 Self-improvement review: ", "💾 小鲶自我复盘: "),
    ("Reply with the number, the option text, or your own answer.", "回复序号、选项内容, 或者直接说你的答案。"),
    ("Multiple selections allowed — reply with the numbers separated by commas or spaces "
     "(e.g. \"1, 3\"), the option text, or your own answer.",
     "可以多选 — 回复序号 (用逗号或空格隔开, 比如 \"1, 3\")、选项内容, 或者直接说你的答案。"),
    ("Reason: ", "原因: "),
]

# 带变量的句子 (f-string) 用正则; 在 _P28_REPLACEMENTS 之后跑。
import re as _re  # noqa: E402

_P28_REGEX = [
    (_re.compile(r"\(([0-9]+) queued\)"), r"(排了 \1 条)"),
    (_re.compile(r"Queued for the next turn\."), "排上了, 下一轮处理。"),
    (_re.compile(r"⏳ Draining ([0-9]+) active agent\(s\) before restart\.\.\."),
     r"⏳ 先等 \1 个进行中的任务做完再重启…"),
    (_re.compile(r"⚠️ Cron job '([^']*)' was interrupted — the gateway is (?:restarting|shutting down) and "
                 r"killed the run before it finished\. No result was produced for this run\."),
     r"⚠️ 定时任务「\1」被中断了 (小鲶在重启), 这次没有结果。"),
    (_re.compile(r"⏳ Agent is running — `/([^`]+)` can't run mid-turn\. Wait for the current response "
                 r"or `/stop` first\."), r"⏳ 小鲶正在做事, /\1 要等做完或发 /stop 后再用。"),
    (_re.compile(r"⚠️ Steer failed: "), "⚠️ 没插进去: "),
    (_re.compile(r"⏩ Steer queued — arrives after the next tool call: "), "⏩ 收到, 下一步就用上: "),
    (_re.compile(r"🟡 /(\S+) cancelled\. Conversation unchanged\."), r"🟡 已取消 /\1, 对话没动。"),
    (_re.compile(r"\n✦ (?:Tip: |提示：)[^\n]*"), ""),   # 小贴士内容是英文的 hermes 用法, 对员工没用, 去掉
    (_re.compile(r"（([0-9]+) 条命令）"), r"(\1 条)"),
    (_re.compile(r"⏳ 正在等待 ([0-9]+) 个活跃代理结束后重启\.\.\."), r"⏳ 先等 \1 个进行中的任务做完再重启…"),
    # zh.yaml 里没翻的 /status /context 行
    (_re.compile(r"\*\*Model:\*\*"), "**模型:**"),
    (_re.compile(r"\*\*Context:\*\*"), "**对话长度:**"),
    (_re.compile(r"\*\*Lifetime tokens billed:\*\* ([^\n]*?) _\(not your current context size; use `/context`\)_"),
     r"**累计用量:** \1"),
    (_re.compile(r"🧠 \*\*Context Window\*\*"), "🧠 **对话长度**"),
    (_re.compile(r"(?m)^Model: "), "模型: "),
    (_re.compile(r"(?m)^Window: ([0-9,]+) tokens"), r"上限: \1 tokens"),
    (_re.compile(r"(?m)^In use: "), "已用: "),
    (_re.compile(r"(?m)^Headroom to limit: "), "剩余: "),
    (_re.compile(r"(?m)^Auto-compresses at: ([^\n]*?) — ([^\n]*?) to go"), r"到 \1 自动整理 (还差 \2)"),
    (_re.compile(r"(?m)^Compressions this session: "), "本对话整理次数: "),
    (_re.compile(r"(?m)^Last compression freed: ([0-9.]+)% of context"), r"上次整理腾出 \1%"),
    (_re.compile(r"No context data available yet\. Send a message to start a session\."),
     "还没有对话记录, 先发条消息。"),
    (_re.compile(r"◐ Session automatically reset \([^)]*\)\. Conversation history cleared\.\n"
                 r"Use /resume to browse and restore a previous session\.\n"
                 r"Adjust reset timing in config\.yaml under session_reset\."),
     "◐ 对话已自动重开, 之前的记录可以发 /resume 找回。"),
    (_re.compile(r"Unknown command `/([^`]+)`\. Type /commands to see what's available, or resend "
                 r"without the leading slash to send as a regular message\."),
     r"没有 /\1 这个命令。发 /commands 看看有哪些, 或者去掉开头的 / 当普通消息发。"),
    (_re.compile(r"⚠️ No activity for ([0-9]+) min\. If the agent does not respond soon, it will be "
                 r"timed out in ([0-9]+) min\. You can continue waiting or use /reset\."),
     r"⚠️ 已经 \1 分钟没动静了, 再过 \2 分钟还没反应就会停下。可以继续等, 或发 /reset 重来。"),
    (_re.compile(r"⏱️ Agent inactive for ([0-9]+) min — no tool calls or API responses\.[\s\S]*?"
                 r"Try again, or use /reset to start fresh\."),
     r"⏱️ 小鲶 \1 分钟没有任何进展, 这次先停下了。再试一次, 或发 /reset 开新对话。"),
    (_re.compile(r"⚠️ Context compression aborted \([^)]*\)\. No messages were dropped — conversation "
                 r"is unchanged\. Run /compress to retry, /reset for a clean session, or check your "
                 r"auxiliary\.compression model configuration\."),
     "⚠️ 整理对话记录没成功, 对话没有丢。可以发 /compress 再试, 或 /reset 开新对话。"),
    (_re.compile(r"⚠️ Processing stopped: (.*?)\. Try again\."), r"⚠️ 处理中断了: \1。再试一次。"),
    (_re.compile(r"✅ Primary model restored: "), "✅ 主模型恢复了: "),
    (_re.compile(r"⚠️ Iteration budget exhausted \(([^)]*)\) — asking model to summarise"),
     r"⚠️ 步数用完了 (\1), 让模型先总结"),
]

# 兜底: 翻完还像整句英文 (连续 6 个以上英文单词、没有中文) → 打 log, 下次补表。
_ENGLISH_RUN = _re.compile(r"(?:[A-Za-z][A-Za-z'’-]*[ ,.;:—-]+){6,}")
_CJK = _re.compile(r"[一-鿿]")

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
        for pat, zh in _P28_REGEX:
            text = pat.sub(zh, text)
    except Exception as e:  # noqa: BLE001
        logger.warning("P28 translate fail (return original): %s", e)
        return text

    # 兜底 (10/13): 整条没有中文、却有一长串英文 → 多半是 hermes 新加 / 改了的提示没进表
    if not _CJK.search(text) and _ENGLISH_RUN.search(text):
        logger.warning("P28 miss: 微信外发仍是英文, 补进 _P28_REPLACEMENTS / _P28_REGEX: %r", text[:200])

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
