"""Proactive · 鲶鱼主动闲聊 — BL-E13 C-MVP (五一 sprint 5/2 收尾).

# 目标

让小鲶按时间段主动找员工聊一句, 而不是员工每次开 Companion 主动问.
避免 employee_journal 长期空着, demo 时跨 session 记忆没说服力.

# 工作原理

1. 读最近 journal (~30 行 tail) 拿上下文
2. 按当前时间段决定话题语气:
   - 早 (06-11): 周计划 / 昨日收尾 / 今日重点
   - 中午 (11-14): 上午进展 / 午餐顺手聊
   - 下午 (14-18): 跟 X 开会聊聊 / 邮件 / 进展
   - 傍晚 (18-22): 今日总结 / 周报草稿 (周五加重)
   - 周末 (六/日全天): 轻松闲聊 / 上周复盘 / 下周准备
3. LLM (qwen-flash) 生成 1-2 句 starter, 引用 journal 里**最近 1 条具体内容**让聊天有连续性

# 反模式 — 必须避免

- 通用废话 ("今天怎么样") → 强制 LLM 引用 journal 具体事项
- 太正式 ("请汇报...") → 同事语气, 不端架子
- 过分热情 ("亲爱的鸿波!") → 不演戏

# 边界

- 没 journal → 返一个邀请聊聊的 starter ("最近怎么样? 攒点东西进 journal 我才能记住你")
- LLM 调用失败 → fallback 模板话术 (按时段)
- LLM API key 没配 → 走纯模板
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("catfish.gateway.proactive")


# 5/26 BL-PROACTIVE-DECOUPLE: gateway 不再读员工本机 journal 文件.
# Companion (有自己的 journal_read Tauri 命令) 在调 /api/proactive/* 时
# 把 journal_tail 当 body / 参数传过来. 老 Companion 不传 → fallback 模板.
# Path home 死代码删, _read_journal_tail 函数删.
_TAIL_BYTES = 8000  # ~30 行 (Companion 端裁切到这长度)


# ── 时间段 → 默认 starter (LLM 失败兜底) ────────────────────


def _time_window(now: datetime) -> str:
    """返 'morning' / 'noon' / 'afternoon' / 'evening' / 'weekend'."""
    if now.weekday() >= 5:  # 周六日
        return "weekend"
    h = now.hour
    if 6 <= h < 11:
        return "morning"
    if 11 <= h < 14:
        return "noon"
    if 14 <= h < 18:
        return "afternoon"
    return "evening"


_FALLBACK_STARTERS: dict[str, list[str]] = {
    "morning": [
        "早. 今天三件最重要的事是啥?",
        "早安, 昨天没收尾的有啥要先处理?",
        "今天计划怎么排?",
    ],
    "noon": [
        "上午做了啥, 下午继续什么?",
        "午餐吃啥, 顺便聊聊上午进展?",
        "上午有啥进展? 下午要不要换换脑子?",
    ],
    "afternoon": [
        "下午有啥要推进的?",
        "刚开了什么会? 要不要帮你整理下要点?",
        "进展如何, 卡哪了?",
    ],
    "evening": [
        "今天大概做了哪些事? 攒一句进周报?",
        "今天怎么样? 有什么要复盘的?",
        "下班前 5 分钟, 聊聊今天的收获?",
    ],
    "weekend": [
        "周末好. 想聊聊上周的事吗?",
        "周末轻松点 — 有啥想梳理的, 或随便聊聊也行.",
        "下周计划想理一理吗? 周末提前规划下.",
    ],
}


def _fallback_starter(now: datetime) -> str:
    """LLM 不可用时按时段挑一个."""
    import random
    win = _time_window(now)
    pool = _FALLBACK_STARTERS.get(win, _FALLBACK_STARTERS["evening"])
    return random.choice(pool)


def _first_sentence(text: str, max_chars: int = 80) -> str:
    """5/26 BL-PROACTIVE-FIRST-SENTENCE: LLM 经常违反"1 句话"要求生成多句, 截首句.

    截 4 类句末: 中文 。 ? ! ; 跟英文 . ? !. 优先 . / 。 末尾 (问号 / 感叹号
    保留全句). 都没找到末尾 → max_chars 截.

    保留末尾的句号 / 问号 (跟同事说话感保持). 多个空行 → 第一段.
    """
    text = text.strip()
    # 第一段 (\n\n 分段)
    para = text.split("\n\n", 1)[0].strip()
    # 找第一个中文 / 英文 句末
    for i, ch in enumerate(para):
        if ch in "。.?!?!":
            # 包含这个 char 作为收尾
            return para[: i + 1].strip()
    # 没找到 → 单行截
    one_line = para.split("\n", 1)[0].strip()
    return one_line[:max_chars]


# ── LLM 上下文感知 starter ──────────────────────────────────


def _build_user_prompt(journal_tail: str, now: datetime) -> str:
    """给 LLM 的指令. 让它读 journal + 时段, 生成 1-2 句 starter."""
    win = _time_window(now)
    win_zh = {
        "morning": "早上 (上班前后)",
        "noon": "中午",
        "afternoon": "下午 (工作中)",
        "evening": "傍晚 / 下班前",
        "weekend": "周末",
    }[win]
    weekday_zh = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()]
    if not journal_tail.strip():
        journal_section = "(员工 journal 还很空, 没多少历史. 用更轻的邀请, 鼓励他多聊点 让你能记住他)"
    else:
        journal_section = f"以下是员工最近的 journal 摘要 (你写过的):\n```markdown\n{journal_tail}\n```"

    return f"""你是鲶鱼 (catfish), 员工的 AI 副手. 现在是 {weekday_zh} {win_zh}, 你想主动找员工聊 1 句.

