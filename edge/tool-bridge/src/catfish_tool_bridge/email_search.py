"""BL-EMAIL-SEARCH-TOOL (5/18 鸿波"对话里检索没搜到邮件") — catfish_email_search
native tool 让 LLM 在 chat 里搜员工的本地邮件 (Apple Mail + Foxmail).

# 真问题
鸿波 5/18 晚问 "现在检索内容的时候会自动检索文件、邮件、对话吗?". 真相:
local_search (文件) 跟 catfish_search_sessions (对话) 都有 LLM tool 暴露,
**邮件那条没有** — `catfish-email search` CLI 在但没包成 tool. 员工问鲶鱼
"找上个月张三那封工资邮件" 时鲶鱼搜不到, 跟 catfish chat-first 范式不符.

补这条: shell out `catfish-email search "<query>" --json` 跨 Apple Mail +
Foxmail 全文搜 (FTS), 5/18 BL-EMAIL-LIST-ADAPTER-FIELD 后 JSON 含 adapter
字段, LLM 看到来源能讲清"在 Mail.app 里 / Foxmail 里".

# 数据源
catfish-email CLI (shell out, 不直读 sqlite — 避开多 adapter / Apple Mail
AS 各自的复杂度, 让 CLI 是单一入口). CLI 自己走 Apple Mail FTS (mailbox
whose subject contains q) + Foxmail mail_fts (FTS3) 跨账号合并.

# 安全
- 不缓存搜索结果 (跟 BL-EMAIL-DESIGN 红线一致: 邮件正文永不缓存)
- 不读邮件正文, 只返 subject + sender + snippet (CLI 给的)
- 永不抛异常 (失败返空 + error 字段, LLM 友好降级)
- subprocess 10s timeout
- query 直接 pass 给 CLI argv (CLI 内部 SQL 参数化, 不拼 SQL)
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from typing import Any

logger = logging.getLogger("catfish.tool_bridge.email_search")

_DEFAULT_LIMIT = 20
_MAX_LIMIT = 50
_SUBPROCESS_TIMEOUT = 10.0


def _find_catfish_email() -> str | None:
    """优先 PATH, 再 ~/.local/bin (跟 hermes plugin install 路径对齐)."""
    # PATH
    p = shutil.which("catfish-email")
    if p:
        return p
    # 用户 home ~/.local/bin
    import os
    cand = os.path.expanduser("~/.local/bin/catfish-email")
    if os.path.exists(cand) and os.access(cand, os.X_OK):
        return cand
    return None


def tool_email_search(args: dict[str, Any]) -> dict[str, Any]:
    """LLM 调入口. args:
        query (str, 必填): 搜索关键字
        folder (str, 可选): * = 跨所有文件夹 (默认), 'Inbox' = 仅收件箱
        limit (int, 可选): 上限 (默认 20, max 50)
        account (str, 可选): 指定账号地址, 跨账号 = 不传

    Returns:
        {ok: bool, matches: [...], count: int, summary: str, error?: str}
    """
    query = str(args.get("query") or "").strip()
    if not query:
        return {
            "ok": False,
            "matches": [],
            "count": 0,
            "summary": "缺 query 参数, 没法搜",
            "error": "query 必填",
        }

    bin_path = _find_catfish_email()
    if bin_path is None:
        return {
            "ok": False,
            "matches": [],
            "count": 0,
            "summary": "catfish-email CLI 没装. 装一下: pip install -e ~/person_task/catfish/edge/email-agent",
            "error": "catfish-email not found in PATH or ~/.local/bin",
        }

    folder = str(args.get("folder") or "*").strip() or "*"
    # `or` 替 None 检测会把 0 也当 falsy 走默认 — 这里要严格区分 None vs 0
    # (0 应该被 clamp 到 floor 1, 不是回到默认 20).
    raw_limit = args.get("limit")
    if raw_limit is None:
        limit = _DEFAULT_LIMIT
    else:
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            limit = _DEFAULT_LIMIT
    limit = max(1, min(_MAX_LIMIT, limit))
    account = args.get("account")

    cmd = [bin_path, "search", query, "--folder", folder,
           "--limit", str(limit), "--json"]
    if account:
        cmd += ["--account", str(account)]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "matches": [],
            "count": 0,
            "summary": f"邮件搜索超时 ({_SUBPROCESS_TIMEOUT}s) — 邮件客户端卡了? 试少一点 limit.",
            "error": "subprocess timeout",
        }
    except OSError as e:
        return {
            "ok": False,
            "matches": [],
            "count": 0,
            "summary": f"catfish-email 调用失败: {e}",
            "error": str(e),
        }

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()[:500]
        return {
            "ok": False,
            "matches": [],
            "count": 0,
            "summary": f"邮件搜索失败 (退出码 {result.returncode}): {stderr}",
            "error": stderr,
        }

    stdout = (result.stdout or "").strip()
    if not stdout:
        return {
            "ok": True,
            "matches": [],
            "count": 0,
            "summary": f"没找到含 '{query}' 的邮件 (跨所有客户端 + 账号)",
        }

    try:
        items = json.loads(stdout)
    except json.JSONDecodeError as e:
        return {
            "ok": False,
            "matches": [],
            "count": 0,
            "summary": f"邮件搜索结果 JSON 解析失败: {e}",
            "error": str(e),
        }
    if not isinstance(items, list):
        items = []

    # 5/18 BL-EMAIL-SEARCH-TOOL: 空 list 单独走"没找到" 文案, 让 LLM 念出来
    # 自然 ("我搜了, 没找到 X 的邮件") 比 "找到 0 封 ..." 顺口.
    if not items:
        return {
            "ok": True,
            "matches": [],
            "count": 0,
            "summary": f"没找到含 '{query}' 的邮件 (跨所有客户端 + 账号)",
        }

    # LLM 看的 matches — 不返 body (隐私 + token 省), 只 subject/sender/date/adapter/account
    matches = [
        {
            "id": it.get("id"),
            "adapter": it.get("adapter"),
            "account": it.get("account"),
            "subject": it.get("subject") or "",
            "sender": it.get("sender") or "",
            "date": it.get("date") or "",
            "is_read": bool(it.get("is_read", False)),
            # snippet 是 body_text 前 ~200 字 (catfish-email list snippet 模式)
            "snippet": (it.get("body_text") or "")[:200],
        }
        for it in items
    ]

    # 按 adapter 分组写一句 summary 给 LLM 念
    by_adapter: dict[str, int] = {}
    for m in matches:
        by_adapter[m["adapter"] or "unknown"] = by_adapter.get(m["adapter"] or "unknown", 0) + 1
    summary_parts = [f"{n} 封 ({a})" for a, n in by_adapter.items()]
    summary = f"找到 {len(matches)} 封含 '{query}' 的邮件: " + " / ".join(summary_parts)

    return {
        "ok": True,
        "matches": matches,
        "count": len(matches),
        "summary": summary,
    }
