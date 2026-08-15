"""BL-COMPRESSION-GATEWAY (5/15 早) — chat_completions 压缩 hook 静态分析守护.

防以后改 chat_completions 时不小心破坏:
  - hook 必须在 sanitize_tools / harden_for_gemini 之后, quota check 之前 (顺序敏感)
  - 必须跳 is_internal_call (防 summarizer/proactive loopback 死循环)
  - 必须跳 _compression_internal (防 compressor 调 gateway 自己再触发)
  - 必须跳**纯** service token (cron / a2a / hermes auxiliary — 真 1-shot 没历史)
  - 但 service token **代表员工** 时必须压 (8/8 修的根因, 见对应用例)
  - exception 必须 silent (不阻塞主流程)

# 8/15: 判据从"扫 app.py 一个文件"改成"扫宿主模块 + 看调用顺序"

那天把 chat_completions 那条流水线里最大的三段搬进了 chat_prepare.py
(prepare_messages / maybe_compress / enforce_quota), 这个文件 7 条全红 ——
而它们要守的策略一个字没变。

老判据有两层都钉死在 app.py 上:
  · **内容**: "maybe_compress_messages 出现在 app_src 里"
  · **顺序**: "compression 的字符位置 < quota check 的字符位置"

第一层改成查真正的宿主 (chat_prepare.py); 第二层改成看 app.py 里那三个
**具名调用**的先后 —— 拆完之后顺序反而更好读了:

    body = prepare_messages(...)      # 注入链的末尾
    body = await maybe_compress(...)  # 压缩
    enforce_quota(...)                # 配额

用"函数名的先后"当顺序判据, 比原来"在 N 行内找 if" 稳得多: 那种写法既怕
代码往下挪一行, 又怕中间插一段注释。

跟同一天 test_outputs_dir_convention / test_picker_reader_single_source 栽的
是同一条 —— 判据钉在文件位置上, 守的却是内容与顺序。
"""
from __future__ import annotations

from pathlib import Path

import pytest

_SRC = Path(__file__).parent.parent / "src" / "catfish_gateway"
APP_PY = _SRC / "app.py"
PREPARE_PY = _SRC / "chat_prepare.py"


@pytest.fixture(scope="module")
def app_src() -> str:
    return APP_PY.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def prep_src() -> str:
    """压缩 hook 现在的宿主。找不到就直说, 别静默跳过。"""
    assert PREPARE_PY.exists(), (
        f"{PREPARE_PY.name} 不见了 —— 压缩 hook 是不是搬回 app.py 或又搬去别处了? "
        "把本文件的 PREPARE_PY 指过去。"
    )
    return PREPARE_PY.read_text(encoding="utf-8")


def test_compression_hook_present(prep_src):
    """BL-COMPRESSION-GATEWAY 标记 + maybe_compress_messages import 都在"""
    assert "BL-COMPRESSION-GATEWAY" in prep_src
    assert "maybe_compress_messages" in prep_src
    assert "from .conversation_compressor import" in prep_src


def test_compression_skips_internal_call(prep_src):
    """is_internal_call 必须在 compression hook 的跳过条件里"""
    lines = prep_src.splitlines()
    found_skip = False
    for i, line in enumerate(lines):
        if "maybe_compress_messages" in line and "import" not in line:
            for prev in lines[max(0, i - 30): i]:
                if "is_internal_call" in prev and "not" in prev:
                    found_skip = True
                    break
            break
    assert found_skip, "compression hook 必须跳 is_internal_call"


def _guard_block(src: str) -> str:
    """compression hook 那个 if 守卫的源码块 (调用点往前 30 行)。"""
    lines = src.splitlines()
    for i, line in enumerate(lines):
        if "maybe_compress_messages" in line and "import" not in line:
            return "\n".join(lines[max(0, i - 30): i])
    raise AssertionError("找不到 maybe_compress_messages 调用点")


def test_compression_skips_plain_service_token(prep_src):
    """纯 service token (不代表谁) 仍然跳 —— cron / a2a / hermes 自己的
    auxiliary_client 确实是 1-shot 没历史, 5/15 的原意在这部分成立。"""
    block = _guard_block(prep_src)
    assert "_service_like" in block, "守卫里要有 service 判定"
    assert '"service"' in block or "is_service_principal" in block or "_is_service_call" in block


