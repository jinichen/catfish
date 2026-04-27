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
            "在 Chrome 当前 tab 真正打开一个 URL. 走原生 CDP page-level Page.navigate, "
            "不经过 hermes browser_navigate (后者实测在 Companion 起的隔离 Chrome 上会"
            "「调用 ✓ 成功但页面没真换」, 模型还会幻觉说「已打开」). \n\n"
            "✅ **优先用这个** 而不是 browser_navigate. 浏览器导航就这一个工具, hermes 的 "
            "browser_navigate 标记为已废弃 (除非这个挂了再 fallback). \n\n"
            "成功返回当前页面 title + url 的真实 CDP 反馈, 让你能验证 navigate 真生效, "
            "不是模型自己编的 \"已打开\". 失败返回具体 CDP 错误."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "要打开的完整 URL, 比如 'https://www.sohu.com'",
                },
                "wait_seconds": {
                    "type": "number",
                    "default": 2.0,
                    "description": "navigate 后等几秒让页面加载, 然后回报 title. 默认 2s",
                },
            },
            "required": ["url"],
        },
        "emoji": "🌐",
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
# 浏览器原生导航 (catfish_browser_goto)
# ============================================================
#
# 历史教训 2026-04-27: hermes 的 browser_navigate 在 Companion 起的隔离 Chrome
# (--user-data-dir=隔离 profile) 上, 调用看似 ✓ 成功但页面物理没换, 模型还会编
# "已打开". 自己写一个走 page-level CDP, 用 /json/list 拿到 active page target,
# Page.navigate 直发, 然后等几秒拿 title + url 验证真生效.

import json as _json  # 模块顶部 import 过, 这里别名避免变量名冲突
import urllib.request as _urlreq

# 全局 websocket 连接池太复杂 — 每次开新连接, navigate 完关. 性能够用 (人在等).


def _http_get_json(url: str, timeout_sec: float = 3.0) -> Any:
    """简单 GET + json.loads, 不用 requests 减依赖."""
    req = _urlreq.Request(url, headers={"User-Agent": "catfish-tool-bridge"})
    with _urlreq.urlopen(req, timeout=timeout_sec) as resp:
        return _json.loads(resp.read().decode("utf-8"))


def _chrome_base() -> str:
    """从 hermes config 或环境变量拿 chrome 调试端口 base url."""
    return os.environ.get("CATFISH_CHROME_BASE", "http://127.0.0.1:9222")


def browser_goto(args: Dict[str, Any]) -> Dict[str, Any]:
    """走原生 CDP Page.navigate. 不依赖 hermes browser_navigate."""
    url = (args.get("url") or "").strip()
    if not url:
        return {"type": "error", "error": "url 必填"}
    wait_seconds = float(args.get("wait_seconds") or 2.0)
    wait_seconds = max(0.0, min(wait_seconds, 30.0))  # 防员工传 9999 卡死

    chrome_base = _chrome_base()

    # 1. 拿 page targets — /json/list 返回所有 page/iframe/worker, 我们要 type=page
    try:
        targets = _http_get_json(f"{chrome_base}/json/list")
    except Exception as e:
        return {
            "type": "error",
            "error": (
                f"连不上 Chrome 调试端口 {chrome_base}/json/list — Chrome 没起?"
                f" 或没开 --remote-debugging-port? 错误: {e}"
            ),
        }

    pages = [
        t for t in targets
        if isinstance(t, dict) and t.get("type") == "page" and t.get("webSocketDebuggerUrl")
    ]
    if not pages:
        return {
            "type": "error",
            "error": (
                "Chrome 在跑但没有 page-type target — 可能所有 tab 都关了? "
                "Companion 控制台点「启动 Catfish Chrome」会自动开个 about:blank"
            ),
        }

    # 拿第一个 page (Companion 起的 Chrome 默认 about:blank 就一个 page)
    page = pages[0]
    ws_url = page["webSocketDebuggerUrl"]

    # 2. 连 page-level WebSocket, 发 Page.navigate
    # hermes venv 里大概率已经装了 websocket-client 或 websockets, 自适应:
    return _cdp_navigate_via_ws(ws_url, url, wait_seconds)


