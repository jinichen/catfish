"""Catfish 原生 tools —— 不来自 hermes registry, 是 catfish 自己加的。

为什么需要:
    Hermes 的 session_search / memory_recall 是面向 LLM 自己"翻历史"用的, 但
    员工聊天里问"今天小鲶学了啥"时, LLM 调 session_search 要么搜不到要么
    答非所问 (今晚截图里就是这样)。我们暴露一个明确的 catfish_today_summary
    工具, 让 LLM 一眼知道该调它。

数据源:
    1. ~/.hermes/USER.md + memories/*.md  → 今天有更新的 memory
    2. ~/.hermes/skills/<ns>/<name>/SKILL.md  → 今天 mtime 落在今天的
    3. ~/.hermes/state.db sessions  → 今天启动的会话 + token 总量
    4. ~/.hermes/state.db messages  → 今天的 tool 调用次数

跟 companion-app/src-tauri/src/commands/learning.rs 是同一份逻辑的 Python
镜像 —— 故意不走 IPC 调 Companion, 因为 tool-bridge 起来时 Companion 不一
定开着 (CLI 也在用 tool-bridge)。两边各自直读 ~/.hermes 是最 robust 的。
"""
from __future__ import annotations

import base64
import os
import platform
import shutil
import sqlite3
import subprocess
import tempfile
import time
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ============================================================
# 工具 schema —— 给 LLM 看的描述
# ============================================================

CATFISH_NATIVE_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "catfish_today_summary",
        "description": (
            "看小鲶今天学到了什么:今天的对话数、工具调用次数、新增/更新的 "
            "memory 条目、新增的 skill、token 消耗总量。当员工问"
            "「今天学了什么」「今天做了啥」「今日活动」「今天有什么新进展」"
            "「小鲶今天怎么样」之类的问题时调用这个 tool, 而不是 "
            "session_search 或 memory_recall —— 那两个是给你自己翻历史的, "
            "回答员工的「今日」相关问题就用 catfish_today_summary。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
        "emoji": "📊",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_screenshot",
        "description": (
            "拍员工屏幕一张图, 返回 base64 PNG. 给视觉模型 (Qwen3-VL / Gemini "
            "vision / Qwen-Flash 多模态) 看员工 GUI 上的内容. \n\n"
            "✅ 调用场景:\n"
            "  - 员工说「这个报错是什么意思」「我屏幕上 X 是什么」\n"
            "  - 员工说「截屏看下」「你看一下我这边」\n"
            "  - GUI 调试: 看一个软件按钮在哪 / 一个对话框在说什么\n"
            "  - 表格/图片识别: 员工用 Excel/Numbers 时不想复制粘贴\n\n"
            "❌ 不该调用:\n"
            "  - 看网页内容 → 用 catfish-browser-task 跟 Chrome 直接交互, "
            "不要绕去截图\n"
            "  - 看本地文件 → 用 read_file\n"
            "  - 员工没明确要求看屏幕但你「想看一下」——不要主动截\n\n"
            "🔒 默认 mode=fullscreen: 拍员工主屏当前内容. 0 权限 0 打扰. "
            "浏览器场景 Chrome 一般占主屏, 拍下来给视觉模型看就够; "
            "桌面应用类似 (员工正在用的窗口就是前台主屏内容). \n"
            "其他模式: interactive=员工框选区域 (员工要精确选一小块时), "
            "window=员工点选窗口 (交互式). \n\n"
            "调用前要在 reason 字段一句话说明为啥要截图, 这句话会写入日志, "
            "员工也会看到."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["fullscreen", "interactive", "window"],
                    "default": "fullscreen",
                    "description": (
                        "fullscreen=全屏(默认, 零打扰零权限, 拍员工主屏当前内容). "
                        "interactive=员工框选(精确选区, 隐私优先). "
                        "window=员工点选某个窗口. "
                        "(注: active_window 模式已废弃 — 它走 osascript 'tell application System Events' 会反复弹 macOS Automation 权限对话框, 体验差)"
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": "为什么要截图 — 一句话, 员工会看到",
                },
            },
            "required": ["reason"],
        },
        "emoji": "📸",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_browser_goto",
        "description": (
            "在 Chrome 当前 tab 打开一个 URL. **走 Playwright 后端** (不再是直 CDP), "
            "Playwright 内部包了 auto-waiting + retry, 比直 CDP 稳得多. 复用 Companion 起的"
            "隔离 Chrome 已登录态 (connect_over_cdp). \n\n"
            "✅ **浏览器导航永远用这个**, 不要用 hermes browser_navigate (那个直 CDP, 失败率高). \n\n"
            "wait_until 选项: 'load' (默认, 等所有资源加载完) / 'domcontentloaded' (只等 DOM, "
            "更快但 JS 可能没跑完) / 'networkidle' (等 500ms 无网络活动, 适合 SPA). \n\n"
            "返回真实页面 title + url, 让你验证 navigate 真生效."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "完整 URL, 例 'https://www.sohu.com'"},
                "wait_until": {
                    "type": "string",
                    "enum": ["load", "domcontentloaded", "networkidle"],
                    "default": "load",
                    "description": "等到什么状态才返回. SPA 用 networkidle, 普通页面 load",
                },
                "timeout_seconds": {
                    "type": "number",
                    "default": 30.0,
                    "description": "navigate 总超时 (秒). 默认 30s",
                },
            },
            "required": ["url"],
        },
        "emoji": "🌐",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_browser_click",
        "description": (
            "点击页面上一个元素. 走 Playwright `page.click()` 内置 auto-waiting: 等元素"
            "出现 + visible + enabled + 不被遮挡, 默认 30s 内自动 retry. 比 hermes "
            "browser_click 失败率低一个数量级. \n\n"
            "selector 用 CSS / text / role 三种语法之一: \n"
            "  - CSS: 'button#submit' / 'input[name=\"username\"]'\n"
            "  - text: 'text=登录' (匹配按钮文字)\n"
            "  - role: 'role=button[name=\"提交\"]' (无障碍语义, 最稳)\n\n"
            "**优先 role**, 其次 text, 最后 CSS. role 不依赖样式 / DOM 结构, 页面改版"
            "也不容易挂. 实在拿不到 role / text 才退到 CSS."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "Playwright selector. 优先 role= / text=, fallback CSS",
                },
                "timeout_seconds": {
                    "type": "number",
                    "default": 30.0,
                    "description": "等元素可点击的最长时间, 默认 30s",
                },
            },
            "required": ["selector"],
        },
        "emoji": "🖱",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_browser_fill",
        "description": (
            "往输入框填文字. 走 Playwright `page.fill()`, auto-waiting 等输入框可写. "
            "适合 input / textarea / [contenteditable]. 自动清空原值再填, 不需要先 click.\n\n"
            "**填密码的两种方式**:\n"
            "  1. **推荐 secret_ref**: secret_ref='keychain://eis_password' (macOS) 或 "
            "'env://EIS_PASSWORD' (跨平台). tool-bridge 从安全源拉值, **密码永不进 LLM 上下文**, "
            "audit log 只记 secret_ref 引用不记密码值. 员工事先用 `security add-generic-password "
            "-a $USER -s eis_password -w '<密码>'` 存到 keychain.\n"
            "  2. **text 直传 (不推荐密码场景)**: text='jiniaA1+' 直接填, 会在 audit 标记 "
            "'credential_field_filled' 但密码已经在 LLM 上下文了.\n\n"
            "**两个字段二选一**: 给了 secret_ref 就忽略 text, 反之亦然. 都没给 → error."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "Playwright selector, 例 'input[name=\"username\"]'",
                },
                "text": {
                    "type": "string",
                    "description": "要填的文字 (明文). 用户名 / 邮箱 / 内容首选这个. 密码场景优先用 secret_ref.",
                },
                "secret_ref": {
                    "type": "string",
                    "description": (
                        "安全源引用, 例 'keychain://eis_password' / 'env://EIS_PASSWORD'. "
                        "tool-bridge 自动拉值, LLM 不会看到真值. 推荐密码场景用这个."
                    ),
                },
                "timeout_seconds": {
                    "type": "number",
                    "default": 10.0,
                    "description": "等元素可写的最长时间. 默认 10s",
                },
            },
            "required": ["selector"],
        },
        "emoji": "⌨️",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_browser_snapshot",
        "description": (
            "拿当前页面的结构化 DOM snapshot (含 visible text + role + ref). 给模型当"
            "「上下文」用 — 想点哪个按钮先 snapshot 看 ref. 走 Playwright `page.accessibility.snapshot()`"
            ", 是 accessibility tree 不是 raw HTML, 模型友好.\n\n"
            "返回字段:\n"
            "  - title: 页面 title\n"
            "  - url: 页面 url (真实 location.href)\n"
            "  - elements: 可见 / 可交互元素列表 (含 role / name / ref / text 摘要)\n"
            "  - 页面太大时 elements 会被截断到 200 个, 提示员工 scroll / 缩小范围"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "max_elements": {
                    "type": "integer",
                    "default": 200,
                    "description": "最多返回多少个元素, 防 IPC 撑爆. 默认 200",
                },
            },
            "required": [],
        },
        "emoji": "🔍",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_skill_backup",
        "description": (
            "更新 / 删除一个 skill 之前**必须**调这个 tool 做 backup. "
            "把当前 SKILL.md 复制到 ~/.hermes/skills/<ns>/<skill>/.versions/<unix-ts>.md. "
            "员工说 '回退 X skill' 时, 模型可以从 .versions/ 拿最近一版替换. \n\n"
            "✅ 调用时机:\n"
            "  - skill_manage(action=update) 之前\n"
            "  - skill_manage(action=delete) 之前 (即使要删, 也留 .versions/ 历史)\n\n"
            "❌ 不该调的场景:\n"
            "  - skill_manage(action=create) (新建无老版可备)\n"
            "  - 员工跟你聊天没明确要改 skill\n\n"
            "调用后会返回 {ok, backup_path, version_count} 让你确认 backup 真做了, "
            "然后再调 skill_manage update/delete 才合规. "
            "不调直接 update 会被 catfish-policy R10 deny."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": (
                        "skill 全名, 例如 'productivity/catfish-email' 或 "
                        "'productivity/expense-submit'. 用 / 分隔 namespace 和 skill 名."
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": "为啥要改/删这个 skill — 一句话, 员工会看到, 也写日志",
                },
            },
            "required": ["skill_name", "reason"],
        },
        "emoji": "💾",
        "toolset": "catfish_native",
        "available": True,
    },
]


