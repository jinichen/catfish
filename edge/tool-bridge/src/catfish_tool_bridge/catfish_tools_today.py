"""Catfish today_summary + screenshot tools — 抽自 catfish_tools.py (5/20 拆分).

工具:
  catfish_today_summary: 今天小鲶学了啥 (~/.hermes/USER.md / memories / skills /
    state.db sessions + messages). 跟 Companion learning.rs 同套数据源.
  catfish_screenshot: macOS / Windows 截屏 (subprocess screencapture / Snipping Tool).

也含 5 个 utility helper (_home / _hermes_dir / _today_start_unix / _is_today /
_unix_to_iso) — 主文件 catfish_tools.py 其他段 (skill_backup / a2a_ask) 也用
所以导出.

5/20: 从 catfish_tools.py 抽出 (625 行).
"""
from __future__ import annotations

import json
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

    # 5/8 BL-FIX3: 恢复返 base64 data_uri (跟 5/7 之前行为一致, LLM 训练时学的就是这样).
    # 上游 Qwen 不接受 role=tool 含 multimodal 的问题, 由 gateway 层 multimodal_tool_unwrap
    # 解决 — gateway 检测到 role=tool 的 content 含 data_uri, 自动**拆出 image** 重组成
    # 紧接着的 role=user multipart message (OpenAI 标准, 上游 Qwen 接受).
    # 这样 LLM 行为完全不变 (它仍然"调 screenshot → 下一轮看 image_url"), 上游也吃.
    size = out_path.stat().st_size
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

