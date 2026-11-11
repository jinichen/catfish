"""每轮对话带上员工确认过的偏好画像 (11/12 鸿波「按这 3 条改」第 1 条)。

画像 (~/.catfish/user_profile.json, tool-bridge user_profile.py 管写) 原来只能等模型自己想起来
调 catfish_user_profile_get —— 实测 30 天 1429 个对话一次都没调, 确认过的偏好等于没用。
现在每轮 prefetch 把**确认过的值**带进去 (几行字), 待确认的提议不带 (没确认就不能当真)。

同时告诉模型: 员工明确说出长期偏好时才调 propose 记原话, 自己推测的不记。
这里只读文件, 不写; 读坏了返空, 不拖垮 prefetch。
"""
from __future__ import annotations

import json
from pathlib import Path

# 跟 tool-bridge user_profile.ALLOWED_FIELDS / Companion UserProfileCard 同一套名字
FIELD_LABEL = {
    "writing_style.tone": "语气",
    "writing_style.length_pref": "回答长短",
    "writing_style.bullet_pref": "列表还是段落",
    "work_pattern.peak_hours": "工作黄金时段",
    "work_pattern.task_pref": "任务怎么呈现",
    "work_pattern.review_pref": "看材料的习惯",
    "personality.pace": "节奏",
    "personality.feedback_style": "反馈方式",
    "personality.deference": "称呼和口吻",
}
VALUE_LABEL = {"formal": "正式", "casual": "随意"}

_RULE = (
    "员工在对话里**明确说出**长期偏好时 (例「以后回答短一点」「别用列表」), "
    "调 catfish_user_profile_propose 记下他的原话; 只是你的推测、或只对这一次的要求, 不记。"
)


def render_profile(catfish_home: Path) -> str:
    try:
        data = json.loads((catfish_home / "user_profile.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    lines = []
    if isinstance(data, dict):
        for field, label in FIELD_LABEL.items():
            f = data.get(field)
            v = f.get("value") if isinstance(f, dict) else None
            if isinstance(v, str) and v.strip():
                lines.append(f"- {label}: {VALUE_LABEL.get(v, v)}")
    head = "## 👤 员工确认过的偏好 (照着做, 员工这一轮另有要求时以这一轮为准)\n\n"
    if not lines:
        return "## 👤 员工偏好画像\n\n还没有确认过的偏好。" + _RULE
    return head + "\n".join(lines) + "\n\n" + _RULE
