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

import re
from typing import List

# 常见密码 / 凭据模式
_CREDENTIAL_PATTERNS = [
    # 中文 — "密码是 xxx" / "密码: xxx" / "密码为 xxx"
    re.compile(r"密码[是为:\s]+\S+", re.IGNORECASE),
    re.compile(r"密钥[是为:\s]+\S+", re.IGNORECASE),
    re.compile(r"口令[是为:\s]+\S+", re.IGNORECASE),
    # 英文 — "password = xxx" / "password: xxx"
    re.compile(r"password\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"passwd\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"\bpwd\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"api[_-]?key\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"\bsecret\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"\btoken\s*[:=]\s*\S+", re.IGNORECASE),
    # OAuth Authorization header
    re.compile(r"Authorization\s*:\s*Bearer\s+\S+", re.IGNORECASE),
]


def detect_credentials_in_text(text: str) -> List[str]:
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
    hits: List[str] = []
    for p in _CREDENTIAL_PATTERNS:
        if p.search(text):
            # 用 pattern 的 source 做 hit name (脱敏, 不含真密码)
            hits.append(p.pattern[:40])
    return hits


def detect_credentials_in_messages(messages: List[dict]) -> List[str]:
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


def make_warning_message(hits: List[str]) -> str:
    """根据 hits 生成给员工看的 warning 文本."""
    return (
        "⚠️ 你的 prompt 含明文密码 / 凭据 (检测到 "
        f"{len(hits)} 处). 这些会进 LLM 上下文 + audit log + 历史 db, 撤销难. "
        "推荐改用 secret_ref: 把密码存进 macOS Keychain "
        "(`security add-generic-password -a $USER -s eis_pwd -w '<密码>'`), "
        "然后让模型调 catfish_browser_fill(secret_ref='keychain://eis_pwd'). "
        "密码永远不进 LLM 上下文."
    )
