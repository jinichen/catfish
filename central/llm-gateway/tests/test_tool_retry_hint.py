"""
BL-A1.2 tool_retry_hint 单测.

跑法:
    cd central/llm-gateway
    pytest tests/test_tool_retry_hint.py -v
"""
from __future__ import annotations

import unittest

from catfish_gateway import tool_retry_hint


def _user(content: str) -> dict:
    return {"role": "user", "content": content}


def _assistant_calling(tool_name: str, args: str = '{"x": 1}') -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": "call_1", "function": {"name": tool_name, "arguments": args}}
        ],
    }


def _tool_result(name: str, content: str) -> dict:
    """tool message with content (字符串, 模拟 catfish dispatch JSON output)."""
    return {"role": "tool", "tool_call_id": "call_1", "name": name, "content": content}


def _tool_ok(name: str) -> dict:
    return _tool_result(name, '{"ok": true, "result": {"x": "value"}}')


def _tool_fail(name: str, error: str = "permission denied") -> dict:
    return _tool_result(name, f'{{"ok": false, "error": "{error}", "tool": "{name}"}}')


class TestToolRetryHint(unittest.TestCase):
    """BL-A1.2: 连续 tool 失败注入 hint."""

    def test_no_messages_no_hint(self):
        """空 messages 不注入."""
        result = tool_retry_hint.inject_tool_retry_hint([])
        self.assertEqual(result, [])

    def test_no_tool_messages_no_hint(self):
        """messages 里没 tool message 不注入."""
        msgs = [
            _user("hi"),
            {"role": "assistant", "content": "hello"},
        ]
        result = tool_retry_hint.inject_tool_retry_hint(msgs)
        self.assertEqual(len(result), len(msgs))

    def test_one_failure_no_hint(self):
        """1 次 tool 失败不注入 (容忍偶发)."""
        msgs = [
            _user("read X"),
            _assistant_calling("read_file"),
            _tool_fail("read_file"),
        ]
        result = tool_retry_hint.inject_tool_retry_hint(msgs)
        # threshold = 2, 只 1 次失败不触发
        self.assertEqual(len(result), 3)
        # 最末尾仍是 tool message, 不是 system hint
        self.assertEqual(result[-1]["role"], "tool")

    def test_two_consecutive_same_tool_fail_inject_light(self):
        """2 次连续相同 tool 同 error → 注入 LIGHT hint."""
        msgs = [
            _user("read X"),
            _assistant_calling("read_file"),
            _tool_fail("read_file", "file not found"),
            _assistant_calling("read_file"),
            _tool_fail("read_file", "file not found"),
        ]
        result = tool_retry_hint.inject_tool_retry_hint(msgs)
        # 应该多了一条 system hint
        self.assertEqual(len(result), len(msgs) + 1)
        # BL-FIX6 (5/8): role 改 user, 防 Qwen Go gRPC adapter 中段 system 撞 400
        self.assertEqual(result[-1]["role"], "user")
        hint = result[-1]["content"]
        # LIGHT hint 含工具名 + error
        self.assertIn("read_file", hint)
        self.assertIn("file not found", hint)
        self.assertIn(tool_retry_hint._HINT_MARKER, hint)
        # LIGHT 提建议 "改参数 / 换工具", 不是 STRONG
        self.assertIn("改参数", hint)

    def test_three_consecutive_inject_strong(self):
        """3 次失败 → 注入 STRONG hint, 建议放弃 / 报员工."""
        msgs = [
            _user("x"),
        ]
        for _i in range(3):
            msgs.append(_assistant_calling("write_file"))
            msgs.append(_tool_fail("write_file", "disk full"))

        result = tool_retry_hint.inject_tool_retry_hint(msgs)
        # BL-FIX6 (5/8): role 改 user, 防 Qwen Go gRPC adapter 中段 system 撞 400
        self.assertEqual(result[-1]["role"], "user")
        hint = result[-1]["content"]
        self.assertIn("write_file", hint)
        # STRONG hint 出现关键词
        self.assertIn("立即停止重试", hint)
        self.assertIn("不要假装成功", hint)

    def test_different_tools_failing_not_consecutive(self):
        """两个不同 tool 各失败 1 次, 不构成"连续相同 tool" → 不注入."""
        msgs = [
            _user("x"),
            _assistant_calling("read_file"),
            _tool_fail("read_file"),
            _assistant_calling("write_file"),
            _tool_fail("write_file"),
        ]
        result = tool_retry_hint.inject_tool_retry_hint(msgs)
        # 不同工具各 1 次, 不构成同工具连续 2 次
        self.assertEqual(len(result), len(msgs))

    def test_success_breaks_failure_chain(self):
        """tool 成功后再失败不算"连续". 倒序扫到 ok 立即断."""
        msgs = [
            _user("x"),
            _assistant_calling("read_file"),
            _tool_fail("read_file"),
            _assistant_calling("read_file"),
            _tool_fail("read_file"),
            _assistant_calling("read_file"),
            _tool_ok("read_file"),  # 成功
            _assistant_calling("read_file"),
            _tool_fail("read_file"),  # 又失败但 chain 已断
        ]
        result = tool_retry_hint.inject_tool_retry_hint(msgs)
        # 最近一次失败链长度=1, 不达 threshold
        self.assertEqual(len(result), len(msgs))

    def test_hint_not_re_injected(self):
        """已经含 hint 的 messages 不再注入 (防重复)."""
        msgs = [
            _user("x"),
            _assistant_calling("read_file"),
            _tool_fail("read_file"),
            _assistant_calling("read_file"),
            _tool_fail("read_file"),
        ]
        # 第一次注入
        once = tool_retry_hint.inject_tool_retry_hint(msgs)
        self.assertEqual(len(once), len(msgs) + 1)
        # 第二次, 应该 idempotent
        twice = tool_retry_hint.inject_tool_retry_hint(once)
        self.assertEqual(len(twice), len(once))

    def test_does_not_mutate_input(self):
        """inject 不修改原 list."""
        msgs = [
            _user("x"),
            _assistant_calling("read_file"),
            _tool_fail("read_file"),
            _assistant_calling("read_file"),
            _tool_fail("read_file"),
        ]
        original_len = len(msgs)
        result = tool_retry_hint.inject_tool_retry_hint(msgs)
        # 原 list 不变
        self.assertEqual(len(msgs), original_len)
        # 返新 list (新 length)
        self.assertEqual(len(result), original_len + 1)