def _cdp_navigate_via_ws(
    ws_url: str, target_url: str, wait_seconds: float
) -> Dict[str, Any]:
    """走任一 ws 库 (websocket-client 同步 / websockets asyncio) 发 CDP 命令。

    设计: 哪个 lib 装了就用哪个, 都没装才让用户去装. 减少员工首次跑撞 ImportError 的概率.
    """
    # 先试同步的 websocket-client (api 最直接)
    try:
        import websocket  # type: ignore  # noqa: PLC0415
        return _cdp_via_websocket_client(ws_url, target_url, wait_seconds, websocket)
    except ImportError:
        pass

    # 退到异步的 websockets (hermes browser tool 多半已装这个)
    try:
        import asyncio  # noqa: PLC0415
        import websockets  # type: ignore  # noqa: PLC0415
        return asyncio.run(
            _cdp_via_websockets_async(ws_url, target_url, wait_seconds, websockets)
        )
    except ImportError:
        pass

    return {
        "type": "error",
        "error": (
            "hermes venv 既没 websocket-client 也没 websockets. 装一个: "
            "HTTPS_PROXY= HTTP_PROXY= ~/.hermes/hermes-agent/venv/bin/pip install "
            "--proxy '' websocket-client"
        ),
    }


def _cdp_via_websocket_client(
    ws_url: str, target_url: str, wait_seconds: float, websocket
) -> Dict[str, Any]:
    """同步实现 (websocket-client lib)."""
    try:
        ws = websocket.create_connection(ws_url, timeout=5)
    except Exception as e:
        return {"type": "error", "error": f"连 page WS 失败: {e}"}

    try:
        ws.send(_json.dumps({"id": 1, "method": "Page.enable"}))
        ws.recv()  # ack

        ws.send(_json.dumps({
            "id": 2,
            "method": "Page.navigate",
            "params": {"url": target_url},
        }))
        navigate_raw = ws.recv()
        navigate_result = _json.loads(navigate_raw)
        if "error" in navigate_result:
            return {
                "type": "error",
                "error": f"Page.navigate 失败: {navigate_result['error']}",
            }

        time.sleep(wait_seconds)

        ws.send(_json.dumps({
            "id": 3,
            "method": "Runtime.evaluate",
            "params": {
                "expression": "JSON.stringify({title: document.title, url: location.href})",
                "returnByValue": True,
            },
        }))
        eval_raw = ws.recv()
        return _format_navigate_result(target_url, eval_raw)
    except Exception as e:
        return {"type": "error", "error": f"navigate 过程异常: {e}"}
    finally:
        try:
            ws.close()
        except Exception:
            pass


async def _cdp_via_websockets_async(
    ws_url: str, target_url: str, wait_seconds: float, websockets
) -> Dict[str, Any]:
    """异步实现 (websockets lib). 接口签名跟 sync 版一致."""
    import asyncio  # noqa: PLC0415
    try:
        async with websockets.connect(ws_url, max_size=2**24) as ws:
            await ws.send(_json.dumps({"id": 1, "method": "Page.enable"}))
            await ws.recv()  # ack

            await ws.send(_json.dumps({
                "id": 2,
                "method": "Page.navigate",
                "params": {"url": target_url},
            }))
            navigate_raw = await ws.recv()
            navigate_result = _json.loads(navigate_raw)
            if "error" in navigate_result:
                return {
                    "type": "error",
                    "error": f"Page.navigate 失败: {navigate_result['error']}",
                }

            await asyncio.sleep(wait_seconds)

            await ws.send(_json.dumps({
                "id": 3,
                "method": "Runtime.evaluate",
                "params": {
                    "expression": "JSON.stringify({title: document.title, url: location.href})",
                    "returnByValue": True,
                },
            }))
            eval_raw = await ws.recv()
            return _format_navigate_result(target_url, eval_raw)
    except Exception as e:
        return {"type": "error", "error": f"navigate 过程异常 (asyncio path): {e}"}


def _format_navigate_result(target_url: str, eval_raw: str) -> Dict[str, Any]:
    """把 Runtime.evaluate 的结果解析成给模型看的 success dict."""
    eval_result = _json.loads(eval_raw)
    page_info = {"title": "(unknown)", "url": "(unknown)"}
    try:
        value = eval_result["result"]["result"]["value"]
        page_info = _json.loads(value)
    except (KeyError, _json.JSONDecodeError, TypeError):
        pass

    matched = (
        target_url in page_info["url"]
        or page_info["url"].startswith(target_url[:20])
    )
    return {
        "type": "ok",
        "navigated_to": target_url,
        "actual_title": page_info["title"],
        "actual_url": page_info["url"],
        "summary": (
            f"已 navigate 到 {target_url}. 页面真实 title='{page_info['title']}', "
            f"真实 url='{page_info['url']}'. "
            f"({'✓ 加载成功' if matched else '⚠ 加载后 url 跟请求不一致, 可能重定向或被拦'})"
        ),
    }


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
    if name == "catfish_skill_backup":
        return skill_backup(args)
    raise ValueError(f"unknown native tool: {name}")
