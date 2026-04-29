"""stats_guard 单测.

覆盖:
  - has_stats_intent 各类关键词命中
  - 否定场景 (问候 / 普通对话不命中)
  - 多语言 (中英文混合)
  - inject_stats_guard 在不同 messages 形态下的行为
"""
from __future__ import annotations

import pytest

from catfish_gateway.stats_guard import has_stats_intent, inject_stats_guard


# ============================================================
# has_stats_intent
# ============================================================


class TestHasStatsIntent:
    def _user(self, text: str) -> list[dict]:
        return [{"role": "user", "content": text}]

    def test_empty(self) -> None:
        assert not has_stats_intent([])
        assert not has_stats_intent(None)

    @pytest.mark.parametrize("text", [
        "这个 CSV 多少行",
        "EIS 资质多少条",
        "帮我统计一下",
        "总数是多少",
        "总共几个",
        "合计金额",
        "按部门分组",
        "按状态分类",
        "平均值是多少",
        "最大金额",
        "最小日期",
        "占比多少",
        "出现了多少次",
        "去重之后",
        "row count 多少",
        "TOTAL 是多少",
        "group by department",
        "average price",
        "Aggregate by month",
    ])
    def test_positive_match(self, text: str) -> None:
        assert has_stats_intent(self._user(text)), f"应该命中: {text}"

    @pytest.mark.parametrize("text", [
        "你好",
        "今天天气怎么样",
        "帮我开一下浏览器",
        "登录 EIS",
        "把这个文件移动到 Downloads",
        "我刚才说错了",
        "记一下我的偏好",
    ])
    def test_negative_no_match(self, text: str) -> None:
        assert not has_stats_intent(self._user(text)), f"不该命中: {text}"

    def test_only_last_user_message(self) -> None:
        """只看最后一条 user, 之前的不算 (那是历史轮)"""
        msgs = [
            {"role": "user", "content": "统计一下"},  # 历史 — 命中但不该用
            {"role": "assistant", "content": "好"},
            {"role": "user", "content": "你好"},  # 最近 — 不命中
        ]
        assert not has_stats_intent(msgs)

    def test_multimodal_text_extracted(self) -> None:
        """multimodal content list 也能提取 text 检测"""
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "这表多少行"},
                    {"type": "image_url", "image_url": {"url": "..."}},
                ],
            }
        ]
        assert has_stats_intent(msgs)

    def test_invalid_msg_safe(self) -> None:
        """msg 不是 dict / 缺 content → 不抛"""
        msgs = ["not a dict", {"role": "user"}, {"role": "user", "content": None}]
        assert not has_stats_intent(msgs)

    def test_skip_non_user_messages(self) -> None:
        """system / assistant 消息含统计词不该触发 (那不是员工要求)"""
        msgs = [
            {"role": "system", "content": "统计一下数据..."},
            {"role": "user", "content": "登录 EIS"},
        ]
        assert not has_stats_intent(msgs)


# ============================================================
# inject_stats_guard
# ============================================================


class TestInjectStatsGuard:
    def test_no_stats_intent_unchanged(self) -> None:
        msgs = [
            {"role": "system", "content": "你是小鲶"},
            {"role": "user", "content": "你好"},
        ]
        assert inject_stats_guard(msgs) == msgs

    def test_stats_intent_appends_to_system(self) -> None:
        msgs = [
            {"role": "system", "content": "你是小鲶"},
            {"role": "user", "content": "这表多少行"},
        ]
        result = inject_stats_guard(msgs)
        sys_content = result[0]["content"]
        assert "你是小鲶" in sys_content
        assert "execute_code" in sys_content
        assert "必须" in sys_content
        # user message 没动
        assert result[1] == msgs[1]

    def test_no_system_no_inject(self) -> None:
        """跟 inject_identity / inject_session_facts 一致, 没 system 不强加"""
        msgs = [{"role": "user", "content": "多少行"}]
        assert inject_stats_guard(msgs) == msgs

    def test_appends_to_last_system_when_multiple(self) -> None:
        msgs = [
            {"role": "system", "content": "first"},
            {"role": "system", "content": "second"},
            {"role": "user", "content": "总数是多少"},
        ]
        result = inject_stats_guard(msgs)
        assert result[0]["content"] == "first"  # 不动
        assert "execute_code" in result[1]["content"]  # 第二个 system 被增强

    def test_multimodal_system_appends_text_part(self) -> None:
        msgs = [
            {
                "role": "system",
                "content": [{"type": "text", "text": "你是小鲶"}],
            },
            {"role": "user", "content": "统计一下"},
        ]
        result = inject_stats_guard(msgs)
        sys_content = result[0]["content"]
        assert isinstance(sys_content, list)
        assert len(sys_content) == 2
        assert "execute_code" in sys_content[1]["text"]

    def test_does_not_mutate_input(self) -> None:
        original = [
            {"role": "system", "content": "original"},
            {"role": "user", "content": "多少条"},
        ]
        snapshot = [dict(m) for m in original]
        inject_stats_guard(original)
        assert [dict(m) for m in original] == snapshot
        assert original[0]["content"] == "original"
