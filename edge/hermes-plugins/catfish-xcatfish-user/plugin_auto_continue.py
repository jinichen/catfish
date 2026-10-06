"""P50 自动续跑 — 模型"说了要做却没调工具"时, 替员工按一次「继续」。

# 症状 (10/5 重点软件企业那条会话, hermes session 20261005_222520_499a61)

同一轮里模型连做 20 次工具调用, 然后某一步返回的是一句 28-39 字的**打算**:
「台账内部两个口径打架…逐项目比对。」—— reasoning 里有 5-9K 字的分析, 可见
正文只有这一句, **tool_calls 为空, finish_reason=stop**。hermes 的循环规则是
"纯文本 + 无工具调用 = 回合结束", 于是回合正常收尾, 员工看到的就是"任务
停了"。手动发一句「继续」它就接着调工具。一晚上停了三次, 三次都是这样
(agent.log: reason=text_response(finish_reason=stop) response_len=28/32/39)。

根因是模型 (qwen-flash, 300K+ 上下文) 的工具调用退化, 不是 hermes 的超时;
hermes 自己只有 empty_response_guard (处理**空**回复), 没有"承诺了动作却没
动作"的续跑。这一层补在 catfish 插件里, 不动 hermes 仓。

# 机制

wrap `APIServerAdapter._run_agent` (chat/completions / sessions / runs 三条路
都走它)。`_orig` 返回后看 result["final_response"]:

  - ≤ 80 字、单段、没代码块、不是在问员工 (没有 ?/？/要不要/需要你…)、
    含"先/再/逐/核/查/比对/更新/重算…"这类**动作意图**词 → 判定为
    "未完成的意图", 用同一会话再跑一轮 `_orig`, user_message 是固定的续跑
    提示 (NUDGE_TEXT), conversation_history = 原历史 + 本轮全部消息
    (含工具调用/结果, 所以模型不会重做已完成的步骤)。
  - 最多续 MAX_NUDGES=2 次; 续跑结果的 final_response 跟前一段用空行拼起来,
    usage 的 token 数相加, result["messages"] 改成**全 transcript 形状**
    (api_server._response_messages_turn_start_index 识别前缀后直接存它),
    所以 api 层存的会话历史跟 SessionDB 一致, 不重复也不丢。
  - 流式路径: 续跑的 delta 走同一个 stream_delta_callback, 员工在同一条
    回复里看到它接着往下做; 两段之间先推一个 "\\n\\n" 分隔。
  - 任何一步 failed / partial / 未 completed / 没有 messages → 不续, 原样返回。

# 不做什么

不判断"上一轮有没有调过工具": 一句短的动作意图本身就该有动作跟着, 哪怕是
回合第一步。误伤的情形 (模型真的只想说一句"先看一下。"然后等员工) 代价是
多跑一轮、提示它"直接做"; 比任务静默停掉便宜得多。
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: 最多替员工按几次「继续」(每条员工消息)。
MAX_NUDGES = 2
#: 超过这个长度的回复不算"一句打算", 当正常回答。
MAX_INTENT_CHARS = 80
#: 续跑时发给模型的 user message。
NUDGE_TEXT = (
    "继续。上一条只说了打算做什么但没有调用工具；"
    "现在直接调用工具把它做完，做完后给出结果，不要再复述计划。"
)

# 动作意图词 —— 来自 10/5 真实停点的措辞 + 同类表达。
_INTENT_RE = re.compile(
    r"(先|再|逐|接着|然后|下一步|继续|重新|重算|重写|重跑|核|查|比对|对表|定位|"
    r"分开|分清|更新|算清|算准|落盘|落文件|补|改|拉出来|看它|钻)"
)
# 在问员工 / 等员工拍板 → 不是未完成的意图, 不续。
_ASK_RE = re.compile(r"[?？]|要不要|需要你|请你|你看|是否|你定|等你|确认一下|告诉我")
# 最后一句是**已经做完的陈述** (带结果/数字/"完了") → 是回答不是打算, 不续。
_DONE_RE = re.compile(r"(完了|完成|已|结论|如下|结果|等于|[0-9０-９])")
_CLAUSE_SPLIT = re.compile(r"[。；;！!]")


def looks_like_unfinished_intent(text: Any) -> bool:
    """一句"打算做 X"而没有做 → True。"""
    if not isinstance(text, str):
        return False
    t = text.strip()
    if not t or len(t) > MAX_INTENT_CHARS:
        return False
    if "```" in t or "\n\n" in t:
        return False
    if _ASK_RE.search(t):
        return False
    # 只看最后一句: 前面是现状描述, 最后一句才是"接下来要干嘛"。
    clauses = [c.strip() for c in _CLAUSE_SPLIT.split(t) if c.strip()]
    if not clauses:
        return False
    last = clauses[-1]
    if _DONE_RE.search(last):
        return False
    return bool(_INTENT_RE.search(last))


def _is_transcript_shaped(history: List[Dict[str, Any]], user_message: Any, msgs: List[Dict[str, Any]]) -> bool:
    """跟 api_server._response_messages_turn_start_index 同判据: msgs 以 history(+user) 开头。"""
    prior = list(history)
    expected = prior + [{"role": "user", "content": user_message}]
    if msgs[: len(expected)] == expected:
        return True
    return bool(prior) and msgs[: len(prior)] == prior


def _full_transcript(history: List[Dict[str, Any]], user_message: Any, result: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    """把 result["messages"] 归一成**全 transcript** (history + user + 本轮消息)。

    返 None = 没法安全拼 (没有 messages), 调用方据此放弃续跑。
    """
    msgs = result.get("messages")
    if not isinstance(msgs, list) or not msgs:
        return None
    if result.get("_compressed") or _is_transcript_shaped(history, user_message, msgs):
        return list(msgs)
    return list(history) + [{"role": "user", "content": user_message}] + list(msgs)


def _merge_usage(a: Any, b: Any) -> Any:
    if not isinstance(a, dict):
        return b
    if not isinstance(b, dict):
        return a
    out = dict(a)
    for k, v in b.items():
        if isinstance(v, (int, float)) and not isinstance(v, bool) and isinstance(out.get(k), (int, float)):
            out[k] = out[k] + v
        elif k not in out:
            out[k] = v
    return out


def wrap_run_agent(orig):
    """返回包了自动续跑的 `_run_agent`。独立成工厂方便测试 (不需要 hermes 可 import)。"""

    async def patched_run_agent(self, *args, **kwargs) -> Tuple[Any, Any]:
        result, usage = await orig(self, *args, **kwargs)
        # 三个调用点都是纯 kwargs; 有位置参数说明是没见过的调用方, 不碰。
        if args or "user_message" not in kwargs:
            return result, usage

        history: List[Dict[str, Any]] = list(kwargs.get("conversation_history") or [])
        user_message = kwargs.get("user_message")
        merged_final = ""
        merged_usage = usage
        cb = kwargs.get("stream_delta_callback")

        for n in range(1, MAX_NUDGES + 1):
            if not isinstance(result, dict):
                break
            if result.get("failed") or result.get("partial") or not result.get("completed", True):
                break
            final = result.get("final_response") or ""
            if not looks_like_unfinished_intent(final):
                break
            transcript = _full_transcript(history, user_message, result)
            if transcript is None:
                logger.info("P50: 想续跑但 result 没有 messages, 放弃 (final=%r)", final[:60])
                break

            logger.info("P50 auto-continue #%d: 模型只说了打算没调工具 (final=%r), 自动续跑", n, final[:60])
            merged_final = (merged_final + "\n\n" + final) if merged_final else final
            if callable(cb):
                try:
                    cb("\n\n")
                except Exception:  # noqa: BLE001
                    pass

            kw = dict(kwargs)
            kw["user_message"] = NUDGE_TEXT
            kw["conversation_history"] = transcript
            eff_sid = result.get("session_id")
            if isinstance(eff_sid, str) and eff_sid and "session_id" in kw:
                kw["session_id"] = eff_sid

            result2, usage2 = await orig(self, *args, **kw)
            merged_usage = _merge_usage(merged_usage, usage2)
            if not isinstance(result2, dict):
                result = result2
                break
            # 存库用的 transcript: 上一轮全量 + 这次的续跑提示 + 这次的消息
            t2 = _full_transcript(transcript, NUDGE_TEXT, result2)
            result = dict(result2)
            if t2 is not None:
                result["messages"] = t2
            history, user_message = transcript, NUDGE_TEXT

        if merged_final and isinstance(result, dict):
            tail = result.get("final_response") or ""
            result["final_response"] = (merged_final + "\n\n" + tail) if tail else merged_final
            return result, merged_usage
        return result, usage

    patched_run_agent._p50_auto_continue = True  # type: ignore[attr-defined]
    return patched_run_agent


def _patch_p50_auto_continue() -> None:
    """装到 APIServerAdapter._run_agent 上 (在 P15 之后装, 续跑也经过 P15 的审批桥)。"""
    try:
        from gateway.platforms.api_server import APIServerAdapter
    except ImportError as e:
        logger.warning("P50: api_server import 失败 (%s), skip patch", e)
        return
    current = APIServerAdapter._run_agent
    if getattr(current, "_p50_auto_continue", False):
        return
    APIServerAdapter._run_agent = wrap_run_agent(current)
    logger.info("P50 auto-continue patched (APIServerAdapter._run_agent wrapped, max %d nudges)", MAX_NUDGES)