{journal_section}

要求:
1. **同事语气, 不端架子**. 不说"亲爱的"/"请汇报"等. 像你身边一个熟人.
2. **如果 journal 有具体事 (项目/资质/汇报/某个决策)**, 引用其中**最近一条**, 问进展. 例: "上次说要做 X, 现在如何?"
3. **尊重员工最近表态 (P3.5.202 7/9)**: 如果 journal 里员工最近对某事说过 "已办完 / 已交付 / 已确认误报 / 暂时关闭 / 暂缓 / 先放放 / 等通知再说 / 先不管" 等**结案 or 主动搁置**信号, **绝不问那件事** — 员工要么办完了, 要么主动搁置会自己回来找. 挑别的**员工还在跟进**的事; 都没有就走轻邀请 ("最近还行吗?" 之类).
4. **如果 journal 太空**, 用轻邀请, 让员工多聊点 (你能记住).
5. **不超过 30 字**. 1-2 句, 一个具体问题.
6. **不堆套话**. 不说"很高兴帮你/有什么需要".
7. **不演戏**. 不夸张感叹号.
8. **事实为准 (P3.5.206 军规)**: 提到员工的事只用 journal 里字面出现的内容, 不做因果推断 / 价值判断 / 生动化修饰. 禁结论词 (决定/因此/意味着/影响/视为红线/直接影响). 允许衔接词 (同时/然后/目前/另外).