# ============================================================
# 实现
# ============================================================

def _home() -> Path:
    return Path(os.environ.get("HOME") or os.environ.get("USERPROFILE") or ".")


def _hermes_dir() -> Path:
    return _home() / ".hermes"


def _today_start_unix() -> float:
    """本地时区今天 00:00:00 的 unix 秒。"""
    today = datetime.now().date()
    return datetime.combine(today, dtime.min).timestamp()


def _is_today(mtime: float) -> bool:
    return mtime >= _today_start_unix()


def _unix_to_iso(secs: float) -> str:
    try:
        return datetime.fromtimestamp(secs).isoformat()
    except Exception:
        return ""


def _collect_memories() -> List[Dict[str, Any]]:
    """读 ~/.hermes/USER.md + memories/*.md, 按 mtime 降序返回。"""
    out: List[Dict[str, Any]] = []
    hermes = _hermes_dir()

    user_md = hermes / "USER.md"
    if user_md.exists():
        try:
            st = user_md.stat()
            out.append({
                "name": "USER",
                "size": st.st_size,
                "modified_at": _unix_to_iso(st.st_mtime),
                "modified_today": _is_today(st.st_mtime),
            })
        except OSError:
            pass

    mem_dir = hermes / "memories"
    if mem_dir.is_dir():
        for entry in mem_dir.iterdir():
            if entry.suffix != ".md" or not entry.is_file():
                continue
            try:
                st = entry.stat()
            except OSError:
                continue
            out.append({
                "name": f"memories/{entry.stem}",
                "size": st.st_size,
                "modified_at": _unix_to_iso(st.st_mtime),
                "modified_today": _is_today(st.st_mtime),
            })

    out.sort(key=lambda m: m["modified_at"], reverse=True)
    return out