def test_compression_does_not_skip_service_on_behalf_of_user(prep_src):
    """8/8: service token **代表员工** 时必须压。

    这是三个月里压缩零触发的根因 —— 5/19 BL-AUTH-DECOUPLE-A1 把 hermes-cli
    改成 service token 代表员工之后, 员工主力对话全落进了 `user.role != "service"`
    这条豁免里。别再改回一刀切。

    判据: effective_user_email != user.sub (resolve_effective_user_email 认定
    的 on-behalf-of)。
    """
    block = _guard_block(prep_src)
    assert "_service_on_behalf" in block, (
        "守卫必须区分「纯 service」和「service 代表员工」—— "
        "一刀切 `user.role != 'service'` 会让压缩对主路径永远不生效"
    )
    assert "effective_user_email != user.sub" in block, (
        "on-behalf-of 的判据应该是 effective_user_email != user.sub"
    )
    # 而且要真的放行, 不是算了个变量不用
    assert "not _service_like or _service_on_behalf" in block


def test_compression_recursive_guard(prep_src):
    """X-Catfish-Compression-Internal header 必须被读出来防自递归"""
    assert "X-Catfish-Compression-Internal" in prep_src
    assert "_compression_internal" in prep_src


def test_compression_wrapped_in_try_except(prep_src):
    """异常必须 silent — try/except 包住 compress 调用"""
    lines = prep_src.splitlines()
    found_try_before = False
    for i, line in enumerate(lines):
        if "maybe_compress_messages" in line and "import" not in line:
            for prev in reversed(lines[max(0, i - 5): i]):
                if prev.strip() == "try:":
                    found_try_before = True
                    break
            break
    assert found_try_before, "maybe_compress_messages 调用必须包在 try: 里"


# ── 顺序: 看 app.py 里那三个具名调用的先后 ──────────────────────────


def _call_pos(app_src: str, name: str) -> int:
    """在 chat_completions 里找 `name(` 的调用位置 (跳过 import 行)。"""
    best = -1
    for i, line in enumerate(app_src.splitlines()):
        if f"{name}(" in line and not line.lstrip().startswith(("from ", "import ", "#")):
            best = i
            break
    assert best >= 0, f"app.py 里找不到 {name}( 的调用 —— 流水线是不是改了?"
    return best


def test_compression_before_quota_check(app_src):
    """compression 必须在 quota check 之前 — 让 quota count 用压缩后的 token,
    不浪费 quota 上 70K 历史的份额."""
    assert _call_pos(app_src, "maybe_compress") < _call_pos(app_src, "enforce_quota"), (
        "compression 应该在 quota check 之前 (省 quota)"
    )


def test_compression_after_inject_chain(app_src):
    """压缩必须在所有 inject 之后 (压完才是真要发的 messages)。

    prepare_messages 是最后一段 message 内容操作 (原来直接看
    prepare_tool_messages, 它现在在 prepare_messages 里面)。
    """
    assert _call_pos(app_src, "prepare_messages") < _call_pos(app_src, "maybe_compress"), (
        "compression 应在 prepare_messages 之后"
    )


def test_prepare_tool_messages_还在压缩之前(prep_src, app_src):
    """原判据是 prepare_tool_messages < maybe_compress_messages。

    拆分后这两个分处两个函数, 但它们的先后仍然要成立 —— 具体表现为
    prepare_tool_messages 在 prepare_messages 里, 而 prepare_messages 在
    maybe_compress 之前 (上一条已断言)。这里补钉住前半句, 免得哪天
    prepare_tool_messages 被挪出去而上一条察觉不到。
    """
    assert "prepare_tool_messages" in prep_src, (
        "prepare_tool_messages 不在 chat_prepare.py 里了 —— 它挪到哪去了? "
        "挪到 maybe_compress 之后的话, 压缩压的就不是最终 messages"
    )


def test_stats_logged_to_request_state(prep_src):
    """compress_stats 应该塞 request.state 让 audit log 能拿到"""
    assert "request.state.compression_stats" in prep_src


def test_uses_final_model_context_window(prep_src):
    """压缩用最终 model 的 context_window (reroute 后), 不是原 model_name"""
    assert 'getattr(model, "context_window"' in prep_src or \
           "model.context_window" in prep_src
