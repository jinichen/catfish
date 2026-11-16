"""对话卡片里的数据图表 (VegaChart, 11/16) —— 出卡 / 改卡前查一遍图表描述。

组件照上游官方扩展目录 catalogs/vegachart/v1 (a2ui_catalog.py), Companion 用 vega-embed 画
(companion-app/src/tabs/Chat/vegaChart.tsx, 那边画之前按同样的规则再查一遍)。

Vega-Lite 本身能做的比「把这几个数画成图」多得多, 这几样在员工电脑上不该有:
  · 从网址取数据 / 图片 (data.url、image 标记、任何 url / href 键) —— 卡片是模型写的, 不能让它往外发请求、加载外面的东西;
  · 完整的 Vega (signals / marks / scales 那套) —— 只认 Vega-Lite, 够画柱状 / 折线 / 面积 / 饼图 / 散点;
  · 太大的数据 —— 卡片是给人一眼看的, 上千个点多半是把整张表塞进来了。
不合格 → ValueError (原因写清楚), 专员带着原因重写 (a2ui_card.generate), card_update 直接拒。
"""
from __future__ import annotations

import json
from typing import Any

MAX_ROWS = 500
MAX_SPEC_BYTES = 64 * 1024
FORBIDDEN_KEYS = {"url", "href"}
VEGA_ONLY_KEYS = {"signals", "marks", "scales", "axes", "legends", "projections"}


def _walk(node: Any, where: str):
    if isinstance(node, dict):
        for k, v in node.items():
            yield k, v, f"{where}.{k}"
            yield from _walk(v, f"{where}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, f"{where}[{i}]")


def check_spec(spec: Any) -> None:
    if not isinstance(spec, dict):
        raise ValueError("VegaChart 的 spec 要是一个 Vega-Lite JSON 对象")
    schema = str(spec.get("$schema") or "")
    if (schema and "vega-lite" not in schema) or VEGA_ONLY_KEYS & set(spec):
        raise ValueError("VegaChart 只支持 Vega-Lite (mark + encoding), 不支持完整的 Vega (signals / marks / scales)")
    if len(json.dumps(spec, ensure_ascii=False).encode("utf-8")) > MAX_SPEC_BYTES:
        raise ValueError(f"图表描述太大 (超过 {MAX_SPEC_BYTES // 1024}KB), 只放要画的那几列数")
    for key, value, where in _walk(spec, "spec"):
        if key in FORBIDDEN_KEYS:
            raise ValueError(f"图表不能从网址取数据或带链接 ({where}); 数据直接写在 data.values 里")
        if key == "mark" and (value == "image" or (isinstance(value, dict) and value.get("type") == "image")):
            raise ValueError("图表不能用 image 标记 (要从网址加载图片)")
        if key == "values" and isinstance(value, list) and len(value) > MAX_ROWS:
            raise ValueError(f"图表数据最多 {MAX_ROWS} 行 ({where} 有 {len(value)} 行); 先汇总再画")


# ── 在一张卡的消息里找图表 ─────────────────────────────────────────

def _pointer_get(doc: Any, path: str) -> Any:
    cur = doc
    for part in [p for p in path.split("/") if p]:
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def _pointer_set(doc: dict, path: str, value: Any) -> dict:
    parts = [p.replace("~1", "/").replace("~0", "~") for p in path.split("/") if p]
    if not parts:
        return value if isinstance(value, dict) else {}
    cur: Any = doc
    for part in parts[:-1]:
        if isinstance(cur, list) and part.isdigit():
            idx = int(part)
            while len(cur) <= idx:
                cur.append({})
            cur = cur[idx]
            continue
        if not isinstance(cur.get(part), (dict, list)):
            cur[part] = {}
        cur = cur[part]
    last = parts[-1]
    if isinstance(cur, list) and last.isdigit():
        idx = int(last)
        while len(cur) <= idx:
            cur.append(None)
        cur[idx] = value
    elif isinstance(cur, dict):
        cur[last] = value
    return doc


def _components(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for m in messages:
        for key in ("createSurface", "updateComponents"):
            out += [c for c in (m.get(key) or {}).get("components") or [] if isinstance(c, dict)]
    return out


def _data_model(messages: list[dict[str, Any]]) -> dict:
    """出卡时的初始数据 (createSurface.dataModel) + 之后每条 updateDataModel, 按顺序叠出现在的数据。"""
    doc: dict = {}
    for m in messages:
        init = (m.get("createSurface") or {}).get("dataModel")
        if isinstance(init, dict):
            doc = json.loads(json.dumps(init))
        upd = m.get("updateDataModel")
        if isinstance(upd, dict):
            doc = _pointer_set(doc, str(upd.get("path") or "/"), json.loads(json.dumps(upd.get("value"))))
    return doc


def _binding(spec: Any) -> str | None:
    """数据绑定: v1.0 写 {"@path": "/chart"} (SDK 编出来的就是这样), 老写法 {"path": ...} 也认。"""
    if isinstance(spec, dict) and len(spec) == 1:
        ref = spec.get("@path", spec.get("path"))
        if isinstance(ref, str):
            return ref
    return None


def check_messages(messages: list[dict[str, Any]]) -> int:
    """一张卡 (出卡时的消息, 或改卡后的全部消息) 里每个 VegaChart: 直接写的 spec 查它, 绑定的 ({"path": ...}) 查数据里那份。
    返回查了几个图表。"""
    charts = [c for c in _components(messages) if c.get("component") == "VegaChart"]
    if not charts:
        return 0
    model = _data_model(messages)
    for c in charts:
        spec = c.get("spec")
        ref = _binding(spec)
        if ref is not None:
            bound = _pointer_get(model, ref)
            if bound is not None:   # 还没推数据 = 先画空的, 推了再查
                check_spec(bound)
        else:
            check_spec(spec)
    return len(charts)


def lift_chart_updates(base: list[dict[str, Any]], updates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """card_update 改的是图表绑定的数据里面一截 (如 /chart/data/values) → 换成对整份 /chart 的一次更新。

    11/16 实测: 上游 @a2ui/react 0.15 里, 绑在 /chart 的组件收不到 /chart/data/values 这种子路径的更新
    (数据层变了, 组件不重画); 整份替换 /chart 就能重画。别的组件 (表单字段、列表里的每一行) 绑的都是叶子路径, 不受影响。"""
    bound = sorted({ref for c in _components(base) if c.get("component") == "VegaChart"
                    for ref in [_binding(c.get("spec"))] if ref}, key=len)
    if not bound:
        return updates
    model = _data_model(base + updates)
    out, lifted = [], set()
    for u in updates:
        path = str((u.get("updateDataModel") or {}).get("path") or "")
        root = next((b for b in bound if path.startswith(b.rstrip("/") + "/")), None)
        if root is None:
            out.append(u)
            continue
        if root not in lifted:
            lifted.add(root)
            surface = u["updateDataModel"].get("surfaceId")
            out.append({"version": u.get("version", "1.0"),
                        "updateDataModel": {"surfaceId": surface, "path": root, "value": _pointer_get(model, root)}})
    return out