直接输出 1 句 starter, 不要前后缀, 不要 markdown."""


async def generate_starter(
    user_email: str | None = None,
    *,
    journal_tail: str = "",
    model_name: str | None = None,
) -> dict[str, Any]:
    """生成主动闲聊 starter.

    5/26 BL-PROACTIVE-DECOUPLE: gateway 不再读员工本机 journal 文件 + state.db.
    Companion 调 endpoint 时把 journal_tail (8KB ~30 行, 已裁切) + model_name
    (员工最近 session model 名) 当 body 字段传过来. 没传 → fallback 模板.

    Args:
        user_email: 员工 email (audit 用)
        journal_tail: Companion 端读 ~/.catfish/employee_journal.md 末尾 8KB 传过来.  # noqa: BOUNDARY
                      gateway 不再自己读. 空字符串 → 跳 LLM 走 fallback
        model_name: Companion 端从自己 hermes state.db 拿员工最近用的 model 名,
                    传过来. None → 跳 LLM 走 fallback

    返:
      {
        "starter": str,      # 1-2 句话, 给员工看的
        "context_hint": str, # 用了 journal 的什么 (调试用)
        "source": "llm" | "fallback",
      }
    """
    now = datetime.now()

    # 5/26: Companion 没传 journal_tail/model_name → 直接 fallback (gateway 不读 fs)
    if not journal_tail or not model_name:
        return {
            "starter": _fallback_starter(now),
            "context_hint": (
                "fallback (Companion 没传 journal_tail/model_name body 字段 — "
                "老 Companion / 没设 journal / 跳 LLM)"
            ),
            "source": "fallback",
        }

    from .config import load_config  # 懒 import
    from .user_model_resolver import resolve_model_obj
    config = load_config()
    origin_obj = resolve_model_obj(model_name, config)
    if origin_obj is None:
        return {
            "starter": _fallback_starter(now),
            "context_hint": (
                f"fallback (没拿到 {user_email} 最近 session model — "
                "新员工 / 老 schema / 全 model 不可达, 跳过 LLM call)"
            ),
            "source": "fallback",
        }
    candidates = [origin_obj]  # 严格只一个候选, 跟员工 chat 同款

    # gateway loopback HTTP (跟 summarizer 同模式, BL-F12)
    import httpx  # 懒 import
    port = os.environ.get("PORT", "8999")
    gateway_url = os.environ.get(
        "CATFISH_GATEWAY_INTERNAL_URL",
        f"http://127.0.0.1:{port}/v1/chat/completions",
    )
    # BL-FIX37 (5/10): 用 internal-only token, 不再走员工 CATFISH_DEV_TOKEN.
    # BL-FIX29 关掉员工 dev_token 通道后, 老 dev_token 这条链 401, gateway 自己
    # 调自己也挂. 现在用启动时随机生成的 internal token, dev_token.py 优先匹配
    # 它返 internal User. 安全: 32B random 在进程内存, 重启即变, 外部抓不到.
    from .auth.dev_token import ensure_internal_dev_token  # 懒 import
    dev_token = ensure_internal_dev_token()
    user_prompt = _build_user_prompt(journal_tail, now)

    last_error: str | None = None
    text = ""
    for attempt_idx, chosen_model in enumerate(candidates, start=1):
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(
                    gateway_url,
                    headers={
                        "Authorization": f"Bearer {dev_token}",
                        "X-Catfish-Skip-Identity": "true",  # 防 SOUL/journal 二次注入死循环
                        "X-Catfish-Internal": "true",  # BL-F17: 主动闲聊不消耗员工 quota
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": chosen_model.name,
                        "messages": [{"role": "user", "content": user_prompt}],
                        "temperature": 0.7,
                        # 5/26 BL-PROACTIVE-MAX-TOKENS-BUMP v2:
                        # - 120 太小, thinking 模型 (deepseek-flash) reasoning 吃 token,
                        #   实答案被截 ("周一 (5" 半截输出)
                        # - 300 仍不够, gemini-flash 违反 "1 句 30 字" 指令产 296 tokens
                        #   仍被截 ("1-5月份资质通报、" 半截)
                        # - 600 给冗长 model 足够余量出完整答案. 我们 post-process 截首句,
                        #   不展示完整 600 tokens. 1 次 starter 600 tokens 成本可忽略.
                        "max_tokens": 600,
                        "stream": False,
                    },
                )
            if resp.status_code == 200:
                data = resp.json()
                choice = data.get("choices", [{}])[0] if data.get("choices") else {}
                msg = choice.get("message", {}) if isinstance(choice, dict) else {}
                finish_reason = choice.get("finish_reason") if isinstance(choice, dict) else None
                # BL-F19+ (5/5 18:30): DeepSeek V4 thinking mode content 空 reasoning_content 有.
                # 优先 content, fallback reasoning_content (可能含 <think> tag, strip 后能用).
                text = (
                    (msg.get("content") or "")
                    or (msg.get("reasoning_content") or "")
                ).strip()
                # 5/26: finish_reason=length → 被截了, 答案不完整, fallback 模板比硬展示半截好
                if finish_reason == "length":
                    last_error = (
                        f"finish_reason=length 答案被截 ({chosen_model.name}, "
                        f"text={len(text)} 字, '{text[:30]}...')"
                    )
                    logger.warning("generate_starter %s 被 max_tokens 截, 切下一个 / fallback",
                                   chosen_model.name)
                    text = ""  # 清空, 否则 post-loop 会把半截内容当 starter 返出 (5/26 鸿波撞)
                    continue
                if attempt_idx > 1:
                    logger.info(
                        "generate_starter 切到第 %d 候选 %s 成功 (text=%d 字)",
                        attempt_idx, chosen_model.name, len(text),
                    )
                if not text:
                    last_error = f"200 但 content 空 ({chosen_model.name})"
                    logger.warning(
                        "generate_starter %s 200 但 content+reasoning_content 都空, 切下一个",
                        chosen_model.name,
                    )
                    continue
                break
            # BL-F19 (5/5 18:00 鸿波报"小鲶不能自动聊天" 修):
            # 之前只 429 切候选, 5xx 直接 break 不切. 实际场景:
            #   candidate 1 catfish-private-main → VPN 断 → 502
            #   candidate 2 catfish-public-qwen-flash → DashScope 免费层 403 → 502
            #   原代码: candidate 1 502 → break → 不试 candidate 3+ deepseek-flash (健康)
            # 改成所有 retriable 错误 (429 + 5xx) 都切候选, 跟 BL-F15 后的 summarizer 一致.
            # 4xx (除 429) 客户端错误 (auth bad / schema 等) break — 切了也是同样错.
            if resp.status_code == 429 or 500 <= resp.status_code < 600:
                last_error = f"{resp.status_code} ({chosen_model.name})"
                logger.info(
                    "generate_starter %s 返 %d (retriable), 切下一个候选",
                    chosen_model.name, resp.status_code,
                )
                continue
            # 4xx (auth / schema 等) — 切了也是同错, 直接 break
            last_error = f"{resp.status_code}"
            logger.warning(
                "generate_starter %s 返 %d (非 retriable), 不再切",
                chosen_model.name, resp.status_code,
            )
            break
        except Exception as e:
            last_error = f"{type(e).__name__}"
            logger.warning("generate_starter %s 异常 %s, 切下一个", chosen_model.name, e)
            continue

    try:
        # 清掉可能的 markdown 装饰
        text = text.strip("`\"'*-> \n")
        if not text:
            raise ValueError(f"全候选 ({len(candidates)}) 都失败, 最后错: {last_error}")
        # 5/26 BL-PROACTIVE-FIRST-SENTENCE: LLM 违反 "1 句话" 时截首句, 不展示长段
        starter = _first_sentence(text, max_chars=80)
        return {
            "starter": starter,
            "context_hint": (
                f"journal 末尾 {len(journal_tail)} 字符 + 时段 {_time_window(now)}"
                + (f" (LLM 多说了 {len(text) - len(starter)} 字, 截首句)" if len(text) > len(starter) else "")
                if journal_tail
                else "journal 空, 走轻邀请"
            ),
            "source": "llm",
        }
    except Exception as e:
        logger.warning("generate_starter 全候选失败 fallback 模板: %s", e)
        return {
            "starter": _fallback_starter(now),
            "context_hint": f"fallback (last_error={last_error})",
            "source": "fallback",
        }


# ============================================================
# BL-E13.5 真主动 Phase B (5/6) — 信号触发的针对性 starter
# ============================================================
#
# 跟 generate_starter (死时间 9:30/14:00/17:30) 并存. 这个函数是给
# useProactiveTriggers 调的: 收到 signal_kind + context, 让 LLM 看着写
# 针对性 starter (而不是泛"今天怎么样").
#
# 跟死时间版区别:
#   - generate_starter: journal + 时段 → 通用 starter
#   - generate_contextual_starter: + signal 类型 + 触发上下文 → 强相关 starter
#     例: "你 30 分钟前说要弄上会材料, 卡哪我帮看看?" 这种
#
# 失败 fallback: 用 frontend 自己拼的本地模板 (frontend 已有), gateway 这边
# 返 {source: "fallback"} 让 frontend 知道走本地模板.


_SIGNAL_KIND_PROMPTS = {
    "silence": (
        "员工 {minutes_ago} 分钟前说了一句 \"{last_user_text}\" 然后没下文了 "
        "(命中动词关键词 {action_hits}). 你想关心一下他卡哪没. "
        "1 句话, 引用他原话里的具体事 (不是泛问), 像同事走过去问."
    ),
    "deadline": (
        "员工的 employee_journal 里提到的某个事 deadline 是 {days_until} 天后 "
        "({date_str}). journal 上下文片段: \"{journal_excerpt}\". "
        "你想提醒一下, 1 句话, 引用具体事项, 问还差啥."
    ),
    "focus": (
        "员工刚切回 Companion (离开 {minutes_away} 分钟). "
        "走前最后一句是 \"{last_user_text}\". "
        "你想自然地接上, 1 句话, 像他刚回来你抬头问一句, 不要太正式."
    ),
}


def _build_contextual_user_prompt(
    signal_kind: str,
    context: dict[str, Any],
    now: datetime,
) -> str:
    """根据 signal 类型 + context 拼 LLM prompt."""
    weekday_zh = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()]
    template = _SIGNAL_KIND_PROMPTS.get(signal_kind, "")
    try:
        signal_desc = template.format(**{k: str(v) for k, v in context.items()})
    except KeyError as e:
        signal_desc = f"signal {signal_kind} 缺 context 字段 {e}"

    return f"""你是鲶鱼 (catfish), 员工的 AI 副手. 现在是 {weekday_zh} {now.hour}:00.