def _extract_description(text: str) -> str:
    """从 SKILL.md frontmatter 抽 description 字段。"""
    stripped = text.lstrip()
    if not stripped.startswith("---"):
        return ""
    after = stripped[3:].lstrip("\n")
    end = after.find("\n---")
    if end < 0:
        return ""
    for line in after[:end].splitlines():
        if line.startswith("description:"):
            value = line[len("description:"):].strip().strip('"').strip("'")
            if value:
                return value
    return ""


def _collect_new_skills() -> List[Dict[str, Any]]:
    """~/.hermes/skills/<ns>/<name>/SKILL.md mtime 落在今天的算"今天新增"。"""
    out: List[Dict[str, Any]] = []
    skills_dir = _hermes_dir() / "skills"
    if not skills_dir.is_dir():
        return out

    for ns_dir in skills_dir.iterdir():
        if not ns_dir.is_dir() or ns_dir.name.startswith("."):
            continue
        for skill_dir in ns_dir.iterdir():
            if not skill_dir.is_dir():
                continue
            manifest = skill_dir / "SKILL.md"
            if not manifest.exists():
                continue
            try:
                st = manifest.stat()
            except OSError:
                continue
            if not _is_today(st.st_mtime):
                continue
            try:
                desc = _extract_description(manifest.read_text(encoding="utf-8"))
            except OSError:
                desc = ""
            out.append({
                "full_name": f"{ns_dir.name}/{skill_dir.name}",
                "description": desc or "(无 description)",
                "modified_at": _unix_to_iso(st.st_mtime),
            })

    out.sort(key=lambda s: s["modified_at"], reverse=True)
    return out


