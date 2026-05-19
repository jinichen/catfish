"""
BL-A1.3 self_critique 单测.

跑法:
    cd central/llm-gateway
    pytest tests/test_self_critique.py -v
"""
from __future__ import annotations

import unittest

from catfish_gateway import self_critique


def _user(c: str) -> dict:
    return {"role": "user", "content": c}


def _assistant(c: str) -> dict:
    return {"role": "assistant", "content": c}


def _assistant_with_tool_call(content: str, tool_name: str) -> dict:
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": tool_name, "arguments": "{}"}}
        ],
    }


def _tool_result(name: str, ok: bool = True) -> dict:
    body = '{"ok": true, "result": "..."}' if ok else '{"ok": false, "error": "x"}'
    return {"role": "tool", "tool_call_id": "c1", "name": name, "content": body}


class TestSelfCritique(unittest.TestCase):
    """BL-A1.3: 检测幻觉完成 → 注入 hint."""

    def test_normal_chat_no_trigger(self):
        """普通聊天不触发."""
        msgs = [
            _user("你好"),
            _assistant("你好, 有什么可以帮你的?"),
        ]
        result = self_critique.inject_completion_critique_hint(msgs)
        self.assertEqual(len(result), len(msgs))

    def test_completion_promise_with_tool_call_no_trigger(self):
        """LLM 说"已生成" + 前面有 execute_code → 真做了, 不触发."""
        msgs = [
            _user("帮我写个 docx"),
            _assistant_with_tool_call(content=None, tool_name="execute_code"),
            _tool_result("execute_code", ok=True),
            _assistant("已生成 ~/Desktop/x.docx, 32 段"),
        ]
        result = self_critique.inject_completion_critique_hint(msgs)
        self.assertEqual(len(result), len(msgs))

    def test_completion_promise_without_tool_call_triggers(self):
        """LLM 说"已生成" + 前面没 tool_call → 幻觉, 触发."""
        msgs = [
            _user("帮我写个 docx"),
            _assistant("好的, 我已生成 ~/Desktop/x.docx"),  # 没真调工具
        ]
        result = self_critique.inject_completion_critique_hint(msgs)
        self.assertEqual(len(result), len(msgs) + 1)
        # BL-FIX8 (5/8): role 改 user, 防 Qwen Go gRPC adapter 中段 system 撞 400
        self.assertEqual(result[-1]["role"], "user")
        self.assertIn(self_critique._HINT_MARKER, result[-1]["content"])
        # hint 含 quoted promise
        self.assertIn("已生成", result[-1]["content"])

    def test_completion_promise_with_failed_tool_call_no_trigger(self):
        """tool 调了但失败 → tool_retry_hint 管, 不是 self_critique 的事.
        self_critique 只看 'tool_call 是否存在', 不看是否成功.
        """
        msgs = [
            _user("写 docx"),
            _assistant_with_tool_call(content=None, tool_name="execute_code"),
            _tool_result("execute_code", ok=False),  # 失败
            _assistant("已生成 ~/x.docx"),  # 但 LLM 假说成功 — self_critique 不管这个 false claim
                                            # tool 真调了 (即使失败), 不触发 self_critique
        ]
        result = self_critique.inject_completion_critique_hint(msgs)
        self.assertEqual(len(result), len(msgs))

    def test_multiple_completion_keywords(self):
        """各种完成承诺关键词都识别."""
        for keyword in [
            "已生成", "已保存", "已完成", "已创建", "已修改",
            "已写入", "已经生成", "已经保存", "已成功", "成功生成",
        ]:
            msgs = [
                _user("写"),
                _assistant(f"好的, {keyword}文件了"),
            ]
            result = self_critique.inject_completion_critique_hint(msgs)
            self.assertEqual(
                len(result), len(msgs) + 1,
                f"关键词 '{keyword}' 应触发但没触发",
            )

    def test_future_intent_no_trigger(self):
        """LLM 说"我马上生成" 是未来意图, 不是已完成, 不触发."""
        msgs = [
            _user("写"),
            _assistant("好的, 我马上生成 docx"),
        ]
        result = self_critique.inject_completion_critique_hint(msgs)
        self.assertEqual(len(result), len(msgs))

    def test_hint_not_re_injected(self):
        """已注入过 hint 不重复."""
        msgs = [
            _user("帮我写 docx"),
            _assistant("已完成报告.docx 文件保存"),
        ]
        once = self_critique.inject_completion_critique_hint(msgs)
        self.assertEqual(len(once), len(msgs) + 1)
        twice = self_critique.inject_completion_critique_hint(once)
        self.assertEqual(len(twice), len(once))

    def test_does_not_mutate_input(self):
        """不修改原 list."""
        msgs = [
            _user("写 docx"),
            _assistant("已完成 ~/Desktop/x.docx 32 段"),
        ]
        original_len = len(msgs)
        result = self_critique.inject_completion_critique_hint(msgs)
        self.assertEqual(len(msgs), original_len)
        self.assertEqual(len(result), original_len + 1)

    def test_short_content_not_triggered(self):
        """短 content (<8 字) 不触发, 避免误判."""
        msgs = [
            _user("hi"),
            _assistant("OK"),  # 太短
        ]
        result = self_critique.inject_completion_critique_hint(msgs)
        self.assertEqual(len(result), len(msgs))

    def test_catfish_run_skill_counts_as_productive(self):
        """catfish_run_skill 也算真做."""
        msgs = [
            _user("写 docx"),
            _assistant_with_tool_call(content=None, tool_name="catfish_run_skill"),
            _tool_result("catfish_run_skill", ok=True),
            _assistant("已生成报告.docx"),
        ]
        result = self_critique.inject_completion_critique_hint(msgs)
        self.assertEqual(len(result), len(msgs))

    def test_unproductive_tool_does_not_count(self):
        """read_file / search 之类只读工具不算"真做事". 说"已生成"还是触发."""
        msgs = [
            _user("写 docx"),
            _assistant_with_tool_call(content=None, tool_name="read_file"),  # 只读
            _tool_result("read_file", ok=True),
            _assistant("已生成 docx"),  # 没调 execute_code, read_file 不算 productive
        ]
        result = self_critique.inject_completion_critique_hint(msgs)
        self.assertEqual(len(result), len(msgs) + 1)


