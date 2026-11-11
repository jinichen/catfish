"""BL-MM7 结构化用户画像 (5/6), 11/12 改: 证据只认员工自己说的, 确认过的值每轮带进对话。

# 跟 catfish_remember 区别

- `session_facts.json` (catfish_remember 写的): 一次对话里的具体硬事实, 对话结束清空。
- `user_profile.json` (本模块): 长期偏好 (语气 / 长短 / 列表还是段落 / 节奏 ...), 9 个固定字段。

# 11/12 为什么改 (鸿波「画像为什么一直在变」「现在的画像功能合理吗」)

1. 原来模型要自己想起来调 get —— 30 天 1429 个对话 0 次; 确认过的偏好没进过对话。
   现在 catfish-memory 插件每轮 prefetch 读这个文件, 把**确认过的值**带进去
   (hermes-plugins/catfish-memory/catfish_memory_profile.py)。
2. 原来证据主要来自「拿关键词套工作日志」(日志是模型写的总结, "偏好.*快" 就算「急」),
   每次日志变了就从头扫一遍, 每项只留 10 条 → 挤出去的旧句子下一轮又当新的加回来,
   「待确认」在几个值之间来回跳, 少数派 (缓 9 次 vs 急 131 次) 也会被提成待确认。
   现在不扫日志了, 证据只有两种: 员工在对话里明确说的 (模型调 propose 记原话),
   员工自己在画像页改的 (直接 confirm)。老文件里没有 src=chat 的证据读的时候清掉。
3. 员工明确说一次就够, 不用攒 3 次: propose 一次就成「待你确认」, 页面上点确认 / 不用,
   或者模型在对话里问一句、员工说「对」再 confirm。

# 原则

- 没确认的提议不进对话, 不覆盖已确认的值。
- 员工锁定的字段不再接受提议。
- 红线字段 (健康 / 财务 / 感情 / 政治 / 宗教 / 家庭) 模型不许 propose。
- 全在 ~/.catfish/user_profile.json, 员工可改、可删、可清空。

# Schema

```json
{"writing_style.tone": {"value": "直接", "locked": false, "last_confirmed": 1234567890.0,
  "proposed_value": "委婉",
  "evidence": [{"text": "以后说话委婉点", "ts": 1234567890.0, "value": "委婉", "src": "chat"}]}}
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

MAX_VALUE_LEN = 200
MAX_EVIDENCE_LEN = 500
MAX_EVIDENCE_PER_FIELD = 3  # 只留来显示「你说过: ...」, 不再按次数攒
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
    if not isinstance(data, dict):
        return {}
    if _drop_legacy_evidence(data):
        try:
            _write_profile(data)
        except OSError:
            pass
    return data


def _drop_legacy_evidence(profile: Dict[str, Any]) -> bool:
    """11/12 之前的证据大多是拿关键词套工作日志套出来的 (不带 src), 清掉;
    提议的值要是没有员工原话撑着, 也一起撤。确认过的值、锁定不动。返回有没有改。"""
    changed = False
    for f in profile.values():
        if not isinstance(f, dict):
            continue
        ev = [e for e in (f.get("evidence") or []) if isinstance(e, dict) and e.get("src") == "chat"]
        if len(ev) != len(f.get("evidence") or []):
            f["evidence"] = ev
            changed = True
        pv = f.get("proposed_value")
        if pv and (pv == f.get("value") or not any(e.get("value") == pv for e in ev)):
            f["proposed_value"] = None
            changed = True
    return changed


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
    """tool: 读画像。9 个固定字段都列 (没定的 value=None), 画像页靠 options 出下拉。

    每个字段: {value, options (None = 自由文本), locked, last_confirmed,
               proposed_value (员工还没确认的提议), proposed_quote (提议依据: 员工原话)}
    确认过的值插件每轮已经带进对话, 模型一般不用再调这个。
    """
    profile = _read_profile()
    summary: Dict[str, Any] = {}
    names = list(ALLOWED_FIELDS) + [k for k in profile if k not in ALLOWED_FIELDS]
    for field in names:
        data = profile.get(field)
        data = data if isinstance(data, dict) else {}
        pv = data.get("proposed_value")
        quotes = [e.get("text") for e in (data.get("evidence") or [])
                  if isinstance(e, dict) and pv and e.get("value") == pv]
        summary[field] = {
            "value": data.get("value"),
            "options": ALLOWED_FIELDS.get(field),
            "locked": bool(data.get("locked")),
            "last_confirmed": data.get("last_confirmed"),
            "proposed_value": pv,
            "proposed_quote": quotes[-1] if quotes else None,
        }
    return {"type": "result", "result": summary}


# ============================================================
# 工具 2: propose — 员工在对话里明确说出长期偏好时调
# ============================================================

def user_profile_propose(args: Dict[str, Any]) -> Dict[str, Any]:
    """tool: 员工明确说了一条长期偏好 → 记成「待确认」(不改已确认的值)。

    args: field, value, evidence (员工原话, 必填)
    returns:
      {type: 'should_confirm', ...}  —— 记下了, 画像页显示待确认; 模型可在回复里顺带问一句
      {type: 'skipped', reason}      —— 锁定了 / 已经是这个值 / 同一句话记过
      {type: 'error', error}
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
        return {"type": "error", "error": "evidence 必填 —— 写员工的原话"}
    if len(evidence) > MAX_EVIDENCE_LEN:
        evidence = evidence[:MAX_EVIDENCE_LEN] + "…"

    profile = _read_profile()
    if len(profile) >= MAX_FIELDS and field not in profile:
        return {"type": "error", "error": f"profile 字段已满 (max {MAX_FIELDS}), 删几个再加"}
    f = _ensure_field(profile, field)
    if f.get("locked"):
        return {"type": "skipped", "reason": "field is locked by user"}
    if f.get("value") == value:
        return {"type": "skipped", "reason": "value already confirmed"}
    ev: List[Dict[str, Any]] = [e for e in (f.get("evidence") or []) if isinstance(e, dict)]
    if f.get("proposed_value") == value and any(e.get("text") == evidence and e.get("value") == value for e in ev):
        return {"type": "skipped", "reason": "evidence already recorded"}
    ev.append({"text": evidence, "ts": time.time(), "value": value, "src": "chat"})
    f["evidence"] = ev[-MAX_EVIDENCE_PER_FIELD:]
    f["proposed_value"] = value
    _write_profile(profile)
    return {
        "type": "should_confirm",
        "field": field,
        "proposed_value": value,
        "current_value": f.get("value"),
        "hint": (
            "已记成待确认 (员工在画像页能看到)。可以在回复里顺带问一句要不要以后都这样; "
            f"员工说是再调 catfish_user_profile_confirm({field!r}, {value!r})。别为这个单独追问。"
        ),
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
    if field and args.get("proposal_only"):
        # 画像页「不用」: 只撤掉待确认的提议, 确认过的值不动
        profile = _read_profile()
        f = profile.get(field)
        if isinstance(f, dict) and f.get("proposed_value"):
            f["proposed_value"] = None
            _write_profile(profile)
        return {"type": "result", "result": f"已撤掉 {field} 的提议"}
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
