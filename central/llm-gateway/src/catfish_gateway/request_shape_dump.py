"""上游返回"什么都不说的 400"时, 把请求的**形状**存一份 (8/10).

## 为什么要这个

内网 Qwen3-VL 对某类请求返:

    error: code = 400 reason =  message =  metadata = map[] cause = <nil>

reason 和 message 全是空的 —— 上游什么都不告诉你。8/10 为这一个 400 做了
两轮共八个探针 (纯对话 / +tools / +多轮 tool_calls / 关 thinking / stream /
max_tokens 七万 / 32 个工具 / 全叠), **全部通过**, 一个都没复现。

到这一步继续猜变量的成本已经高过抓真身了: 每猜一轮要构造 payload、跑、读结果,
而真身就在进程里, 存下来直接比对。

## 只存形状, 不存内容

军规「中央端是严禁看到员工端的数据」。这个 dump 的用途是**比对结构**, 不需要
正文:

  · content   只记类型和长度 (str/list/None + 多少字), **不记正文**
  · tool_calls 只记函数名和参数长度, 不记参数值
  · tools     记 name + 参数结构的键名, 不记 description 正文
  · 图片      只记有几个 image_url、每个的 scheme (data:/http:), 不记数据

拿这些去比对"哪一条跟探针不一样"完全够用, 而且这份文件即使被误发出去也不含
任何员工内容。

## 什么时候触发

只在**上游返回的错误信息为空**时 —— 有话说的错误 (余额不足 / 超上下文 /
rate limit) 本来就能从日志读出原因, 不需要 dump。开关默认开, 出问题时
CATFISH_DISABLE_SHAPE_DUMP=1 一键关。
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("catfish.gateway.shape_dump")

ENV_DISABLE = "CATFISH_DISABLE_SHAPE_DUMP"
ENV_DIR = "CATFISH_SHAPE_DUMP_DIR"

#: 留几份。只存形状, 一份几十 KB, 但也没必要无限涨。
KEEP_LAST = 20

#: "上游什么都没说"的判据。有具体错因的不 dump —— 那些从日志就能看懂。
#:
#: 形态取自 8/10 实盘: `error: code = 400 reason =  message =  metadata = map[] cause = <nil>`
#: 判据写成"去掉已知的空壳字段后还剩不剩下人能读的内容", 而不是精确匹配那一串
#: —— 精确匹配换个网关版本就失效, 而失效的样子是"再也不 dump 了", 又是一个
#: 静默失败。
_SHELL_TOKENS = re.compile(
    r"(error|code|reason|message|metadata|cause)\s*[:=]\s*"
    r"(<nil>|map\[\]|null|none|\d+)?",
    re.I,
)

#: litellm / openai SDK 会在真错误前面糊一层自己的类名和 provider 名。
#:
#: ⚠ 第一版漏了这个: 判据只剥空壳字段, 于是
#:     "litellm.BadRequestError: OpenAIException - error: code = 400 reason = ..."
#: 里的 `litellm.BadRequestError` `OpenAIException` 被当成"实义内容", 判成
#: "有话说" → **不 dump**。而线上抛出来的**正是这一条** —— 裸的那句只在
#: 更底层出现。等于这个 dump 对真正要抓的场景恰好不生效。
#:
#: 又是"检查写了但恒不触发"。这次是拿真实异常文本跑判据当场抓出来的 ——
#: 只用那句裸错误自测就会漏。
_SDK_NOISE = re.compile(
    r"(litellm\.\w+|\w*Exception|\w*Error|APIStatusError|Provider List:.*|"
    r"https?://\S+|LiteLLM|OpenAI)",
    re.I,
)


def is_opaque_upstream_error(text: str) -> bool:
    """这个错误信息是不是"什么都没说"。

    依次剥掉 SDK 噪音 (类名/provider 名/文档链接) 和已知空壳字段, 看剩下还有
    没有实义字符。剩得下 (比如 "Insufficient Balance" / "context length
    exceeded" / "reasoning_content must be passed back") 就是有话说, 不 dump。
    """
    if not text:
        return True
    rest = _SDK_NOISE.sub("", text)
    rest = _SHELL_TOKENS.sub("", rest)
    rest = re.sub(r"[\s{}\[\]().,;:=<>'\"|_-]+", "", rest)
    return len(rest) < 8


def _describe_content(c: Any) -> dict[str, Any]:
    """content 的形状。**不含正文。**"""
    if c is None:
        return {"type": "None"}
    if isinstance(c, str):
        return {"type": "str", "len": len(c)}
    if isinstance(c, list):
        parts: dict[str, int] = {}
        images: list[str] = []
        for p in c:
            if not isinstance(p, dict):
                parts["_non_dict"] = parts.get("_non_dict", 0) + 1
                continue
            t = str(p.get("type", "?"))
            parts[t] = parts.get(t, 0) + 1
            if t == "image_url":
                u = (p.get("image_url") or {}).get("url") or ""
                images.append(u.split(":", 1)[0][:12] if ":" in u else "无 scheme")
        out: dict[str, Any] = {"type": "list", "n": len(c), "parts": parts}
        if images:
            out["image_schemes"] = images
        return out
    return {"type": type(c).__name__}


def _describe_message(m: Any) -> dict[str, Any]:
    if not isinstance(m, dict):
        return {"_non_dict": type(m).__name__}
    d: dict[str, Any] = {"role": m.get("role"), "content": _describe_content(m.get("content"))}
    tcs = m.get("tool_calls")
    if tcs is not None:
        if isinstance(tcs, list):
            d["tool_calls"] = [
                {
                    "name": ((tc.get("function") or {}).get("name") if isinstance(tc, dict) else None),
                    "args_len": len(((tc.get("function") or {}).get("arguments") or ""))
                    if isinstance(tc, dict) else None,
                    "has_id": bool(tc.get("id")) if isinstance(tc, dict) else None,
                }
                for tc in tcs
            ]
        else:
            d["tool_calls"] = {"_not_list": type(tcs).__name__}
    if "tool_call_id" in m:
        d["tool_call_id"] = bool(m.get("tool_call_id"))
    # 上游若要求回传, 这里能一眼看出有没有
    if "reasoning_content" in m:
        d["reasoning_content_len"] = len(m.get("reasoning_content") or "")
    extra = set(m) - {"role", "content", "tool_calls", "tool_call_id", "name",
                      "reasoning_content"}
    if extra:
        d["_extra_keys"] = sorted(extra)
    return d


def _describe_tool(t: Any) -> dict[str, Any]:
    if not isinstance(t, dict):
        return {"_non_dict": type(t).__name__}
    fn = t.get("function") or {}
    params = fn.get("parameters") or {}
    props = params.get("properties") or {}
    return {
        "name": fn.get("name"),
        "desc_len": len(fn.get("description") or ""),
        "param_keys": sorted(props)[:20] if isinstance(props, dict) else "_not_dict",
        # 这几个关键字是 schema 不兼容的常见来源, 有就标出来
        "schema_features": sorted(
            k for k in ("anyOf", "oneOf", "allOf", "$ref", "additionalProperties")
            if k in json.dumps(params, ensure_ascii=False)
        ),
    }


def build_shape(params: dict[str, Any]) -> dict[str, Any]:
    """从发给上游的 params 里提炼形状。纯函数, 好测。"""
    msgs = params.get("messages") or []
    tools = params.get("tools") or []
    return {
        "model": params.get("model"),
        "stream": params.get("stream"),
        "max_tokens": params.get("max_tokens"),
        "temperature": params.get("temperature"),
        "tool_choice": params.get("tool_choice"),
        "n_messages": len(msgs),
        "n_tools": len(tools),
        "top_level_keys": sorted(k for k in params if k != "messages" and k != "tools"),
        "messages": [_describe_message(m) for m in msgs],
        "tools": [_describe_tool(t) for t in tools],
    }


def _dump_dir() -> Path:
    d = os.environ.get(ENV_DIR)
    if d:
        return Path(d)
    return Path.home() / "Library" / "Logs" / "catfish" / "shape-dumps"


def _prune(directory: Path) -> None:
    files = sorted(directory.glob("shape-*.json"), key=lambda p: p.name)
    for old in files[:-KEEP_LAST]:
        try:
            old.unlink()
        except OSError:
            pass


def dump_on_opaque_error(params: dict[str, Any], model_name: str, err: BaseException) -> str | None:
    """上游错误没内容时存一份形状。返回文件路径, 没存返 None。

    **绝不抛** —— 这是诊断辅助, 它自己挂掉不能影响本来就已经在失败的请求。
    """
    try:
        if os.environ.get(ENV_DISABLE, "").lower() in ("1", "true", "yes"):
            return None
        text = str(err)
        if not is_opaque_upstream_error(text):
            return None

        shape = build_shape(params)
        shape["_error"] = text[:400]
        shape["_model"] = model_name
        shape["_ts"] = time.strftime("%Y-%m-%d %H:%M:%S")

        directory = _dump_dir()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"shape-{time.strftime('%Y%m%d-%H%M%S')}-{model_name}.json"
        path.write_text(json.dumps(shape, ensure_ascii=False, indent=2), encoding="utf-8")
        _prune(directory)

        logger.warning(
            "上游返了个什么都没说的错误 (%s) —— 已存请求**形状** (不含正文) 到 %s. "
            "消息 %d 条 / 工具 %d 个 / stream=%s / max_tokens=%s. "
            "拿它跟 scripts/probe-internal-400.sh 的探针比对, 找那条不一样的。",
            model_name, path, shape["n_messages"], shape["n_tools"],
            shape["stream"], shape["max_tokens"],
        )
        return str(path)
    except Exception as e:  # noqa: BLE001
        logger.debug("shape dump 自己挂了 (%s), 忽略", type(e).__name__)
        return None
