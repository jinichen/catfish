"""execute_code 守卫 helpers — 抽自 adapter.py (5/21 拆分).

两个守卫:
    _check_execute_code_misuse    — 拦"sandbox 里调 catfish_* 必死锁"功能错
    _check_execute_code_security  — 拦"sandbox 里做坏事"安全错 (越权/外联/凭证读取)

设计要点:
    - 都限 execute_code / shell_exec / python / bash 这四个 hermes 沙箱工具
    - 命中返 friendly error 字符串, 上游直接拒绝;  返 None = OK
    - security 守卫支持 env CATFISH_EXEC_GUARD=warn 只 log 不拦 (开发期 / 信任环境)
    - 不绝对完备 (能被 obfuscate 绕), 但显式拦 = "鲶鱼明确禁止", audit 留痕
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict

logger = logging.getLogger("catfish.tool_bridge.adapter")


#: execute_code 误用守卫 — sandbox 子进程拿不到 hermes session, 调 catfish_*
#: 必死锁. 这些子串只要在脚本里出现, 大概率是模型搞错 (踩过坑 2026-04-28 鸿波 demo).
_EXECUTE_CODE_FORBIDDEN_PATTERNS = (
    "catfish_browser_",
    "catfish_screenshot",
    "catfish_skill_",
    "catfish_tool_bridge",
    "import catfish_",
    "from catfish_",
)


def _check_execute_code_misuse(name: str, args: Dict[str, Any]) -> str | None:
    """检测 execute_code 沙箱误调用 catfish 工具. 命中返回 friendly error 字符串.

    返回 None = OK; 字符串 = 应该立即拒绝 + 把字符串塞进 error 字段.

    为啥拦: hermes execute_code 是 bash/python sandbox 子进程, 跟 hermes 主进程
    完全隔离, 拿不到 tool-bridge unix socket / browser session. 模型在脚本里
    `import catfish_browser_*` 或调对应函数必死锁等 30s timeout, 浪费员工时间.
    SOUL.md § execute_code 红线已经写过纪律, 这里加工程兜底.
    """
    if name not in {"execute_code", "shell_exec", "python", "bash"}:
        return None
    # 拼起来: code / command / input 等常见字段
    text_parts: list[str] = []
    for key in ("code", "command", "input", "script", "args"):
        v = args.get(key)
        if isinstance(v, str):
            text_parts.append(v)
        elif isinstance(v, list):
            text_parts.extend(str(x) for x in v if isinstance(x, str))
    text = "\n".join(text_parts).lower()
    if not text:
        return None
    hits = [p for p in _EXECUTE_CODE_FORBIDDEN_PATTERNS if p.lower() in text]
    if not hits:
        return None
    return (
        f"⚠️ {name} 沙箱里检测到 catfish 工具调用 ({hits[0]}). "
        f"这必失败 — sandbox 子进程拿不到 hermes browser session / tool-bridge socket. "
        f"请用原生 tool calling 直接调 catfish_browser_* 等, 不要写脚本调. "
        f"详见 SOUL.md § execute_code 红线."
    )


# 5/6 安全 P1 G3: execute_code 安全守卫.
#
# 真正的 sandbox 在 hermes 那边 (我们这边只是 dispatcher), 但能在 dispatcher 层
# 拦"明显不合规"的脚本: 越权 path / 外联 / 危险 shell / 凭证读取.
# 不绝对完备 (能被 obfuscate 绕), 但显式拦截 = "鲶鱼明确禁止这种行为", audit 留痕.
#
# 命中时:
#   - 默认: 返回 error (LLM 看到, 不执行)
#   - env CATFISH_EXEC_GUARD=warn: 只 log, 不拦 (开发期调试用)
_EXECUTE_CODE_DANGEROUS_PATTERNS: tuple[tuple[str, str], ...] = (
    # ── 凭证 / 敏感目录 ────────────────────────────
    ("~/.ssh", "读员工 SSH 私钥 — 严禁"),
    ("/.ssh/id_", "读员工 SSH 私钥 — 严禁"),
    ("~/.aws/credentials", "读 AWS 凭证 — 严禁"),
    ("~/.docker/config.json", "读 Docker registry 凭证 — 严禁"),
    ("/library/keychains", "读 macOS Keychain — 严禁 (用 secret_resolver / keychain://)"),
    ("/etc/shadow", "读 Linux 密码 hash — 严禁"),
    ("/etc/passwd", "读系统账户清单 — 严禁"),
    ("netrc", "读 ~/.netrc 凭证 — 严禁"),
    # ── 网络外联 (data exfil 风险) ────────────────
    ("curl http", "外联网络 — 严禁 (用 catfish_browser_* / catfish_fetch_url, 走 audit)"),
    ("curl -x", "外联网络 — 严禁"),
    ("wget http", "外联网络 — 严禁"),
    ("requests.get(", "Python 外联网络 — 严禁 (走 catfish_fetch_url 留 audit)"),
    ("requests.post(", "Python 外联网络 — 严禁"),
    ("urllib.request.urlopen(", "Python 外联网络 — 严禁"),
    ("urllib2.urlopen(", "Python 外联网络 — 严禁"),
    ("httpx.get(", "Python 外联网络 — 严禁"),
    ("httpx.post(", "Python 外联网络 — 严禁"),
    ("aiohttp.clientsession", "Python 外联网络 — 严禁"),
    ("socket.connect(", "Python raw socket — 严禁"),
    # ── 危险 shell ──────────────────────────────
    ("rm -rf /", "递归删根目录 — 严禁"),
    ("rm -rf ~", "递归删 home — 严禁"),
    (":(){:|:&};:", "fork bomb — 严禁"),
    ("dd if=/dev/", "raw disk 操作 — 严禁"),
    ("mkfs.", "格式化 — 严禁"),
    ("> /dev/sd", "写裸盘 — 严禁"),
    ("chmod 777 /", "全盘权限放开 — 严禁"),
)


def _check_execute_code_security(name: str, args: Dict[str, Any]) -> str | None:
    """检测 execute_code 脚本里的危险操作 (越权/外联/凭证). 命中返回 error 字符串.

    限定 execute_code / shell_exec / python / bash 工具.
    跟 _check_execute_code_misuse 协同: misuse 拦"调错 catfish 工具" (功能错),
    security 拦"做坏事" (安全错).

    env CATFISH_EXEC_GUARD=warn 只 log 不拦 (开发期 / 信任环境用).
    """
    if name not in {"execute_code", "shell_exec", "python", "bash"}:
        return None
    text_parts: list[str] = []
    for key in ("code", "command", "input", "script", "args"):
        v = args.get(key)
        if isinstance(v, str):
            text_parts.append(v)
        elif isinstance(v, list):
            text_parts.extend(str(x) for x in v if isinstance(x, str))
    text = "\n".join(text_parts).lower()
    if not text:
        return None

    hits = [(p, reason) for p, reason in _EXECUTE_CODE_DANGEROUS_PATTERNS if p.lower() in text]
    if not hits:
        return None

    pattern, reason = hits[0]
    mode = (os.environ.get("CATFISH_EXEC_GUARD") or "deny").strip().lower()
    msg = (
        f"🛡️ execute_code 安全守卫拦截: 检测到 {pattern!r} — {reason}. "
        f"鲶鱼禁止 LLM 通过 execute_code 做这些. "
        f"如需读特定文件/调 API, 用对应的 catfish_* 工具走 audit log."
    )
    if mode == "warn":
        # warn 模式只记录不拦 (默认 deny, 开发期调试可设 warn)
        logger.warning("[exec_guard:warn] %s | text 前 200 字: %s", msg, text[:200])
        return None
    return msg
