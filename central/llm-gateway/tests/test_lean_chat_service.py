"""BL-LEAN-CHAT (5/15 凌晨) — 服务 token (token_use=service) 自动 ultra-lean.

5/14 23:00 端到端测时单 chat "1+1=?" 撞 35713 input tokens, 80% 来自 SOUL/identity +
session_facts/journal/feedback/hints. 这些都是给"用户"看的元数据, 服务调用 (hermes-cli /
cron / a2a) 没有用户身份, 不需要这些.

修法: chat_completions 入口看 user.role, 如果是 "service":
  1. skip identity (跳 SOUL/SOUL_FFCS/memory inject) — 大头 ~15-20K
  2. 走 lean 模式 (跳 session_facts / journal / feedback / hints / stats_guard) — ~5-10K
  3. 但 skills_catalog / session_goal / session_meta 仍注入 (功能必需)

预期: 服务 chat input 从 35K 砍到 ~3K (12x 节省).

跟现有 _lean (env CATFISH_LEAN_INJECT / header X-Catfish-Teaching-Mode) 同模式 —
这次加第三个触发源 user.role == "service", 不破坏前两条.
"""
from __future__ import annotations

from pathlib import Path

import pytest

APP_PY = Path(__file__).parent.parent / "src" / "catfish_gateway" / "app.py"


@pytest.fixture(scope="module")
def app_src() -> str:
    return APP_PY.read_text(encoding="utf-8")


# ─── service token → skip identity ────────────────────────────


def test_service_role_triggers_skip_identity(app_src):
    """user.role == 'service' 必须强制 skip=True 让 inject_identity_if_needed 跳过 SOUL"""
    # 在 inject_identity_if_needed 调用之前 (chat_completions 入口段), 应该有
    # 'if user.role == "service":' 配 'skip = True'
    found_check = False
    found_assignment_after = False
    lines = app_src.splitlines()
    for i, line in enumerate(lines):
        if 'user.role == "service"' in line and "skip" not in line:
            found_check = True
            # 后续 5 行内应该有 skip = True
            for follow in lines[i + 1 : i + 5]:
                if "skip = True" in follow:
                    found_assignment_after = True
                    break
            if found_assignment_after:
                break
    assert found_check, "chat_completions 应该检 user.role == 'service'"
    assert found_assignment_after, "service token 应该 skip = True 跳 identity inject"


def test_service_skip_logged(app_src):
    """service token skip identity 时应该 log info 让 audit 可追溯"""
    # 看 BL-LEAN-CHAT 注释附近有 logger.info "service token" "skip identity"
    assert "BL-LEAN-CHAT" in app_src, "BL-LEAN-CHAT 注释应在 chat_completions 内标识修改点"
    assert "service token" in app_src and "skip identity" in app_src, (
        "service token skip identity 应该 log info 给 audit 追"
    )


# ─── service token → 自动 lean ───────────────────────────────


def test_service_role_triggers_auto_lean(app_src):
    """user.role == 'service' 必须使 _lean = True (自动跳所有非核心 inject)"""
    # 应该定义 _service_lean = (user.role == 'service')
    found_service_lean_var = any(
        "_service_lean" in line and "user.role" in line and "service" in line
        for line in app_src.splitlines()
    )
    assert found_service_lean_var, "应该定义 _service_lean = (user.role == 'service')"
    # _lean 表达式 (跨多行) 应该把 _service_lean 加进 or 链里
    assert "_service_lean" in app_src, "_lean 表达式应包含 _service_lean"


def test_service_lean_logged(app_src):
    """service auto-lean 时也 log info"""
    # 找 'service token' + 'auto-lean' 字样
    assert "auto-lean" in app_src, "service token auto-lean 应该 log info"


# ─── 不破坏现有触发源 ──────────────────────────────────────────


def test_existing_teaching_mode_header_still_works(app_src):
    """老 X-Catfish-Teaching-Mode header 仍触发 lean — 不能破坏 BL-LEAN-SESSION 行为"""
    assert "X-Catfish-Teaching-Mode" in app_src
    assert '_teaching_mode = request.headers.get("X-Catfish-Teaching-Mode") == "1"' in app_src


def test_existing_env_var_still_works(app_src):
    """老 CATFISH_LEAN_INJECT env 仍触发 lean — 不能破坏老部署"""
    assert 'os.environ.get("CATFISH_LEAN_INJECT", "0")' in app_src


def test_lean_default_off_for_user_tokens(app_src):
    """普通用户 token (role=admin/manager/employee) 不该自动 lean — 不破坏 Companion 用户体验"""
    # 检查 _lean 表达式不应该是无条件的 True
    # 静态: 没有 '_lean = True' 这种独立赋值
    bad_lines = [
        line for line in app_src.splitlines()
        if line.strip() == "_lean = True"
    ]
    assert not bad_lines, f"_lean 不应被无条件设 True, 找到: {bad_lines}"


# ─── 上线注释完整 ─────────────────────────────────────────────


def test_bl_lean_chat_marker_in_skip_identity_block(app_src):
    """BL-LEAN-CHAT 标记应该出现在 skip identity + auto-lean 两段附近, 让以后 grep 找到"""
    bl_marker_count = app_src.count("BL-LEAN-CHAT")
    assert bl_marker_count >= 2, (
        f"BL-LEAN-CHAT 标记应至少出现 2 次 (skip identity 段 + auto-lean 段), "
        f"实际 {bl_marker_count} 次"
    )
