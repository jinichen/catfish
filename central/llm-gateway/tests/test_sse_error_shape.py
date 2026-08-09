"""流式出错时发给客户端的 error 形状 (8/9 加)。

# 为什么值得单独钉一条

`_stream_chat_completion` 出错时会 yield 一条 `data: {"error": ...}`。这条的
**形状**决定了客户端能不能看到我们写的人话文案。

老写法是裸字符串 `{"error": "上游拥堵, 试试…"}`。OpenAI 官方客户端 (hermes
用的就是它) 的判据在 `openai/_streaming.py`:

    if is_mapping(data) and data.get("error"):
        error = data.get("error")
        if is_mapping(error):                       ← 字符串不是 mapping
            message = error.get("message")
        if not message or not isinstance(message, str):
            message = "An error occurred during streaming"   ← 落这里
        raise APIError(message=message, ...)

于是**文案被整个丢掉**, 客户端只拿到一句无信息量的英文。

# 8/9 实测的代价

P44 进度探针刚上线就照出来这个: hermes 拿着那句空话重试 3 次 (退避 2s / 6s),
把 ~22K token 的 prompt 白烧三遍, 最后返 200 但正文是
"API call failed after 3 retries..."。员工看到的是"等很久然后没结果", 而真实
原因一路都没传出去 —— **网关那边其实早就算出人话了**。

这类 bug 不会让任何测试变红 (HTTP 200、SSE 格式合法、字段名也对), 只会让排查
永远差一层。所以判据不能是"有没有 error 字段", 得是"客户端到底看得见什么"。
"""
from __future__ import annotations

import io
import json
import re
import tokenize
from pathlib import Path

import pytest

APP_PY = Path(__file__).resolve().parent.parent / "src" / "catfish_gateway" / "app.py"


def openai_client_message(sse_payload: str) -> str | None:
    """照抄 openai/_streaming.py 的判据 —— 返回客户端最终会看到的 message。

    这不是"我们希望它怎么解", 是它**实际**怎么解。照抄的意义在于: 哪天升级
    openai 库改了判据, 我们改这里、跟着重新对答案, 而不是靠记忆。
    """
    data = json.loads(sse_payload)
    if isinstance(data, dict) and data.get("error"):
        message = None
        error = data.get("error")
        if isinstance(error, dict):
            message = error.get("message")
        if not message or not isinstance(message, str):
            message = "An error occurred during streaming"
        return message
    return None


GENERIC = "An error occurred during streaming"


def _strip_comments(src: str) -> str:
    """去掉注释再做字面量断言。

    ⚠ 这是**第二次**踩同一个坑了 (8/9 一天内两次):
      · tests/test_facts_pg_write_closed.py —— 修 facts_pipeline 时, 我在修复处
        写注释解释"原来这里是 f-string 内插", 结果测试把注释当成违规现场。
      · 这条 —— 我在 app.py 里写注释解释"老写法是 {'error': friendly}",
        测试又报红。

    规律很清楚: **解释修复的注释必然含有被修复的写法**。凡是对源码做字面量
    断言的测试, 第一步就得剥注释, 否则测试永远跟文档打架。
    """
    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type != tokenize.COMMENT:
                out.append(tok.string)
    except (tokenize.TokenError, IndentationError):
        return src  # 词法出错退回原文, 宁可误报也不放行
    return "\n".join(out)


def test_裸字符串会把文案吞掉_这是老写法的毛病():
    """反证: 少了这条, 有人"简化"回裸字符串就没人发现。"""
    old = json.dumps({"error": "上游模型拥堵, 试试切大模型"}, ensure_ascii=False)
    assert openai_client_message(old) == GENERIC, "老写法居然没吞? 那判据抄错了"


def test_对象形状能把人话传出去():
    new = json.dumps(
        {"error": {"message": "上游模型拥堵, 试试切大模型", "type": "upstream_error"}},
        ensure_ascii=False,
    )
    assert openai_client_message(new) == "上游模型拥堵, 试试切大模型"


@pytest.mark.parametrize("bad", [
    {"error": {"type": "upstream_error"}},          # 少 message
    {"error": {"message": 42}},                     # message 不是字符串
    {"error": {"message": ""}},                     # 空串
])
def test_对象但_message_不合格照样被吞(bad):
    """提醒: 光把 error 改成对象不够, message 必须是**非空字符串**。"""
    assert openai_client_message(json.dumps(bad)) == GENERIC


def test_app_里那条_yield_确实是对象形状():
    """结构性: 直接读源码, 防有人改回裸字符串。

    判据故意宽松 (只看那一行有没有 "error": {"message"), 不解析整个表达式 ——
    严了会因为格式化换行假红, 而这条真正要防的是"退回裸字符串"这一种改法。
    """
    src = _strip_comments(APP_PY.read_text(encoding="utf-8"))
    # 裸字符串的写法: {'error': friendly} / {"error": friendly}
    naked = re.search(r"""\{\s*['"]error['"]\s*:\s*friendly\s*\}""", src)
    assert naked is None, (
        "app.py 里又出现了 {'error': friendly} 裸字符串写法 —— "
        "OpenAI 客户端会把文案整个丢掉, 只抛一句 "
        f"{GENERIC!r}。改成 {{'error': {{'message': friendly, 'type': ...}}}}"
    )
    # ⚠ 用正则不用 `in` 子串: _strip_comments 是按 token 重拼的, token 之间统一
    #   插了换行, 所以 `"message": friendly` 这种**连续**子串在剥完之后不存在。
    #   (第一版就是这么写的, 变异测试时才发现它恒假 —— 闸看着是红的, 其实红错
    #    了地方。判据必须对"剥完之后的形态"成立, 不是对原文成立。)
    assert re.search(r"""['"]message['"]\s*:\s*friendly""", src), (
        "找不到 message: friendly —— 流式错误的人话文案没往外传?"
    )
