"""FSRS 复习排期 (11/18, docs/LEARNING-PLAN.md §5) —— 换掉题库原来的 SM-2。

FSRS (Free Spaced Repetition Scheduler, Anki 23.10 起的默认算法) 用三个量描述一张卡:
  · 难度 D (1–10): 这张卡对这个人有多难;
  · 稳定性 S (天): 记忆保持率从 100% 掉到 90% 要多少天;
  · 可提取率 R: 隔了 t 天还想得起来的概率 = (1 + F·t/S)^-0.5。
每次复习按「这次想没想起来、想起来费不费劲」更新 D 和 S, 下次复习排在 R 掉到 90% 的那天 (= S 天后)。

这里是 FSRS-4.5 的公式 + 官方默认参数 (open-spaced-repetition/fsrs4anki, MIT), 自己用 Python 写 ——
不加依赖 (tool-bridge 跑在 hermes 的 Python 里, 客户机离线装, 多一个包就多一道坑)。
不按个人数据训练参数 (数据太少), 跟原来 SM-2 一样是「冷启动规则」, 但遗忘曲线更准。

当天内的「学习步」简化: 忘了 (again) → 10 分钟后再来一遍; 其余直接按天排。
"""
from __future__ import annotations

import math
from typing import Any

W = (0.4872, 1.4003, 3.7145, 13.8206, 5.1618, 1.2298, 0.8975, 0.031, 1.6474, 0.1367, 1.0461,
     2.1072, 0.0793, 0.3246, 1.587, 0.2272, 2.8755)
DECAY = -0.5
FACTOR = 0.9 ** (1 / DECAY) - 1          # = 19/81
RETENTION = 0.9
MAX_INTERVAL = 365
RELEARN_MINUTES = 10
DAY = 86400.0
RATINGS = {"again": 1, "hard": 2, "good": 3, "easy": 4}


def _clamp_d(d: float) -> float:
    return min(10.0, max(1.0, d))


def init_stability(g: int) -> float:
    return max(W[g - 1], 0.1)


def init_difficulty(g: int) -> float:
    return _clamp_d(W[4] - W[5] * (g - 3))


def retrievability(elapsed_days: float, s: float) -> float:
    return (1 + FACTOR * max(0.0, elapsed_days) / s) ** DECAY


def interval_days(s: float) -> int:
    return int(min(MAX_INTERVAL, max(1, round(s / FACTOR * (RETENTION ** (1 / DECAY) - 1)))))


def next_difficulty(d: float, g: int) -> float:
    nd = d - W[6] * (g - 3)
    return _clamp_d(W[7] * init_difficulty(3) + (1 - W[7]) * nd)


def recall_stability(d: float, s: float, r: float, g: int) -> float:
    hard = W[15] if g == 2 else 1.0
    easy = W[16] if g == 4 else 1.0
    return s * (1 + math.exp(W[8]) * (11 - d) * s ** (-W[9]) * (math.exp((1 - r) * W[10]) - 1) * hard * easy)


def forget_stability(d: float, s: float, r: float) -> float:
    return min(s, W[11] * d ** (-W[12]) * ((s + 1) ** W[13] - 1) * math.exp((1 - r) * W[14]))


def review(card: dict[str, Any] | None, rating: str, now: float) -> dict[str, Any]:
    """一次复习 → 新状态 {stability, difficulty, state, due, interval (天), reps, lapses, last_review}。
    card 为 None 或 stability 为空 = 新卡。纯函数。"""
    g = RATINGS.get(rating)
    if not g:
        raise ValueError(f"rating 只能是 {tuple(RATINGS)}")
    c = dict(card or {})
    reps, lapses = int(c.get("reps") or 0), int(c.get("lapses") or 0)
    s, d = c.get("stability"), c.get("difficulty")
    if not s or d is None:
        s, d = init_stability(g), init_difficulty(g)
    else:
        last = float(c.get("last_review") or now)
        r = retrievability((now - last) / DAY, float(s))
        d0 = float(d)
        d = next_difficulty(d0, g)
        s = forget_stability(d0, float(s), r) if g == 1 else recall_stability(d0, float(s), r, g)
        if g == 1:
            lapses += 1
    if g == 1:
        due, iv, state = now + RELEARN_MINUTES * 60, 0.0, "relearning"
    else:
        iv, state = float(interval_days(s)), "review"
        due = now + iv * DAY
    return {"stability": round(s, 4), "difficulty": round(d, 4), "state": state, "due": due, "interval": iv,
            "reps": reps + 1, "lapses": lapses, "last_review": now}


def preview(card: dict[str, Any] | None, now: float) -> dict[str, str]:
    """四个按钮各自排到什么时候 (给界面写在按钮上); 保证 难 ≤ 想起来了 < 很轻松。"""
    out, days = {}, {}
    for name in RATINGS:
        st = review(card, name, now)
        days[name] = st["interval"]
    if days["good"] < days["hard"]:
        days["good"] = days["hard"]
    if days["easy"] <= days["good"]:
        days["easy"] = days["good"] + 1
    for name, iv in days.items():
        out[name] = f"{RELEARN_MINUTES} 分钟" if name == "again" else human_days(iv)
    return out


def ordered_interval(card: dict[str, Any] | None, rating: str, now: float) -> float:
    """跟 preview 一样的保序规则, 真正排期时用 (按钮上写的跟实际排的一致)。"""
    st = review(card, rating, now)
    if rating in ("good", "easy"):
        hard = review(card, "hard", now)["interval"]
        good = max(review(card, "good", now)["interval"], hard)
        if rating == "good":
            return good
        return max(st["interval"], good + 1)
    return st["interval"]


def human_days(iv: float) -> str:
    if iv < 1:
        return "今天"
    if iv < 30:
        return f"{int(iv)} 天"
    if iv < 365:
        return f"{round(iv / 30)} 个月"
    return "1 年"
