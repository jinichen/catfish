"""catfish_tools 单测 —— 重点是 collect_today_summary 在边界条件下不挂。

边界:
    1. ~/.hermes 不存在 → 全 0, 不抛
    2. ~/.hermes/USER.md 存在但 mtime 是昨天 → memories_updated_today = 0
    3. memories/ 里既有今天又有历史的 → 今天的算
    4. skills 目录里 SKILL.md 没 frontmatter → description 是 "(无 description)"
    5. state.db 不存在 → 全 0, 不挂
    6. state.db 存在但 schema 跟期望不一样 → 走 except 分支返回 0

不打 sqlite3 的真 db, 用 tmpdir + 临时建 schema 的方式。
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

import pytest

from catfish_tool_bridge import catfish_tools


# ---------- helpers ----------

def _set_home(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    """让 catfish_tools._home() 看到 path 为 home"""
    monkeypatch.setenv("HOME", str(path))
    # USERPROFILE 也清掉, 免得 Windows 路径污染
    monkeypatch.delenv("USERPROFILE", raising=False)


def _touch(path: Path, text: str = "", mtime: float | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def _create_state_db(db_path: Path) -> sqlite3.Connection:
    """建一个跟 hermes state.db 兼容的 schema(只建 catfish_tools 用到的字段)。"""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            source TEXT,
            started_at REAL,
            input_tokens INTEGER,
            output_tokens INTEGER,
            cache_read_tokens INTEGER,
            cache_write_tokens INTEGER,
            reasoning_tokens INTEGER
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            role TEXT,
            timestamp REAL,
            tool_calls TEXT
        );
        """
    )
    conn.commit()
    return conn


# ---------- tests: 边界 ----------

def test_no_hermes_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """~/.hermes 不存在 → 不抛, 所有数字 0"""
    _set_home(monkeypatch, tmp_path)
    result = catfish_tools.collect_today_summary()
    assert result["sessions_today"] == 0
    assert result["tool_calls_today"] == 0
    assert result["total_tokens_today"] == 0
    assert result["memories_updated_today"] == 0
    assert result["new_skills_count"] == 0
    assert result["memories"] == []
    assert result["new_skills"] == []
    assert "今天还没动静" in result["summary"]


