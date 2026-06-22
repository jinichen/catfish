"""catfish-bookkeep — 本地语音记账 plugin.

P3.5.75 (6/22 鸿波 "可以做一个我可以开始让自己记账") ship 整:
  - 注册 3 个 hermes tool: bookkeep_add / bookkeep_query / bookkeep_summarize
  - 数据落 ~/.catfish/bookkeep.jsonl (append-only, 数据零出端)
  - LLM 听到员工语音/文字说收支金额, 自动调 bookkeep_add (description 直白教 LLM)
  - 0 approval (本地 append jsonl, 数据不出端, 跟 SOUL 一致)
  - 复用 catfish 本地 STT (Whisper.cpp + ffmpeg avfoundation, 五一 sprint 已 ship)
  - 复用 picker_state.json 模型联动 (P3.5.28/42/74 同款链)

# 数据 schema (~/.catfish/bookkeep.jsonl 每行 1 条)

    {
      "id": "bk_20260622_201433_a3f1",     # bk_<UTC秒>_<4 hex random>
      "ts": "2026-06-22T20:14:33+08:00",   # 本地 tz ISO8601
      "kind": "支出",                      # 支出 / 收入
      "amount": 320.0,                     # CNY 元 (浮点, 角分原值)
      "category": "餐饮",                  # 自由文本, LLM 鼓励用 8 默认类
      "note": "中午外卖 + 晚上麻辣烫"     # 自由备注 (可空)
    }

# Tool description 设计 (P3.5.72 教训)

3 个 tool description 都遵循 P3.5.72 整出来的 "直白 + code 例子, 0 审批/沙箱/安全
keyword" 原则 — 不让 LLM 把记账解读成"敏感操作 → 让用户自己跑". 用 imperative
英文风格 + 中文例子, 跟 execute_code 改后描述同款.

# 跟 catfish-memory 对比

catfish-memory 是 MemoryProvider (走 ctx.register_memory_provider), 本 plugin 是
standalone tool plugin (走 ctx.register_tool), 跟 catfish-xcatfish-user 同款 kind.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("catfish.bookkeep")


# ─────────────────────────────────────────────────────────────────────────────
# 常量 / 路径
# ─────────────────────────────────────────────────────────────────────────────


def _catfish_home() -> Path:
    """~/.catfish/ (允许 CATFISH_HOME env 覆盖 — 给单测用 tmp_path)."""
    override = os.getenv("CATFISH_HOME")
    if override:
        return Path(override)
    return Path.home() / ".catfish"


def _bookkeep_path() -> Path:
    return _catfish_home() / "bookkeep.jsonl"


# 默认 8 类 — 仅鼓励用, LLM 可自填新的, plugin 不强校验
DEFAULT_CATEGORIES = [
    "餐饮", "交通", "购物", "工资", "医疗", "转账", "房租", "其他",
]


# ─────────────────────────────────────────────────────────────────────────────
# 纯函数 helpers (单测主要测这层)
# ─────────────────────────────────────────────────────────────────────────────


def _gen_id() -> str:
    """生成 bk_<YYYYMMDD_HHMMSS>_<4 hex> id (本地时间 + 随机后缀防 race)."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    suf = secrets.token_hex(2)
    return f"bk_{ts}_{suf}"


def _now_iso_local() -> str:
    """本地 tz ISO8601 (e.g. '2026-06-22T20:14:33+08:00')."""
    # astimezone() 无参 → 系统本地 tz
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _parse_date_to_iso(date_str: Optional[str]) -> str:
    """LLM 可能填 '2026-06-21' 或 '2026-06-21T12:00:00'. 转成完整 ISO8601."""
    if not date_str:
        return _now_iso_local()
    # 已含 'T' 当完整 datetime
    try:
        if "T" in date_str:
            dt = datetime.fromisoformat(date_str)
        else:
            # 仅日期, 用当地正午 (避免 tz 切换造成日期偏)
            dt = datetime.fromisoformat(f"{date_str}T12:00:00")
        if dt.tzinfo is None:
            dt = dt.astimezone()
        return dt.isoformat(timespec="seconds")
    except (ValueError, TypeError):
        # 解析失败 fallback now, 不让记账失败
        logger.warning("date 解析失败 (%r), fallback now", date_str)
        return _now_iso_local()