class TestPromiseDetection(unittest.TestCase):
    """完成承诺关键词识别边界 case."""

    def test_yi_jing_alone_not_promise(self):
        """'已经' 单独不算承诺 (常用语)."""
        has, _ = self_critique._has_completion_promise("我已经看到了")
        # '已经' + '看到' 没匹配完成关键词 (要 '已经生成 / 已经保存' 等)
        self.assertFalse(has)

    def test_yi_zhi_not_promise(self):
        """'已知' 不是完成承诺."""
        has, _ = self_critique._has_completion_promise("已知员工偏好")
        self.assertFalse(has)

    def test_english_completion_keyword(self):
        """英文 saved successfully 也算."""
        has, _ = self_critique._has_completion_promise(
            "The file has been saved successfully to ~/x.docx"
        )
        self.assertTrue(has)


# ─── BL-LLM-PLAN-WITHOUT-ACT (5/19): plan-then-stop 单测 ──────────────────


class TestPlanIntentDetection(unittest.TestCase):
    """_has_plan_intent 单元: 各 plan 模式识别 + 不误伤真完成."""

    def test_json_plan_detected(self):
        """JSON plan {"plan": [...], "step": N, "action": "..."} 识别."""
        content = (
            '好的, 我整理了 plan:\n'
            '```json\n'
            '{"plan": [{"step": 1, "action": "catfish_run_skill", "what": "生成周报"}]}\n'
            '```\n'
        )
        is_plan, _ = self_critique._has_plan_intent(content)
        self.assertTrue(is_plan)

    def test_chinese_step_plan_detected(self):
        """中文 "第 1 步 / step 1" 识别."""
        for content in [
            "好的, 我将:\n第 1 步: 读取数据\n第 2 步: 生成文件\n开始执行:",
            "我会:\nstep 1: 调用 weekly-report skill\nstep 2: 写文件",
            "接下来我会:\n1. 拉取员工数据\n2. 生成报表\n3. 保存到桌面",
            "我打算先调 skill 生成周报, 然后保存到本地.\n开始执行:",
        ]:
            with self.subTest(content=content[:30]):
                is_plan, _ = self_critique._has_plan_intent(content)
                self.assertTrue(is_plan, f"应识别为 plan: {content[:50]}")

    def test_real_delivery_not_plan(self):
        """真交付物 (含路径 + 字节数) 不当 plan, 防误伤."""
        content = (
            "周报已生成: /Users/hongbo/Desktop/weekly-2026-05-19.docx\n"
            "文件大小 32 KB, 共 12 段, 内容覆盖本周 5 项进展."
        )
        is_plan, _ = self_critique._has_plan_intent(content)
        self.assertFalse(is_plan, "含真路径 + 大小, 不应判 plan")

    def test_short_content_not_plan(self):
        """太短 (< 20 字) 不判 plan."""
        is_plan, _ = self_critique._has_plan_intent("好的我将做")
        self.assertFalse(is_plan)

    def test_normal_chat_not_plan(self):
        """普通对话不判 plan."""
        is_plan, _ = self_critique._has_plan_intent(
            "你好, 这周天气不错, 我刚刚看了一本书, 觉得挺有意思的, 推荐你也看看"
        )
        self.assertFalse(is_plan)


