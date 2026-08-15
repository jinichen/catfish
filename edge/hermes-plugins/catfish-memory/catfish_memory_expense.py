"""记账 (P3.5.x) —— 从 catfish_memory.py 拆出 (8/15 第 2 趟)。

5 个模块级 helper (id 生成 / 日期归一 / 追加 / 读全量 / 窗口汇总) +
2 个 provider 方法 (prefetch 里的汇总渲染 + memory 工具的 kind=expense 路由)。

模块级那 5 个**必须由 catfish_memory 继续 re-export** —— tests/test_expense.py
是 `from catfish_memory import _expense_read_all, ...` 直接拿的。
"""
from __future__ import annotations

import logging
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 相对优先绝对兜底 —— 见 tests/test_loader_fidelity.py
try:
    from .catfish_memory_helpers import (  # noqa: F401
        _catfish_home,
    )
except ImportError:  # 独立脚本模式 (无父包)
    from catfish_memory_helpers import (  # noqa: F401
        _catfish_home,
    )

# 跟 catfish_memory.py 同名 —— logging.getLogger 同名返回同一对象
logger = logging.getLogger("catfish.memory.plugin")


# ── P3.5.78 (6/22 鸿波): expense kind helpers — 收支记账 ──
#
# 从 catfish-bookkeep plugin (P3.5.75 已 revert) 搬过来, 内嵌作 module-level
# helpers, 跟 _query_token_set / _jaccard_similarity 同款位置. _route_to_expense
# 用. jsonl schema 跟 P3.5.75 完全兼容 (你昨晚那条 bk_20260622_224824_9868 保留).

def _expense_gen_id() -> str:
    """生成 bk_<YYYYMMDD_HHMMSS>_<4 hex> id (本地时间 + 4hex 随机后缀防 race).

    跟 P3.5.75 bookkeep.py _gen_id 同款格式.
    """
    import secrets
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    suf = secrets.token_hex(2)
    return f"bk_{ts}_{suf}"


def _expense_parse_date_to_iso(date_str: Optional[str]) -> str:
    """ISO8601 解析 — '2026-06-21' 或 '2026-06-21T12:00:00' 都接.

    空 / 解析失败 → fallback now (本地 tz). 不抛 (LLM 给坏 date 不让记账失败).
    """
    if not date_str:
        return datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        if "T" in date_str:
            dt = datetime.fromisoformat(date_str)
        else:
            dt = datetime.fromisoformat(f"{date_str}T12:00:00")
        if dt.tzinfo is None:
            dt = dt.astimezone()
        return dt.isoformat(timespec="seconds")
    except (ValueError, TypeError):
        logger.warning("expense date 解析失败 (%r), fallback now", date_str)
        return datetime.now().astimezone().isoformat(timespec="seconds")


def _expense_append_record(catfish_home: Path, record: Dict[str, Any]) -> None:
    """append 一条 bookkeep.jsonl. 父目录不存在自动建."""
    path = catfish_home / "bookkeep.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _expense_read_all(catfish_home: Path) -> List[Dict[str, Any]]:
    """读全 bookkeep.jsonl 记录. 不存在 / 解析失败行 → 跳过."""
    path = catfish_home / "bookkeep.jsonl"
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
            continue
    return out


def _expense_summarize_window(
    records: List[Dict[str, Any]], since_epoch: float
) -> Tuple[float, float, int]:
    """聚合 since_epoch 之后的: (total_in, total_out, count)."""
    total_in = 0.0
    total_out = 0.0
    n = 0
    for r in records:
        ts = r.get("ts", "")
        try:
            if "T" not in ts:
                continue
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.astimezone()
            if dt.timestamp() < since_epoch:
                continue
        except (ValueError, TypeError):
            continue
        n += 1
        amount = float(r.get("amount") or 0)
        if r.get("kind") == "收入":
            total_in += amount
        elif r.get("kind") == "支出":
            total_out += amount
    return round(total_in, 2), round(total_out, 2), n