def _collect_db_stats() -> Dict[str, int]:
    """state.db: 今天的 sessions / token / tool_calls 数。"""
    db_path = _hermes_dir() / "state.db"
    fallback = {"sessions_today": 0, "tool_calls_today": 0, "total_tokens_today": 0}
    if not db_path.exists():
        return fallback

    today_start = _today_start_unix()
    try:
        # read-only 打开,免得污染 hermes 自己的 WAL
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error:
        return fallback

    try:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS n,
                COALESCE(SUM(
                    COALESCE(input_tokens, 0) +
                    COALESCE(output_tokens, 0) +
                    COALESCE(cache_read_tokens, 0) +
                    COALESCE(cache_write_tokens, 0) +
                    COALESCE(reasoning_tokens, 0)
                ), 0) AS tok
            FROM sessions
            WHERE started_at >= ?
            """,
            (today_start,),
        ).fetchone()
        sessions = int(row[0] or 0)
        tokens = int(row[1] or 0)

        tool_row = conn.execute(
            """
            SELECT COUNT(*) FROM messages
            WHERE timestamp >= ?
              AND tool_calls IS NOT NULL
              AND tool_calls != ''
            """,
            (today_start,),
        ).fetchone()
        tool_calls = int(tool_row[0] or 0)
    except sqlite3.Error:
        return fallback
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return {
        "sessions_today": sessions,
        "tool_calls_today": tool_calls,
        "total_tokens_today": tokens,
    }


# ============================================================
# 软技能维度 (#46 沟通能力进步追踪)
# ============================================================

#: 沟通方法论关键词清单 (跟 Rust learning.rs 对齐)
KNOWN_METHODOLOGIES = [
    "STAR", "SBI", "NVC", "非暴力沟通", "金字塔", "Pyramid",
    "SPIN", "DESC", "Disagree and commit",
]


def _week_start_unix(weeks_ago: int) -> float:
    """给定"几周前"的本周一 00:00 unix 秒。"""
    today = datetime.now().date()
    days_since_monday = today.weekday()  # Monday=0
    this_monday = today - timedelta(days=days_since_monday)
    target_monday = this_monday - timedelta(weeks=weeks_ago)
    midnight = datetime.combine(target_monday, dtime.min)
    return midnight.timestamp()


def _collect_soft_skill_stats() -> Dict[str, Any]:
    """演练 / 邮件起草 / 方法论暴露 跨周指标。

    跟 Rust learning.rs 的 collect_soft_skill_stats 镜像。
    """
    fallback = {
        "coaching_sessions_today": 0,
        "coaching_sessions_this_week": 0,
        "coaching_sessions_prev_week": 0,
        "emails_drafted_today": 0,
        "methodologies_this_week": [],
    }
    db_path = _hermes_dir() / "state.db"
    if not db_path.exists():
        return fallback

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error:
        return fallback

    today_start = _today_start_unix()
    this_week_start = _week_start_unix(0)
    prev_week_start = _week_start_unix(1)

    try:
        # 演练: assistant content 含"做对了" + "改进点"
        coaching_today = int(conn.execute(
            """
            SELECT COUNT(DISTINCT session_id) FROM messages
            WHERE timestamp >= ?
              AND role = 'assistant'
              AND content LIKE '%做对了%'
              AND content LIKE '%改进点%'
            """,
            (today_start,),
        ).fetchone()[0] or 0)

        coaching_this_week = int(conn.execute(
            """
            SELECT COUNT(DISTINCT session_id) FROM messages
            WHERE timestamp >= ?
              AND role = 'assistant'
              AND content LIKE '%做对了%'
              AND content LIKE '%改进点%'
            """,
            (this_week_start,),
        ).fetchone()[0] or 0)

        coaching_prev_week = int(conn.execute(
            """
            SELECT COUNT(DISTINCT session_id) FROM messages
            WHERE timestamp >= ? AND timestamp < ?
              AND role = 'assistant'
              AND content LIKE '%做对了%'
              AND content LIKE '%改进点%'
            """,
            (prev_week_start, this_week_start),
        ).fetchone()[0] or 0)

        # 邮件起草: tool_calls 含 catfish-email
        emails_drafted = int(conn.execute(
            """
            SELECT COUNT(*) FROM messages
            WHERE timestamp >= ?
              AND tool_calls LIKE '%catfish-email%'
            """,
            (today_start,),
        ).fetchone()[0] or 0)

        # 本周接触的方法论
        rows = conn.execute(
            """
            SELECT content FROM messages
            WHERE timestamp >= ?
              AND role = 'assistant'
              AND content IS NOT NULL
              AND length(content) > 50
            LIMIT 2000
            """,
            (this_week_start,),
        ).fetchall()
        hits = set()
        for (content,) in rows:
            if not content:
                continue
            for term in KNOWN_METHODOLOGIES:
                if term in content:
                    hits.add(term)
        # 按 KNOWN 顺序输出 (UI 稳定)
        methodologies = [m for m in KNOWN_METHODOLOGIES if m in hits]

    except sqlite3.Error:
        return fallback
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return {
        "coaching_sessions_today": coaching_today,
        "coaching_sessions_this_week": coaching_this_week,
        "coaching_sessions_prev_week": coaching_prev_week,
        "emails_drafted_today": emails_drafted,
        "methodologies_this_week": methodologies,
    }


def _build_summary(
    memories_today: int,
    skills_today: int,
    sessions: int,
    tool_calls: int,
    coaching_today: int = 0,
    emails_drafted: int = 0,
) -> str:
    if (
        memories_today == 0
        and skills_today == 0
        and sessions == 0
        and coaching_today == 0
        and emails_drafted == 0
    ):
        return "今天还没动静——跟小鲶聊聊它就开始学了。"
    parts: List[str] = []
    if sessions > 0:
        parts.append(f"{sessions} 次对话")
    if tool_calls > 0:
        parts.append(f"调用 {tool_calls} 次工具")
    if coaching_today > 0:
        parts.append(f"演练 {coaching_today} 次")
    if emails_drafted > 0:
        parts.append(f"起草 {emails_drafted} 封邮件")
    if memories_today > 0:
        parts.append(f"更新 {memories_today} 条 memory")
    if skills_today > 0:
        parts.append(f"新增 {skills_today} 个 skill")
    return f"今天小鲶 {'、'.join(parts)}。"


def _collect_audit_stats_today() -> Dict[str, Any]:
    """从 ~/.hermes/.catfish_audit.jsonl 拿今天的 tool 调用统计.

    跟 db_stats 的 tool_calls_today 不同 — db_stats 来自 hermes state.db (cli/companion
    sessions), audit.jsonl 来自 tool-bridge dispatch (含 catfish native tool 调用).
    audit 视角是"tool-bridge 服务的所有调用", 更准.
    """
    fallback = {"tool_invocations_today": 0, "tool_failures_today": 0}
    try:
        from . import audit  # 避免顶层 import 循环
    except ImportError:
        return fallback

    today_iso = datetime.now().date().isoformat()  # 例 "2026-04-28"
    try:
        events = audit.read_events(since_iso=today_iso, limit=10000)
    except Exception:  # noqa: BLE001
        return fallback

    failures = sum(1 for e in events if not e.get("ok", True))
    return {
        "tool_invocations_today": len(events),
        "tool_failures_today": failures,
    }


def _collect_unused_skills(days: int = 30) -> List[Dict[str, Any]]:
    """找 ~/.hermes/skills/<ns>/<name>/SKILL.md mtime 超过 N 天前的 skill.

    判定原则: SKILL.md 文件 mtime 是 last touch (创建 / update / 员工手动改). N 天没动
    + 没出现在 audit 里 = 候选 unused. 给员工建议删 (走 catfish_skill_backup +
    skill_manage delete 流程, 详见 docs/SKILL-LIFECYCLE.md 阶段 5).

    catfish-* skill 排除掉 (软链管理, R6 也禁止删).
    """
    out: List[Dict[str, Any]] = []
    skills_dir = _hermes_dir() / "skills"
    if not skills_dir.is_dir():
        return out

    cutoff_unix = time.time() - days * 86400

    for ns_dir in skills_dir.iterdir():
        if not ns_dir.is_dir() or ns_dir.name.startswith("."):
            continue
        for skill_dir in ns_dir.iterdir():
            if not skill_dir.is_dir():
                continue
            # catfish-* skill 通过 install.sh 软链, 不算"员工 unused"
            if skill_dir.is_symlink():
                continue
            manifest = skill_dir / "SKILL.md"
            if not manifest.exists():
                continue
            try:
                mtime = manifest.stat().st_mtime
            except OSError:
                continue
            if mtime > cutoff_unix:
                continue
            out.append({
                "full_name": f"{ns_dir.name}/{skill_dir.name}",
                "last_modified": _unix_to_iso(mtime),
                "days_since_modified": int((time.time() - mtime) / 86400),
            })

    out.sort(key=lambda s: s["days_since_modified"], reverse=True)
    return out


def collect_today_summary() -> Dict[str, Any]:
    """返回与 Tauri learning_today_stats 对齐的字段(camelCase 风格), 给 LLM 看。"""
    memories = _collect_memories()
    memories_today = sum(1 for m in memories if m["modified_today"])

    new_skills = _collect_new_skills()
    skills_today = len(new_skills)

    db_stats = _collect_db_stats()
    sessions = db_stats["sessions_today"]
    tool_calls = db_stats["tool_calls_today"]
    tokens = db_stats["total_tokens_today"]

    soft = _collect_soft_skill_stats()

    # BL-C15: audit-based 字段 (tool 调用监控)
    audit_today = _collect_audit_stats_today()

    # BL-C15/C16: 30 天 SKILL.md mtime 没动的 skill, 候选 unused (给员工删/留建议)
    unused_skills = _collect_unused_skills(days=30)

    summary = _build_summary(
        memories_today, skills_today, sessions, tool_calls,
        soft["coaching_sessions_today"], soft["emails_drafted_today"],
    )

    return {
        "summary": summary,
        "date": datetime.now().date().isoformat(),
        "sessions_today": sessions,
        "tool_calls_today": tool_calls,
        "total_tokens_today": tokens,
        "memories_updated_today": memories_today,
        "memories": [m for m in memories if m["modified_today"]],
        "new_skills_count": skills_today,
        "new_skills": new_skills,
        # ==== 软技能维度 (#46) ====
        "coaching_sessions_today": soft["coaching_sessions_today"],
        "coaching_sessions_this_week": soft["coaching_sessions_this_week"],
        "coaching_sessions_prev_week": soft["coaching_sessions_prev_week"],
        "emails_drafted_today": soft["emails_drafted_today"],
        "methodologies_this_week": soft["methodologies_this_week"],
        # ==== Skill lifecycle 阶段 4 (BL-C15/C16, audit-based) ====
        "tool_invocations_today": audit_today["tool_invocations_today"],
        "tool_failures_today": audit_today["tool_failures_today"],
        "skill_unused_30d": unused_skills,
        "generated_at": _unix_to_iso(time.time()),
    }


# ============================================================
# 截图 (catfish_screenshot)
# ============================================================
#
# 设计要点:
#   1. 默认 interactive mode → 员工框选, 隐私优先
#   2. 同时返回 base64 PNG + 临时文件路径, 调用方可二选一
#   3. screencapture 退码 0 即使员工 ESC 取消 — 用 "文件不存在或为空" 判取消
#   4. Win 没有原生交互式截图工具 → 退到 fullscreen 并在结果里说明
#   5. 不主动 cleanup 临时文件 — 让员工自查 /tmp/catfish-shot-*.png

# 锁住单次截图最大字节, 防员工框了一个 27" 5K 屏幕一截 50MB 卡死 socket
# (asyncio readline 默认 limit=64KB, 我们在 server.py 把它提到 16MB)
_MAX_SCREENSHOT_BYTES = 12 * 1024 * 1024  # 12MB raw PNG, base64 后 ~16MB


def _screencapture_macos(mode: str, out_path: Path) -> Tuple[bool, Optional[str]]:
    """macOS 用系统自带 screencapture. 返回 (是否成功, 错误信息)。

    flags:
      -x          静音(无快门声)
      -i          交互模式: 框选区域 (按 ESC 取消)
      -W          配合 -i, 让员工点选窗口

    (active_window 模式已废弃 — 它需要 osascript "tell application System Events"
     拿前台窗口 ID, 触发 macOS Automation 权限弹窗, 而 hermes venv 的 python3.11
     没 Apple 代码签名, macOS 不持久化授权, 反复弹. 删掉了.)
    """
    if not shutil.which("screencapture"):
        return False, "找不到 screencapture (非 macOS 或系统残缺)"

    cmd = ["screencapture", "-x"]
    if mode == "interactive":
        cmd.append("-i")
    elif mode == "fullscreen":
        pass  # 默认就是全屏
    elif mode == "window":
        cmd.extend(["-i", "-W"])
    else:
        return False, f"未知 mode: {mode}"
    cmd.append(str(out_path))

    try:
        # interactive 模式员工可能磨蹭很久, 给宽裕超时
        # fullscreen 是即拍即出, 几秒就回
        timeout = 10 if mode == "fullscreen" else 120
        subprocess.run(cmd, timeout=timeout, capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        return False, "screencapture 超时"
    except OSError as e:
        return False, f"启动 screencapture 失败: {e}"

    # screencapture 即使员工按 ESC 也退码 0, 用文件状态判定
    if not out_path.exists() or out_path.stat().st_size == 0:
        # 清掉空文件免得垃圾
        try:
            out_path.unlink(missing_ok=True)
        except OSError:
            pass
        if mode in ("interactive", "window"):
            return False, "员工取消了截图(或选区为空)"
        return False, "截图失败 (文件不存在或为空)"
    return True, None


def _screencapture_windows(mode: str, out_path: Path) -> Tuple[bool, Optional[str]]:
    """Windows 用 PIL.ImageGrab.grab() — 只能全屏, interactive/window 不支持。"""
    try:
        from PIL import ImageGrab  # type: ignore
    except ImportError:
        return False, "Windows 截图需要 Pillow: pip install Pillow"

    try:
        # PIL 不区分 mode, 都拍全屏。caller 已经把 interactive/window 改成 fullscreen
        img = ImageGrab.grab()
        img.save(str(out_path), "PNG")
    except Exception as e:
        return False, f"截图失败: {e}"
    return True, None


def capture_screenshot(args: Dict[str, Any]) -> Dict[str, Any]:
    """catfish_screenshot tool 主入口。"""
    mode = (args.get("mode") or "fullscreen").lower()
    reason = (args.get("reason") or "").strip()

    if not reason:
        return {
            "type": "error",
            "error": "reason 必填 — 一句话说明为啥要截屏, 员工会看到",
        }
    if mode not in {"interactive", "fullscreen", "window"}:
        return {
            "type": "error",
            "error": (
                f"未知 mode: {mode}. 只接受 "
                "fullscreen / interactive / window. "
                "(active_window 模式已废弃, 用 fullscreen)"
            ),
        }

    # 临时文件 — /tmp/catfish-shot-<unix>.png. 不删, 给员工 / debug 用
    ts = int(time.time())
    out_path = Path(tempfile.gettempdir()) / f"catfish-shot-{ts}.png"

    sysname = platform.system()
    fallback_note: Optional[str] = None
    if sysname == "Darwin":
        ok, err = _screencapture_macos(mode, out_path)
    elif sysname == "Windows":
        # Win 没有原生交互式 / 窗口选择 / active window — 全退到 fullscreen
        if mode in {"interactive", "window"}:
            fallback_note = (
                f"Windows 没有原生 {mode} 截图, 自动退到 fullscreen. "
                "敏感窗口请提前关掉再让 catfish 拍."
            )
            mode = "fullscreen"
        ok, err = _screencapture_windows(mode, out_path)
    else:
        return {
            "type": "error",
            "error": f"不支持的平台: {sysname} (目前只支持 macOS / Windows)",
        }

    if not ok:
        return {"type": "error", "error": err or "截图失败"}

    size = out_path.stat().st_size
    if size > _MAX_SCREENSHOT_BYTES:
        # 文件还是留着让员工自己处理, 但不返回 base64 (会撑爆 socket)
        return {
            "type": "error",
            "error": (
                f"截图太大 ({size // (1024*1024)} MB > "
                f"{_MAX_SCREENSHOT_BYTES // (1024*1024)} MB 上限). "
                f"用 mode=interactive 框选小一点的区域. 文件: {out_path}"
            ),
        }

    raw = out_path.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")

    result: Dict[str, Any] = {
        "type": "image",
        "format": "png",
        "encoding": "base64",
        "data": b64,
        "data_uri": f"data:image/png;base64,{b64}",
        "path": str(out_path),
        "size_bytes": size,
        "captured_at": _unix_to_iso(time.time()),
        "mode": mode,
        "reason": reason,
        "summary": f"截图完成 ({mode}, {size // 1024} KB), 路径: {out_path}",
    }
    if fallback_note:
        result["platform_note"] = fallback_note
    return result


# ============================================================
# 浏览器全栈 (catfish_browser_*) — 走 Playwright connect_over_cdp
# ============================================================
#
# 历史:
#   v1 (2026-04-27): hermes browser_navigate 直 CDP, 在 Companion 隔离 Chrome 上
#      ✓ 调用成功但页面没真换, 模型幻觉 "已打开". 自己写直 CDP 绕开 — catfish_browser_goto.
#   v2 (2026-04-28): 直 CDP 撞 Chrome 138+ --remote-allow-origins 限制, 没自动等待 / iframe
#      处理代码量大, 失败率仍高.
#   v3 (2026-04-28, 当前): 全栈换 Playwright connect_over_cdp(http://127.0.0.1:9222)
#      复用员工已登录 Chrome, 但 API 用 Playwright 的稳健版 (auto-waiting / retry / iframe).
#      失败率从 ~30% → ~5%.
#
# 设计要点:
#   - 不装 chromium binary (Playwright 默认会装 ~150MB), 用 connect_over_cdp 复用员工 Chrome
#   - 每次操作开新 Playwright instance + connect → 操作 → close. 性能够用 (人在等)
#   - 全 sync API (sync_playwright), 因为 tool_bridge 工具调用是 await asyncio.to_thread

import os as _os


def _chrome_base() -> str:
    """从 hermes config 或环境变量拿 chrome 调试端口 base url."""
    return _os.environ.get("CATFISH_CHROME_BASE", "http://127.0.0.1:9222")


def _connect_playwright_browser(playwright):
    """connect_over_cdp 复用 Companion 起的 Chrome.

    Returns:
        (browser, context, page) — context 是第一个 BrowserContext, page 是第一个 page.
        失败抛 RuntimeError, 上层 catch 转 friendly error.
    """
    chrome_base = _chrome_base()
    try:
        browser = playwright.chromium.connect_over_cdp(chrome_base)
    except Exception as e:
        raise RuntimeError(
            f"连不上 Chrome CDP {chrome_base}: {e}. "
            "Chrome 没起? Companion 控制台点'启动 Catfish Chrome'."
        ) from e

    contexts = browser.contexts
    if not contexts:
        # 极少见 — Chrome 没任何 context (新启动), 创建一个
        context = browser.new_context()
    else:
        context = contexts[0]

    pages = context.pages
    if not pages:
        page = context.new_page()
    else:
        page = pages[0]  # 第一个 page (about:blank 或员工正在用的 tab)

    return browser, context, page


def _import_playwright():
    """lazy import playwright, 失败友好提示装."""
    try:
        from playwright.sync_api import sync_playwright  # type: ignore  # noqa: PLC0415
        return sync_playwright
    except ImportError as e:
        raise RuntimeError(
            "缺 playwright 包. 装一下 (在 hermes venv): "
            "HTTPS_PROXY= HTTP_PROXY= ~/.hermes/hermes-agent/venv/bin/pip install "
            "--proxy '' playwright"
        ) from e


def browser_goto(args: Dict[str, Any]) -> Dict[str, Any]:
    """走 Playwright `page.goto()`. connect_over_cdp 复用员工已登录 Chrome."""
    url = (args.get("url") or "").strip()
    if not url:
        return {"type": "error", "error": "url 必填"}
    wait_until = (args.get("wait_until") or "load").lower()
    if wait_until not in {"load", "domcontentloaded", "networkidle"}:
        wait_until = "load"
    timeout_ms = int(float(args.get("timeout_seconds") or 30.0) * 1000)
    timeout_ms = max(1000, min(timeout_ms, 120_000))

    try:
        sync_playwright = _import_playwright()
    except RuntimeError as e:
        return {"type": "error", "error": str(e)}

    try:
        with sync_playwright() as p:
            try:
                browser, context, page = _connect_playwright_browser(p)
            except RuntimeError as e:
                return {"type": "error", "error": str(e)}

            try:
                response = page.goto(url, wait_until=wait_until, timeout=timeout_ms)
                # 拿真实 title + url (Playwright 内部已经等到目标 wait_until 状态)
                actual_title = page.title()
                actual_url = page.url
                http_status = response.status if response else None

                matched = url in actual_url or actual_url.startswith(url[:20])
                return {
                    "type": "ok",
                    "navigated_to": url,
                    "actual_title": actual_title,
                    "actual_url": actual_url,
                    "http_status": http_status,
                    "summary": (
                        f"已 navigate 到 {url}. 真实 title='{actual_title}', "
                        f"url='{actual_url}', http={http_status}. "
                        f"({'✓ 加载成功' if matched else '⚠ url 跟请求不一致, 可能重定向'})"
                    ),
                }
            finally:
                # 不关 browser (它是员工日常 Chrome, 关了就糟); 不关 page (要保留状态给后续 tool 用)
                pass
    except Exception as e:
        return {"type": "error", "error": f"playwright goto 异常: {type(e).__name__}: {e}"}


def browser_click(args: Dict[str, Any]) -> Dict[str, Any]:
    """走 Playwright `page.click()`. auto-waiting 等元素出现 + visible + clickable."""
    selector = (args.get("selector") or "").strip()
    if not selector:
        return {"type": "error", "error": "selector 必填"}
    timeout_ms = int(float(args.get("timeout_seconds") or 30.0) * 1000)
    timeout_ms = max(1000, min(timeout_ms, 120_000))

    try:
        sync_playwright = _import_playwright()
    except RuntimeError as e:
        return {"type": "error", "error": str(e)}

    try:
        with sync_playwright() as p:
            try:
                browser, context, page = _connect_playwright_browser(p)
            except RuntimeError as e:
                return {"type": "error", "error": str(e)}

            try:
                page.click(selector, timeout=timeout_ms)
                # 点击后页面可能跳, 等一下 + 拿新 url + title
                page.wait_for_load_state("domcontentloaded", timeout=5000)
                return {
                    "type": "ok",
                    "selector": selector,
                    "current_url": page.url,
                    "current_title": page.title(),
                    "summary": f"✓ 点击 '{selector}' 成功. 当前页面: {page.title()}",
                }
            except Exception as e:
                # Playwright 的 timeout / element not found 都是常见错, friendly 化
                err_str = str(e)
                if "Timeout" in err_str or "timeout" in err_str:
                    return {
                        "type": "error",
                        "error": (
                            f"等不到元素 '{selector}' 可点击 (超时 {timeout_ms}ms). "
                            "selector 写错? 元素被 modal 遮住? 先 catfish_browser_snapshot 看 DOM"
                        ),
                    }
                return {"type": "error", "error": f"click 失败: {type(e).__name__}: {e}"}
    except Exception as e:
        return {"type": "error", "error": f"playwright click 异常: {type(e).__name__}: {e}"}


def browser_fill(args: Dict[str, Any]) -> Dict[str, Any]:
    """走 Playwright `page.fill()`. 自动清空原值再填.

    历史:
      v1 (2026-04-28 早): 拒填 password 字段 → 实测员工需要登录场景, 拒了核心废.
      v2 (2026-04-28 中): 允许填 + 加 security_audit 标记 → 但密码仍在 LLM 上下文.
      v3 (2026-04-28 当前): 加 secret_ref 字段, 密码从 keychain / env 拉, **永不进 LLM 上下文**.
        text 字段保留 (用户名 / 邮箱 / 内容用), secret_ref 跟 text 二选一.
    """
    selector = (args.get("selector") or "").strip()
    text = args.get("text")
    secret_ref = (args.get("secret_ref") or "").strip()

    if not selector:
        return {"type": "error", "error": "selector 必填"}

    # secret_ref 跟 text 二选一. 都没给 → error. 都给 → 优先 secret_ref + warning.
    if not secret_ref and (text is None or text == ""):
        return {
            "type": "error",
            "error": "必须给 'text' 或 'secret_ref' 之一. 密码场景用 secret_ref",
        }

    timeout_ms = int(float(args.get("timeout_seconds") or 10.0) * 1000)
    timeout_ms = max(1000, min(timeout_ms, 60_000))

    # 检测密码 / 凭据字段
    selector_lower = selector.lower()
    is_credential_field = (
        "password" in selector_lower
        or "pwd" in selector_lower
        or "passwd" in selector_lower
    )

    # 解析 secret_ref (如果有), 拿到真实密码值
    actual_text: str
    used_secret_ref = False
    if secret_ref:
        try:
            from . import secret_resolver  # noqa: PLC0415
            actual_text = secret_resolver.resolve_secret(secret_ref)
            used_secret_ref = True
        except Exception as e:  # SecretResolveError 或其他
            return {
                "type": "error",
                "error": f"secret_ref 解析失败: {e}",
            }
    else:
        actual_text = str(text)
        # 检测员工是不是把 secret_ref 写错位置 (写到 text 字段了)
        try:
            from . import secret_resolver  # noqa: PLC0415
            if secret_resolver.is_secret_ref(actual_text):
                return {
                    "type": "error",
                    "error": (
                        f"text='{actual_text[:30]}...' 看起来是 secret_ref. "
                        "应该传到 secret_ref 字段, 不是 text 字段."
                    ),
                }
        except ImportError:
            pass

    try:
        sync_playwright = _import_playwright()
    except RuntimeError as e:
        return {"type": "error", "error": str(e)}

    try:
        with sync_playwright() as p:
            try:
                browser, context, page = _connect_playwright_browser(p)
            except RuntimeError as e:
                return {"type": "error", "error": str(e)}

            try:
                page.fill(selector, actual_text, timeout=timeout_ms)
                result: Dict[str, Any] = {
                    "type": "ok",
                    "selector": selector,
                    "filled_chars": len(actual_text),
                    "summary": f"✓ 在 '{selector}' 填了 {len(actual_text)} 个字符",
                }
                # 标 audit:
                #   - 用了 secret_ref → "credential_via_secret_ref" (好的实践)
                #   - 直接 text + 是密码字段 → "credential_field_filled" (不好的实践, 提醒)
                if used_secret_ref:
                    result["security_audit"] = "credential_via_secret_ref"
                    result["secret_ref_used"] = secret_ref  # 记 ref 不记值
                    result["summary"] += f" (从 {secret_ref} 拉值, 密码不进 LLM 上下文)"
                elif is_credential_field:
                    result["security_audit"] = "credential_field_filled"
                    result["security_note"] = (
                        "selector 看起来是密码 / 凭据字段, 但 text 是明文 (已经在 LLM 上下文了). "
                        "下次推荐用 secret_ref='keychain://<name>' 或 'env://<NAME>' "
                        "让密码从安全源拉, 不进 LLM."
                    )
                return result
            except Exception as e:
                err_str = str(e)
                if "Timeout" in err_str or "timeout" in err_str:
                    return {
                        "type": "error",
                        "error": (
                            f"等不到 '{selector}' 可写 (超时 {timeout_ms}ms). "
                            "selector 错? 输入框被 disabled? 用 catfish_browser_snapshot 看一下"
                        ),
                    }
                return {"type": "error", "error": f"fill 失败: {type(e).__name__}: {e}"}
    except Exception as e:
        return {"type": "error", "error": f"playwright fill 异常: {type(e).__name__}: {e}"}


def browser_snapshot(args: Dict[str, Any]) -> Dict[str, Any]:
    """走 Playwright `page.accessibility.snapshot()` 拿结构化 DOM."""
    max_elements = int(args.get("max_elements") or 200)
    max_elements = max(10, min(max_elements, 500))

    try:
        sync_playwright = _import_playwright()
    except RuntimeError as e:
        return {"type": "error", "error": str(e)}

    try:
        with sync_playwright() as p:
            try:
                browser, context, page = _connect_playwright_browser(p)
            except RuntimeError as e:
                return {"type": "error", "error": str(e)}

            try:
                title = page.title()
                url = page.url
                # accessibility snapshot 给模型用比 raw HTML 友好得多
                a11y = page.accessibility.snapshot()

                # 把 a11y tree 平铺成 element 列表 (限制深度防爆)
                elements: List[Dict[str, Any]] = []
                _flatten_a11y(a11y, elements, max_count=max_elements)

                truncated = len(elements) >= max_elements
                return {
                    "type": "ok",
                    "title": title,
                    "url": url,
                    "elements": elements[:max_elements],
                    "element_count": len(elements),
                    "truncated": truncated,
                    "summary": (
                        f"页面 '{title}' ({url}) 有 {len(elements)} 个可见元素"
                        + (" — 截断到 200, 想看更多 scroll 后再 snapshot" if truncated else "")
                    ),
                }
            except Exception as e:
                return {"type": "error", "error": f"snapshot 失败: {type(e).__name__}: {e}"}
    except Exception as e:
        return {"type": "error", "error": f"playwright snapshot 异常: {type(e).__name__}: {e}"}


def _flatten_a11y(
    node: Optional[Dict[str, Any]],
    out: List[Dict[str, Any]],
    max_count: int = 200,
    depth: int = 0,
) -> None:
    """把 accessibility tree 递归平铺成 element 列表. 超 max_count 立刻停."""
    if not node or len(out) >= max_count:
        return
    role = node.get("role", "")
    name = node.get("name", "")
    # 只收 "有意义" 的元素 (有 name 或可交互 role)
    interesting_roles = {
        "button", "link", "textbox", "checkbox", "radio", "combobox",
        "menuitem", "tab", "heading", "img", "img-text", "form",
    }
    if name or role in interesting_roles:
        out.append({
            "role": role,
            "name": name[:100] if name else "",
            "depth": depth,
        })

    for child in node.get("children", []) or []:
        if len(out) >= max_count:
            break
        _flatten_a11y(child, out, max_count=max_count, depth=depth + 1)


# ============================================================
# Skill backup (catfish_skill_backup)
# ============================================================
#
# 配套 catfish-policy R10 + SOUL "Skill 生成纪律"扩展 + docs/SKILL-LIFECYCLE.md.
# 防御 skill 退化: skill_manage(action=update/delete) 之前必须先调本 tool 备份,
# 老版会留在 ~/.hermes/skills/<ns>/<skill>/.versions/<unix-ts>.md, 员工说"回退"
# 时模型从这里拿最近一版替换.

import shutil as _shutil  # noqa: E402  (renamed alias to avoid shadowing)


def skill_backup(args: Dict[str, Any]) -> Dict[str, Any]:
    """把当前 skill 的 SKILL.md 复制到 .versions/<unix-ts>.md."""
    skill_name = (args.get("skill_name") or "").strip()
    reason = (args.get("reason") or "").strip()

    if not skill_name:
        return {
            "type": "error",
            "error": "skill_name 必填, 格式 'namespace/skill_name', 例如 'productivity/catfish-email'",
        }
    if not reason:
        return {
            "type": "error",
            "error": "reason 必填, 一句话说明为啥要改/删这个 skill",
        }
    if "/" not in skill_name:
        return {
            "type": "error",
            "error": (
                f"skill_name 格式错: '{skill_name}'. 必须是 'namespace/skill', "
                "例如 'productivity/expense-submit'"
            ),
        }

    skills_root = _hermes_dir() / "skills"
    skill_dir = skills_root / skill_name
    skill_md = skill_dir / "SKILL.md"

    # skill_dir 可能是软链 (catfish 自家 skill 走 install.sh 软链回源代码),
    # 这种 skill 不能让 LLM 改, R6 已防, 但这里也加一道
    if skill_dir.is_symlink():
        return {
            "type": "error",
            "error": (
                f"skill '{skill_name}' 是软链 (大概率是 catfish 自家 skill, "
                "由 install.sh 管理), LLM 不能改. 想改让员工跑 install.sh 重装"
            ),
        }

    if not skill_md.exists():
        return {
            "type": "error",
            "error": (
                f"找不到 {skill_md}. skill '{skill_name}' 可能不存在, "
                "或者 namespace/name 拼错了. 用 skill_view / skill_list 确认下"
            ),
        }

    # backup 到 .versions/<unix-ts>.md
    versions_dir = skill_dir / ".versions"
    versions_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    backup_path = versions_dir / f"{ts}.md"

    try:
        _shutil.copy2(skill_md, backup_path)
    except OSError as e:
        return {"type": "error", "error": f"backup 失败: {e}"}

    # 看下 .versions/ 现在有几版, 给个 UI hint
    try:
        version_count = sum(
            1 for p in versions_dir.iterdir() if p.is_file() and p.suffix == ".md"
        )
    except OSError:
        version_count = 1

    return {
        "type": "ok",
        "skill_name": skill_name,
        "backup_path": str(backup_path),
        "version_count": version_count,
        "reason": reason,
        "summary": (
            f"已 backup '{skill_name}' 到 {backup_path}. 现在 .versions/ 有 "
            f"{version_count} 个历史版本. 现在可以安全调 skill_manage update/delete."
        ),
    }


# ============================================================
# dispatch 入口
# ============================================================

NATIVE_TOOL_NAMES = {t["name"] for t in CATFISH_NATIVE_TOOLS}


def is_native(name: str) -> bool:
    return name in NATIVE_TOOL_NAMES


def dispatch_native(name: str, args: Dict[str, Any]) -> Any:
    if name == "catfish_today_summary":
        return collect_today_summary()
    if name == "catfish_screenshot":
        return capture_screenshot(args)
    if name == "catfish_browser_goto":
        return browser_goto(args)
    if name == "catfish_browser_click":
        return browser_click(args)
    if name == "catfish_browser_fill":
        return browser_fill(args)
    if name == "catfish_browser_snapshot":
        return browser_snapshot(args)
    if name == "catfish_skill_backup":
        return skill_backup(args)
    raise ValueError(f"unknown native tool: {name}")
