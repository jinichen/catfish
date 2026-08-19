"""从上游返回里取 OCR 答案的判据。

8/19 实撞: 员工登 EIS, 验证码连撞三次, 界面说"识别不了"。真相是上游**一个字
答案都没返** —— vision 角色指向自建 Qwen3-VL, 默认开思考, 而这里发的是
`max_tokens: 30`(注释写着"验证码很短" —— 短的是答案不是生成过程), 思考把额度
吃光, `content` 回来是空串。

错误消息当时说的是「识别置信度低 (text='' confidence=0.00)」, 把人往"图片太糊"
上带, 查了两轮才落到这儿。所以这里钉两件事:

  1. content 空但 reasoning 有 → 认成"没返答案", **不是**"认不出"
  2. 诊断消息里要说得出是哪一种, 别再糊成一句"置信度低"
"""
from __future__ import annotations

import pytest

from catfish_tool_bridge.recognize_captcha import _extract_answer, _estimate_confidence


def ans(msg: dict) -> str:
    return _extract_answer(msg)[0]


def why(msg: dict) -> str:
    return _extract_answer(msg)[1]


class Test正常:
    def test_干净的_content(self) -> None:
        assert _extract_answer({"content": "5KBz"}) == ("5KBz", "")

    def test_首尾空白剥掉(self) -> None:
        assert ans({"content": "  cZf3\n"}) == "cZf3"

    def test_content_有值时不看_reasoning(self) -> None:
        # 有答案就用答案 —— reasoning 里那堆话不该有机会污染结果
        assert ans({"content": "W6UV", "reasoning_content": "让我仔细看看这张图"}) == "W6UV"


class Test内联think块:
    def test_剥掉_think_留下答案(self) -> None:
        assert ans({"content": "<think>这几个字符有点糊</think>5KBz"}) == "5KBz"

    def test_带属性的_think_标签也认(self) -> None:
        assert ans({"content": '<think type="x">嗯</think>ab12'}) == "ab12"

    def test_多个_think_块(self) -> None:
        assert ans({"content": "<think>一</think>ab<think>二</think>12"}) == "ab12"

    def test_think_块跨多行_关键(self) -> None:
        # 真实的思考输出**一定是多行的** —— 第一版这里全是单行样本, 于是
        # "把 re.S 去掉"这个变异跑绿了 (M3 没抓到)。单行样本测的是我脑子里
        # 的形状, 不是线上的形状。
        blob = (
            "<think>\n"
            "第一个字符看着像 5, 也可能是 S。\n"
            "第二个是 K, 大写。\n"
            "再看第三、第四…\n"
            "</think>\n"
            "5KBz"
        )
        assert ans({"content": blob}) == "5KBz"

    def test_整段都是_think_剥完为空(self) -> None:
        got, note = _extract_answer({"content": "<think>还没想完</think>"})
        assert got == ""
        assert "只有 <think>" in note


class Test没返答案:
    """★ 这一组就是 8/19 那次的形状。"""

    def test_content_空_reasoning_有(self) -> None:
        got, note = _extract_answer({"content": "", "reasoning_content": "我先看第一个字符…" * 20})
        assert got == ""
        # 必须说清是"没返答案", 而且要点出 max_tokens —— 那才是能动手的地方
        assert "只返了思考没返答案" in note
        assert "max_tokens" in note

    def test_content_是_None(self) -> None:
        got, note = _extract_answer({"content": None, "reasoning_content": "嗯"})
        assert got == ""
        assert "只返了思考没返答案" in note

    def test_两个都空(self) -> None:
        got, note = _extract_answer({"content": "", "reasoning_content": ""})
        assert got == ""
        assert "都是空的" in note

    def test_字段整个缺(self) -> None:
        got, note = _extract_answer({})
        assert got == ""
        assert note

    def test_非字符串类型不炸(self) -> None:
        for bad in [{"content": 42}, {"content": ["a"]}, {"reasoning_content": {"x": 1}},
                    {"content": None, "reasoning_content": None}]:
            got, note = _extract_answer(bad)
            assert got == ""
            assert note, f"没给诊断: {bad}"


class Test诊断跟置信度是两条路:
    """★★ 判据不能混。

    混了的后果就是 8/19: "没返答案"被算成 confidence=0.0, 错误消息说"识别置信度低",
    员工和模型都以为是图片糊, 于是一遍遍换验证码重试 —— 而真正要动的是 max_tokens。
    """

    def test_空答案不该走置信度那条路(self) -> None:
        # _estimate_confidence 对空串确实返 0.0 —— 所以**更**不能靠它区分,
        # 它对"图片糊认不出"和"根本没返回"给的是同一个数。
        assert _estimate_confidence("", None) == 0.0
        assert _estimate_confidence("?", None) == 0.0
        # 区分只能靠 _extract_answer 的第二个返回值
        _, a = _extract_answer({"content": "", "reasoning_content": "长长的思考"})
        _, b = _extract_answer({"content": "?"})
        assert a != b
        assert "max_tokens" in a
        assert "max_tokens" not in b, "'?'是模型明说认不出, 跟额度没关系"

    def test_模型明说认不出时_答案原样传下去(self) -> None:
        # '?' 是 prompt 里约定的"看不清"信号, 它是**有效返回**,
        # 该交给 _estimate_confidence 判 0.0, 不该被当成"没返答案"
        got, note = _extract_answer({"content": "?"})
        assert got == "?"
        assert note == ""
