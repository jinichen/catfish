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
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("catfish.gateway.proactive")


_JOURNAL_PATH = Path.home() / ".catfish" / "employee_journal.md"
_JOURNAL_PATH_FALLBACK = Path.home() / ".hermes" / "employee_journal.md"  # 老路径兼容
_TAIL_BYTES = 8000  # ~30 行


def _read_journal_tail() -> str:
    """读 journal 末尾 ~8KB. 没有返空."""
    for path in (_JOURNAL_PATH, _JOURNAL_PATH_FALLBACK):
        try:
            if path.exists():
                with path.open("rb") as f:
                    f.seek(0, 2)
                    size = f.tell()
                    f.seek(max(0, size - _TAIL_BYTES))
                    raw = f.read()
                # 截到第一个完整 段开头
                text = raw.decode("utf-8", errors="ignore")
                idx = text.find("\n## ")
                if idx > 0:
                    text = text[idx + 1:]
                return text.strip()
        except Exception as e:
            logger.warning("读 journal %s 失败: %s", path, e)
    return ""


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
3. **如果 journal 太空**, 用轻邀请, 让员工多聊点 (你能记住).
4. **不超过 30 字**. 1-2 句, 一个具体问题.
5. **不堆套话**. 不说"很高兴帮你/有什么需要".
6. **不演戏**. 不夸张感叹号.

直接输出 1 句 starter, 不要前后缀, 不要 markdown."""


async def generate_starter() -> dict[str, Any]:
    """生成主动闲聊 starter.

    返:
      {
        "starter": str,      # 1-2 句话, 给员工看的
        "context_hint": str, # 用了 journal 的什么 (调试用, 前端可以选择不显示)
        "source": "llm" | "fallback",
      }
    """
    now = datetime.now()
    journal_tail = _read_journal_tail()

    # BL-F14: 不写死模型, 按 use_case tag 选 (private 优先, 内网挂了用 public).
    # catalog 改了不用动这里. 全无可用模型 → 走 fallback 模板.
    from .config import load_config  # 懒 import
    from .internal_models import pick_internal_model  # 懒 import
    config = load_config()
    chosen_model = pick_internal_model("proactive_starter", config)
    if chosen_model is None:
        return {
            "starter": _fallback_starter(now),
            "context_hint": "fallback (catalog 没可用 chat 模型, 全部 api key 没配?)",
            "source": "fallback",
        }

    # gateway loopback HTTP (跟 summarizer 同模式, BL-F12)
    import httpx  # 懒 import
    port = os.environ.get("PORT", "8999")
    gateway_url = os.environ.get(
        "CATFISH_GATEWAY_INTERNAL_URL",
        f"http://127.0.0.1:{port}/v1/chat/completions",
    )
    dev_token = os.environ.get("CATFISH_DEV_TOKEN", "dev-token-local")
    user_prompt = _build_user_prompt(journal_tail, now)

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                gateway_url,
                headers={
                    "Authorization": f"Bearer {dev_token}",
                    "X-Catfish-Skip-Identity": "true",  # 防 SOUL/journal 二次注入死循环
                    "Content-Type": "application/json",
                },
                json={
                    "model": chosen_model.name,
                    "messages": [{"role": "user", "content": user_prompt}],
                    "temperature": 0.7,  # 比 summarizer 高, 让话术不死板
                    "max_tokens": 120,
                    "stream": False,
                },
            )
        if resp.status_code != 200:
            raise RuntimeError(f"gateway 返 {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        text = (data.get("choices", [{}])[0].get("message", {}).get("content", "") or "").strip()
        # 清掉可能的 markdown 装饰
        text = text.strip("`\"'*-> \n")
        if not text:
            raise ValueError("LLM 返空")
        return {
            "starter": text[:120],
            "context_hint": (
                f"journal 末尾 {len(journal_tail)} 字符 + 时段 {_time_window(now)}"
                if journal_tail
                else "journal 空, 走轻邀请"
            ),
            "source": "llm",
        }
    except Exception as e:
        logger.warning("generate_starter LLM 失败, fallback: %s", e)
        return {
            "starter": _fallback_starter(now),
            "context_hint": f"fallback (LLM err: {type(e).__name__})",
            "source": "fallback",
        }


__all__ = ["generate_starter"]
