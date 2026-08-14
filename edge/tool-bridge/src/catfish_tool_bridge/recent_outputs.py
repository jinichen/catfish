"""BL-FIX-TIMEOUT-OUTPUTS (5/13 鸿波"做不出文档") — catfish_list_my_outputs tool.

跟 gateway recent_outputs 同源 (~/.catfish/output/), 但 tool-bridge 直读, 不
跨进程. LLM 调这个 tool 知道自己 (跨 session) 写过哪些文件, 不再"反复重做".

用例:
- 鸿波: "我刚才让你做的 xlsx 在哪?" → LLM 调 catfish_list_my_outputs(hours_back=2)
- LLM 自己想确认 "我之前做过这个吗" → 主动调
- 跨 session: 鸿波新建对话问 "上次合并 8 项资质的 xlsx 还在吗" → 列今天文件就找到
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger("catfish.tool_bridge.recent_outputs")


#: 产出目录。`outputs` 是 8/14 起的统一约定; `output` 是历史目录, 保留只读。
#
# 为什么两个都扫: 员工机器上 output/ 里有 309 个历史文件 (周报 / 汇报 / 对标报告),
# 改名之后如果只读 outputs/, 这些在 LLM 眼里就等于凭空消失了。写入方已全部
# 切到 outputs/, 所以 output/ 只会越来越旧, 不会再长。
_OUTPUT_DIRS = ("outputs", "output")


def _output_dirs() -> list[Path]:
    catfish_home = os.environ.get("CATFISH_HOME", "").strip()
    base = Path(catfish_home).expanduser() if catfish_home else Path.home() / ".catfish"
    return [base / name for name in _OUTPUT_DIRS]


def _human_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def list_recent(*, hours_back: int = 24, limit: int = 20) -> list[dict[str, Any]]:
    cutoff = (datetime.now() - timedelta(hours=max(0, hours_back))).timestamp()
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        # ⚠ 8/14: 原来是 `d.iterdir()` + `if not entry.is_file(): continue` ——
        #    **非递归**, 子目录直接跳过。而 weekly-report / leadership-briefing
        #    写的是 `<日期>/<时间>_slug/xxx.docx`, 也就是说这个工具从来列不出
        #    那两个 skill 的产出。员工问"我那份汇报呢", LLM 拿到的是一份看起来
        #    完整、实际漏掉整类文件的清单 —— 比报错更难发现。
        #    改成 rglob 递归。
        for d in _output_dirs():
            if not d.exists() or not d.is_dir():
                continue
            for entry in d.rglob("*"):
                if not entry.is_file():
                    continue
                if entry.name.startswith("."):   # .DS_Store 之类
                    continue
                try:
                    st = entry.stat()
                except OSError:
                    continue
                if st.st_mtime < cutoff:
                    continue
                key = str(entry.resolve())
                if key in seen:
                    continue
                seen.add(key)
                out.append({
                    "path": str(entry),
                    "name": entry.name,
                    "size": st.st_size,
                    "size_human": _human_size(st.st_size),
                    "mtime": st.st_mtime,
                    "mtime_iso": datetime.fromtimestamp(st.st_mtime).isoformat(),
                    "ext": entry.suffix.lower(),
                })
    except OSError as e:
        logger.warning("BL-FIX-TIMEOUT-OUTPUTS: 列产出目录失败: %s", e)
        return []
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out[: max(1, int(limit))]


def tool_list_my_outputs(args: dict[str, Any]) -> dict[str, Any]:
    """catfish_list_my_outputs tool 入口.

    args:
      hours_back: int (默认 24, 0 = 全部时间)
      limit: int (默认 20, 上限 100)
      ext_filter: str (可选, '.xlsx' / '.docx' / 等过滤)
    """
    try:
        hours_back = int(args.get("hours_back", 24))
    except (TypeError, ValueError):
        hours_back = 24
    if hours_back == 0:
        hours_back = 24 * 365  # 1 年, 当作"全部"
    try:
        limit = max(1, min(100, int(args.get("limit", 20))))
    except (TypeError, ValueError):
        limit = 20
    ext_filter = (args.get("ext_filter") or "").strip().lower()

    items = list_recent(hours_back=hours_back, limit=limit * 3)  # 多取一些, 后过滤
    if ext_filter:
        if not ext_filter.startswith("."):
            ext_filter = "." + ext_filter
        items = [x for x in items if x["ext"] == ext_filter]
    items = items[:limit]

    if not items:
        return {
            "ok": True,
            "count": 0,
            "items": [],
            "summary": (
                f"📁 过去 {hours_back}h 没找到文件 (~/.catfish/output/ 空). "
                f"可能是: 鲶鱼还没调 execute_code 写过 / 写到了别的目录"
            ),
        }

    # 按 ext 分组算 summary
    by_ext: dict[str, int] = {}
    for x in items:
        by_ext[x["ext"] or "(no ext)"] = by_ext.get(x["ext"] or "(no ext)", 0) + 1

    summary_lines = [
        f"📁 过去 {hours_back}h 鲶鱼写了 {len(items)} 个文件 (倒序最新):"
    ]
    for x in items[:10]:
        when = x["mtime_iso"][:16]
        summary_lines.append(f"  - {when}  {x['name']}  ({x['size_human']})")
    if by_ext:
        ext_list = sorted(by_ext.items(), key=lambda kv: -kv[1])
        summary_lines.append(
            "类型分布: " + ", ".join(f"{k}({v})" for k, v in ext_list)
        )

    return {
        "ok": True,
        "count": len(items),
        "items": items,
        "by_ext": by_ext,
        "summary": "\n".join(summary_lines),
    }


__all__ = ["list_recent", "tool_list_my_outputs"]
