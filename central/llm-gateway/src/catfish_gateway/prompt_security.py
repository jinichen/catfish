r"""Prompt 含敏感凭据检测中间件.

# 为啥需要
==========
员工跟模型说"登录 X, 密码是 jiniaA1+" 这种 prompt, 密码就进了 LLM 上下文 →
进 SOUL middleware → 进 audit metrics → 进 Companion 历史 db → 多处落盘.
撤销难, 合规风险大.

我们的核心建议: 让员工用 secret_ref (keychain://eis_password) 替代明文密码.
但员工习惯一时改不过来, gateway 这层做**检测 + 警告**, 不强拦:

  - prompt 含明显密码模式 → response 加 warning: "你 prompt 含敏感凭据, 建议 secret_ref"
  - audit log 加 security_concern='prompt_credential_detected' 标记
  - 不拦截调用 (员工知道在干嘛)

# 检测策略
==========
正则匹配几种常见密码自然语言写法:

中文:
  密码[是为:][\s]*[非空白]+
  密码[是为:][\s]*[非空白]+
  密钥[是为:][\s]*[非空白]+
  口令[是为:][\s]*[非空白]+
  token[是为:][\s]*[非空白]+

英文:
  password[\s]*[:=][\s]*\S+
  passwd[\s]*[:=][\s]*\S+
  pwd[\s]*[:=][\s]*\S+
  api[_-]?key[\s]*[:=][\s]*\S+
  secret[\s]*[:=][\s]*\S+
  token[\s]*[:=][\s]*\S+

OAuth Authorization header:
  Authorization:[\s]*Bearer[\s]+\S+

注: 假阳性是必然的 (例: "如何重置密码" 也会撞). 我们策略是 **warn 不拦**,
员工自己 judge.

# 为啥不更激进的检测
====================
- 用 LLM 二次扫描 prompt → 慢 + 贵 + 又把 prompt 喂一遍模型 (反而扩散)
- 用 ML 分类器 → 训练数据难, 假阳性更难调
- 简单 regex 已经覆盖 80% 场景, 假阳性可接受

# 隐私
======
检测到的密码值**不写进 audit log**, 只标记 'prompt_credential_detected'.
audit log 里没具体密码值.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger("catfish.gateway.prompt_security")

# 常见密码 / 凭据模式
#
# BL-FIX13 (5/8): 中文 separator 必须是 是/为/: 之一 (不是 \s), 否则
# "请输入密码" / "用户名、密码、" 这种正常陈述句都撞误报. 之前 [是为:\s]+ 是 bug
# (把 \s 塞进 separator 集合, 跟 docstring 设计意图不符 — 设计是
# `密码[是为:][\s]*\S+`, 实际代码把 \s 塞进集合放宽了, 跟没限一样).
_CREDENTIAL_PATTERNS = [
    # 中文 — "密码是 xxx" / "密码: xxx" / "密码为 xxx"
    # separator 必须是 是/为/: , 后面允许有空格再跟非空字符 (>=4 字符防"密码: 1" 这种太短)
    re.compile(r"密码[是为:]\s*\S{4,}", re.IGNORECASE),
    re.compile(r"密钥[是为:]\s*\S{4,}", re.IGNORECASE),
    re.compile(r"口令[是为:]\s*\S{4,}", re.IGNORECASE),
    # 英文 — "password = xxx" / "password: xxx"
    re.compile(r"password\s*[:=]\s*\S{4,}", re.IGNORECASE),
    re.compile(r"passwd\s*[:=]\s*\S{4,}", re.IGNORECASE),
    re.compile(r"\bpwd\s*[:=]\s*\S{4,}", re.IGNORECASE),
    re.compile(r"api[_-]?key\s*[:=]\s*\S{4,}", re.IGNORECASE),
    re.compile(r"\bsecret\s*[:=]\s*\S{4,}", re.IGNORECASE),
    re.compile(r"\btoken\s*[:=]\s*\S{4,}", re.IGNORECASE),
    # OAuth Authorization header
    re.compile(r"Authorization\s*:\s*Bearer\s+\S{4,}", re.IGNORECASE),
    # ── 8/8: 按**形状**认, 不靠关键词前缀 ────────────────────────────
    #
    # 借鉴 hermes v0.20 agent/monitoring/redaction.py 的做法 —— 它在
    # redact_sensitive_text 之外**另加** "bearer/token-shape patterns"。
    # 抄的是这个思路, 不是代码: 网关是独立进程 / 独立 venv, 中央服务器上
    # 根本没装 hermes, import 不到 (5/11 BL-HERMES013-1 那次也是同样的镜像法)。
    #
    # 为什么关键词不够: 上面那些模式全都要求前面有 password= / token: 之类的
    # 引子。而上游回显往往是裸的:
    #     "unexpected token eyJhbGciOi... "     ← JWT 直接跟在普通英文后面
    #     "invalid key sk-proj-AbCd..."
    # 8/8 实测这两条都漏。而员工机 hermes 配置里的 model.api_key 就是  # noqa: BOUNDARY (举例说明 JWT 来源, 中央端不读这个文件)
    # eyJ 开头的 JWT, 上游 401 回显时会原样落进 audit。
    #
    # 长度门槛写得比较高 (JWT 三段各 >=10, sk- 后 >=20), 因为这两条**没有
    # 关键词兜底**, 误伤的是正常文本。base64 片段在错误信息里也常见, 但
    # 凑不齐"三段点分且每段够长"这个形状。
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
]


def detect_credentials_in_text(text: str) -> list[str]:
    """扫一段文本, 返回所有 match 到的 pattern 类型 (不返回真值).

    Args:
        text: 要扫的文本 (例: user message content)

    Returns:
        list of pattern names that matched. 空列表 = 没检测到.
        例: ['password=', '密码:', 'Bearer-header']

    永远不抛.
    """
    if not text or not isinstance(text, str):
        return []
    hits: list[str] = []
    for p in _CREDENTIAL_PATTERNS:
        if p.search(text):
            # 用 pattern 的 source 做 hit name (脱敏, 不含真密码)
            hits.append(p.pattern[:40])
    return hits


# 8/8: scrub_credentials_in_text 删了 —— 唯一的调用方 metrics.py 改成存**分类码**
# 之后它就成了死代码 (军规: 死代码要删掉)。
#
# 值得记一下这个顺序: 我先加固了它 (fail-closed + token 形状, commit b1baf9c),
# 紧接着按"中央端严禁看到员工数据"把 metrics 改成不存原文 —— 后一个改动把前一个
# 的对象整个拿掉了。加固之前没先问"这个字段到底该不该存原文", 是顺序搞反了。
#
# 但那次加的**两条 token 形状模式留着了**: 它们在 _CREDENTIAL_PATTERNS 里,
# detect_credentials_in_text / detect_credentials_in_messages 共用同一张表
# (app.py:3114 给员工发"你 prompt 里有凭据"的警告), 员工粘贴裸 JWT / sk- key
# 现在认得出来。那半边不是死代码。
#
# 真要脱敏一段自由文本时: 别复活这个函数, 先想想那段文本该不该出现在中央端。


def detect_credentials_in_messages(messages: list[dict]) -> list[str]:
    """扫 OpenAI-style messages 数组里所有 user content, 检测密码模式.

    Args:
        messages: [{role, content}, ...] (OpenAI chat completion messages)

    Returns:
        所有 match 到的 pattern 名字 (去重). 空 = 没检测到.
    """
    if not messages or not isinstance(messages, list):
        return []
    all_hits: set = set()
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        # 只扫 user 消息 (assistant / system / tool 不扫)
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        # content 可能是 str 也可能是 multimodal list
        if isinstance(content, str):
            all_hits.update(detect_credentials_in_text(content))
        elif isinstance(content, list):
            # multimodal: [{type:"text", text:"..."}, {type:"image_url", ...}]
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    all_hits.update(detect_credentials_in_text(part.get("text", "")))
    return sorted(all_hits)


def make_warning_message(hits: list[str]) -> str:
    """根据 hits 生成给员工看的 warning 文本."""
    return (
        "⚠️ 你的 prompt 含明文密码 / 凭据 (检测到 "
        f"{len(hits)} 处). 这些会进 LLM 上下文 + audit log + 历史 db, 撤销难. "
        "推荐改用 secret_ref: 把密码存进 macOS Keychain "
        "(`security add-generic-password -a $USER -s eis_pwd -w '<密码>'`), "
        "然后让模型调 catfish_browser_fill(secret_ref='keychain://eis_pwd'). "
        "密码永远不进 LLM 上下文."
    )
