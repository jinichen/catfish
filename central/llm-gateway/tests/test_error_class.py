"""上游错误归类 —— 中央端审计不存自由文本。

背景见 src/catfish_gateway/error_class.py 的模块 docstring 和
docs/CENTRAL-EDGE-DATA-BOUNDARY.md。一句话: gateway_audit 允许的字段是列死的,
`error` 不在里面, 而它原来存上游异常原文 (可能回显 prompt), 还会显示在中央
管理面板上。
"""
from __future__ import annotations

import pytest

from catfish_gateway.error_class import ERROR_CLASSES, classify_upstream_error


# ── 这条是这个模块存在的理由, 别的都是附带 ──────────────────────────


@pytest.mark.parametrize("hostile", [
    # 上游把员工 prompt 原样回显 —— 正是老实现会存进审计的那种
    'BadRequest: messages[0].content = "帮我看下张伟的体检报告, 他手机 13800138000"',
    'InternalServerError: echo of prompt: 关于CIC资质认证需要财务负责人提供的相关材料',
    "APIError: your input was: sk-proj-AbCdEf0123456789 and password: hunter2",
    "Error: 员工邮箱 chenhongbo@ffcs.cn 的会话内容 ......",
])
def test_返回值绝不含上游任何片段(hostile):
    """封闭词表的意义: 返回值是我们写死的常量, 上游内容不可能变成其中之一。

    截断 + 脱敏做不到这一点 —— 它们处理的还是上游那串东西, 漏一个模式就漏一段。
    """
    out = classify_upstream_error(hostile)
    assert out in ERROR_CLASSES, out
    # 逐词核对: 输入里任何一个长度 >=4 的词都不该出现在输出里
    for word in {w for w in hostile.replace('"', " ").split() if len(w) >= 4}:
        assert word.lower() not in out.lower(), f"{word!r} 漏进了 {out!r}"


def test_任何输入都只返回词表里的值():
    for bad in [None, "", "   ", 123, [], {}, b"bytes", "\x00\xff"]:
        assert classify_upstream_error(bad) in ERROR_CLASSES, bad


def test_归类器不抛():
    class _Weird:
        def lower(self):
            raise RuntimeError("炸")

    # 不是 str → 直接 unknown; 真炸了也只降级
    assert classify_upstream_error(_Weird()) == "unknown"


# ── 分类正确性 (拿 8/8 审计里实际出现过的原文) ────────────────────


@pytest.mark.parametrize("raw,want", [
    # 8/8 DeepSeek 余额烧光那次的真实报文
    ('litellm.BadRequestError: DeepseekException - {"error":{"message":'
     '"Insufficient Balance","code":"invalid_request_error"}}', "insufficient_balance"),
    # 审计里占 63% 的那条
    ("APIError: litellm.APIError: APIError: OpenAIException - "
     "The free tier of the model has been exhausted", "quota_exhausted"),
    # 占 27%
    ("APIConnectionError: litellm.APIConnectionError: GeminiException - "
     "Cannot connect to host generativelanguage.googleapis.com", "connection"),
    ("InternalServerError: litellm.InternalServerError: DeepseekException - "
     "Internal Server Error", "upstream_5xx"),
    ("litellm.Timeout: APITimeoutError - Request timed out.", "timeout"),
    ("litellm.BadRequestError: OpenAIException - Invalid schema for function "
     "'browser_back'", "bad_request"),
    ('{"detail":"model not found: gpt-5.6-luna"}', "model_not_found"),
])
def test_实际报文归类正确(raw, want):
    assert classify_upstream_error(raw) == want


def test_余额不足要排在_bad_request_前面():
    """顺序敏感: DeepSeek 的余额不足被 litellm 映射成 400, 两条都能命中,
    必须归成 insufficient_balance —— 运维看到 bad_request 会去查我们发的参数,
    方向就错了。"""
    raw = "litellm.BadRequestError: Insufficient Balance"
    assert classify_upstream_error(raw) == "insufficient_balance"


# ── 结构性 ──────────────────────────────────────────────────────


def test_词表全小写下划线_没有会漏内容的形态():
    """词表值会进 PG / CSV / 前端。限死形态, 免得哪天有人往里加一个带模板占位
    的"类别"(例 f"upstream:{name}") —— 那等于把自由文本又放回来了。"""
    import re
    for c in ERROR_CLASSES:
        assert re.fullmatch(r"[a-z][a-z0-9_]*", c), c


def test_metrics_写进去的就是分类码():
    """端到端: 确认接线在。不 mock classify —— 要保的正是"接线在"。"""
    from catfish_gateway import metrics

    rec: dict = {}
    original = metrics._persist_record
    metrics._persist_record = rec.update
    try:
        metrics.log_request_metadata(
            user="a@b.cn", model="m", prompt_tokens=1, completion_tokens=1,
            latency_ms=1.0, status="error",
            error='BadRequest: messages[0] = "员工的私密内容 password: hunter2"',
        )
    finally:
        metrics._persist_record = original

    assert rec["error"] in ERROR_CLASSES, rec["error"]
    assert "员工" not in rec["error"]
    assert "hunter2" not in rec["error"]