class TestPlanThenStopHint(unittest.TestCase):
    """inject_plan_then_stop_hint 触发逻辑."""

    def test_json_plan_no_tool_call_triggers(self):
        """JSON plan + 没 tool_call → 触发 plan hint."""
        msgs = [
            _user("帮我写周报"),
            _assistant(
                '我整理了 plan:\n```json\n'
                '{"plan": [{"step": 1, "action": "catfish_run_skill", '
                '"what": "生成周报"}]}\n```'
            ),
        ]
        result = self_critique.inject_plan_then_stop_hint(msgs)
        self.assertEqual(len(result), len(msgs) + 1)
        self.assertEqual(result[-1]["role"], "user")
        self.assertIn(self_critique._PLAN_HINT_MARKER, result[-1]["content"])

    def test_chinese_step_plan_no_tool_call_triggers(self):
        """中文 step plan + 没 tool_call → 触发."""
        msgs = [
            _user("帮我做 PPT"),
            _assistant(
                "好的, 我将:\n"
                "第 1 步: 收集材料\n"
                "第 2 步: 写大纲\n"
                "第 3 步: 生成 PPT\n"
                "开始执行:"
            ),
        ]
        result = self_critique.inject_plan_then_stop_hint(msgs)
        self.assertEqual(len(result), len(msgs) + 1)
        self.assertIn("plan-then-stop", result[-1]["content"])

    def test_real_delivery_no_trigger(self):
        """真完成 (含路径 + 大小) → 不触发 plan hint (防误伤)."""
        msgs = [
            _user("写周报"),
            _assistant_with_tool_call(content=None, tool_name="catfish_run_skill"),
            _tool_result("catfish_run_skill", ok=True),
            _assistant(
                "周报已生成: /Users/hongbo/Desktop/weekly.docx\n"
                "文件大小 32 KB, 12 段, 包含本周 5 项进展."
            ),
        ]
        result = self_critique.inject_plan_then_stop_hint(msgs)
        self.assertEqual(len(result), len(msgs), "真交付物不应触发 plan hint")

    def test_plan_with_tool_call_no_trigger(self):
        """plan 文本 + 同范围有 productive tool_call → 不触发 (plan + 真做 OK)."""
        msgs = [
            _user("写周报"),
            _assistant_with_tool_call(
                content="好的, 我将:\n第 1 步: 调 skill\n第 2 步: 收尾",
                tool_name="catfish_run_skill",
            ),
            _tool_result("catfish_run_skill", ok=True),
        ]
        result = self_critique.inject_plan_then_stop_hint(msgs)
        self.assertEqual(len(result), len(msgs), "plan + 真调 tool 不应触发")

    def test_pure_tool_call_no_trigger(self):
        """纯 tool_call 路径 (content 空 / 无 plan 文本) → 不触发."""
        msgs = [
            _user("写周报"),
            _assistant_with_tool_call(content=None, tool_name="catfish_run_skill"),
        ]
        result = self_critique.inject_plan_then_stop_hint(msgs)
        self.assertEqual(len(result), len(msgs))

    def test_normal_chat_no_trigger(self):
        """普通聊天不触发."""
        msgs = [
            _user("你好"),
            _assistant("你好, 有什么可以帮你的吗?"),
        ]
        result = self_critique.inject_plan_then_stop_hint(msgs)
        self.assertEqual(len(result), len(msgs))

    def test_plan_hint_not_re_injected(self):
        """plan hint 已注入过不重复."""
        msgs = [
            _user("写周报"),
            _assistant("我将:\n第 1 步: 收集\n第 2 步: 生成\n开始执行:"),
        ]
        once = self_critique.inject_plan_then_stop_hint(msgs)
        self.assertEqual(len(once), len(msgs) + 1)
        twice = self_critique.inject_plan_then_stop_hint(once)
        self.assertEqual(len(twice), len(once), "plan hint 不应重复注入")

    def test_does_not_mutate_input(self):
        """不修改原 list."""
        msgs = [
            _user("写周报"),
            _assistant("我将:\n第 1 步: 收集\n第 2 步: 生成\n开始执行:"),
        ]
        original_len = len(msgs)
        result = self_critique.inject_plan_then_stop_hint(msgs)
        self.assertEqual(len(msgs), original_len)
        self.assertEqual(len(result), original_len + 1)


