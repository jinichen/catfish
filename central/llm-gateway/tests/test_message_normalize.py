"""message_normalize —— 空 tool_calls 清理 (8/8).

起因: chat_default 从 deepseek 切到 dashscope 的 qwen-flash 之后每次对话
400 `Empty tool_calls is not supported in message.`。同一份 messages
deepseek 一直收着不吭声, 百炼校验严。
"""

from catfish_gateway.message_normalize import drop_empty_tool_calls, normalize_messages


def test_空数组被删掉():
    msgs = [{"role": "assistant", "content": "好的", "tool_calls": []}]
    out, n = drop_empty_tool_calls(msgs)
    assert n == 1
    assert "tool_calls" not in out[0]
    assert out[0]["content"] == "好的"


def test_非空的不动():
    tc = [{"id": "call_1", "type": "function", "function": {"name": "f", "arguments": "{}"}}]
    msgs = [{"role": "assistant", "content": None, "tool_calls": tc}]
    out, n = drop_empty_tool_calls(msgs)
    assert n == 0
    assert out is msgs, "没改动就该原样返回, 不要白白拷一份 400 条的 messages"
    assert out[0]["tool_calls"] == tc


def test_没有这个键的不动():
    msgs = [{"role": "user", "content": "hi"}]
    out, n = drop_empty_tool_calls(msgs)
    assert n == 0
    assert out is msgs


def test_None_也算空():
    """`"tool_calls": null` 跟空数组一样, 上游一样嫌。"""
    out, n = drop_empty_tool_calls([{"role": "assistant", "content": "x", "tool_calls": None}])
    assert n == 1
    assert "tool_calls" not in out[0]


def test_老版_function_call_一并处理():
    out, n = drop_empty_tool_calls([{"role": "assistant", "content": "x", "function_call": {}}])
    assert n == 1
    assert "function_call" not in out[0]


def test_删完没有content的补空串():
    """assistant 既没 content 又没 tool_calls = 空消息, Qwen 系同样报错。

    跟 conversation_compressor._strip_orphan_tool_boundary 的处理保持一致。
    """
    out, n = drop_empty_tool_calls([{"role": "assistant", "content": None, "tool_calls": []}])
    assert n == 1
    assert out[0]["content"] == ""


def test_不写穿原对象():
    """params 里的 messages 跟调用方共享, fallback 重试还要再用一次。

    原地改的话: 第一次调用改了对象, 第二次 (换个 model 重试) 拿到的是被
    改过的 —— 这次的改动是良性的, 但这个坑一旦成立, 下一个人加个别的
    改写就会在重试链上放大。
    """
    orig = {"role": "assistant", "content": "x", "tool_calls": []}
    msgs = [orig]
    out, _ = drop_empty_tool_calls(msgs)
    assert orig["tool_calls"] == [], "原 message 不该被动"
    assert out[0] is not orig


def test_只拷要改的那条():
    keep = {"role": "user", "content": "hi"}
    msgs = [keep, {"role": "assistant", "content": "x", "tool_calls": []}]
    out, n = drop_empty_tool_calls(msgs)
    assert n == 1
    assert out[0] is keep, "没问题的 message 应该直接复用, 别整份深拷"


def test_多条一起数对():
    msgs = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b", "tool_calls": []},
        {"role": "assistant", "content": "c", "function_call": None},
        {"role": "assistant", "content": "d", "tool_calls": [{"id": "1"}]},
    ]
    out, n = drop_empty_tool_calls(msgs)
    assert n == 2
    assert out[3]["tool_calls"] == [{"id": "1"}]


def test_normalize_messages_写回_params():
    params = {"model": "openai/qwen", "messages": [{"role": "assistant", "tool_calls": []}]}
    normalize_messages(params)
    assert "tool_calls" not in params["messages"][0]


def test_normalize_messages_没有messages不炸():
    params = {"model": "openai/qwen"}
    normalize_messages(params)  # embedding 之类的请求没有 messages
    assert "messages" not in params


def test_messages_不是list也不炸():
    out, n = drop_empty_tool_calls("不是列表")
    assert n == 0
    assert out == "不是列表"
