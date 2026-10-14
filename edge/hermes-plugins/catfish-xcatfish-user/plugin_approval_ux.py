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
import time
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


def _load_readonly():
    """同目录的 readonly_code.py。插件包名带 dash, 相对导入不稳 —— 跟 plugin._import_sibling 同套路。"""
    try:
        from . import readonly_code  # type: ignore
        return readonly_code
    except Exception:  # noqa: BLE001
        pass
    try:
        import readonly_code  # type: ignore
        return readonly_code
    except ImportError:
        pass
    import importlib.util
    import sys
    from pathlib import Path
    path = Path(__file__).with_name("readonly_code.py")
    spec = importlib.util.spec_from_file_location("catfish_readonly_code", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["catfish_readonly_code"] = mod
    spec.loader.exec_module(mod)
    return mod


def _code_of(command: str) -> Optional[str]:
    if not command.startswith(_EXEC_PREFIX):
        return None
    body = command[len(_EXEC_PREFIX):]
    return body[:-3] if body.endswith("\nPY") else body


def format_approval_prompt(command: str, description: str) -> str:
    """中文审批提示: 要做什么 → 为什么要你同意 → 回 1 执行 / 2 不要。不展示代码 (10/13 鸿波:
    "非程序员对代码不感兴趣")。只给单次批准。"""
    # 第一行以「：」结尾 —— hermes 微信适配器 (weixin.py _should_split_short_chat_block_for_weixin)
    # 会把 2~6 行的"聊天式"短消息拆成一串气泡; 首行像标题就不拆。10/13 实测拆开后第二次审批
    # 被埋在「2 条新消息」下面, 员工以为卡住了。
    global _LAST_SUMMARY, _PROMPT_SENT_AT
    _PROMPT_SENT_AT = time.time()          # P56 用: 判断重复的「1」是不是在回答这条新审批
    code = _code_of(command)
    if code is not None:
        rc = _load_readonly()
        v = rc.analyze(code)
        what = rc.describe(v).replace("*", "").replace("`", "")   # 微信会渲染 markdown
        _LAST_SUMMARY = what
        lines = ["🔐 小鲶要执行一个操作，需要你同意：", f"要做的事: {what}"]
        if v.effects:
            lines.append("原因: 它会" + "、".join(v.effects[:3]))
    else:
        cmd = command.strip()
        if len(cmd) > 80:
            cmd = cmd[:80] + "…"
        _LAST_SUMMARY = f"运行命令 {cmd}"
        lines = ["🔐 小鲶要运行一条命令，需要你同意：", f"命令: {cmd}",
                 f"原因: {translate_reason(description)}"]
    lines += ["", "回复 1 执行 · 2 不要执行"]
    return "\n".join(lines)


# ── 危险命令原因 (hermes tools/approval.py DANGEROUS_PATTERNS 等) → 中文 ──────────
# 10/13 盘点: 审批「原因」全是 hermes 的英文描述。逐条译; 不认识的 (hermes 升级新加的)
# 不再露英文, 给一句通用说明 + 打 log, 下次补表。
DANGER_ZH = {
    # 10/14 hermes v0.22 新加的
    "cloud metadata endpoint access (instance credentials)": "读取云服务器的登录凭据",
    "dynamic shell word may expand to arbitrary program execution flag": "命令里有动态拼出来的参数, 可能变成运行任意程序",
    "find dynamic shell word may expand to destructive flag": "查找命令里有动态拼出来的参数, 可能变成删除",
    "invisible or bidirectional Unicode control character (possible obfuscation)": "命令里藏了看不见的字符, 可能在伪装",
    "package manager uninstall": "卸载软件",
    "pipe a credential file into a curl/wget upload (possible exfiltration)": "把密码 / 凭据文件上传到外部",
    "upload a secret or credential file via curl/wget (possible exfiltration)": "把密码 / 凭据文件上传到外部",
    "delete in root path": "删除系统根目录下的东西",
    "recursive delete": "成批删除文件", "recursive delete (long flag)": "成批删除文件",
    "recursive delete (flags after operands)": "成批删除文件",
    "Windows cmd destructive delete": "成批删除文件", "Windows PowerShell destructive delete": "成批删除文件",
    "PowerShell encoded command execution": "运行一段被编码隐藏的命令",
    "PowerShell destructive delete (Remove-Item)": "成批删除文件",
    "Windows destructive delete (recursive/quiet switch)": "不提示地成批删除文件",
    "pipe remote content to PowerShell (iwr | iex)": "下载网上的脚本并直接运行",
    "execute remote content via Invoke-Expression": "下载网上的脚本并直接运行",
    "force kill processes (taskkill /F)": "强制结束程序", "force kill processes (Stop-Process -Force)": "强制结束程序",
    "format filesystem (Format-Volume)": "格式化磁盘", "wipe disk (Clear-Disk)": "清空整块磁盘",
    "disk partitioning (diskpart)": "改磁盘分区", "format drive (format.com)": "格式化磁盘",
    "wipe free space (cipher /w)": "擦除磁盘空闲空间", "grant Everyone access (icacls)": "把文件权限开放给所有人",
    "reset ACLs recursively (icacls /reset)": "成批重置文件权限",
    "delete volume shadow copies (vssadmin)": "删除系统还原备份", "delete backups (wbadmin)": "删除备份",
    "modify boot configuration (bcdedit /set)": "改开机启动设置", "registry delete (reg delete)": "删除注册表项",
    "registry value delete (Remove-ItemProperty -Force)": "删除注册表项",
    "force stop service (Stop-Service -Force)": "强制停止系统服务", "stop/delete service (sc)": "停止或删除系统服务",
    "access to SSH keys (Windows path)": "读取登录密钥", "access to Hermes secrets (Windows path)": "读取小鲶的密钥文件",
    "world/other-writable permissions": "把文件改成谁都能改", "recursive world/other-writable (long flag)": "把文件改成谁都能改",
    "recursive chown to root": "成批改文件归属", "recursive chown to root (long flag)": "成批改文件归属",
    "format filesystem": "格式化磁盘", "disk copy": "整盘复制 / 覆盖磁盘", "write to block device": "直接写磁盘",
    "SQL DROP": "删除数据库表", "SQL DELETE without WHERE": "清空数据库表里的数据", "SQL TRUNCATE": "清空数据库表",
    "overwrite system config": "覆盖系统配置", "stop/restart system service": "停止或重启系统服务",
    "kill all processes": "结束所有程序", "force kill processes": "强制结束程序",
    "force kill processes (killall -KILL)": "强制结束程序", "force kill processes (killall -s KILL)": "强制结束程序",
    "kill processes by regex (killall -r)": "按名字成批结束程序", "fork bomb": "让电脑卡死的命令",
    "pipe remote content to shell": "下载网上的脚本并直接运行",
    "execute remote script via process substitution": "下载网上的脚本并直接运行",
    "execute remote content via command substitution": "下载网上的脚本并直接运行",
    "pipe decoded content to shell (possible command obfuscation)": "运行一段被编码隐藏的命令",
    "pipe xxd-decoded content to shell (possible command obfuscation)": "运行一段被编码隐藏的命令",
    "pipe tr-transformed output to shell (possible command obfuscation)": "运行一段被编码隐藏的命令",
    "pipe openssl-decoded content to shell (possible command obfuscation)": "运行一段被编码隐藏的命令",
    "overwrite system file via tee": "覆盖系统文件", "overwrite system file via redirection": "覆盖系统文件",
    "overwrite project env/config via tee": "覆盖项目配置文件", "overwrite project env/config via redirection": "覆盖项目配置文件",
    "xargs with rm": "成批删除文件", "find -exec/-execdir rm": "成批删除文件", "find -delete": "成批删除文件",
    "stop/restart hermes gateway (kills running agents)": "重启小鲶 (正在做的事会中断)",
    "hermes update (restarts gateway, kills running agents)": "升级并重启小鲶 (正在做的事会中断)",
    "docker with remote daemon redirect (-H/--host)": "操作别的机器上的容器",
    "docker with daemon redirect (--context: alternate daemon)": "操作别的机器上的容器",
    "docker context use (switches default daemon for future commands)": "切换容器的默认目标机器",
    "podman with remote daemon redirect (--url/--connection/--identity)": "操作别的机器上的容器",
    "podman remote mode (-r/--remote: remote daemon)": "操作别的机器上的容器",
    "docker/podman daemon redirect via environment (DOCKER_HOST/CONTAINER_HOST)": "操作别的机器上的容器",
    "docker compose restart/stop/kill/down (container lifecycle)": "停止或重启容器服务",
    "docker restart/stop/kill (container lifecycle)": "停止或重启容器",
    "start gateway outside systemd (use 'systemctl --user restart hermes-gateway')": "绕开系统管理启动小鲶",
    "kill hermes/gateway process (self-termination)": "结束小鲶自己 (正在做的事会中断)",
    "kill process via pgrep/pidof expansion (self-termination)": "按名字结束程序 (可能结束小鲶自己)",
    "kill process via backtick pgrep/pidof expansion (self-termination)": "按名字结束程序 (可能结束小鲶自己)",
    "stop/restart hermes launchd service (kills running agents)": "重启小鲶的后台服务 (正在做的事会中断)",
    "copy/move file into system config path": "往系统配置目录放文件", "overwrite project env/config file": "覆盖项目配置文件",
    "copy/move file into sensitive credential/SSH/shell-rc path": "往存放密钥 / 登录配置的地方放文件",
    "in-place edit of sensitive credential/SSH/shell-rc path": "直接改密钥 / 登录配置文件",
    "in-place edit of sensitive credential/SSH/shell-rc path (long flag)": "直接改密钥 / 登录配置文件",
    "in-place edit of sensitive credential/SSH/shell-rc path (perl/ruby)": "直接改密钥 / 登录配置文件",
    "in-place edit of system config": "直接改系统配置", "in-place edit of system config (long flag)": "直接改系统配置",
    "in-place edit of Hermes config/env": "直接改小鲶的配置", "in-place edit of Hermes config/env (long flag)": "直接改小鲶的配置",
    "in-place edit of Hermes config/env (perl/ruby)": "直接改小鲶的配置",
    "shell execution via heredoc": "运行一段内嵌脚本", "script execution via heredoc": "运行一段内嵌脚本",
    "git reset --hard (destroys uncommitted changes)": "丢掉代码里还没保存的改动",
    "git force push (rewrites remote history)": "强制覆盖远端代码历史",
    "git force push short flag (rewrites remote history)": "强制覆盖远端代码历史",
    "git clean with force (deletes untracked files)": "删除代码目录里没登记的文件",
    "git branch force delete": "强制删除代码分支", "git branch force delete (long flags)": "强制删除代码分支",
    "git branch force delete (long flags, force-first)": "强制删除代码分支",
    "chmod +x followed by immediate execution": "把文件改成可执行并马上运行",
    "sudo with privilege flag (stdin/askpass/shell/list)": "用管理员权限运行",
    "sudo with combined-flag privilege escalation": "用管理员权限运行",
    "command parser limit exceeded": "命令太复杂, 看不清会做什么",
    "command parser limit or malformed executable payload": "命令太复杂, 看不清会做什么",
    "script execution via -e/-c flag": "运行一段内嵌脚本", "shell command via -c/-lc flag": "运行一段内嵌命令",
    "stop/restart hermes gateway via shell-spliced verb (kills running agents)": "重启小鲶 (正在做的事会中断)",
    "dangerous command": "可能有风险的命令",
}
_REASON_PREFIXES = (
    ("arbitrary program execution via ", "通过别的程序运行任意命令"),
    ("Security scan", "安全扫描发现了风险"),
    ("Plugin requires approval for ", "插件要求先确认"),
    ("MCP server '", "外部工具服务要求先确认"),
    ("MCP tool '", "一个不受信任的外部工具要运行"),
    ("Server '", "一个不受信任的外部工具要运行"),
)


def translate_reason(description: str) -> str:
    """hermes 的英文原因 → 中文。多条用 "; " 连接的逐条译、去重; 不认识的不露英文。"""
    out = []
    for part in [p.strip() for p in str(description or "").split("; ") if p.strip()]:
        zh = DANGER_ZH.get(part)
        if zh is None:
            zh = next((z for pre, z in _REASON_PREFIXES if part.startswith(pre)), None)
        if zh is None:
            logger.warning("P51: 危险命令原因没有中文 (hermes 新加的?), 补进 DANGER_ZH: %r", part[:120])
            zh = "可能删改重要数据或影响系统"
        if zh not in out:
            out.append(zh)
    return "、".join(out) or "可能有风险的命令"


_LAST_SUMMARY: Optional[str] = None          # 最近一次审批提示的「要做的事」, 提示语用
_RESOLVED_AT: dict = {}                      # session_key → 上次回答审批的时间 (monotonic)
_RECENT_S = 15 * 60


def build_hint(summary: Optional[str], recently_resolved: bool) -> str:
    """员工在审批等待时说了别的 (「卡住了吗」「我不是回了 1」) —— 告诉他现在在等什么。"""
    head = "你刚才回的已经收到并执行了 ✅ 现在是新的一个操作在等你同意" if recently_resolved \
        else "现在有一个操作在等你同意"
    what = f"：{summary}" if summary else ""
    return f"{head}{what}。回复 1 执行 · 2 不要"


_HINT = build_hint(None, False)


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
            if not intent and "\n" in raw.strip():
                # 10/14 09:57: 微信把挨着发的两条合成一条「为什么一种重复 ？\n1」—— 最后一行是回答就按回答
                intent = parse_approval_reply(raw.strip().splitlines()[-1])
            if intent:
                global _LAST_RESOLVED_AT
                logger.info("P52 审批回复: %r → %s (session=%s)", raw[:20], intent, str(session_key)[:12])
                # 改写成 hermes 原版认得的 approve / deny, 交回原 handler: 它负责 resolve + 回执。
                # 只有单次 approve —— 不改写成 session / always (见文件头「只给单次批准」)。
                event.text = intent
                _RESOLVED_AT[session_key] = time.monotonic()
                _LAST_RESOLVED_AT = time.time()
            elif len(_STRIP.sub("", raw.strip())) <= _MAX_REPLY_LEN:
                logger.info("P52 审批在等, 回复认不出 %r → 提示 1 / 2, 不打断任务", raw[:20])
                last = _RESOLVED_AT.get(session_key)
                hint = build_hint(_LAST_SUMMARY,
                                  last is not None and time.monotonic() - last < _RECENT_S)
                try:
                    # v0.22 (10/14): _adapter_for_source 改名 _delivery_adapter_for (多 bot 时找回收消息的那个)
                    pick = getattr(self, "_delivery_adapter_for", None) or self._adapter_for_source
                    adapter = pick(event.source)
                    if adapter:
                        anchor = self._reply_anchor_for_event(event)
                        await adapter._send_with_retry(
                            chat_id=event.source.chat_id, content=hint, reply_to=anchor,
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


# ── P54 · 只读代码免批 ──────────────────────────────────────────────────
#
# 10/13 鸿波微信实测: 查一份资质列表弹五六次审批, 每次都是读几个 .md。员工原话「只读代码免批」。
# readonly_code.analyze 判定只读 (白名单, 拿不准一律算有副作用) → 直接放行, 不弹审批;
# 其余照走 hermes 原 check_execute_code_guard (单次审批, P51 的大白话提示)。
# 分析本身出错 → 走原 guard (宁可多问一次, 不能漏)。

def wrap_execute_code_guard(orig, analyzer):
    def patched(code, env_type, has_host_access=False, *args, **kwargs):
        try:
            verdict = analyzer.analyze(code or "")
        except Exception as e:  # noqa: BLE001
            logger.warning("P54: 只读分析出错, 照常审批 (%s)", e)
            verdict = None
        if verdict is not None and verdict.read_only:
            logger.info("P54 只读代码免批: %s", analyzer.describe(verdict)[:60])
            return {"approved": True, "message": None, "readonly_auto": True}
        return orig(code, env_type, has_host_access, *args, **kwargs)

    patched._catfish_p54 = True
    return patched


def _patch_p54_readonly_execute_code() -> None:
    try:
        import tools.approval as _approval
    except ImportError as e:
        logger.warning("P54: tools.approval import 失败 (%s), skip", e)
        return
    orig = getattr(_approval, "check_execute_code_guard", None)
    if orig is None:
        logger.warning("P54: tools.approval.check_execute_code_guard 不在 (hermes 改了?), skip")
        return
    if getattr(orig, "_catfish_p54", False):
        return
    _approval.check_execute_code_guard = wrap_execute_code_guard(orig, _load_readonly())
    logger.info("P54 只读 execute_code 免审批 patched")


# ── P55 · 审批超时要让员工知道 ────────────────────────────────────────────
#
# 10/13 14:38: 第二个审批没人回, 5 分钟后 hermes 返回 "BLOCKED: … timed out without user
# response" 给模型, 微信里**什么都不发**; 模型最后回「台账已落盘」, 只字不提那一步没做。
# hermes 超时时没有往聊天里发消息的通道 (guard 跑在工具线程), 所以在给模型的结果里加一句
# 必须照做的中文要求, 由模型的回复带给员工。

TIMEOUT_NOTE = (
    "\n\n[必须告诉员工] 刚才这一步等了几分钟员工没回复, 已经取消, 没有执行。"
    "回复员工时要明确说这一步没做 (例: 「刚才要你同意的那一步你没回复, 我没执行; 需要的话说一声我重做」), "
    "不要说已经全部完成。"
)


def add_timeout_note(result):
    if isinstance(result, dict) and not result.get("approved") \
            and "timed out without user response" in str(result.get("message") or ""):
        result = dict(result)
        result["message"] = str(result["message"]) + TIMEOUT_NOTE
    return result


def wrap_guard_with_timeout_note(orig):
    def patched(*args, **kwargs):
        return add_timeout_note(orig(*args, **kwargs))

    # 保留里层的 _catfish_p54 等标记: 插件重载时 P54 的"已装过"判断才不会再包一层
    patched.__dict__.update(getattr(orig, "__dict__", {}))
    patched._catfish_p55 = True
    return patched


def _patch_p55_approval_timeout_note() -> None:
    try:
        import tools.approval as _approval
        import tools.terminal_tool as _terminal
    except ImportError as e:
        logger.warning("P55: tools.approval / terminal_tool import 失败 (%s), skip", e)
        return
    # execute_code: 包在 P54 外面 (P54 先装); 终端命令: terminal_tool 用别名 _check_all_guards_impl
    targets = ((_approval, "check_execute_code_guard"), (_approval, "check_all_command_guards"),
               (_terminal, "_check_all_guards_impl"))
    for mod, attr in targets:
        orig = getattr(mod, attr, None)
        if orig is None:
            logger.warning("P55: %s.%s 不在 (hermes 改了?), skip 这一处", mod.__name__, attr)
            continue
        if not getattr(orig, "_catfish_p55", False):
            setattr(mod, attr, wrap_guard_with_timeout_note(orig))
    logger.info("P55 审批超时告诉员工 patched")


# ── P56 · 连着两次回「1」, 第二次别被当成重复消息吞掉 ─────────────────────────
#
# 10/13 15:08: 第一个审批回「1」批了; 18 秒后第二个审批, 员工再回「1」—— hermes 日志里**没有
# 这条入站**。weixin.py:1481-1488 有"内容去重": 同一个人 5 分钟内发同样的文字, 当成通道重投
# 直接丢掉 (iLink 偶尔会换个 message_id 把同一条消息再投一次)。审批回答恰恰总是同一个「1」。
#
# 不能简单放行「1」: 通道晚到的重投如果落在新审批之后, 就成了员工没看见就批准。所以三条都满足
# 才放行: ① 是审批回答; ② 上一次同样的回答之后又发过新的审批提示, 或者当前审批还没被回答过
# (上一个「1」被微信合并进别的话里了); ③ 这条消息自己的发送时间 (create_time_ms) 在新提示之后
# —— 重投的旧消息带的是旧时间。拿不到发送时间就不放行 (宁可多问)。

_PROMPT_SENT_AT = 0.0        # 最近一次审批提示生成的时间 (time.time), format_approval_prompt 里记
_CLOCK_SLACK_S = 2.0         # 手机 / 服务器 / 本机时钟的误差余量
_LOGGED_FIELDS = False
_LAST_RESOLVED_AT = 0.0      # 最近一次审批真被回答 (P52 认出 1/2) 的时间 (time.time)


def should_bypass_content_dedup(prev_seen: Optional[float], created_s: Optional[float],
                                prompt_at: float, resolved_at: float = 0.0) -> bool:
    """放行条件: 这条发在当前审批提示之后 (重投的旧消息带旧时间, 拦住), 并且满足其一:
      · 上一次同样的回答之后又来了新审批 (10/13 15:08 连着两个审批);
      · 当前这个审批还没被回答过 —— 10/14 09:57 上一个「1」被微信合并进别的话里没起作用,
        之后再回的「1」都被当重复吞了, 审批超时。"""
    if prev_seen is None or created_s is None:
        return False
    if created_s < prompt_at - _CLOCK_SLACK_S:
        return False
    return prompt_at > prev_seen or prompt_at > resolved_at


def _created_seconds(message) -> Optional[float]:
    v = message.get("create_time_ms") if isinstance(message, dict) else None
    try:
        return float(v) / 1000.0 if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def wrap_process_message(orig, extract_text):
    """包 WeixinAdapter._process_message(self, message)。只在放行条件全满足时从去重缓存里删掉那一条。"""
    import hashlib

    async def patched(self, message):
        global _LOGGED_FIELDS
        try:
            if not _LOGGED_FIELDS and isinstance(message, dict):
                _LOGGED_FIELDS = True
                logger.info("P56 微信入站消息字段 (只记字段名, 核对有没有发送时间): %s", sorted(message.keys()))
            text = extract_text(message.get("item_list") or []) if isinstance(message, dict) else ""
            if text and parse_approval_reply(text):
                sender = str(message.get("from_user_id") or "").strip()
                key = f"content:{sender}:{hashlib.md5(text.encode()).hexdigest()}"   # 跟 weixin.py:1485 同式
                seen = getattr(getattr(self, "_dedup", None), "_seen", None)
                prev = seen.get(key) if isinstance(seen, dict) else None
                created = _created_seconds(message)
                if should_bypass_content_dedup(prev, created, _PROMPT_SENT_AT, _LAST_RESOLVED_AT):
                    seen.pop(key, None)
                    logger.info("P56 放行重复的审批回答 %r (上一次 %.0fs 前; 之后有新审批或当前审批还没回答)", text, time.time() - prev)
                elif prev is not None and created is None:
                    logger.warning("P56 重复的审批回答 %r 没有 create_time_ms, 按重复丢弃 (宁可多问)", text)
        except Exception as e:  # noqa: BLE001 — 判断出错就走原逻辑
            logger.debug("P56 判断异常 (走原逻辑): %s", e)
        return await orig(self, message)

    patched._catfish_p56 = True
    return patched


def _patch_p56_weixin_repeat_answer() -> None:
    try:
        from gateway.platforms import weixin as _wx
    except ImportError as e:
        logger.warning("P56: gateway.platforms.weixin import 失败 (%s), skip", e)
        return
    cls = getattr(_wx, "WeixinAdapter", None)
    orig = getattr(cls, "_process_message", None) if cls else None
    extract = getattr(_wx, "_extract_text", None)
    if orig is None or extract is None:
        logger.warning("P56: WeixinAdapter._process_message / _extract_text 不在 (hermes 改了?), skip")
        return
    if getattr(orig, "_catfish_p56", False):
        return
    cls._process_message = wrap_process_message(orig, extract)
    logger.info("P56 微信连着回「1」不被去重吞掉 patched")