class TestSelfCritiqueAggregate(unittest.TestCase):
    """聚合入口 inject_self_critique 覆盖两条 detect."""

    def test_completion_promise_path_via_aggregate(self):
        """完成承诺路径走聚合入口也能触发."""
        msgs = [
            _user("帮我写 docx"),
            _assistant("好的, 我已生成 ~/Desktop/x.docx"),
        ]
        # 注: assistant content 含 "~/Desktop/x.docx" 路径, plan detect 不会触发
        # 但完成承诺 detect 仍触发 (因为含 "已生成")
        result = self_critique.inject_self_critique(msgs)
        self.assertEqual(len(result), len(msgs) + 1)
        self.assertIn(self_critique._HINT_MARKER, result[-1]["content"])

    def test_plan_then_stop_path_via_aggregate(self):
        """plan-then-stop 路径走聚合入口能触发."""
        msgs = [
            _user("写周报"),
            _assistant(
                "我将:\n"
                "第 1 步: 收集数据\n"
                "第 2 步: 生成文件\n"
                "开始执行:"
            ),
        ]
        result = self_critique.inject_self_critique(msgs)
        # 只触发 plan hint (没完成承诺关键词)
        self.assertEqual(len(result), len(msgs) + 1)
        self.assertIn(self_critique._PLAN_HINT_MARKER, result[-1]["content"])

    def test_normal_chat_no_trigger_aggregate(self):
        """普通聊天聚合入口也不触发."""
        msgs = [
            _user("你好"),
            _assistant("你好, 有什么可以帮你的吗?"),
        ]
        result = self_critique.inject_self_critique(msgs)
        self.assertEqual(len(result), len(msgs))

    def test_real_delivery_no_trigger_aggregate(self):
        """真完成 (路径 + 大小) 走聚合入口不触发 plan hint, 也不触发完成承诺
        (因为有 tool_call 配套).
        """
        msgs = [
            _user("写周报"),
            _assistant_with_tool_call(content=None, tool_name="catfish_run_skill"),
            _tool_result("catfish_run_skill", ok=True),
            _assistant(
                "周报已生成: /Users/hongbo/Desktop/weekly.docx (32 KB, 12 段)"
            ),
        ]
        result = self_critique.inject_self_critique(msgs)
        self.assertEqual(len(result), len(msgs), "真完成不应触发任何 hint")


if __name__ == "__main__":
    unittest.main(verbosity=2)