def test_user_md_modified_yesterday(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """USER.md 昨天改的 → memories_updated_today = 0, 它不在 'memories' 列表里"""
    _set_home(monkeypatch, tmp_path)
    yesterday = time.time() - 86400 * 2  # 给点 buffer 防边界
    _touch(tmp_path / ".hermes" / "USER.md", "old", mtime=yesterday)

    result = catfish_tools.collect_today_summary()
    assert result["memories_updated_today"] == 0
    # memories 字段只返回今天更新的(实现选择: 只挑 modified_today)
    assert result["memories"] == []


def test_user_md_modified_today(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """USER.md 今天改的 → memories_updated_today = 1, summary 有提"""
    _set_home(monkeypatch, tmp_path)
    _touch(tmp_path / ".hermes" / "USER.md", "today fresh")

    result = catfish_tools.collect_today_summary()
    assert result["memories_updated_today"] == 1
    assert any(m["name"] == "USER" for m in result["memories"])
    assert "memory" in result["summary"]


def test_memories_subdir_today_and_yesterday(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """memories/ 里两个 .md, 一今一昨 → 只今天的进 memories_updated_today"""
    _set_home(monkeypatch, tmp_path)
    yesterday = time.time() - 86400 * 2
    _touch(tmp_path / ".hermes" / "memories" / "today.md", "fresh")
    _touch(tmp_path / ".hermes" / "memories" / "old.md", "stale", mtime=yesterday)

    result = catfish_tools.collect_today_summary()
    assert result["memories_updated_today"] == 1
    names = [m["name"] for m in result["memories"]]
    assert "memories/today" in names
    assert "memories/old" not in names


def test_memories_skips_non_md(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """memories/ 里有 .txt / 子目录, 不能炸"""
    _set_home(monkeypatch, tmp_path)
    _touch(tmp_path / ".hermes" / "memories" / "ignore.txt", "x")
    (tmp_path / ".hermes" / "memories" / "subdir").mkdir(parents=True)

    result = catfish_tools.collect_today_summary()
    assert result["memories_updated_today"] == 0


def test_skill_today(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """skills/<ns>/<name>/SKILL.md mtime 是今天 → 算今天新增"""
    _set_home(monkeypatch, tmp_path)
    skill_md = tmp_path / ".hermes" / "skills" / "dogfood" / "auto-reply" / "SKILL.md"
    _touch(skill_md, "---\nname: auto-reply\ndescription: 自动回飞书消息\n---\n# Body\n")

    result = catfish_tools.collect_today_summary()
    assert result["new_skills_count"] == 1
    assert result["new_skills"][0]["full_name"] == "dogfood/auto-reply"
    assert result["new_skills"][0]["description"] == "自动回飞书消息"


def test_skill_no_frontmatter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """SKILL.md 没 frontmatter → description fallback 到 "(无 description)" """
    _set_home(monkeypatch, tmp_path)
    skill_md = tmp_path / ".hermes" / "skills" / "dogfood" / "rough" / "SKILL.md"
    _touch(skill_md, "# 直接是 markdown 没 frontmatter\n")

    result = catfish_tools.collect_today_summary()
    assert result["new_skills_count"] == 1
    assert result["new_skills"][0]["description"] == "(无 description)"


def test_skill_yesterday_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """昨天的 SKILL.md 不算今天新增"""
    _set_home(monkeypatch, tmp_path)
    yesterday = time.time() - 86400 * 2
    skill_md = tmp_path / ".hermes" / "skills" / "ns" / "old" / "SKILL.md"
    _touch(skill_md, "---\nname: old\ndescription: 老的\n---\n", mtime=yesterday)

    result = catfish_tools.collect_today_summary()
    assert result["new_skills_count"] == 0


def test_db_today_sessions_and_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """state.db 今天 2 session, 1 条带 tool_calls 的 message → 数字对得上"""
    _set_home(monkeypatch, tmp_path)
    db_path = tmp_path / ".hermes" / "state.db"
    conn = _create_state_db(db_path)
    now = time.time()
    yesterday = now - 86400 * 2

    # 今天 2 session, 昨天 1
    conn.execute(
        "INSERT INTO sessions VALUES ('s1', 'companion', ?, 100, 200, 50, 10, 5)",
        (now,),
    )
    conn.execute(
        "INSERT INTO sessions VALUES ('s2', 'cli', ?, 1000, 2000, 0, 0, 0)",
        (now,),
    )
    conn.execute(
        "INSERT INTO sessions VALUES ('s_old', 'cli', ?, 99999, 0, 0, 0, 0)",
        (yesterday,),
    )

    # 今天 1 条 tool_calls 消息, 1 条普通消息, 昨天 1 条
    conn.execute(
        "INSERT INTO messages (session_id, role, timestamp, tool_calls) "
        "VALUES ('s1', 'assistant', ?, '[{\"name\":\"x\"}]')",
        (now,),
    )
    conn.execute(
        "INSERT INTO messages (session_id, role, timestamp, tool_calls) "
        "VALUES ('s1', 'user', ?, NULL)",
        (now,),
    )
    conn.execute(
        "INSERT INTO messages (session_id, role, timestamp, tool_calls) "
        "VALUES ('s_old', 'assistant', ?, '[{\"name\":\"y\"}]')",
        (yesterday,),
    )
    conn.commit()
    conn.close()

    result = catfish_tools.collect_today_summary()
    assert result["sessions_today"] == 2
    # s1: 100+200+50+10+5 = 365; s2: 1000+2000 = 3000; total 3365
    assert result["total_tokens_today"] == 3365
    assert result["tool_calls_today"] == 1
    # summary 应该提到 2 次对话和 1 次工具
    assert "2 次对话" in result["summary"]
    assert "1 次工具" in result["summary"]


def test_db_empty_no_today(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """state.db 存在但今天没活动 → 全 0"""
    _set_home(monkeypatch, tmp_path)
    db_path = tmp_path / ".hermes" / "state.db"
    conn = _create_state_db(db_path)
    yesterday = time.time() - 86400 * 2
    conn.execute(
        "INSERT INTO sessions VALUES ('s_old', 'cli', ?, 100, 200, 0, 0, 0)",
        (yesterday,),
    )
    conn.commit()
    conn.close()

    result = catfish_tools.collect_today_summary()
    assert result["sessions_today"] == 0
    assert result["tool_calls_today"] == 0
    assert result["total_tokens_today"] == 0


def test_db_corrupt_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """state.db 有但 schema 跟期望不一样 → 走异常分支返回 0, 不抛到上层"""
    _set_home(monkeypatch, tmp_path)
    db_path = tmp_path / ".hermes" / "state.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE wrong_table (x INT)")
    conn.commit()
    conn.close()

    # 不应该抛
    result = catfish_tools.collect_today_summary()
    assert result["sessions_today"] == 0
    assert result["tool_calls_today"] == 0
    assert result["total_tokens_today"] == 0


# ---------- tests: native dispatch ----------

def test_is_native_positive() -> None:
    assert catfish_tools.is_native("catfish_today_summary") is True


def test_is_native_negative() -> None:
    assert catfish_tools.is_native("session_search") is False
    assert catfish_tools.is_native("") is False


def test_dispatch_native_unknown() -> None:
    with pytest.raises(ValueError, match="unknown native tool"):
        catfish_tools.dispatch_native("nope", {})


def test_dispatch_native_today_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """dispatch_native 跟 collect_today_summary 输出一致"""
    _set_home(monkeypatch, tmp_path)
    via_dispatch = catfish_tools.dispatch_native("catfish_today_summary", {})
    via_direct = catfish_tools.collect_today_summary()
    # generated_at 会差几毫秒, 比对其它字段
    assert via_dispatch.keys() == via_direct.keys()
    assert via_dispatch["sessions_today"] == via_direct["sessions_today"]
    assert via_dispatch["summary"] == via_direct["summary"]


# ---------- tests: schema shape ----------

def test_native_tool_schema_shape() -> None:
    """LLM 看到的 schema 必须有 name / description / input_schema 三件套"""
    for tool in catfish_tools.CATFISH_NATIVE_TOOLS:
        assert "name" in tool and tool["name"]
        assert "description" in tool and len(tool["description"]) > 20
        assert "input_schema" in tool
        assert tool["input_schema"]["type"] == "object"
        assert "properties" in tool["input_schema"]
