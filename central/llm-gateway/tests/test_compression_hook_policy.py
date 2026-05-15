"""BL-COMPRESSION-GATEWAY (5/15 早) — chat_completions 压缩 hook 静态分析守护.

防以后改 chat_completions 时不小心破坏:
  - hook 必须在 sanitize_tools / harden_for_gemini 之后, quota check 之前 (顺序敏感)
  - 必须跳 is_internal_call (防 summarizer/proactive loopback 死循环)
  - 必须跳 _compression_internal (防 compressor 调 gateway 自己再触发)
  - 必须跳 service token (它们是 1-shot 没历史)
  - exception 必须 silent (不阻塞主流程)
"""
from __future__ import annotations

from pathlib import Path

import pytest

APP_PY = Path(__file__).parent.parent / "src" / "catfish_gateway" / "app.py"


@pytest.fixture(scope="module")
def app_src() -> str:
    return APP_PY.read_text(encoding="utf-8")


def test_compression_hook_present(app_src):
    """BL-COMPRESSION-GATEWAY 标记 + maybe_compress_messages import 都在"""
    assert "BL-COMPRESSION-GATEWAY" in app_src
    assert "maybe_compress_messages" in app_src
    assert "from .conversation_compressor import" in app_src


def test_compression_skips_internal_call(app_src):
    """is_internal_call 必须在 compression hook 的跳过条件里"""
    # 找包含 'maybe_compress_messages' 调用的代码段, 应该被 if not is_internal_call 包
    lines = app_src.splitlines()
    found_skip = False
    for i, line in enumerate(lines):
        if "maybe_compress_messages" in line:
            # 往前 10 行找 if condition
            for prev in lines[max(0, i - 10): i]:
                if "is_internal_call" in prev and "not" in prev:
                    found_skip = True
                    break
            break
    assert found_skip, "compression hook 必须跳 is_internal_call"


def test_compression_skips_service_token(app_src):
    """service token 也跳, 它们 1-shot 没历史"""
    # 找 'service' 跟 user.role 同窗口
    lines = app_src.splitlines()
    found = False
    for i, line in enumerate(lines):
        if "maybe_compress_messages" in line:
            for prev in lines[max(0, i - 15): i]:
                if 'user.role' in prev and '"service"' in prev:
                    found = True
                    break
            break
    assert found, "compression hook 必须跳 user.role == 'service'"


def test_compression_recursive_guard(app_src):
    """X-Catfish-Compression-Internal header 必须被读出来防自递归"""
    assert "X-Catfish-Compression-Internal" in app_src
    assert "_compression_internal" in app_src


def test_compression_wrapped_in_try_except(app_src):
    """异常必须 silent — try/except 包住 compress 调用"""
    lines = app_src.splitlines()
    in_compression_block = False
    found_try_before = False
    for i, line in enumerate(lines):
        if "maybe_compress_messages" in line and "import" not in line:
            # 往前 5 行找 try:
            for prev in reversed(lines[max(0, i - 5): i]):
                if prev.strip() == "try:":
                    found_try_before = True
                    break
            break
    assert found_try_before, "maybe_compress_messages 调用必须包在 try: 里"


def test_compression_before_quota_check(app_src):
    """compression 必须在 quota check 之前 — 让 quota count 用压缩后的 token,
    不浪费 quota 上 70K 历史的份额."""
    idx_compression = app_src.find("maybe_compress_messages")
    idx_quota_check = app_src.find("_quota_module.check_quota")
    assert idx_compression < idx_quota_check, (
        "compression hook 应该在 quota check 之前 (省 quota), 现 compression "
        f"在 {idx_compression} quota 在 {idx_quota_check}"
    )


def test_compression_after_inject_chain(app_src):
    """压缩必须在所有 inject 之后 (压完才是真要发的 messages).
    具体: 在 prepare_tool_messages 之后 (那是最后一个 message 内容操作)"""
    idx_compression = app_src.find("maybe_compress_messages")
    idx_prepare = app_src.find("prepare_tool_messages")
    assert idx_prepare < idx_compression, (
        "compression 应在 prepare_tool_messages 之后, 现 prepare 在 "
        f"{idx_prepare} compression 在 {idx_compression}"
    )


def test_stats_logged_to_request_state(app_src):
    """compress_stats 应该塞 request.state 让 audit log 能拿到"""
    assert "request.state.compression_stats" in app_src


def test_uses_final_model_context_window(app_src):
    """压缩用最终 model 的 context_window (reroute 后), 不是原 model_name"""
    # 找 maybe_compress_messages 附近的 'model' 引用 (应该是 reroute 后的 model 对象)
    assert "getattr(model, \"context_window\"" in app_src or \
           "model.context_window" in app_src
