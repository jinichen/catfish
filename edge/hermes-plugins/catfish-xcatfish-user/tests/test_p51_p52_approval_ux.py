"""P51 / P52 审批好用: 回答按意思认、认不出不打断、提示大白话 + 回 1 执行 / 2 不要。

10/13 鸿波定 A: 文字审批渠道**只给单次批准** —— 微信会话长寿, 「本会话都允许」实际等于
长期放行。所以「都允许」「本次会话」「session」也只算单次。

不 import hermes: 测 parse / format / wrap_* 工厂, 用假 runner / adapter。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import plugin_approval as p14
import plugin_approval_ux as ux


# ── 回答解析 ────────────────────────────────────────────────────────────

def test_the_1013_reply_that_used_to_interrupt_is_understood_as_once():
    # 10/13 截图: 提示要「/批准 本次会话」, 员工打的是「/审批本次会话」→ 原来打断任务
    assert ux.parse_approval_reply("/审批本次会话") == "approve"


def test_numbers_and_casual_chinese():
    for s in ("1", "１", "一", "好", "好的", "行", "可以", "同意", "批准", "/批准", "执行吧",
              "ok", "OK", "yes", "嗯", "没问题", "👍", "/批准 执行"):
        assert ux.parse_approval_reply(s) == "approve", s
    for s in ("2", "3", "不要", "/拒绝", "拒绝", "取消", "算了", "no", "deny", "别执行", "👎"):
        assert ux.parse_approval_reply(s) == "deny", s


def test_session_or_always_wording_is_only_once():
    # 不存在会话级 / 永久批准: 这些都只批这一次
    for s in ("都允许", "这次会话都可以", "/批准 本次会话", "/批准本次会话", "以后都行",
              "always", "session", "总是允许"):
        assert ux.parse_approval_reply(s) == "approve", s


def test_negation_never_reads_as_approval():
    # 「不同意」含「同意」、「不对」含「对」、「不可以」含「可以」—— 必须是拒绝
    for s in ("不同意", "不对", "不可以", "不行", "不执行", "别", "没必要", "不批"):
        assert ux.parse_approval_reply(s) == "deny", s
    # 否定字开头但意思是同意的口语
    for s in ("没问题", "不错", "没事"):
        assert ux.parse_approval_reply(s) == "approve", s


def test_unrelated_or_long_text_is_not_an_answer():
    assert ux.parse_approval_reply("今天天气怎么样") is None
    assert ux.parse_approval_reply("") is None
    assert ux.parse_approval_reply(None) is None
    # 长句 = 员工在交代别的事, 不硬解析 (哪怕里面有「可以」)
    assert ux.parse_approval_reply("可以先别跑这个吗我想先看看报价单的格式对不对") is None


def test_full_width_numerals_and_punctuation():
    assert ux.parse_approval_reply(" 1。") == "approve"
    assert ux.parse_approval_reply("／拒绝！") == "deny"


# ── 审批提示 ────────────────────────────────────────────────────────────

CODE = "execute_code <<'PY'\nimport glob\n# 找 weixin 平台适配器的源码位置\nprint(glob.glob('*'))\nPY"


def test_prompt_is_plain_language_with_two_choices():
    msg = ux.format_approval_prompt(CODE, "execute_code script execution ...")
    assert "要做的事: 找 weixin 平台适配器的源码位置" in msg
    assert "回复 1 执行 · 2 不要执行" in msg
    assert "都允许" not in msg and "会话" not in msg and "3 " not in msg     # 不提供会话级批准
    assert "/批准" not in msg and "/approve" not in msg                      # 不再要人背斜杠口令
    # 10/13 鸿波「非程序员对代码不感兴趣」: 不展示代码
    assert "glob" not in msg and "```" not in msg and "PY" not in msg


def test_prompt_stays_one_bubble_in_wechat():
    # hermes weixin.py: 2~6 行、每行像聊天的短消息会被拆成一串气泡 (10/13 第二次审批被埋);
    # 首行像标题 (≤24 字且以冒号结尾) 就不拆。这里按它的判据钉住首行。
    for cmd in (CODE, "rm -rf /tmp/x"):
        first = ux.format_approval_prompt(cmd, "x").splitlines()[0].strip()
        assert first.endswith(("：", ":")) and len(first) <= 24, first


def test_prompt_without_comment_and_for_terminal_commands():
    msg = ux.format_approval_prompt("execute_code <<'PY'\nprint(1)\nPY", "x")
    assert "代码里没写说明" in msg
    msg = ux.format_approval_prompt("rm -rf /tmp/x", "recursive delete")
    assert "运行一条命令" in msg and "原因: recursive delete" in msg and "命令: rm -rf /tmp/x" in msg


def test_coding_pragma_is_not_taken_as_the_description():
    # 10/13 实测「要做的事: -*- coding: utf-8 -*-」
    msg = ux.format_approval_prompt(
        "execute_code <<'PY'\n# -*- coding: utf-8 -*-\n# 出福富资质台账\nimport openpyxl\nPY", "x")
    assert "要做的事: 出福富资质台账" in msg and "coding" not in msg


def test_long_code_never_bloats_the_message():
    msg = ux.format_approval_prompt("execute_code <<'PY'\n" + "x = 1\n" * 200 + "PY", "x")
    assert len(msg) < 200 and "x = 1" not in msg
    msg = ux.format_approval_prompt("echo " + "a" * 500, "x")
    assert len(msg) < 300 and "…" in msg


def test_wrap_format_keeps_smart_denied_and_falls_back_on_error(monkeypatch):
    def orig(command, description, prefix, **kw):
        return "ORIGINAL"

    f = ux.wrap_format(orig)
    assert f(CODE, "d", "/", smart_denied=True) == "ORIGINAL"          # 管理员单次放行: 原文
    assert "回复 1 执行" in f(CODE, "d", "/", allow_session=True, allow_permanent=True)
    monkeypatch.setattr(ux, "format_approval_prompt", lambda *a, **k: 1 / 0)
    assert f(CODE, "d", "/") == "ORIGINAL"                              # 出错不能让审批发不出去


# ── busy handler ──────────────────────────────────────────────────────

class _Adapter:
    def __init__(self):
        self.sent = []

    async def _send_with_retry(self, **kw):
        self.sent.append(kw["content"])


class _Runner:
    def __init__(self):
        self.adapter = _Adapter()

    def _adapter_for_source(self, source):
        return self.adapter

    def _reply_anchor_for_event(self, event):
        return None

    def _thread_metadata_for_source(self, source, anchor):
        return {}


def _event(text, control=True):
    return SimpleNamespace(text=text, allow_gateway_control=control,
                           source=SimpleNamespace(chat_id="c1"))


def _drive(text, pending=True, control=True):
    seen = []

    async def orig(self, event, session_key):
        seen.append(event.text)
        return "ORIG"

    runner = _Runner()
    h = ux.wrap_busy_handler(orig, lambda key: pending)
    result = asyncio.run(h(runner, _event(text, control), "sess-1"))
    return result, seen, runner.adapter.sent


def test_understood_reply_is_rewritten_for_hermes_and_never_as_session():
    assert _drive("/审批本次会话") == ("ORIG", ["approve"], [])
    assert _drive("好的") == ("ORIG", ["approve"], [])
    assert _drive("2") == ("ORIG", ["deny"], [])
    assert _drive("不同意") == ("ORIG", ["deny"], [])
    # hermes 原版认 "session" / "always" —— 我们绝不改写成它们
    for s in ("session", "always", "都允许"):
        assert _drive(s)[1] == ["approve"], s


def test_short_unclear_reply_gets_hint_and_does_not_interrupt(monkeypatch):
    monkeypatch.setattr(ux, "_LAST_SUMMARY", None)
    monkeypatch.setattr(ux, "_RESOLVED_AT", {})
    result, seen, sent = _drive("啥意思")
    assert result is True and seen == [] and sent == [ux._HINT]
    assert "1 执行" in ux._HINT and "2 不要" in ux._HINT and "会话" not in ux._HINT


def test_hint_says_what_is_waiting_and_that_the_last_1_went_through(monkeypatch):
    # 10/13 14:38: 回 1 批了第一个 → 第二个审批被埋 → 「我不是回了1，你没收到吗？」
    monkeypatch.setattr(ux, "_RESOLVED_AT", {})
    ux.format_approval_prompt("execute_code <<'PY'\n# 出福富资质台账\nimport os\nos.remove('x')\nPY", "x")
    assert _drive("1")[1] == ["approve"]                     # 第一个: 批了, 记下时间
    _, seen, sent = _drive("我不是回了1，你没收到吗？")           # 第二个在等
    assert seen == []
    assert sent[0].startswith("你刚才回的已经收到并执行了")
    assert "出福富资质台账" in sent[0] and "回复 1 执行 · 2 不要" in sent[0]


def test_long_reply_goes_to_original_path():
    text = "先别管这个了帮我把达远智能的报价单再出一版打八五折"
    assert _drive(text) == ("ORIG", [text], [])


def test_nothing_changes_without_a_pending_approval():
    assert _drive("1", pending=False) == ("ORIG", ["1"], [])
    assert _drive("好", control=False) == ("ORIG", ["好"], [])


def test_empty_text_such_as_photo_is_not_swallowed():
    assert _drive("") == ("ORIG", [""], [])


# ── P14 斜杠命令那条路: 同样只给单次 ──────────────────────────────────

def test_p14_aliases_never_grant_session_or_permanent():
    targets = set(p14._APPROVE_ALIASES.values())
    assert targets <= {"/approve", "/deny"}, targets
    for k in ("/批准 本次会话", "/批准本次会话", "本次会话", "总是批准", "/approve session", "/approve always"):
        assert p14._APPROVE_ALIASES[k] == "/approve", k
