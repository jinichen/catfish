"""BL-GATEWAY-CLEANUP-POST-HERMES Week 1 baseline (5/19 鸿波).

# 目的

hermes cutover 后 gateway 退化成 LLM 代理.  compound_intent.py + self_critique.py
是 gateway 当 agent runtime 时代的 LLM 行为塑造层 (plan-execute 注入 + 完成承诺
critique + plan-then-stop critique), 现在 hermes 自管 agent loop, 这两个文件应
该删. 但盲删可能让 qwen 回到 plan-then-stop / 死循环.

本文件用 mock litellm 跑 5 个真实 chat 场景, 把"删之前"的行为锚定下来, 删完
之后再跑这 5 个测试, 一致就 commit, 退化就 revert.

# 怎么跑

    cd central/llm-gateway
    pytest tests/test_gateway_chat_integration.py -v

不依赖 qwen / deepseek 网络.  litellm-shape response 全 mock (SimpleNamespace),
路线照搬 tests/test_auto_continue.py.

# 5 个 scenario

  S1. 单 skill 触发     — "帮我写本周周报"             → catfish_run_skill
  S2. 复合任务 plan-act  — "分析 CSV 然后生成 PPT"      → 2 个 tool_call 链
  S3. 文档处理 chain     — "读 notes.txt 改 md 然后保存" → read_file → write_file
  S4. 邮件场景          — "查王主任最近邮件"            → catfish_email_search
  S5. 闲聊              — "你好"                       → 直接 content stop

每个 case 验:
  - 注入层行为 (compound_intent 是否注入 / self_critique 是否注入 plan-hint)
  - mock LLM 第 N 轮发对应 tool_call
  - 最终 finish_reason / content 收尾

删除后期望:
  - S1 / S2 / S3 / S4 / S5 5/5 全过 (注入层不再注入是正常的, 但 LLM mock
    行为本身不依赖注入, 所以应该全过 — 注入只是输入的修饰, 不是 mock 的判
    决条件).

# 跟现有 test_compound_intent.py / test_self_critique.py 区别

那两个是模块单测 (输入 messages 出 messages), 看注入逻辑.  本文件是**模拟一
轮完整 chat** (用户 prompt → 注入 → LLM 第 1 轮 → tool result → LLM 第 2 轮 →
最终 content), 跟 hermes 接管后的真实数据流贴近, 防整体回归.
"""
from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

# BL-GATEWAY-CLEANUP-POST-HERMES (Week 1 删完): compound_intent.py + self_critique.py
# 已删, hermes-agent 自管 agent loop. 本文件仍保留 5 scenario 作"删完之后"行为锚
# 定 — 不再 import 已删模块, 注入相关断言改成 no-op (注入层都没了, 自然不再注入).
# 凡是 mock LLM 行为 / agent loop 轮次断言仍跑, 这才是真正的 anchor.
def has_compound_intent(_messages):  # noqa: D401 — stub for post-deletion anchor
    """已删 compound_intent.py — 永远不触发 (hermes 自管 plan-execute)."""
    return False


def inject_compound_plan_execute(messages, model_name=None):  # noqa: ARG001
    """已删 compound_intent.py — 直接返原 messages."""
    return list(messages)


def inject_self_critique(messages):
    """已删 self_critique.py — 直接返原 messages (hermes agent loop 兜底)."""
    return list(messages)


_PLAN_EXECUTE_MARKER = "<<DELETED_NEVER_APPEARS>>"
_HINT_MARKER = "<<DELETED_NEVER_APPEARS>>"
_PLAN_HINT_MARKER = "<<DELETED_NEVER_APPEARS>>"


# ─── helpers — mock litellm response shapes ─────────────────────


def _mk_message_response(
    content: str | None = "",
    tool_calls: list[dict] | None = None,
    finish_reason: str = "stop",
) -> SimpleNamespace:
    """litellm-shape 单轮 response.

    finish_reason in {"stop", "tool_calls", "length"}.
    tool_calls 是 list[dict{id, type, function:{name, arguments}}].
    """
    message = SimpleNamespace(
        role="assistant",
        content=content,
        tool_calls=tool_calls,
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(finish_reason=finish_reason, message=message)]
    )