def _append_record(record: Dict[str, Any]) -> None:
    """append 一条 jsonl. 父目录不存在自动建."""
    path = _bookkeep_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _read_all_records() -> List[Dict[str, Any]]:
    """读全部 jsonl 记录. 不存在 / 解析失败行 → 跳过."""
    path = _bookkeep_path()
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                out.append(obj)
        except (json.JSONDecodeError, ValueError):
            # bad line 静默跳过 — append-only 没回写需求, 容损坏 1 行
            continue
    return out


def _filter_records(
    records: List[Dict[str, Any]],
    days: Optional[int] = None,
    kind: Optional[str] = None,
    category: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """按条件过滤 records.

    - days: 最近 N 天 (跟 now 比, 不用 start/end)
    - start/end: ISO date/datetime, days 给了忽略 start/end
    - kind / category: 精确匹配
    """
    out = records
    if days is not None and days > 0:
        cutoff = time.time() - days * 86400
        out = [
            r for r in out
            if _record_epoch(r.get("ts", "")) >= cutoff
        ]
    elif start or end:
        start_epoch = _iso_to_epoch(start) if start else 0
        end_epoch = _iso_to_epoch(end) if end else time.time() + 86400
        out = [
            r for r in out
            if start_epoch <= _record_epoch(r.get("ts", "")) <= end_epoch
        ]
    if kind:
        out = [r for r in out if r.get("kind") == kind]
    if category:
        out = [r for r in out if r.get("category") == category]
    return out


def _iso_to_epoch(s: str) -> float:
    """ISO8601 → epoch. 失败返 0."""
    if not s:
        return 0.0
    try:
        if "T" not in s:
            s = f"{s}T00:00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.astimezone()
        return dt.timestamp()
    except (ValueError, TypeError):
        return 0.0


def _record_epoch(ts: str) -> float:
    return _iso_to_epoch(ts)


def _summarize(records: List[Dict[str, Any]], group_by: str = "category") -> Dict[str, Any]:
    """聚合: 总收入 / 总支出 / 净额 / 按 group_by 分组."""
    total_in = 0.0
    total_out = 0.0
    groups: Dict[str, Dict[str, float]] = {}
    for r in records:
        amount = float(r.get("amount") or 0)
        kind = r.get("kind", "")
        key = str(r.get(group_by) or "未分类")
        if key not in groups:
            groups[key] = {"income": 0.0, "expense": 0.0, "count": 0}
        groups[key]["count"] += 1
        if kind == "收入":
            total_in += amount
            groups[key]["income"] += amount
        elif kind == "支出":
            total_out += amount
            groups[key]["expense"] += amount
    return {
        "total_income": round(total_in, 2),
        "total_expense": round(total_out, 2),
        "net": round(total_in - total_out, 2),
        "count": len(records),
        f"by_{group_by}": {
            k: {
                "income": round(v["income"], 2),
                "expense": round(v["expense"], 2),
                "count": int(v["count"]),
            }
            for k, v in groups.items()
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Tool schema (LLM 看到的)
# ─────────────────────────────────────────────────────────────────────────────


def get_bookkeep_add_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": ["支出", "收入"],
                "description": "支出 (花钱) 或 收入 (收钱). 必填.",
            },
            "amount": {
                "type": "number",
                "description": "金额 (CNY 元), 必填, 必须 > 0.",
            },
            "category": {
                "type": "string",
                "description": (
                    "分类. 鼓励用默认 8 类: "
                    + " / ".join(DEFAULT_CATEGORIES)
                    + ". 也可自填新的 (e.g. '订阅', '健身'). 不填默认 '其他'."
                ),
            },
            "note": {
                "type": "string",
                "description": "备注 (e.g. '中午外卖 + 晚上麻辣烫'). 可空.",
            },
            "date": {
                "type": "string",
                "description": (
                    "日期/时间 (ISO8601: '2026-06-21' 或 '2026-06-21T12:00:00'). "
                    "不填默认现在. 用员工提到的 '昨天' / '上周三' 等相对时间, 你自己算出绝对日期填."
                ),
            },
        },
        "required": ["kind", "amount"],
    }