# 触发上下文 (你为什么现在主动开口)

{signal_desc}

# 要求

1. **同事语气, 不端架子**. 不说"亲爱的"/"请汇报". 像他身边一个熟人.
2. **必须引用具体事** — 触发上下文里的员工原话 / 项目名 / 日期等. 不要泛问.
3. **尊重员工最近表态 (P3.5.202 7/9)**: 触发上下文里如果员工原话是 "已办完 / 已交付 / 已确认误报 / 暂时关闭 / 暂缓 / 先放放 / 等通知再说 / 先不管" 等**结案 or 主动搁置**信号, **不要问那件事** — 员工要么办完了, 要么主动搁置. 遇到这种情况直接输出空字符串, 别硬凑一句 starter (frontend 会走本地兜底或跳过).
4. **不超过 30 字**. 1 句话, 一个具体问题或一句关心.
5. **不堆套话**. 不"很高兴帮你"/"有什么需要".
6. **直接说话**, 别介绍自己 ("我是小鲶, 来看看你..."). 同事不会每次都自报家门.
7. **事实为准 (P3.5.206 军规)**: 只引用触发上下文里字面出现的内容 (员工原话 / 项目名 / 日期 / 邮件主题等), 不做因果推断 / 价值判断 / 生动化修饰. 禁结论词 (决定/因此/意味着/影响/视为红线/直接影响). 允许衔接词 (同时/然后/目前/另外).

