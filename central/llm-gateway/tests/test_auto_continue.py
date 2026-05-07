"""
BL-A1.1 auto-continue 单测.

跑法:
    cd central/llm-gateway
    pytest tests/test_auto_continue.py -v
"""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock
from types import SimpleNamespace

from catfish_gateway import auto_continue


def _mk_response(content: str, finish_reason: str | None = "stop") -> SimpleNamespace:
    """造一个 litellm-shape 的 mock response (SimpleNamespace 模拟 pydantic 对象)."""
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason=finish_reason,
                message=SimpleNamespace(content=content, role="assistant"),
            )
        ]
    )


class TestAutoContinue(unittest.TestCase):
    """BL-A1.1: auto-continue on length 单测.

    8 case 覆盖核心行为:
      1. finish_reason=stop 不续, 返 1 次
      2. finish_reason=length 续 1 次
      3. finish_reason=length 连续 5 次撞上限放弃
      4. finish_reason=tool_calls 不续 (即使 caller 期望)
      5. enable=False 关掉 auto-continue
      6. content 累加正确 (3 段拼一起)
      7. body 不被原地修改 (deep copy 验证)
      8. continuation hint 拼对 (含 last 200 chars 锚点)
    """

    def test_finish_stop_no_continue(self):
        """finish_reason=stop → 不续, 返 1 次."""
        invoker = AsyncMock(return_value=_mk_response("hello", "stop"))
        body = {"messages": [{"role": "user", "content": "hi"}]}

        response, count = asyncio.run(
            auto_continue.call_with_auto_continue(body, invoker=invoker)
        )

        self.assertEqual(count, 0)
        self.assertEqual(invoker.await_count, 1)
        self.assertEqual(auto_continue._extract_message_content(response), "hello")

    def test_finish_length_continues_once(self):
        """第一次 length, 第二次 stop → 续 1 次, 累加 content."""
        responses = [
            _mk_response("part 1 ", "length"),
            _mk_response("part 2 stop", "stop"),
        ]
        invoker = AsyncMock(side_effect=responses)
        body = {"messages": [{"role": "user", "content": "write a long thing"}]}

        response, count = asyncio.run(
            auto_continue.call_with_auto_continue(body, invoker=invoker)
        )

        self.assertEqual(count, 1)
        self.assertEqual(invoker.await_count, 2)
        full = auto_continue._extract_message_content(response)
        self.assertEqual(full, "part 1 part 2 stop")

    def test_finish_length_max_continuations_giveup(self):
        """连续 length 5 次 (default max), 第 6 次还 length → 放弃, 返 5 段累加."""
        responses = [_mk_response(f"chunk{i} ", "length") for i in range(7)]
        invoker = AsyncMock(side_effect=responses)
        body = {"messages": [{"role": "user", "content": "x"}]}

        response, count = asyncio.run(
            auto_continue.call_with_auto_continue(
                body,
                invoker=invoker,
                max_continuations=5,
            )
        )

        # max=5 意思是续了 5 次 = 总共 6 次调用 (1 初 + 5 续)
        self.assertEqual(count, 5)
        self.assertEqual(invoker.await_count, 6)
        # 6 段累加
        full = auto_continue._extract_message_content(response)
        self.assertEqual(full, "chunk0 chunk1 chunk2 chunk3 chunk4 chunk5 ")

    def test_finish_tool_calls_no_continue(self):
        """tool_calls 状态必须保留给 client 处理, 不可续."""
        invoker = AsyncMock(return_value=_mk_response("calling tool", "tool_calls"))
        body = {"messages": [{"role": "user", "content": "search xxx"}]}

        response, count = asyncio.run(
            auto_continue.call_with_auto_continue(body, invoker=invoker)
        )

        self.assertEqual(count, 0)
        self.assertEqual(invoker.await_count, 1)

    def test_enable_false_skips_loop(self):
        """enable=False → 调一次直接返, 不进 loop (即使 length)."""
        invoker = AsyncMock(return_value=_mk_response("truncated", "length"))
        body = {"messages": [{"role": "user", "content": "x"}]}

        response, count = asyncio.run(
            auto_continue.call_with_auto_continue(
                body, invoker=invoker, enable=False
            )
        )

        self.assertEqual(count, 0)
        self.assertEqual(invoker.await_count, 1)

    def test_content_accumulation_three_segments(self):
        """3 段 length 后 stop, 累加正确 4 段."""
        responses = [
            _mk_response("段1", "length"),
            _mk_response("段2", "length"),
            _mk_response("段3", "length"),
            _mk_response("段4结束", "stop"),
        ]
        invoker = AsyncMock(side_effect=responses)
        body = {"messages": [{"role": "user", "content": "x"}]}

        response, count = asyncio.run(
            auto_continue.call_with_auto_continue(body, invoker=invoker)
        )

        self.assertEqual(count, 3)
        full = auto_continue._extract_message_content(response)
        self.assertEqual(full, "段1段2段3段4结束")

    def test_caller_body_not_mutated(self):
        """body 是 deep copy, caller 原 body 不被改 (防 audit / log 错乱)."""
        responses = [
            _mk_response("a", "length"),
            _mk_response("b", "stop"),
        ]
        invoker = AsyncMock(side_effect=responses)
        original_body = {
            "messages": [{"role": "user", "content": "test"}],
            "model": "qwen-main",
        }

        asyncio.run(
            auto_continue.call_with_auto_continue(original_body, invoker=invoker)
        )

        # 续写后, caller 原 body 应该不变
        self.assertEqual(len(original_body["messages"]), 1)
        self.assertEqual(original_body["messages"][0]["content"], "test")
        self.assertEqual(original_body["model"], "qwen-main")

    def test_continuation_hint_includes_last_chunk_anchor(self):
        """续写 hint 应该含 last 200 chars 锚点, 让 LLM 知道接哪."""
        long_content = "X" * 100 + "结尾 200 字符锚点ABCDEFG" + "Y" * 50
        responses = [
            _mk_response(long_content, "length"),
            _mk_response("续写完", "stop"),
        ]
        invoker = AsyncMock(side_effect=responses)
        body = {"messages": [{"role": "user", "content": "write"}]}

        asyncio.run(
            auto_continue.call_with_auto_continue(body, invoker=invoker)
        )

        # 第二次调用时, body.messages 应该含 hint, hint 含 last 200 chars
        second_call_body = invoker.await_args_list[1].args[0]
        msgs = second_call_body["messages"]
        # 末尾应该是 user hint
        self.assertEqual(msgs[-1]["role"], "user")
        hint = msgs[-1]["content"]
        # hint 应该含锚点字符串末尾
        self.assertIn("YY", hint)  # last_chunk 的末尾几个 Y
        # hint 应该含禁止重复的指令
        self.assertIn("不要", hint)
        # 倒数第二条是 assistant 含完整旧 content
        self.assertEqual(msgs[-2]["role"], "assistant")
        self.assertEqual(msgs[-2]["content"], long_content)


class TestExtractHelpers(unittest.TestCase):
    """各种 response shape 兼容性测试 (防 litellm 升级 shape 变了导致 panic)."""

    def test_extract_finish_reason_from_dict_response(self):
        """response 是 dict (litellm 偶有这种)."""
        resp = {"choices": [{"finish_reason": "length", "message": {"content": "x"}}]}
        self.assertEqual(auto_continue._extract_finish_reason(resp), "length")

    def test_extract_finish_reason_from_object_response(self):
        """response 是 pydantic-like object."""
        resp = _mk_response("x", "stop")
        self.assertEqual(auto_continue._extract_finish_reason(resp), "stop")

    def test_extract_finish_reason_handles_empty_choices(self):
        """response 没 choices → 返 None, 不抛."""
        resp = SimpleNamespace(choices=[])
        self.assertIsNone(auto_continue._extract_finish_reason(resp))

    def test_extract_message_content_handles_none(self):
        """content=None → 返 '' (不抛)."""
        resp = SimpleNamespace(
            choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=None))]
        )
        self.assertEqual(auto_continue._extract_message_content(resp), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