def get_bookkeep_query_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "days": {
                "type": "integer",
                "description": "查最近 N 天 (默认 30). 跟 start/end 互斥, days 优先.",
            },
            "kind": {
                "type": "string",
                "enum": ["支出", "收入"],
                "description": "只查支出或收入. 不填查全部.",
            },
            "category": {
                "type": "string",
                "description": "只查指定分类. 不填查全部.",
            },
            "start": {
                "type": "string",
                "description": "起始日期 ISO8601 (e.g. '2026-06-01'). 跟 days 互斥.",
            },
            "end": {
                "type": "string",
                "description": "截止日期 ISO8601 (e.g. '2026-06-30'). 跟 days 互斥.",
            },
            "limit": {
                "type": "integer",
                "description": "返多少条 (默认 50, 上限 200). 倒序 (最新先).",
            },
        },
    }


def get_bookkeep_summarize_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "days": {
                "type": "integer",
                "description": "汇总最近 N 天 (默认 30). 跟 start/end 互斥.",
            },
            "start": {
                "type": "string",
                "description": "起始日期 ISO8601. 跟 days 互斥.",
            },
            "end": {
                "type": "string",
                "description": "截止日期 ISO8601. 跟 days 互斥.",
            },
            "group_by": {
                "type": "string",
                "enum": ["category", "kind"],
                "description": "分组维度. 默认 category (按分类) — 也可 kind (按支出/收入).",
            },
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Tool description (LLM 决策合同 — P3.5.72 教训: 直白 + 例子, 0 审批/沙箱 keyword)
# ─────────────────────────────────────────────────────────────────────────────


BOOKKEEP_ADD_DESCRIPTION = (
    "Record a financial transaction (income or expense) to the local bookkeeping ledger. "
    "Call this whenever the user mentions spending or receiving money — even casually in chat. "
    "Don't ask the user to log it themselves. Don't ask for confirmation.\n"
    "\n"
    "Examples (just emit tool_call(bookkeep_add, ...)):\n"
    "- user '今天买了 5 块煎饼' → kind=\"支出\", amount=5, category=\"餐饮\", note=\"煎饼\"\n"
    "- user '工资 25000 到账' → kind=\"收入\", amount=25000, category=\"工资\"\n"
    "- user '昨天打车花了 32' → kind=\"支出\", amount=32, category=\"交通\", date=\"<昨天的 ISO date>\"\n"
    "- user '中午外卖 35 晚上麻辣烫 28' → 调 2 次 add: 35/餐饮/外卖 + 28/餐饮/麻辣烫\n"
    "\n"
    "Behavior:\n"
    "- Always emit the tool_call directly when amount + kind clear.\n"
    "- Multiple transactions in one user message → multiple tool_calls.\n"
    "- Relative dates (昨天/上周/前天) → compute absolute ISO date and put in `date`.\n"
    "- Categories: prefer 餐饮 / 交通 / 购物 / 工资 / 医疗 / 转账 / 房租 / 其他. Free text OK.\n"
)

BOOKKEEP_QUERY_DESCRIPTION = (
    "Query past bookkeeping records. Use when the user asks about their spending / income history.\n"
    "\n"
    "Examples:\n"
    "- user '我这周花了多少' → bookkeep_query(days=7, kind=\"支出\")\n"
    "- user '六月餐饮多少钱' → bookkeep_query(start=\"2026-06-01\", end=\"2026-06-30\", category=\"餐饮\")\n"
    "- user '最近的几笔' → bookkeep_query(limit=10)\n"
    "\n"
    "Returns structured list (newest first). Format the result naturally for the user."
)

BOOKKEEP_SUMMARIZE_DESCRIPTION = (
    "Aggregate bookkeeping records into a report (total income / expense / net + grouped breakdown). "
    "Use when the user wants overview / report / monthly summary.\n"
    "\n"
    "Examples:\n"
    "- user '这个月账单' → bookkeep_summarize(days=30) (or start/end of current month)\n"
    "- user '六月的支出分类' → bookkeep_summarize(start=\"2026-06-01\", end=\"2026-06-30\", group_by=\"category\")\n"
    "- user '收入支出对比' → bookkeep_summarize(days=30, group_by=\"kind\")\n"
    "\n"
    "Returns {total_income, total_expense, net, count, by_<group_by>}. "
    "Format the report naturally for the user — bullet by group, highlight largest categories."
)


# ─────────────────────────────────────────────────────────────────────────────
# Tool handler (hermes 调这, 返 JSON str)
# ─────────────────────────────────────────────────────────────────────────────


