"""prompt token 估算按上游真实用量校准 (10/23)。

# 病

`estimate_prompt_tokens` 用 LiteLLM 的通用分词器估 prompt —— 不是上游模型自己的分词器。
10/23 实测内网 Qwen (qwen_v3_6_35b_a3b):

    中文正文      估 1633  真 998    高估 1.64 倍
    带中文的 JSON 估 3687  真 2572   高估 1.43 倍
    代码          估 1447  真 1451   准

这个估算同时决定两件事, 高估两头出事:
  · 装不装得下 (context_preflight): 鸿波 10/23 在 Mac 上转录一份 27 页的规划, 上游上一轮
    实报 72K token (窗口 128K), 网关却估成 13 万 → 413「对话太长了」→ Hermes 压缩到 2 万,
    再长回去, 再 413 …… 最后「couldn't shrink it enough」。
  · 回复能有多长 (max_tokens = 窗口 − 估算 − 余量): 估多了回复额度就被砍。Windows 上让模型把
    整份材料写进 wiki 条目, 一条工具调用写到一半撞上限被截, 续写又从头写同一段 →
    「kept its response going 3 times with no new content」。

# 做法

每个上游回包都带真实的 prompt_tokens。按上游模型记「真实 / 估算」的比值 (指数平均),
后面的估算乘上这个比值。分词器因模型而异, 所以按模型分别学, 不写死系数。

  · 只学大请求 (估算 ≥ _MIN_LEARN_TOKENS): 小请求里网关自己加的系统段占比大, 比值不代表对话本身。
  · 比值夹在 [0.4, 1.5]: 防一次异常回包把估算带偏。
  · 学够 _MIN_SAMPLES 次才用, 用的时候多留 _SAFETY 余量 —— 宁可略高估 (多压缩一次),
    不要低估 (上游回一个原因全空的 400)。
  · 只在进程内存里 (重启从头学, 几轮对话就学回来)。
"""
from __future__ import annotations

import logging
import threading
from typing import Dict, Tuple

logger = logging.getLogger("catfish.gateway.token_calibration")

_MIN_LEARN_TOKENS = 2000
_MIN_SAMPLES = 3
_ALPHA = 0.2
_CLAMP = (0.4, 1.5)
_SAFETY = 1.05

_lock = threading.Lock()
#: 上游模型名 → (比值, 样本数)
_ratios: Dict[str, Tuple[float, int]] = {}


def record(upstream_model: str | None, estimate: int, real: int) -> None:
    """一次上游回包: 估算 (未校准的) vs 上游实报的 prompt_tokens。"""
    if not upstream_model or estimate < _MIN_LEARN_TOKENS or real <= 0:
        return
    r = min(_CLAMP[1], max(_CLAMP[0], real / estimate))
    with _lock:
        prev = _ratios.get(upstream_model)
        if prev is None:
            ratio, n = r, 1
        else:
            ratio, n = prev[0] * (1 - _ALPHA) + r * _ALPHA, prev[1] + 1
        _ratios[upstream_model] = (ratio, n)
    if n in (_MIN_SAMPLES, 20) or (n % 100 == 0):
        logger.info("prompt 估算校准: model=%s 真实/估算≈%.2f (样本 %d)", upstream_model, ratio, n)


def factor(upstream_model: str | None) -> float:
    """估算该乘的系数; 没学够返 1.0 (照旧)。"""
    if not upstream_model:
        return 1.0
    with _lock:
        hit = _ratios.get(upstream_model)
    if hit is None or hit[1] < _MIN_SAMPLES:
        return 1.0
    return min(_CLAMP[1], hit[0] * _SAFETY)


def apply(upstream_model: str | None, estimate: int) -> int:
    return int(estimate * factor(upstream_model)) if estimate > 0 else estimate


def _reset_for_tests() -> None:
    with _lock:
        _ratios.clear()