class TestExtractStatus(unittest.TestCase):
    """tool message 状态提取的边界 case."""

    def test_extract_ok_false_json(self):
        msg = _tool_fail("read_file", "denied")
        name, err = tool_retry_hint._extract_tool_message_status(msg)
        self.assertEqual(name, "read_file")
        self.assertIn("denied", err)

    def test_extract_ok_true_returns_none_error(self):
        msg = _tool_ok("read_file")
        name, err = tool_retry_hint._extract_tool_message_status(msg)
        self.assertEqual(name, "read_file")
        self.assertIsNone(err)

    def test_extract_traceback_string_treated_as_error(self):
        msg = _tool_result("execute_code", "Traceback (most recent call last):\nValueError: x")
        name, err = tool_retry_hint._extract_tool_message_status(msg)
        self.assertEqual(name, "execute_code")
        self.assertIsNotNone(err)

    def test_extract_non_tool_role_returns_none(self):
        msg = _user("hi")
        name, err = tool_retry_hint._extract_tool_message_status(msg)
        self.assertIsNone(name)
        self.assertIsNone(err)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestBLFIX6HintRoleIsUser(unittest.TestCase):
    """BL-FIX6 (5/8): hint role 必须是 user, 防 Qwen Go gRPC adapter 中段 system 撞 400"""

    def test_hint_injected_as_user_role(self):
        """注入的 hint message 必须是 role=user"""
        msgs = [
            _user("do X"),
            _assistant_calling("foo"),
            _tool_fail("foo", "err"),
            _assistant_calling("foo"),
            _tool_fail("foo", "err"),
        ]
        result = tool_retry_hint.inject_tool_retry_hint(msgs)
        last = result[-1]
        self.assertEqual(last["role"], "user")
        self.assertNotEqual(last["role"], "system")
        # marker 还在
        self.assertIn(tool_retry_hint._HINT_MARKER, last["content"])

    def test_existing_user_hint_blocks_re_injection(self):
        """老历史里有 user role 的 hint, 不重复注入"""
        msgs = [
            _user("do X"),
            _assistant_calling("foo"),
            _tool_fail("foo", "err"),
            _assistant_calling("foo"),
            _tool_fail("foo", "err"),
            {"role": "user", "content": tool_retry_hint._HINT_MARKER + "\n旧 hint"},
        ]
        result = tool_retry_hint.inject_tool_retry_hint(msgs)
        # 没新增
        self.assertEqual(len(result), len(msgs))

    def test_existing_system_hint_blocks_re_injection(self):
        """兼容老 BL-A1.2 部署留下的 system role hint, 不重复注入"""
        msgs = [
            _user("do X"),
            _assistant_calling("foo"),
            _tool_fail("foo", "err"),
            _assistant_calling("foo"),
            _tool_fail("foo", "err"),
            {"role": "system", "content": tool_retry_hint._HINT_MARKER + "\n老 hint"},
        ]
        result = tool_retry_hint.inject_tool_retry_hint(msgs)
        self.assertEqual(len(result), len(msgs))