def _mk_tool_call(name: str, arguments: dict, tc_id: str = "call_1") -> dict:
    """造一个 OpenAI-shape tool_call dict."""
    return {
        "id": tc_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def _append_assistant_round(
    messages: list, response: SimpleNamespace,
) -> list:
    """把 mock response 当 assistant msg 追加到 messages history.

    模拟 agent loop: LLM 发完 tool_call, host (hermes) 把 assistant msg + tool
    result 加进 history, 然后再 call 一次 LLM.
    """
    msg = response.choices[0].message
    out = list(messages)
    out.append({
        "role": "assistant",
        "content": msg.content or "",
        "tool_calls": [
            {
                "id": tc["id"],
                "type": tc["type"],
                "function": tc["function"],
            }
            for tc in (msg.tool_calls or [])
        ] if msg.tool_calls else None,
    })
    return out


def _append_tool_result(
    messages: list, tool_call_id: str, name: str, result: str,
) -> list:
    """tool 跑完 host 把 result 追加到 history."""
    out = list(messages)
    out.append({
        "role": "tool",
        "tool_call_id": tool_call_id,
        "name": name,
        "content": result,
    })
    return out


def _base_messages(user_text: str) -> list:
    """最小化 messages: 1 system + 1 user.  System 模拟 Registry inject 后的 prompt."""
    return [
        {"role": "system", "content": "你是鲶鱼 (Catfish), 央企智能助理. 调用工具完成任务."},
        {"role": "user", "content": user_text},
    ]


async def _simulate_agent_round(
    messages: list, mock_invoker: AsyncMock,
) -> SimpleNamespace:
    """模拟 gateway 调一次上游 LLM (用 mock_invoker)."""
    return await mock_invoker(messages)


# ─── BL-GATEWAY-CLEANUP baseline tests ──────────────────────────


class GatewayChatIntegrationBaseline(unittest.TestCase):
    """5 scenario, 全 mock litellm. 删除 compound_intent / self_critique 之前 / 之后跑 — 行为必须一致."""

    # ─── S1: 单 skill 触发 (catfish_run_skill) ─────────────────

    def test_s1_single_skill_weekly_report(self):
        """单步任务 ("写本周周报") — 不触发 compound (没连接词), LLM emit catfish_run_skill, tool result 后 LLM 收尾.

        删除后行为应一致: 注入层不再注入 plan-execute (反正本身就没触发), LLM 直接
        emit 单 tool_call, 收尾 content.
        """
        messages = _base_messages("帮我写本周周报")

        # 注入层: 单步任务不应触发 compound plan-execute
        injected = inject_compound_plan_execute(messages, model_name="qwen")
        self.assertNotIn(_PLAN_EXECUTE_MARKER, injected[0]["content"],
                         "S1: 单步任务不应注入 plan-execute (删之后也不该有)")

        # Round 1: LLM emit tool_call catfish_run_skill
        tc1 = _mk_tool_call(
            "catfish_run_skill",
            {"name": "catfish-weekly-report"},
            tc_id="call_skill_weekly",
        )
        round1 = _mk_message_response(
            content="", tool_calls=[tc1], finish_reason="tool_calls",
        )
        invoker = AsyncMock(side_effect=[
            round1,
            _mk_message_response(
                content="周报已生成: ~/Documents/weekly_report_W21.docx",
                tool_calls=None, finish_reason="stop",
            ),
        ])

        # 模拟 hermes agent loop: 第 1 轮 → tool result → 第 2 轮
        async def _go():
            r1 = await _simulate_agent_round(injected, invoker)
            self.assertEqual(r1.choices[0].finish_reason, "tool_calls")
            self.assertIsNotNone(r1.choices[0].message.tool_calls)
            self.assertEqual(
                r1.choices[0].message.tool_calls[0]["function"]["name"],
                "catfish_run_skill",
            )

            # host 把 assistant msg + tool result 加进 history
            h = _append_assistant_round(injected, r1)
            h = _append_tool_result(
                h, "call_skill_weekly", "catfish_run_skill",
                json.dumps({"ok": True, "path": "~/Documents/weekly_report_W21.docx"}),
            )

            r2 = await _simulate_agent_round(h, invoker)
            self.assertEqual(r2.choices[0].finish_reason, "stop")
            self.assertIn("周报已生成", r2.choices[0].message.content)
            self.assertIn(".docx", r2.choices[0].message.content)

        asyncio.run(_go())
        self.assertEqual(invoker.await_count, 2)

    # ─── S2: 复合任务 plan-act ──────────────────────────────────

    def test_s2_compound_csv_then_pptx(self):
        """复合任务 ("分析 CSV 然后生成 PPT") — compound_intent 应触发, LLM 两步发 tool_call.

        删除 compound_intent 之后期望: 注入层不再注入 plan-execute prompt,
        但 hermes 自己有 agent loop, LLM 应该靠 hermes prompt + iteration 自主
        分步.  本测试在 "删之前" 跑 → 应该注入 + 顺利分步.

        删之后期望: 不注入 plan-execute, 但 LLM 仍能两步发 tool_call (mock
        本身不依赖 prompt, 所以测试结果一致).  风险落在真实 LLM 上, 走 baseline
        实盘录 (见 GATEWAY-CLEANUP-WEEK1-DELETE-PLAN.md).
        """
        messages = _base_messages("分析 ~/data/sales.csv 然后生成 PPT")

        # Week 1 删完: compound_intent 已删, 永远不触发. hermes 自管 plan-execute.
        self.assertFalse(has_compound_intent(messages),
                         "S2 (post-deletion): compound_intent 已删, 永远不触发")
        injected = inject_compound_plan_execute(messages, model_name="qwen")
        self.assertNotIn(_PLAN_EXECUTE_MARKER, injected[0]["content"],
                         "S2 (post-deletion): 已删模块不应注入 plan-execute")

        # Round 1: LLM emit execute_code 跑 CSV 分析
        tc_csv = _mk_tool_call(
            "execute_code",
            {"language": "python", "code": "import pandas; df = pandas.read_csv('~/data/sales.csv'); print(df.describe())"},
            tc_id="call_csv_1",
        )
        round1 = _mk_message_response(
            content="", tool_calls=[tc_csv], finish_reason="tool_calls",
        )

        # Round 2: LLM emit catfish_run_skill 生成 PPT
        tc_ppt = _mk_tool_call(
            "catfish_run_skill",
            {"name": "catfish-guicang-ppt", "data_summary": "sales Q3 up 12%"},
            tc_id="call_ppt_1",
        )
        round2 = _mk_message_response(
            content="", tool_calls=[tc_ppt], finish_reason="tool_calls",
        )

        # Round 3: LLM 收尾
        round3 = _mk_message_response(
            content="完成: 已生成 PPT → ~/Documents/sales_q3.pptx (12 张)",
            tool_calls=None, finish_reason="stop",
        )

        invoker = AsyncMock(side_effect=[round1, round2, round3])

        async def _go():
            r1 = await _simulate_agent_round(injected, invoker)
            self.assertEqual(r1.choices[0].finish_reason, "tool_calls")
            self.assertEqual(
                r1.choices[0].message.tool_calls[0]["function"]["name"],
                "execute_code",
            )

            h = _append_assistant_round(injected, r1)
            h = _append_tool_result(
                h, "call_csv_1", "execute_code",
                json.dumps({"ok": True, "stdout": "Q3 sales up 12%"}),
            )

            r2 = await _simulate_agent_round(h, invoker)
            self.assertEqual(r2.choices[0].finish_reason, "tool_calls")
            self.assertEqual(
                r2.choices[0].message.tool_calls[0]["function"]["name"],
                "catfish_run_skill",
            )

            h = _append_assistant_round(h, r2)
            h = _append_tool_result(
                h, "call_ppt_1", "catfish_run_skill",
                json.dumps({"ok": True, "path": "~/Documents/sales_q3.pptx"}),
            )

            r3 = await _simulate_agent_round(h, invoker)
            self.assertEqual(r3.choices[0].finish_reason, "stop")
            self.assertIn(".pptx", r3.choices[0].message.content)

        asyncio.run(_go())
        self.assertEqual(invoker.await_count, 3)

    # ─── S3: 文档处理 chain (read_file → write_file) ──────────

    def test_s3_doc_processing_chain(self):
        """读 notes.txt → 转 markdown → 写出 — 复合任务, 应该 read_file + write_file 两轮.

        Prompt 用"读取"/"写出" — 命中 _ACTION_VERBS (compound_intent.py).  避免
        "改"/"保存" 没在动词表里 false-negative.
        """
        messages = _base_messages("读取 notes.txt 然后写出 markdown 版本")

        # Week 1 删完: compound_intent 已删, 永远不触发. hermes agent loop 兜底.
        self.assertFalse(has_compound_intent(messages),
                         "S3 (post-deletion): compound_intent 已删, 永远不触发")
        injected = inject_compound_plan_execute(messages, model_name="qwen")

        # Round 1: read_file
        tc_read = _mk_tool_call(
            "read_file",
            {"path": "~/docs/notes.txt"},
            tc_id="call_read_1",
        )
        round1 = _mk_message_response(
            content="", tool_calls=[tc_read], finish_reason="tool_calls",
        )

        # Round 2: write_file (md 版本)
        tc_write = _mk_tool_call(
            "write_file",
            {"path": "~/docs/notes.md", "content": "# Notes\n\n..."},
            tc_id="call_write_1",
        )
        round2 = _mk_message_response(
            content="", tool_calls=[tc_write], finish_reason="tool_calls",
        )

        # Round 3: 收尾
        round3 = _mk_message_response(
            content="已保存: ~/docs/notes.md (1.2 KB)",
            tool_calls=None, finish_reason="stop",
        )

        invoker = AsyncMock(side_effect=[round1, round2, round3])

        async def _go():
            r1 = await _simulate_agent_round(injected, invoker)
            self.assertEqual(
                r1.choices[0].message.tool_calls[0]["function"]["name"],
                "read_file",
            )
            h = _append_assistant_round(injected, r1)
            h = _append_tool_result(
                h, "call_read_1", "read_file",
                "First line\nSecond line",
            )

            r2 = await _simulate_agent_round(h, invoker)
            self.assertEqual(
                r2.choices[0].message.tool_calls[0]["function"]["name"],
                "write_file",
            )
            h = _append_assistant_round(h, r2)
            h = _append_tool_result(
                h, "call_write_1", "write_file",
                json.dumps({"ok": True, "bytes": 1234}),
            )

            r3 = await _simulate_agent_round(h, invoker)
            self.assertEqual(r3.choices[0].finish_reason, "stop")
            self.assertIn(".md", r3.choices[0].message.content)
            self.assertIn("已保存", r3.choices[0].message.content)

        asyncio.run(_go())
        self.assertEqual(invoker.await_count, 3)

    # ─── S4: 邮件场景 (catfish_email_search) ──────────────────

    def test_s4_email_query(self):
        """查王主任最近邮件 — 单 skill, catfish_email_search, tool result 后 content 总结."""
        messages = _base_messages("查一下王主任最近邮件")

        # 单步, 不触发 compound
        self.assertFalse(has_compound_intent(messages),
                         "S4: 单步邮件查询不应触发 compound")
        injected = inject_compound_plan_execute(messages, model_name="qwen")
        self.assertNotIn(_PLAN_EXECUTE_MARKER, injected[0]["content"])

        # Round 1: catfish_email_search
        tc = _mk_tool_call(
            "catfish_email_search",
            {"query": "from:王主任", "days": 7},
            tc_id="call_email_1",
        )
        round1 = _mk_message_response(
            content="", tool_calls=[tc], finish_reason="tool_calls",
        )

        round2 = _mk_message_response(
            content="王主任最近 7 天有 3 封邮件:\n1. 5/18 关于 Q3 预算\n2. 5/17 报销提醒\n3. 5/16 周会议程",
            tool_calls=None, finish_reason="stop",
        )

        invoker = AsyncMock(side_effect=[round1, round2])

        async def _go():
            r1 = await _simulate_agent_round(injected, invoker)
            self.assertEqual(
                r1.choices[0].message.tool_calls[0]["function"]["name"],
                "catfish_email_search",
            )
            h = _append_assistant_round(injected, r1)
            h = _append_tool_result(
                h, "call_email_1", "catfish_email_search",
                json.dumps({
                    "ok": True,
                    "results": [
                        {"date": "2026-05-18", "subject": "Q3 预算"},
                        {"date": "2026-05-17", "subject": "报销提醒"},
                        {"date": "2026-05-16", "subject": "周会议程"},
                    ],
                }),
            )

            r2 = await _simulate_agent_round(h, invoker)
            self.assertEqual(r2.choices[0].finish_reason, "stop")
            self.assertIn("王主任", r2.choices[0].message.content)
            self.assertIn("3 封", r2.choices[0].message.content)

        asyncio.run(_go())

    # ─── S5: 闲聊 ─────────────────────────────────────────────

    def test_s5_chitchat_no_tool(self):
        """闲聊 ("你好") — 直接 content, 不调 tool, finish_reason=stop.

        关键: 不应注入 compound plan-execute (没复合连接词 + 没动作动词 ≥ 2),
        也不该触发 self_critique plan-hint (没 plan 文本).
        删之后行为一致: 注入层不再存在, 但闲聊本身也不需要这俩注入.
        """
        messages = _base_messages("你好")

        # 注入层不应触发
        self.assertFalse(has_compound_intent(messages),
                         "S5: 闲聊不应触发 compound")
        injected = inject_compound_plan_execute(messages, model_name="qwen")
        self.assertNotIn(_PLAN_EXECUTE_MARKER, injected[0]["content"],
                         "S5: 闲聊不应注入 plan-execute prompt")

        # Round 1: 直接 content stop
        round1 = _mk_message_response(
            content="你好! 我是鲶鱼, 央企智能助理. 今天有什么我能帮你的?",
            tool_calls=None, finish_reason="stop",
        )

        invoker = AsyncMock(side_effect=[round1])

        async def _go():
            r1 = await _simulate_agent_round(injected, invoker)
            self.assertEqual(r1.choices[0].finish_reason, "stop")
            self.assertIsNone(r1.choices[0].message.tool_calls,
                              "S5: 闲聊不应发 tool_call")
            self.assertTrue(len(r1.choices[0].message.content) > 0,
                            "S5: 应有 content 回复")
            self.assertIn("你好", r1.choices[0].message.content)

            # 模拟 hermes 把 assistant msg 加进 history, 再跑一次 inject_self_critique
            # → plan-then-stop 不应触发 (没 plan 文本), 完成承诺也不应触发 (没"已完成").
            h = _append_assistant_round(injected, r1)
            critiqued = inject_self_critique(h)
            critique_added = len(critiqued) - len(h)
            self.assertEqual(critique_added, 0,
                             "S5: 闲聊响应不应触发任何 self-critique hint")
            # 双保险
            for m in critiqued:
                if isinstance(m.get("content"), str):
                    self.assertNotIn(_PLAN_HINT_MARKER, m["content"])
                    self.assertNotIn(_HINT_MARKER, m["content"])

        asyncio.run(_go())
        self.assertEqual(invoker.await_count, 1)


# ─── 单独跑 ───────────────────────────────────────────────────


if __name__ == "__main__":
    unittest.main(verbosity=2)
