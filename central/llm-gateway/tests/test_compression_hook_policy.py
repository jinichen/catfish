"""守住压缩边界：Catfish 网关不再执行语义上下文压缩。

Hermes 会话层负责语义压缩；Catfish 只保留确定性的消息准备、工具结果裁剪、
消息规范化、配额和最终上下文长度校验。
"""
from pathlib import Path


_SRC = Path(__file__).parent.parent / "src" / "catfish_gateway"
APP_PY = _SRC / "app.py"
PREPARE_PY = _SRC / "chat_prepare.py"


def test_chat_pipeline_has_no_catfish_semantic_compression_hook():
    """生产聊天入口不得重新接回 Catfish 的 LLM 摘要压缩器。"""
    app_src = APP_PY.read_text(encoding="utf-8")
    prepare_src = PREPARE_PY.read_text(encoding="utf-8")

    assert "conversation_compressor" not in app_src
    assert "conversation_compressor" not in prepare_src
    assert "maybe_compress" not in app_src
    assert "maybe_compress" not in prepare_src


def test_chat_pipeline_keeps_deterministic_preparation_and_quota():
    """移除语义压缩后，基础请求准备和配额守卫仍在主链路。"""
    app_src = APP_PY.read_text(encoding="utf-8")

    assert "prepare_messages(" in app_src
    assert "sanitize_tools(" in app_src
    assert "enforce_quota(" in app_src
