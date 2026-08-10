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


# ─────────────────────────────────────────────────────────────────────
# 8/10: system 归一 —— 「未知错误」查了一上午的真身
#
# 内网 Qwen (Go 网关) 只认一条 system, 多了返
#     error: code = 400 reason =  message =  metadata = map[] cause = <nil>
# 打端点逐字复现过 (下面每条用例的期望都对应一次真实 curl):
#     [system, user]                ✅
#     [system, user, system, user]  ❌ 逐字复现线上 400
#     [system, user,  user , user]  ✅   ← 只差中间那条 role
#     [system, system, user]        ❌   ← 开头连着两条也不行
# ─────────────────────────────────────────────────────────────────────

from catfish_gateway.message_normalize import collapse_extra_system  # noqa: E402


def _roles(msgs):
    return [m["role"] for m in msgs]


def test_压缩摘要插中间_降级成user_不挪位置():
    """conversation_compressor 的形状。**位置不能变** —— 摘要代表"这里曾经有
    467 条对话", 挪到开头就跑到它概括的内容前面去了, 时序全错。"""
    msgs = [
        {"role": "system", "content": "你是小鲶"},
        {"role": "user", "content": "问题一"},
        {"role": "system", "content": "[此前 467 条对话的压缩摘要]"},
        {"role": "user", "content": "问题二"},
    ]
    out, merged, demoted = collapse_extra_system(msgs)
    assert _roles(out) == ["system", "user", "user", "user"]
    assert (merged, demoted) == (0, 1)
    assert out[2]["content"] == "[此前 467 条对话的压缩摘要]", "内容不能动"
    assert out[0] is msgs[0] or out[0]["content"] == "你是小鲶", "首条 system 不该被改"


def test_开头连续system_合并成一条():
    """identity_inject / model_handoff 都是 insert(0, ...) 造出来的形状。
    这两条本来就都是系统指令, 合并语义不变。"""
    msgs = [
        {"role": "system", "content": "身份"},
        {"role": "system", "content": "人设"},
        {"role": "user", "content": "你好"},
    ]
    out, merged, demoted = collapse_extra_system(msgs)
    assert _roles(out) == ["system", "user"]
    assert out[0]["content"] == "身份\n\n人设"
    assert (merged, demoted) == (1, 0)


def test_只有一条system_原样返回不做无谓拷贝():
    """绝大多数请求走这条 —— 不能因为加了这道闸就每次都重建列表。"""
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    out, merged, demoted = collapse_extra_system(msgs)
    assert out is msgs
    assert (merged, demoted) == (0, 0)


def test_一条system都没有():
    msgs = [{"role": "user", "content": "u"}, {"role": "assistant", "content": "a"}]
    out, merged, demoted = collapse_extra_system(msgs)
    assert out is msgs and (merged, demoted) == (0, 0)


def test_开头合并加中间降级_同时发生():
    msgs = [
        {"role": "system", "content": "身份"},
        {"role": "system", "content": "人设"},
        {"role": "user", "content": "问题一"},
        {"role": "system", "content": "[摘要]"},
        {"role": "assistant", "content": "答"},
    ]
    out, merged, demoted = collapse_extra_system(msgs)
    assert _roles(out) == ["system", "user", "user", "assistant"]
    assert out[0]["content"] == "身份\n\n人设"
    assert (merged, demoted) == (1, 1)


def test_不改原列表():
    """就地改会污染 caller 手里的 messages (compressor 还拿它算 token)。"""
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {"role": "system", "content": "摘要"},
    ]
    snapshot = [dict(m) for m in msgs]
    collapse_extra_system(msgs)
    assert msgs == snapshot, "原列表被改了"


def test_tool_calls_配对不受影响():
    """归一不能把 tool 序列打乱 —— 那是 5/15 撞过的另一个 400。"""
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "system", "content": "摘要"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
        {"role": "tool", "tool_call_id": "c1", "content": "r"},
    ]
    out, _, _ = collapse_extra_system(msgs)
    assert _roles(out) == ["system", "assistant", "tool"]
    assert out[1]["tool_calls"] == [{"id": "c1"}]
    assert out[2]["tool_call_id"] == "c1"


def test_归一之后全局只剩一条system():
    """这条是总闸 —— 上面几条各测一个规则, 这条测**结论**。
    随便怎么组合, 出来必须只有一条 system。"""
    import itertools
    for combo in itertools.product(["system", "user", "assistant"], repeat=4):
        msgs = [{"role": r, "content": r} for r in combo]
        out, _, _ = collapse_extra_system(msgs)
        n = sum(1 for m in out if m["role"] == "system")
        assert n <= 1, f"{combo} 归一后仍有 {n} 条 system"
        assert len(out) <= len(msgs), f"{combo} 消息变多了"