class CatfishBookkeepProvider:
    """Plugin namespace 容器 — 跟 catfish-memory CatfishMemoryProvider 同款.

    Hermes ctx.register_tool 不强制要求 provider class (catfish-policy 就是模块级),
    但带 class 有 2 好处:
      1. is_available() / load 顺序统一 (跟 catfish-memory 同模板)
      2. 单测 monkeypatch CATFISH_HOME 后 instance 独立, 不污染其它 test
    """

    def is_available(self) -> bool:
        """无外部依赖, 一定 available."""
        return True

    # ---- handler 1: bookkeep_add ------------------------------------------------

    def handle_bookkeep_add(self, args: Dict[str, Any], **kw: Any) -> str:
        try:
            kind = args.get("kind")
            amount_raw = args.get("amount")
            if kind not in ("支出", "收入"):
                return json.dumps(
                    {"success": False, "error": "kind 必填且必须 '支出' 或 '收入'"},
                    ensure_ascii=False,
                )
            try:
                amount = float(amount_raw)
            except (TypeError, ValueError):
                return json.dumps(
                    {"success": False, "error": f"amount 必须是数字, got {amount_raw!r}"},
                    ensure_ascii=False,
                )
            if amount <= 0:
                return json.dumps(
                    {"success": False, "error": "amount 必须 > 0"},
                    ensure_ascii=False,
                )

            category = args.get("category") or "其他"
            note = args.get("note") or ""
            date = args.get("date")  # 可空
            ts = _parse_date_to_iso(date)

            record = {
                "id": _gen_id(),
                "ts": ts,
                "kind": kind,
                "amount": round(amount, 2),
                "category": str(category),
                "note": str(note),
            }
            _append_record(record)
            logger.info(
                "bookkeep_add: id=%s kind=%s amount=%.2f category=%s",
                record["id"], kind, amount, category,
            )
            return json.dumps(
                {
                    "success": True,
                    "id": record["id"],
                    "recorded": record,
                    "message": f"记一笔 {kind} {amount:.2f} ({category})",
                },
                ensure_ascii=False,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("bookkeep_add 异常")
            return json.dumps(
                {"success": False, "error": f"内部异常: {e}"},
                ensure_ascii=False,
            )

    # ---- handler 2: bookkeep_query ----------------------------------------------

    def handle_bookkeep_query(self, args: Dict[str, Any], **kw: Any) -> str:
        try:
            days = args.get("days")
            if days is None and not (args.get("start") or args.get("end")):
                days = 30  # 默认最近 30 天
            kind = args.get("kind")
            category = args.get("category")
            start = args.get("start")
            end = args.get("end")
            limit = int(args.get("limit") or 50)
            limit = max(1, min(limit, 200))

            records = _read_all_records()
            filtered = _filter_records(
                records,
                days=days if days else None,
                kind=kind,
                category=category,
                start=start,
                end=end,
            )
            # 倒序 (最新先)
            filtered.sort(key=lambda r: r.get("ts", ""), reverse=True)
            sliced = filtered[:limit]

            return json.dumps(
                {
                    "success": True,
                    "count": len(filtered),
                    "returned": len(sliced),
                    "records": sliced,
                },
                ensure_ascii=False,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("bookkeep_query 异常")
            return json.dumps(
                {"success": False, "error": f"内部异常: {e}"},
                ensure_ascii=False,
            )

    # ---- handler 3: bookkeep_summarize ------------------------------------------

    def handle_bookkeep_summarize(self, args: Dict[str, Any], **kw: Any) -> str:
        try:
            days = args.get("days")
            start = args.get("start")
            end = args.get("end")
            group_by = args.get("group_by") or "category"
            if group_by not in ("category", "kind"):
                group_by = "category"
            if days is None and not (start or end):
                days = 30  # 默认最近 30 天

            records = _read_all_records()
            filtered = _filter_records(
                records,
                days=days if days else None,
                start=start,
                end=end,
            )
            summary = _summarize(filtered, group_by=group_by)
            return json.dumps(
                {
                    "success": True,
                    "window": {
                        "days": days,
                        "start": start,
                        "end": end,
                    },
                    "summary": summary,
                },
                ensure_ascii=False,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("bookkeep_summarize 异常")
            return json.dumps(
                {"success": False, "error": f"内部异常: {e}"},
                ensure_ascii=False,
            )
