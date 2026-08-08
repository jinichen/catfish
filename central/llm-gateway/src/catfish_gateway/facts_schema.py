"""facts 落中央 PG 的**字段白名单** —— 只有约定内的键能进去 (8/9 加)。

# 为什么要这一层

8/8 抓到 `facts_pipeline._llm_json` 往返回值里塞了个 `raw` (LLM 的整段原始
输出, 最多 6000 token)。它**只写不读** —— 全网关只有一处产生, 没有任何代码
或前端消费。但它一路落到中央 PG:

    _llm_json 返 {"error":…, "raw": text}
      → run_extract 返 {**result, "facts": []}          ← raw 跟着走
        → facts_router → pg_write_facts_json(fact_id, facts)
          → json.dumps(整个 dict) → fact_changes.facts_json (jsonb)

**问题不在那一个键, 在写法。** `json.dumps(整个 dict)` 意味着: 上游多返一个
键 / 谁调试时多塞一个字段, 就会静默落进中央存储, 而且没有任何测试会红。

对比隔壁已经守住的两条:

    审计记录 (metrics)      facts 落 PG (8/9 之前)
    ─────────────────────  ────────────────────────
    参数列死 12 个          json.dumps(整个 dict)
    没有 `**kwargs`         上游返什么存什么
    PG extra 兜底列实测      —— 无
      6619 条 0 个键
    有 CI 闸                ← 8/9 之前没有

同样的形状在 `facts_db.py` 里有 **4 处**, 不是 1 处:

    pg_write_facts_json   facts_json  jsonb   ← 8/8 那次的现场
    pg_upsert_fact        facts_json  jsonb   ← 同内容另一条路
    pg_replace_patches    changes_json jsonb
    pg_audit              meta_json   jsonb

# 判据

跟 8/8 `error` 改封闭词表、去掉 `raw` 是同一条:
**「这个字段的值来自哪里」, 不是「这次的内容是谁的」。**

facts 的输入是管理员上传的公司/政府规章 (`upload_fact` 有 `_require_admin`),
今天不是员工数据。但 `facts_json` 装的是 **LLM 对那份文档的复述** —— 结构上
不受控, 上游返什么就是什么。白名单管的是"结构上能不能进", 跟这一次装的是
谁的内容无关。

# 做法: 投影, 不是校验

这几个函数**不报错、不拒绝写入**, 只把约定外的键丢掉再返回。理由:

  - facts 是管理员的运维动作, 不是员工的 chat 主路径。为一个多余的键让
    整个 extract 失败, 换来的是运维困惑, 不是安全。
  - 丢弃会 `logger.warning`, 该发现的能发现。
  - 白名单本身钉在 `tests/test_facts_pg_write_closed.py` 里, 想加键得动那个
    测试 —— 这才是"要过 review"的那道闸。

`full_new_content` (改写后的完整 skill 正文) 走的是**独立 text 列**, 不在这
几个 jsonb 里, 所以不归这里管。它是 patch 的产品本体, 该在中央。
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger("catfish.gateway.facts_schema")

#: extract 结果的**顶层**键。改这里要同时改 tests/test_facts_pg_write_closed.py。
#:
#: `error` 在列里是因为失败路径也会落盘 (`run_extract` 返 `{**result, "facts": []}`),
#: 前端 FactDetail 要显示它。注意: 它装的是我们自己写的诊断串 ——
#: `_llm_json` 里那条 `LLM 调用失败` 8/9 已改成只带 `classify_upstream_error`
#: 的分类码, 不再内插上游异常原文。
FACTS_JSON_KEYS = frozenset({
    "summary",          # 整篇 1-2 句话总结
    "effective_date",   # ISO 日期 / null
    "facts",            # 事实点数组, 逐项再过 FACT_POINT_KEYS
    "error",            # 失败时的诊断 (分类码 / 我们自己的常量串)
})

#: 单个事实点的键 —— 严格对齐 `facts_pipeline.EXTRACT_PROMPT_SYSTEM` 的 schema。
#:
#: `raw_quote` 在列里是**有意的**: prompt 明确要求逐字摘录 50-200 字, 下游
#: generate_patch 要靠它「严格按数字/条款编号改, 不要发挥」。删了这个功能就废了。
#: 它是这条链的产品本体, 不是意外落进来的。
FACT_POINT_KEYS = frozenset({
    "id", "title", "summary", "category",
    "keywords", "raw_quote", "impact_scope",
})

#: patch 里单条改动的键 —— 对齐 `GENERATE_PATCH_PROMPT_SYSTEM`。
PATCH_CHANGE_KEYS = frozenset({"description", "old_snippet", "new_snippet"})

#: fact_audit.meta_json 的键 —— 对齐 `facts_router` 里 6 处 `_audit(...)` 调用。
#: 全是计数 / 序号 / skill 名, 没有正文。
AUDIT_META_KEYS = frozenset({
    "filename", "size",                                   # upload
    "facts_count", "impacts_count", "patches_count",      # extract / analyze
    "patch_idx", "skill", "new_version",                  # approve / reject
})

#: 键名本身也来自上游 (LLM 返的 JSON), 所以打日志前先过一道形状检查 ——
#: 同一条判据递归应用到"键名"这个值上。不合形状的不打原文。
_SAFE_KEY_NAME = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def _safe_names(keys) -> list[str]:
    """把要打进日志的键名压成安全形状; 不合规的不打原文。"""
    out = []
    for k in keys:
        ks = k if isinstance(k, str) else type(k).__name__
        out.append(ks if _SAFE_KEY_NAME.match(ks) else "<非常规键名>")
    return sorted(out)


def _project(obj: dict, allowed: frozenset[str], where: str) -> dict:
    """留下 allowed 里的键, 其余丢掉 + warn。obj 不是 dict 就返空 dict。"""
    if not isinstance(obj, dict):
        return {}
    dropped = [k for k in obj if k not in allowed]
    if dropped:
        logger.warning(
            "facts 落 PG: %s 丢掉 %d 个约定外的键 %s "
            "(白名单在 facts_schema.py; 真要加先改 tests/test_facts_pg_write_closed.py)",
            where, len(dropped), _safe_names(dropped),
        )
    return {k: v for k, v in obj.items() if k in allowed}


def project_facts_json(data) -> dict:
    """extract 结果 → 只留白名单内的键 (含 facts[] 逐项投影)。永不抛。"""
    try:
        out = _project(data, FACTS_JSON_KEYS, "facts_json")
        if "facts" in out:
            raw_facts = out["facts"]
            if isinstance(raw_facts, list):
                out["facts"] = [
                    _project(f, FACT_POINT_KEYS, "facts_json.facts[]")
                    for f in raw_facts
                    if isinstance(f, dict)
                ]
            else:
                logger.warning(
                    "facts 落 PG: facts 不是数组 (是 %s), 按空数组处理",
                    type(raw_facts).__name__,
                )
                out["facts"] = []
        return out
    except Exception:  # noqa: BLE001 — 投影器不该抛; 抛了宁可写空也不挂业务
        logger.exception("project_facts_json 出错, 按空 dict 处理")
        return {}


def project_patch_changes(changes) -> list[dict]:
    """patch 的 changes[] → 逐项只留白名单键。永不抛。"""
    try:
        if not isinstance(changes, list):
            return []
        return [
            _project(c, PATCH_CHANGE_KEYS, "changes_json[]")
            for c in changes
            if isinstance(c, dict)
        ]
    except Exception:  # noqa: BLE001
        logger.exception("project_patch_changes 出错, 按空数组处理")
        return []


def project_audit_meta(meta) -> dict:
    """fact_audit.meta_json → 只留白名单键。永不抛。"""
    try:
        return _project(meta or {}, AUDIT_META_KEYS, "meta_json")
    except Exception:  # noqa: BLE001
        logger.exception("project_audit_meta 出错, 按空 dict 处理")
        return {}
