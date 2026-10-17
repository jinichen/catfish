"""工作流里「交给小鲶判断」的一步 (docs/WORKFLOW-SKILL-PLAN.md §2.2)。

比设计稿再收紧一点: 这一步**不给任何工具**, 只看 `input` 交进来的数据 (前面步骤的结果),
按 `returns` 交回字段。要查东西放在前一步 (查邮件 / 搜文件), 要动手放在后一步 —— 判断归判断。
交回的字段缺了 / 类型不对就算这一步失败, 不往下传半截。
"""
from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

RETURN_TYPES = ("text", "number", "list", "bool", "date")
MAX_INPUT_CHARS = 40_000

_PROMPT = """你在执行一个工作流里的一步, 只做下面这件事, 不要多做、不要客套。

要做的事:
{instruction}

可以用的数据 (前面步骤的结果, JSON; 只依据它判断, 不要编造里面没有的东西):
{data}

只输出一个 JSON 对象, 不要任何别的文字, 必须正好包含这些字段:
{fields}
"""


def _call_llm(prompt: str) -> str:
    """走本机 gateway (跟其它 tool-bridge 内部判断同一条路), 失败抛 RuntimeError。"""
    import httpx  # noqa: PLC0415

    from . import picker_state, role_resolver  # noqa: PLC0415
    from .browser_locate import GATEWAY_URL, _read_id_token  # noqa: PLC0415

    token = _read_id_token()
    if not token:
        raise RuntimeError("还没登录鲶鱼账号, 小鲶判断这一步用不了模型")
    model = picker_state.read_picker_model() or role_resolver.resolve("chat_default") or "catfish-private-main"
    try:
        with httpx.Client(timeout=120.0) as client:
            r = client.post(f"{GATEWAY_URL}/v1/chat/completions", headers={"Authorization": f"Bearer {token}"},
                            json={"model": model, "messages": [{"role": "user", "content": prompt}],
                                  "temperature": 0.1, "max_tokens": 4000, "stream": False})
    except httpx.RequestError as e:
        raise RuntimeError(f"连不上模型: {e}") from None
    if r.status_code != 200:
        raise RuntimeError(f"模型返回 {r.status_code}: {r.text[:200]}")
    return (r.json().get("choices") or [{}])[0].get("message", {}).get("content") or ""


def parse_json(text: str) -> dict[str, Any]:
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.S).strip()
    if not t.startswith("{"):
        m = re.search(r"\{.*\}", t, flags=re.S)
        t = m.group(0) if m else t
    try:
        v = json.loads(t)
    except ValueError as e:
        raise ValueError(f"小鲶没有按要求交回 JSON: {e}") from None
    if not isinstance(v, dict):
        raise ValueError("小鲶交回的不是 JSON 对象")
    return v


def check_returns(got: dict[str, Any], returns: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    problems = []
    for name, typ in returns.items():
        if name not in got or got[name] is None:
            problems.append(f"缺「{name}」")
            continue
        v = got[name]
        if typ == "number":
            try:
                v = float(v)
                v = int(v) if v.is_integer() else v
            except (TypeError, ValueError):
                problems.append(f"「{name}」要数字")
                continue
        elif typ == "list":
            if not isinstance(v, list):
                problems.append(f"「{name}」要列表")
                continue
        elif typ == "bool":
            if not isinstance(v, bool):
                problems.append(f"「{name}」要 true / false")
                continue
        elif typ == "date":
            try:
                v = date.fromisoformat(str(v)[:10]).isoformat()
            except ValueError:
                problems.append(f"「{name}」要日期 (年-月-日)")
                continue
        else:
            v = str(v)
        out[name] = v
    if problems:
        raise ValueError("小鲶交回的结果不完整: " + "; ".join(problems))
    return out


def run(instruction: str, data: Any, returns: dict[str, str], *, llm=None) -> dict[str, Any]:
    text = json.dumps(data, ensure_ascii=False, default=str)
    if len(text) > MAX_INPUT_CHARS:
        text = text[:MAX_INPUT_CHARS] + f"\n…(数据太长, 只给了前 {MAX_INPUT_CHARS} 字)"
    fields = "\n".join(f'- "{k}": {t}' for k, t in returns.items())
    raw = (llm or _call_llm)(_PROMPT.format(instruction=instruction, data=text, fields=fields))
    out = check_returns(parse_json(raw), returns)
    return {"ok": True, **out}