# ============================================================
# 5/18 BL-HERMES-AUTO-CONTINUE-LIMIT: hard cap
# ============================================================


class TestHardCap(unittest.TestCase):
    """连续 5 次同 tool 失败 → should_hard_cap True + build_hard_cap_abort_response 返合成响应."""

    def _build_failure_chain(self, n: int, tool: str = "foo") -> list[dict]:
        """构造 n 轮 assistant tool_call + tool fail."""
        msgs = [_user("do X")]
        for _ in range(n):
            msgs.append(_assistant_calling(tool))
            msgs.append(_tool_fail(tool, "permission denied"))
        return msgs

    def test_should_hard_cap_below_threshold_returns_false(self):
        msgs = self._build_failure_chain(4)
        hit, tool, _ = tool_retry_hint.should_hard_cap(msgs)
        self.assertFalse(hit)
        self.assertIsNone(tool)

    def test_should_hard_cap_at_threshold_returns_true(self):
        """5 次连续同 tool 失败 → hit"""
        msgs = self._build_failure_chain(5)
        hit, tool, summary = tool_retry_hint.should_hard_cap(msgs)
        self.assertTrue(hit)
        self.assertEqual(tool, "foo")
        self.assertIn("permission denied", summary)

    def test_should_hard_cap_well_above_threshold(self):
        """89 次也认 (用户实盘场景)"""
        msgs = self._build_failure_chain(10)  # 10 已足够测, 不必真造 89
        hit, tool, _ = tool_retry_hint.should_hard_cap(msgs)
        self.assertTrue(hit)
        self.assertEqual(tool, "foo")

    def test_hard_cap_skipped_if_tool_succeeds_mid_chain(self):
        """失败链中插一个 tool 成功 → 重置计数, 不撞 cap"""
        msgs = [_user("do X")]
        for _ in range(3):
            msgs += [_assistant_calling("foo"), _tool_fail("foo", "err")]
        msgs += [_assistant_calling("foo"), _tool_ok("foo")]  # 成功! 重置
        for _ in range(3):
            msgs += [_assistant_calling("foo"), _tool_fail("foo", "err")]
        hit, _, _ = tool_retry_hint.should_hard_cap(msgs)
        self.assertFalse(hit)  # 后段只 3 次 < 5

    def test_hard_cap_different_tools_dont_count(self):
        """不同 tool 各 3 次失败, 不构成 5 次连续 — 不撞 cap"""
        msgs = [_user("do X")]
        for _ in range(3):
            msgs += [_assistant_calling("foo"), _tool_fail("foo", "err")]
        for _ in range(3):
            msgs += [_assistant_calling("bar"), _tool_fail("bar", "err")]
        hit, _, _ = tool_retry_hint.should_hard_cap(msgs)
        self.assertFalse(hit)

    def test_build_hard_cap_abort_response_shape(self):
        """合成响应 shape 跟 OpenAI chat.completion 兼容"""
        resp = tool_retry_hint.build_hard_cap_abort_response(
            model="qwen-122b",
            tool_name="foo",
            error_summary="尝试 #1: bad path",
            count=7,
        )
        self.assertEqual(resp["object"], "chat.completion")
        self.assertEqual(resp["model"], "qwen-122b")
        self.assertEqual(resp["x_catfish_synthetic"], "hard_cap_abort")
        choice = resp["choices"][0]
        self.assertEqual(choice["finish_reason"], "stop")
        self.assertEqual(choice["message"]["role"], "assistant")
        self.assertIn("foo", choice["message"]["content"])
        self.assertIn("7", choice["message"]["content"])
        # 重点: 无 tool_calls → hermes agent loop 收到 stop 退出
        self.assertNotIn("tool_calls", choice["message"])

    def test_build_hard_cap_abort_response_no_token_usage(self):
        """合成响应 token usage = 0, 不算 quota"""
        resp = tool_retry_hint.build_hard_cap_abort_response(
            model="x", tool_name="foo", error_summary="e", count=5,
        )
        self.assertEqual(resp["usage"]["total_tokens"], 0)
