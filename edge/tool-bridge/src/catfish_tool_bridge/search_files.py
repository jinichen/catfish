"""catfish_search_files native tool — 搜员工本机已索引的文件 (8/14).

# 为什么补这个

鸿波 8/14: 让小鲶找一份"AI 场景梳理"文件, 它答「本地文件搜索环境无法启动」,
两轮都没找到。而 Companion 仪表盘同时显示索引好好的: 10,518 个文件 / 332.9 MB /
2 分钟前刚入库。

查下来两边都没说谎 —— **索引在工作, 但 LLM 手上没有任何能查它的工具**:

  · `catfish-local-search` 没注册成 hermes 的 MCP server (config.yaml 里只有
    `catfish-tools` 一个)
  · `catfish-search` 二进制压根没装 (~/.local/bin / .catfish/bin / .hermes/bin 都没有)
  · `commands/skills.rs:946` 明确拒绝员工自加 `catfish-` 开头的 MCP
    ("核心 MCP 保留命名"), 所以员工想自己补也补不了

模型说的"搜索环境无法启动"是它自己的转述 —— 它只有 `catfish_search_docs`
(只搜 strategic_docs/) 和 `catfish_search_attachments` (只搜上传过的附件),
够不着通用文件, 于是编了个说辞。

而那份文件**就在索引里**: ~/.catfish/uploads/1785827576-业务场景梳理清单.xlsx。

# 为什么走 tool-bridge 直读, 不修 MCP 注册

`autostart.rs:255-270` 记着 `catfish-tools` 当初是一模一样的处境:

    谁都不装, 谁都不能装, 而所有代码都假设它在
    …autostart 管 tool-bridge / local-search / chrome, 唯独不管 MCP 注册

当时的结论是"既然它是主链路依赖, 就该由平台自己保证它在", 于是加了
`ensure_catfish_tools_mcp_registered()`。local-search 是同一个病。

与其给它再加一层自动注册 (还得先装二进制), 不如让 tool-bridge 直读 search.db:
tool-bridge 本身是被 autostart 自动注册的, 一次砍掉「装二进制」和「注册第二个
MCP」两个会失效的手工步骤。这个访问方式仓里已有先例 —— style_fingerprint.py
就是直读同一个库。

# trigram 分词器的三个坑 (都是拿真库测出来的, 不是看文档猜的)

索引建表是 `tokenize='trigram'`:

  1. **少于 3 个字符静默返回 0 行, 不报错。** `"资质"`(2 字) → 0, `"AI"` → 0。
     在员工眼里这跟"文件不存在"长得一模一样 —— 所以本模块把它单独识别出来
     (`short_terms`), 绝不让它伪装成"没找到"。

  2. **坑 1 会伪装成别的结论 —— 我在这儿栽过一次, 留个记号。**
     我先测出 `'"业务场景" AND "梳理"'` → 0 行、`'"业务场景" "梳理"'` → 6 行,
     据此断定"trigram 下 AND 关键字失效", 还把它写进了注释和测试。

     **是错的。** 真因是 `"梳理"` 只有 2 个字, 单独就返 0 (坑 1), 于是任何
     含它的组合都是 0 —— 跟用不用 AND 没关系。换成两个都 ≥3 字的词重测:

         "业务场景" "梳理清单"     → 4      "业务场景" AND "梳理清单"     → 4
         "人工智能" "评估模型"     → 22     "人工智能" AND "评估模型"     → 22
         "资质对标" "中电福富"     → 50     "资质对标" AND "中电福富"     → 50

     完全一致。AND 和并列都是隐式 AND, 都能用。本模块用并列纯粹是因为不用
     考虑关键词转义, 不是因为 AND 有问题。

     教训: 拿一个**本身就命中不了**的词去做对照组, 得到的差异全是假的。

  3. **`path` 列是 UNINDEXED, MATCH 搜不到文件名。** 而 `title` 很多行是内容
     哈希不是文件名。所以按文件名找必须另走 LIKE —— 本模块内容和文件名双路查,
     再合并去重。
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("catfish.tool_bridge.search_files")

#: 跟 style_fingerprint.py 用同一个库 (员工本机 local_search 索引)。
_DB_NAME = "search.db"

#: 撞锁时最多等这么久。全量索引一跑几分钟, sqlite 默认 busy_timeout=0 会当场抛
#: "database is locked" —— 跟 style_fingerprint.DB_BUSY_TIMEOUT_SEC 对齐。
DB_BUSY_TIMEOUT_SEC = 30.0

#: trigram 的硬下限。低于此的词进 MATCH 只会静默返 0 行。
MIN_TERM_CHARS = 3

DEFAULT_LIMIT = 10
MAX_LIMIT = 50
SNIPPET_TOKENS = 16


def _db_path() -> Path:
    """~/.catfish/search.db。CATFISH_HOME 覆盖给测试用 (跟 search_docs 同约定)。"""
    if env := os.environ.get("CATFISH_HOME"):
        return Path(env) / _DB_NAME
    return Path.home() / ".catfish" / _DB_NAME


def _split_terms(query: str) -> tuple[list[str], list[str]]:
    """拆词, 返回 (可用于 MATCH 的, 太短被丢的)。

    太短的**不是静默丢弃** —— caller 要把它们回报给 LLM, 否则"AI" 这种词被吞掉
    之后, 搜不到的原因就永远查不出来了 (见模块 docstring 坑 1)。
    """
    raw = [t for t in re.split(r"\s+", query.strip()) if t]
    usable = [t for t in raw if len(t) >= MIN_TERM_CHARS]
    short = [t for t in raw if len(t) < MIN_TERM_CHARS]
    return usable, short


def _fts_expr(terms: list[str]) -> str:
    """拼 FTS5 表达式: 每个词加双引号, 词之间**并列**(隐式 AND)。

    用并列而不是 `AND` 关键字, 只是省掉一层转义顾虑 —— 两者实测等价
    (见模块 docstring 坑 2 里那三组对照, 以及我在那儿栽的那一跤)。
    双引号按 FTS5 规矩用两个双引号转义。
    """
    return " ".join('"' + t.replace('"', '""') + '"' for t in terms)


def _connect(path: Path) -> sqlite3.Connection:
    try:
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=DB_BUSY_TIMEOUT_SEC)
    except sqlite3.Error:
        # 有些环境 URI 只读打不开 (例如某些网络盘), 退回普通打开
        return sqlite3.connect(str(path), timeout=DB_BUSY_TIMEOUT_SEC)


def search_files(query: str, limit: int = DEFAULT_LIMIT,
                 file_types: list[str] | None = None) -> dict[str, Any]:
    """内容 MATCH + 文件名 LIKE 双路查, 合并去重。"""
    db = _db_path()
    usable, short = _split_terms(query)
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))

    if not db.exists():
        return {
            "matches": [], "count": 0, "error": "index_unavailable",
            "summary": (
                "员工本机还没建过文件索引 (找不到 search.db)。别说'没找到这个文件' —— "
                "要让他去 Companion 仪表盘 → 服务/配额 → 搜索范围, 加目录并建索引。"
            ),
        }

    conn = _connect(db)
    try:
        rows: dict[str, dict[str, Any]] = {}

        # ── 路 1: 全文内容 (FTS5 MATCH) ──
        if usable:
            sql = (
                "SELECT path, file_type, snippet(documents, 2, '⟦', '⟧', '…', ?) "
                "FROM documents WHERE documents MATCH ? "
                "ORDER BY bm25(documents) LIMIT ?"
            )
            for path, ftype, snip in conn.execute(
                sql, (SNIPPET_TOKENS, _fts_expr(usable), limit * 3)
            ):
                rows[path] = {"path": path, "file_type": ftype,
                              "snippet": (snip or "").replace("\n", " ").strip(),
                              "matched_by": "content"}

        # ── 路 2: 文件名 —— 查 file_meta, **不要查 documents** ──
        #
        # ⚠ 这里踩过一个会静默给错答案的坑, 记下来:
        #
        #   `documents` 是 FTS5 虚拟表, `path` 列是 UNINDEXED。在它上面写 LIKE,
        #   SQLite 会把约束下推给 FTS5, 而 FTS5 处理不了 LIKE —— **返回 0 行,
        #   不报错**。实测 `WHERE path LIKE '%uploads%'` 返 0, 而库里几千行都
        #   匹配; 同一条件在 file_meta (普通 B-tree 表) 上返 115 行。
        #
        #   更阴的是它**不稳定**: 写成 `path LIKE ? OR path LIKE ?` 时 SQLite
        #   改走全表扫, 又能返出结果 —— 我最早就是这么误判"LIKE 能用"的。
        #   而那个全表扫要读整个 586MB 的 FTS5 内容, 当场把进程搞成 Segfault。
        #
        #   所以文件名一律走 file_meta: 普通表, LIKE 正常, 还自带 mtime。
        #
        # 这一路**不设最短长度** —— LIKE 是子串匹配, "AI" 这种 2 字词在文件名上
        # 完全可用, 正是 trigram 够不着的那部分。
        name_terms = [t for t in re.split(r"\s+", query.strip()) if t]
        if name_terms:
            where = " AND ".join("path LIKE ?" for _ in name_terms)
            args = [f"%{t}%" for t in name_terms] + [limit * 3]
            for path, mtime in conn.execute(
                f"SELECT path, mtime FROM file_meta WHERE {where} "
                f"ORDER BY mtime DESC LIMIT ?", args
            ):
                if path in rows:
                    rows[path]["matched_by"] = "content+filename"
                    rows[path]["mtime"] = mtime
                else:
                    rows[path] = {"path": path, "file_type": Path(path).suffix.lower(),
                                  "snippet": "", "matched_by": "filename", "mtime": mtime}

        # 给内容命中的那批补 mtime (file_meta 是普通表, IN 查询安全)
        need_mtime = [p for p, r in rows.items() if "mtime" not in r]
        if need_mtime:
            ph = ",".join("?" * len(need_mtime))
            for p, mt in conn.execute(
                f"SELECT path, mtime FROM file_meta WHERE path IN ({ph})", need_mtime
            ):
                if p in rows:
                    rows[p]["mtime"] = mt
        out = list(rows.values())
        if file_types:
            want = {t if t.startswith(".") else f".{t}" for t in file_types}
            out = [r for r in out if (r.get("file_type") or "") in want]

        # 文件名命中排前面 (员工说"找一份叫 X 的文件"时这最符合预期), 其次按 mtime 新→旧
        rank = {"filename": 0, "content+filename": 0, "content": 1}
        out.sort(key=lambda r: (rank.get(r["matched_by"], 2), -(r.get("mtime") or 0)))
        out = out[:limit]
    finally:
        conn.close()

    res: dict[str, Any] = {"matches": out, "count": len(out)}
    if short:
        # ⚠ 这条是本工具存在的一半理由。少于 3 字的词在 trigram 下静默返 0,
        #   不说出来的话, "搜不到"和"这词没法搜"在现场长得一模一样。
        res["short_terms"] = short
        res["short_terms_note"] = (
            f"这些词少于 {MIN_TERM_CHARS} 个字符, 全文索引 (trigram) 搜不了, "
            f"只在文件名里匹配了: {', '.join(short)}。"
            "想搜内容请换更长的词。"
        )
    if not out:
        if not usable and short:
            res["summary"] = (
                f"没找到。注意: 你给的词 ({', '.join(short)}) 全都少于 "
                f"{MIN_TERM_CHARS} 个字符, 内容全文搜不了 —— **这不等于文件不存在**。"
                "换个长一点的关键词再试。"
            )
        else:
            res["summary"] = (
                "索引里没有匹配的文件。可能是: (1) 关键词不对, 换个说法; "
                "(2) 文件不在索引范围内 —— 员工可以在 Companion 仪表盘 → "
                "服务/配额 → 搜索范围 里加目录。别直接断言文件不存在。"
            )
    else:
        res["summary"] = f"命中 {len(out)} 个文件。"
    return res


def tool_search_files(args: dict) -> dict[str, Any]:
    """dispatch 入口. args = {"query": str, "limit"?: int, "file_types"?: list[str]}."""
    query = str(args.get("query", "")).strip()
    if not query:
        return {"matches": [], "count": 0,
                "summary": "query 是必填。给个关键字 (文件名片段或正文里的词都行)。"}
    start = time.time()
    try:
        out = search_files(
            query=query,
            limit=args.get("limit") or DEFAULT_LIMIT,
            file_types=args.get("file_types"),
        )
    except Exception as e:  # 永远不抛 (跟 search_docs / search_skills 同约定)
        logger.warning("catfish_search_files 挂了 (%s)", e, exc_info=True)
        return {"matches": [], "count": 0, "error": type(e).__name__,
                "summary": f"搜索出错 ({type(e).__name__}: {e})。别说'文件不存在' —— "
                           "这是工具出错, 不是没找到。"}
    out["latency_ms"] = round((time.time() - start) * 1000, 1)
    return out


__all__ = ["search_files", "tool_search_files"]
