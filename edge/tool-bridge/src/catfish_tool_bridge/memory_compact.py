"""「整理记忆」的精简建议 (11/4)。

鸿波 11/4「memory.md 和 user.md 没什么效果」: 原来「🧹 整理记忆」只找重复 (memory_dedupe),
可两份记忆里没有重复, 只是条目写得长 (最长一条近 600 字), 找完 0 条, 占用照旧 90%+。

这里让模型把长条目改短、讲同一件事的几条合成一条 (事实 / 数字 / 名字 / 规则一个不丢),
每条建议存成 hermes 的待批写入 (``write_approval.stage_write``, 钉住原文 ``matched_entry``),
出现在卡片「待你确认的整理建议」里 —— 员工点同意才改, 原文后来变了 hermes 会拒绝。
重点一次只留一批: 再点「整理记忆」先放弃上一批还没批的精简建议, 不叠。
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

ORIGIN = "catfish_compact"
_SEP = "§"
_MIN_LEN = 80          # 再短的条目不值得改
_MIN_SAVING = 0.15     # 改完至少省 15%, 否则不提
_DEFAULT_LIMIT = {"user": 1375, "memory": 2200}  # hermes 默认; 鲶鱼配置里一般是 2000 / 4000


def _limit(target: str) -> int:
    from .adapter_todo import _read_hermes_memory_config  # noqa: PLC0415

    key = "user_char_limit" if target == "user" else "memory_char_limit"
    try:
        return int(_read_hermes_memory_config().get(key, _DEFAULT_LIMIT[target]))
    except (TypeError, ValueError):
        return _DEFAULT_LIMIT[target]


def _prompt(target: str, entries: list[str], limit: int) -> str:
    total = sum(len(e) for e in entries)
    kind = "员工本人 (身份、偏好、习惯)" if target == "user" else "项目 / 技术 / 工作口径"
    listing = "\n".join(f"[{i}] {e}" for i, e in enumerate(entries))
    return (
        f"下面是员工的长期记忆 ({kind}), 每次对话都会整段带上, 上限 {limit} 字, 现在 {total} 字。\n"
        f"请提精简建议, 目标总字数降到 {int(limit * 0.6)} 字左右。\n\n"
        "两种改法:\n"
        "- rewrite: 把一条改短。删掉重复说法、修饰、举例、来龙去脉; 保留每个事实、数字、人名、专有名词、规则和禁忌, 意思不能变\n"
        "- merge: 几条讲同一件事 / 同一类偏好的合成一条, 同样不丢信息\n"
        f"规则: 不许新增或推测任何信息; 少于 {_MIN_LEN} 字且不重复的条目不用动; 每条只能出现在一个建议里; "
        f"新内容里不要出现「{_SEP}」。\n\n"
        f"记忆条目:\n{listing}\n\n"
        '只返 JSON, 不要别的文字:\n'
        '{"ops":[{"type":"rewrite","index":3,"content":"改短后的整条"},'
        '{"type":"merge","indices":[1,5],"content":"合并后的整条"}]}'
    )


def _llm(prompt: str) -> str:
    """走本机网关, 跟记忆去重同一个模型 (员工记忆敏感, 默认内网 chat_default)。失败返空串。"""
    try:
        import httpx  # noqa: PLC0415

        from . import picker_state, role_resolver  # noqa: PLC0415
        from .browser_locate import GATEWAY_URL, _read_id_token  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return ""
    token = _read_id_token()
    if not token:
        return ""
    model = picker_state.read_picker_model() or role_resolver.resolve("chat_default") or "catfish-private-main"
    try:
        with httpx.Client(timeout=150.0) as client:
            resp = client.post(
                f"{GATEWAY_URL}/v1/chat/completions",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"model": model, "messages": [{"role": "user", "content": prompt}],
                      "temperature": 0.1, "max_tokens": 4000, "stream": False},
            )
        if resp.status_code != 200:
            logger.warning("精简记忆: 网关返回 %s", resp.status_code)
            return ""
        return (resp.json().get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
    except Exception as error:  # noqa: BLE001
        logger.warning("精简记忆: 调模型失败: %s", error)
        return ""


def _parse(text: str) -> list[dict[str, Any]]:
    if text.startswith("```"):
        text = "\n".join(ln for ln in text.splitlines() if not ln.startswith("```")).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return []
    try:
        ops = json.loads(text[start:end + 1]).get("ops")
    except (ValueError, AttributeError):
        return []
    return [op for op in ops if isinstance(op, dict)] if isinstance(ops, list) else []


def validate_ops(raw: list[dict[str, Any]], entries: list[str]) -> list[dict[str, Any]]:
    """只留安全的建议: 编号合法、不重叠、新内容非空不含分隔符、真省下 15% 以上。"""
    used: set[int] = set()
    out: list[dict[str, Any]] = []
    for op in raw:
        kind = op.get("type")
        if kind == "rewrite":
            idx = [op.get("index")]
        elif kind == "merge":
            idx = op.get("indices") if isinstance(op.get("indices"), list) else []
        else:
            continue
        if not idx or any(not isinstance(i, int) or isinstance(i, bool) or not 0 <= i < len(entries) for i in idx):
            continue
        idx = list(dict.fromkeys(idx))
        if kind == "merge" and len(idx) < 2:
            continue
        if used & set(idx):
            continue
        content = str(op.get("content") or "").strip()
        before = sum(len(entries[i]) for i in idx)
        if not content or _SEP in content or len(content) > before * (1 - _MIN_SAVING):
            continue
        used.update(idx)
        out.append({"type": kind, "originals": [entries[i] for i in idx], "content": content,
                    "saved": before - len(content)})
    return out


def _payload(target: str, op: dict[str, Any]) -> dict[str, Any]:
    first, *rest = op["originals"]
    replace = {"action": "replace", "old_text": first, "matched_entry": first, "content": op["content"]}
    if not rest:
        return {"target": target, **replace}
    removes = [{"action": "remove", "old_text": e, "matched_entry": e} for e in rest]
    return {"action": "batch", "target": target, "operations": [replace, *removes]}


def propose(params: dict[str, Any]) -> dict[str, Any]:
    target = params.get("target")
    if target not in ("user", "memory"):  # 先校验参数, 再碰 hermes (CI 里没装 hermes)
        raise ValueError("target 只能是 user 或 memory")

    from tools import write_approval as wa  # noqa: PLC0415
    from tools.memory_tool import load_on_disk_store  # noqa: PLC0415
    entries = [e for e in load_on_disk_store()._entries_for(target) if e.strip()]  # noqa: SLF001
    limit = _limit(target)
    total = sum(len(e) for e in entries)
    base = {"ok": True, "target": target, "total_chars": total, "char_limit": limit}
    if not any(len(e) >= _MIN_LEN for e in entries):
        return {**base, "staged": 0, "saved_chars": 0}
    raw = _llm(_prompt(target, entries, limit))
    if not raw:
        return {"ok": False, "target": target, "error": "小鲶这会儿连不上模型, 过一会儿再点「整理记忆」"}
    ops = validate_ops(_parse(raw), entries)
    # 上一批还没批的精简建议作废, 免得同一条原文挂两份建议
    for rec in wa.list_pending(wa.MEMORY):
        if rec.get("origin") == ORIGIN and (rec.get("payload") or {}).get("target") == target:
            wa.discard_pending(wa.MEMORY, str(rec.get("id")))
    for op in ops:
        label = "合并" if op["type"] == "merge" else "精简"
        wa.stage_write(wa.MEMORY, _payload(target, op), summary=f"{label}记忆, 省 {op['saved']} 字", origin=ORIGIN)
    return {**base, "staged": len(ops), "saved_chars": sum(op["saved"] for op in ops)}