class _ExpenseMixin:
    """见模块 docstring。"""

    def _render_expense_summary(self, catfish_home: Path) -> str:
        """P3.5.78 (6/22 鸿波): 注入 expense 最近收支 summary.

        跟 _render_session_meta 同款轻量化 — 不全量列 jsonl, 只算 (今日/本周/本月)
        × (支出/收入/笔数). LLM 看 prefetch 自然知道 expense 数据存在, 员工问
        "今天花了多少" 直接基于 prefetch 答, 不需要 bookkeep_query tool.

        jsonl 不存在 / 解析失败 → 返空 (不影响其它 section).
        """
        try:
            records = _expense_read_all(catfish_home)
            if not records:
                return ""

            now = datetime.now().astimezone()
            now_epoch = now.timestamp()

            # 今日: 00:00 起
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            today_in, today_out, today_n = _expense_summarize_window(
                records, today_start.timestamp()
            )

            # 本月: 当月 1 日 00:00 起
            month_start = today_start.replace(day=1)
            month_in, month_out, month_n = _expense_summarize_window(
                records, month_start.timestamp()
            )

            # 全部
            all_in, all_out, all_n = _expense_summarize_window(records, 0)

            lines = ["## 💸 收支 (catfish bookkeep)"]
            if today_n > 0:
                lines.append(
                    f"- 今日: 支出 ¥{today_out:.2f} / 收入 ¥{today_in:.2f} "
                    f"/ 净 {today_in - today_out:+.2f} ({today_n} 笔)"
                )
            if month_n > 0:
                lines.append(
                    f"- 本月: 支出 ¥{month_out:.2f} / 收入 ¥{month_in:.2f} "
                    f"/ 净 {month_in - month_out:+.2f} ({month_n} 笔)"
                )
            if all_n > 0:
                lines.append(
                    f"- 累计: 支出 ¥{all_out:.2f} / 收入 ¥{all_in:.2f} "
                    f"/ 净 {all_in - all_out:+.2f} ({all_n} 笔)"
                )
            if len(lines) == 1:
                return ""  # 只有标题没数据, 跳过
            return "\n".join(lines)
        except (OSError, ValueError) as e:
            logger.debug("expense_summary render 失败 %s", e)
            return ""

    def _route_to_expense(self, args: Dict[str, Any]) -> str:
        """kind=expense → append ~/.catfish/bookkeep.jsonl.

        schema 字段:
          - direction: "支出" | "收入"  (必填)
          - amount: float (必填, > 0)
          - category: str (可选, 默认 '其他')
          - note: str (可选)
          - date: ISO8601 str (可选, 默认 now)
        """
        import json as _json

        direction = args.get("direction")
        amount_raw = args.get("amount")
        if direction not in ("支出", "收入"):
            return _json.dumps({
                "success": False,
                "error": "kind=expense: direction 必填且必须 '支出' 或 '收入'",
            }, ensure_ascii=False)
        try:
            amount = float(amount_raw)
        except (TypeError, ValueError):
            return _json.dumps({
                "success": False,
                "error": f"kind=expense: amount 必须是数字, got {amount_raw!r}",
            }, ensure_ascii=False)
        if amount <= 0:
            return _json.dumps({
                "success": False,
                "error": "kind=expense: amount 必须 > 0",
            }, ensure_ascii=False)

        category = args.get("category") or "其他"
        note = args.get("note") or ""
        date_str = args.get("date")
        ts = _expense_parse_date_to_iso(date_str)

        catfish_home = self._catfish_home_cached or _catfish_home()
        record = {
            "id": _expense_gen_id(),
            "ts": ts,
            "kind": str(direction),  # P3.5.75 兼容: jsonl row 字段名仍叫 "kind"
            "amount": round(amount, 2),
            "category": str(category),
            "note": str(note),
        }

        try:
            _expense_append_record(catfish_home, record)
            logger.info(
                "_route_to_expense: id=%s direction=%s amount=%.2f category=%s",
                record["id"], direction, amount, category,
            )
            return _json.dumps({
                "success": True,
                "routed_to": "bookkeep.jsonl",
                "id": record["id"],
                "recorded": record,
                "message": f"记一笔 {direction} {amount:.2f} ({category})",
            }, ensure_ascii=False)
        except Exception as e:  # noqa: BLE001
            logger.exception("_route_to_expense 异常")
            return _json.dumps({
                "success": False,
                "error": f"expense 写失败: {e}",
            }, ensure_ascii=False)

