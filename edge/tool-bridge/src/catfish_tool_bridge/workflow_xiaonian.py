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


def _call_llm(prompt: str, source: str = "plugin:toolbridge-workflow") -> str:
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
            r = client.post(f"{GATEWAY_URL}/v1/chat/completions?catfish_source={source}", headers={"Authorization": f"Bearer {token}"},
                            json={"model": model, "messages": [{"role": "user", "content": prompt}],
                                  "temperature": 0.1, "max_tokens": 4000, "stream": False})
    except httpx.RequestError as e:
        raise RuntimeError(f"连不上模型: {e}") from None
    if r.status_code != 200:
        raise RuntimeError(f"模型返回 {r.status_code}: {r.text[:200]}")
    return (r.json().get("choices") or [{}])[0].get("message", {}).get("content") or ""


def _balanced_objects(t: str):
    """按花括号配平依次切出候选对象 (跳过字符串里的括号)。模型常在 JSON 前后加话, 或给两个对象 (先一个示例再一个真的)。"""
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(t):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start >= 0:
                yield t[start:i + 1]
                start = -1


def parse_json(text: str) -> dict[str, Any]:
    """模型的回答 → 对象。11/02 一次运行死在这: 回答是空的 (Expecting value: char 0)。
    现在: 去掉 <think> 段和代码围栏; 不是整段 JSON 就按括号配平找对象, 几个候选里取最后一个能解析的
    (模型先给示例再给结果时, 结果在后); 空回答单独说清楚。"""
    t = (text or "").strip()
    t = re.sub(r"<think>.*?</think>", "", t, flags=re.S).strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.S).strip()
    if not t:
        raise ValueError("小鲶没有按要求交回 JSON: 回答是空的")
    candidates = [t] if t.startswith("{") else []
    candidates += [c for c in _balanced_objects(t) if c != t]
    last_err: str | None = None
    for c in reversed(candidates):
        try:
            v = json.loads(c)
        except ValueError as e:
            last_err = str(e)
            continue
        if isinstance(v, dict):
            return v
        last_err = "不是 JSON 对象"
    raise ValueError(f"小鲶没有按要求交回 JSON: {last_err or '回答里没有 JSON 对象'}")


_RETRY_SUFFIX = """

上一次你的回答没法解析 ({why}); 回答开头是: {head}
这次**只输出 JSON 对象本身**, 第一个字符必须是 {{, 不要解释、不要代码围栏、不要示例。"""


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
    call = llm or _call_llm
    prompt = _PROMPT.format(instruction=instruction, data=text, fields=fields)
    raw = call(prompt)
    try:
        got = parse_json(raw)
    except ValueError as e:
        # 模型偶尔不守格式 (空回答 / 一段话): 带着它上次的开头再要一次, 还不行才算这一步失败
        head = json.dumps((raw or "")[:80], ensure_ascii=False)
        raw = call(prompt + _RETRY_SUFFIX.format(why=str(e).replace("小鲶没有按要求交回 JSON: ", ""), head=head))
        got = parse_json(raw)
    out = check_returns(got, returns)
    return {"ok": True, **out}