直接输出 1 句, 不要前后缀, 不要 markdown."""


async def generate_contextual_starter(
    signal_kind: str,
    context: dict[str, Any],
    user_email: str | None = None,
    *,
    model_name: str | None = None,
) -> dict[str, Any]:
    """生成信号触发的针对性 starter.

    5/26 BL-PROACTIVE-DECOUPLE: gateway 不再读 state.db. Companion 调时传
    model_name (从员工 hermes state.db 拿). 没传 → fallback.
    """
    now = datetime.now()

    # 校验 signal_kind
    if signal_kind not in _SIGNAL_KIND_PROMPTS:
        return {
            "starter": "",
            "context_hint": f"unknown signal_kind: {signal_kind}",
            "source": "fallback",
        }

    # 5/26: Companion 没传 model_name → fallback (gateway 不读 state.db)
    if not model_name:
        return {
            "starter": "",
            "context_hint": "fallback (Companion 没传 model_name body 字段)",
            "source": "fallback",
        }

    from .config import load_config  # noqa: PLC0415
    from .user_model_resolver import resolve_model_obj  # noqa: PLC0415
    config = load_config()
    origin_obj = resolve_model_obj(model_name, config)
    if origin_obj is None:
        return {
            "starter": "",
            "context_hint": f"fallback (model {model_name} 不可达, frontend 走本地模板)",
            "source": "fallback",
        }
    candidates = [origin_obj]

    import httpx  # noqa: PLC0415
    port = os.environ.get("PORT", "8999")
    gateway_url = os.environ.get(
        "CATFISH_GATEWAY_INTERNAL_URL",
        f"http://127.0.0.1:{port}/v1/chat/completions",
    )
    # BL-FIX37 (5/10): internal-only token, 跟 generate_starter 同款.
    from .auth.dev_token import ensure_internal_dev_token  # 懒 import
    dev_token = ensure_internal_dev_token()
    user_prompt = _build_contextual_user_prompt(signal_kind, context, now)

    last_error: str | None = None
    text = ""
    for attempt_idx, chosen_model in enumerate(candidates, start=1):
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                # 8s timeout — 信号触发 starter 没必要等长 (frontend 5s 兜底也够)
                resp = await client.post(
                    gateway_url,
                    headers={
                        "Authorization": f"Bearer {dev_token}",
                        "X-Catfish-Skip-Identity": "true",
                        "X-Catfish-Internal": "true",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": chosen_model.name,
                        "messages": [{"role": "user", "content": user_prompt}],
                        "temperature": 0.6,  # 信号触发更稳, 比死时间低一点
                        # 5/26 BL-PROACTIVE-MAX-TOKENS-BUMP v2: 80 → 600 (跟 generate_starter
                        # 同步, 防 thinking 模型 reasoning + 冗长 model 被截; post-process
                        # 截首句不展示完整 600 tokens).
                        "max_tokens": 600,
                        "stream": False,
                    },
                )
            if resp.status_code == 200:
                data = resp.json()
                choice = data.get("choices", [{}])[0] if data.get("choices") else {}
                msg = choice.get("message", {}) if isinstance(choice, dict) else {}
                finish_reason = choice.get("finish_reason") if isinstance(choice, dict) else None
                text = (
                    (msg.get("content") or "")
                    or (msg.get("reasoning_content") or "")
                ).strip()
                if finish_reason == "length":
                    last_error = (
                        f"finish_reason=length 答案被截 ({chosen_model.name}, "
                        f"text={len(text)} 字)"
                    )
                    text = ""  # 清空, 防 post-loop 用半截内容
                    continue
                if not text:
                    last_error = f"200 但 content 空 ({chosen_model.name})"
                    continue
                break
            if resp.status_code == 429 or 500 <= resp.status_code < 600:
                last_error = f"{resp.status_code} ({chosen_model.name})"
                continue
            last_error = f"{resp.status_code}"
            break
        except Exception as e:
            last_error = f"{type(e).__name__}"
            continue

    text = text.strip("`\"'*-> \n")
    if text:
        # 5/26 BL-PROACTIVE-FIRST-SENTENCE: 截首句, 不展示长段
        starter = _first_sentence(text, max_chars=80)
        return {
            "starter": starter,
            "context_hint": f"signal={signal_kind}, llm via {len(candidates)} candidates",
            "source": "llm",
        }
    return {
        "starter": "",
        "context_hint": f"fallback (signal={signal_kind}, last_error={last_error})",
        "source": "fallback",
    }


__all__ = ["generate_starter", "generate_contextual_starter"]
