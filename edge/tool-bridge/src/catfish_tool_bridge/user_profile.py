"""BL-MM7 结构化用户画像 — 5/6 鸿波"BL-MM7、MM8 直接开始" 后 ship.

# 跟 catfish_remember 区别

- `session_facts.json` (catfish_remember 写的): **session 内** 员工告诉的具体硬事实
  例: eis_url='http://eis.ffcs.cn', password_ref='keychain://eis_password'.
  session 结束清空.
- `user_profile.json` (本模块): **长期** 员工画像, 跨 session 累积
  例: writing_style.tone='直接', personality.pace='急', work_pattern.task_pref='列清单'.
  永远不清空 (除非员工主动 reset). 5-10 个 trait, 每个累积 evidence_count.

# 设计原则

1. **3 次 evidence 才 propose**: LLM 不能"看到一次就武断", 同 trait 累积 ≥ 3 次独立 evidence
   后才返 'should_confirm', 让 LLM 跟员工确认 ("我感觉你写汇报偏直接, 对吗?").
2. **员工锁定**: 员工 confirm 时可 lock=True, LLM 不再 propose 改这个字段.
3. **红线字段**: 健康/财务/感情/政治/宗教/家庭关系 — LLM 严禁 propose.
   只员工自己 confirm 触发 (例: 员工说"我天主教", LLM 不能存; 员工显式说"记一下我天主教",
   员工自己确认才存).
4. **透明可控**: 全部画像在 ~/.catfish/user_profile.json, 员工可 inspect / edit / reset.
   Dashboard UserProfileCard 显示全部字段 + 一键清空.

# Schema

```json
{
  "writing_style.tone": {
    "value": "直接",
    "evidence": [
      {"text": "员工说'别绕弯子'", "ts": 1234567890.0},
      ...最多 10 条
    ],
    "locked": false,
    "last_confirmed": 1234567890.0,
    "proposed_value": null
  },
  ...
}
```
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


USER_PROFILE_PATH = Path.home() / ".catfish" / "user_profile.json"

# 允许的字段 (扩展时加这里). 用 dotted name 分类.
# 值类型: list of allowed values (枚举) 或 None (自由文本)
ALLOWED_FIELDS: Dict[str, Optional[List[str]]] = {
    # 文书风格
    "writing_style.tone": ["formal", "casual", "直接", "委婉", "幽默"],
    "writing_style.length_pref": ["短", "中", "长"],
    "writing_style.bullet_pref": ["列表", "段落", "混合"],
    # 工作模式
    "work_pattern.peak_hours": None,  # 自由文本: "9:00-12:00 / 14:00-18:00"
    "work_pattern.task_pref": ["列清单", "看图表", "纯文字", "对照表"],
    "work_pattern.review_pref": ["先看摘要", "全量看", "只看异常"],
    # 性格 / 沟通
    "personality.pace": ["急", "缓"],
    "personality.feedback_style": ["大点拨", "细节确认", "结果导向"],
    "personality.deference": ["平等", "尊重正式", "随意"],
}

# 5/6 红线 — 这些字段 LLM 严禁 propose, 只员工自己 confirm 触发
NO_PROPOSE_FIELDS = {
    "personal.relationship",
    "personal.health",
    "personal.financial",
    "personal.political",
    "personal.religious",
    "personal.family",
}

MIN_EVIDENCE_TO_PROPOSE = 3
MAX_VALUE_LEN = 200
MAX_EVIDENCE_LEN = 500
MAX_EVIDENCE_PER_FIELD = 10
MAX_FIELDS = 50  # 防员工/LLM 乱塞


def _read_profile() -> Dict[str, Any]:
    """读 user_profile.json, 文件不存在或损坏返空 dict."""
    if not USER_PROFILE_PATH.exists():
        return {}
    try:
        with open(USER_PROFILE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_profile(profile: Dict[str, Any]) -> None:
    """原子写: 先写 .tmp 再 rename."""
    USER_PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = USER_PROFILE_PATH.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)
    tmp.replace(USER_PROFILE_PATH)


def _ensure_field(profile: Dict[str, Any], field: str) -> Dict[str, Any]:
    """字段不存在就建. 返回该字段 dict (in-place)."""
    if field not in profile:
        profile[field] = {
            "value": None,
            "evidence": [],
            "locked": False,
            "last_confirmed": None,
            "proposed_value": None,
        }
    return profile[field]


def _validate_field(field: str) -> Optional[str]:
    """返 error 字符串或 None (合法)."""
    if not isinstance(field, str) or not field.strip():
        return "field 必填"
    if len(field) > 100:
        return "field 太长 (max 100)"
    if field in NO_PROPOSE_FIELDS:
        return f"红线字段, LLM 不能 propose: {field}"
    return None


def _validate_value(field: str, value: str) -> Optional[str]:
    """返 error 字符串或 None."""
    if not isinstance(value, str):
        return "value 必须是字符串"
    if len(value) > MAX_VALUE_LEN:
        return f"value 太长 (max {MAX_VALUE_LEN})"
    allowed = ALLOWED_FIELDS.get(field)
    if allowed is not None and value not in allowed:
        return f"value 必须是 {allowed} 之一 (字段 {field}), 拿到 {value!r}"
    return None


# ============================================================
# 工具 1: get — 读全部 (LLM 自动注入 system prompt 用)
# ============================================================

def user_profile_get(args: Dict[str, Any]) -> Dict[str, Any]:
    """tool: 读完整画像. LLM 在 chat 开始时自动调一次, 拿当前画像注入 prompt.

    返回精简形式 (不含 evidence 全文, 节省 tokens):
      {field: {value, evidence_count, locked, last_confirmed_iso}}
    """
    profile = _read_profile()
    summary: Dict[str, Any] = {}
    for field, data in profile.items():
        if not isinstance(data, dict):
            continue
        summary[field] = {
            "value": data.get("value"),
            "evidence_count": len(data.get("evidence") or []),
            "locked": bool(data.get("locked")),
            "last_confirmed": data.get("last_confirmed"),
            "proposed_value": data.get("proposed_value"),
        }
    return {"type": "result", "result": summary}


# ============================================================
# 工具 2: propose — LLM 看到 evidence 时调
# ============================================================

def user_profile_propose(args: Dict[str, Any]) -> Dict[str, Any]:
    """tool: LLM 攒 evidence + 满 N 次后建议跟员工确认.

    args:
      field: str, 例 "writing_style.tone"
      value: str, LLM 推断的值, 例 "直接"
      evidence: str, 这次的 evidence 文本 (员工原话或对话引用), 例 "员工说'别绕弯子'"

    返回:
      {type: 'result', result: {field, evidence_count}} — 累积中, 还没到 propose 阈值
      {type: 'should_confirm', ...} — 累积满 N 次, LLM 应该跟员工确认
      {type: 'skipped', reason} — locked / 红线 / 同 value 已 confirmed
      {type: 'error', error} — 校验失败
    """
    field = (args.get("field") or "").strip()
    value = (args.get("value") or "").strip()
    evidence = (args.get("evidence") or "").strip()

    err = _validate_field(field)
    if err:
        return {"type": "error", "error": err}
    err = _validate_value(field, value)
    if err:
        return {"type": "error", "error": err}
    if not evidence:
        return {"type": "error", "error": "evidence 必填 — quote 员工原话或对话上下文"}
    if len(evidence) > MAX_EVIDENCE_LEN:
        evidence = evidence[:MAX_EVIDENCE_LEN] + "…"

    profile = _read_profile()
    if len(profile) >= MAX_FIELDS and field not in profile:
        return {"type": "error", "error": f"profile 字段已满 (max {MAX_FIELDS}), 删几个再加"}

    f = _ensure_field(profile, field)

    if f.get("locked"):
        return {"type": "skipped", "reason": "field is locked by user"}

    if f.get("value") == value and f.get("last_confirmed"):
        # 已确认过同一个值, 不再累积
        return {"type": "skipped", "reason": "value already confirmed"}

    # 累 evidence
    ev_list: List[Dict[str, Any]] = list(f.get("evidence") or [])
    ev_list.append({"text": evidence, "ts": time.time(), "value": value})
    if len(ev_list) > MAX_EVIDENCE_PER_FIELD:
        ev_list = ev_list[-MAX_EVIDENCE_PER_FIELD:]
    f["evidence"] = ev_list
    f["proposed_value"] = value

    _write_profile(profile)

    # 阈值检查: 同 value 的 evidence 数 ≥ MIN
    same_value_count = sum(1 for e in ev_list if e.get("value") == value)
    if same_value_count >= MIN_EVIDENCE_TO_PROPOSE and f.get("value") != value:
        return {
            "type": "should_confirm",
            "field": field,
            "proposed_value": value,
            "current_value": f.get("value"),
            "evidence_count": same_value_count,
            "evidence_examples": [e["text"] for e in ev_list[-3:] if e.get("value") == value],
            "hint": (
                f"已累积 {same_value_count} 次 '{value}' 的 evidence. "
                f"跟员工自然语言确认 (引用具体 evidence 不要编), "
                f"员工说同意后再调 user_profile_confirm({field!r}, {value!r}) 落盘."
            ),
        }

    return {
        "type": "result",
        "result": {
            "field": field,
            "evidence_count": same_value_count,
            "needed_to_propose": MIN_EVIDENCE_TO_PROPOSE,
        },
    }


# ============================================================
# 工具 3: confirm — 员工确认 (UI 或 LLM 拿到员工同意后调)
# ============================================================

def user_profile_confirm(args: Dict[str, Any]) -> Dict[str, Any]:
    """tool: 落盘 confirm 后的值. 员工 UI 直接改 (locked 可选), 或 LLM propose
    后员工说"对" → LLM 调这个.

    args:
      field: str
      value: str (新值)
      locked: bool, 默认 False. True = 锁了 LLM 不再 propose 改.
    """
    field = (args.get("field") or "").strip()
    value = (args.get("value") or "").strip()
    locked = bool(args.get("locked", False))

    err = _validate_field(field)
    # 红线字段员工自己 confirm OK (不走 propose 路径), 这里放过
    if err and "红线" not in err:
        return {"type": "error", "error": err}
    err = _validate_value(field, value)
    if err:
        return {"type": "error", "error": err}

    profile = _read_profile()
    if len(profile) >= MAX_FIELDS and field not in profile:
        return {"type": "error", "error": f"profile 字段已满 (max {MAX_FIELDS})"}

    f = _ensure_field(profile, field)
    old_value = f.get("value")
    f["value"] = value
    f["last_confirmed"] = time.time()
    f["locked"] = locked
    f["proposed_value"] = None  # clear pending propose
    _write_profile(profile)

    return {
        "type": "result",
        "result": {
            "field": field,
            "value": value,
            "previous_value": old_value,
            "locked": locked,
        },
    }


# ============================================================
# 工具 4: clear — 员工 reset (透明可控)
# ============================================================

def user_profile_clear(args: Dict[str, Any]) -> Dict[str, Any]:
    """tool: 清空全部画像 (员工 Dashboard 一键). 直接删文件."""
    field = (args.get("field") or "").strip()
    if field:
        # 只清单个字段
        profile = _read_profile()
        if field in profile:
            del profile[field]
            _write_profile(profile)
            return {"type": "result", "result": f"已清字段 {field}"}
        return {"type": "result", "result": f"字段 {field} 本来就不存在"}
    # 清全部
    if USER_PROFILE_PATH.exists():
        USER_PROFILE_PATH.unlink()
    return {"type": "result", "result": "已清空全部画像"}
