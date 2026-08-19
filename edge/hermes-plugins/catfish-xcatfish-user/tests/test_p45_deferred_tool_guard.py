"""P45: 被 defer 的工具不许被模糊改名成另一个工具 —— 不变式测试。

8/19 实盘: 鸿波说「固化 eis-login SKILL」, 小鲶连发六次 catfish_browser_fill,
参数是 freeze_skill 的。看着像模型犯傻, 其实是 repair_tool_call 在**可见**列表里
模糊匹配了一个**被 defer** 的名字。实算 (只在 P43 那 11 个可见工具里比):

    catfish_teach_start   → catfish_search_docs   0.872
    catfish_freeze_skill  → catfish_browser_fill  0.800

cutoff 是 0.7, 而 catfish 工具名共享 28 字符前缀 `mcp__catfish_tools__catfish_`,
这个阈值在这种名字上形同虚设。

这次改动的失效方式全是**静默**的, 所以每条都要钉:

  1. 判据太宽 → 把幻觉名字也拦了, 真的拼写错误不再被修
  2. 判据太窄 → 只挡住某几个名字, 换个工具照样被改
  3. 大小写/连字符这种"同一个名字"被误拦 → 上游本来能修的不修了
  4. 错误消息没指向 tool_call → 模型知道调不到, 但不知道该怎么调
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import plugin_deferred_tool_guard as g  # noqa: E402

P = "mcp__catfish_tools__catfish_"


class FakeAgent:
    def __init__(self, visible):
        self.valid_tool_names = set(visible)


# 线上 P43 提升过、因而可见的 11 个
VISIBLE = {P + n for n in [
    "wiki_search", "search_docs", "today_summary", "email_search",
    "read_tool_archive", "browser_goto", "browser_snapshot", "browser_click",
    "browser_fill", "browser_find_by_text", "recognize_captcha",
]} | {"read_file", "write_file", "execute_code", "tool_call", "tool_describe"}


@pytest.fixture
def patched(monkeypatch):
    """装上 patch, 并把 is_deferrable_tool_name 换成可控的桩。

    桩的语义**照抄上游**: 没注册 → False; core → False; 已注册的 MCP/插件 → True。
    """
    # ⚠ 桩必须**忠于上游语义**: is_deferrable_tool_name 只看 registry + core,
    #   **完全不看可见性**。第一版我把 `n not in VISIBLE` 写进桩里, 结果它替真代码
    #   挡掉了 `if name in valid: return False` 那道早退 —— 变异删掉早退, 测试照样
    #   绿 (M5)。桩比真事窄一格, 测的就是桩不是代码。
    CORE = {P + n for n in [                     # P43 提升进 _HERMES_CORE_TOOLS 的
        "wiki_search", "search_docs", "today_summary", "email_search",
        "read_tool_archive", "browser_goto", "browser_snapshot", "browser_click",
        "browser_fill", "browser_find_by_text", "recognize_captcha",
    ]}
    registered_mcp = {P + n for n in [
        "teach_start", "teach_end", "freeze_skill", "browser_locate",
        "skill_publish", "wiki_publish",
    ]} | CORE

    fake_ts = type(sys)("tools.tool_search")
    fake_ts.TOOL_CALL_NAME = "tool_call"
    fake_ts.is_deferrable_tool_name = lambda n: (
        n in registered_mcp and n not in CORE and n not in {"tool_call", "tool_describe"}
    )
    monkeypatch.setitem(sys.modules, "tools.tool_search", fake_ts)
    monkeypatch.setitem(sys.modules, "tools", type(sys)("tools"))

    calls = {}

    def orig_repair(agent, name):
        """桩: 模拟上游 —— 精确命中就返, 否则模糊匹配可见列表。"""
        from difflib import get_close_matches
        calls["last"] = name
        low = name.lower()
        if low in agent.valid_tool_names:
            return low
        hit = get_close_matches(low, agent.valid_tool_names, n=1, cutoff=0.7)
        return hit[0] if hit else None

    fake_arh = type(sys)("agent.agent_runtime_helpers")
    fake_arh.repair_tool_call = orig_repair
    fake_cl = type(sys)("agent.conversation_loop")
    fake_cl._invalid_tool_name_error_content = (
        lambda name, valid: f"Tool '{name}' does not exist. Available tools: x"
    )
    fake_agent_pkg = type(sys)("agent")
    fake_agent_pkg.agent_runtime_helpers = fake_arh
    fake_agent_pkg.conversation_loop = fake_cl
    monkeypatch.setitem(sys.modules, "agent", fake_agent_pkg)
    monkeypatch.setitem(sys.modules, "agent.agent_runtime_helpers", fake_arh)
    monkeypatch.setitem(sys.modules, "agent.conversation_loop", fake_cl)

    g.install()
    return fake_arh, fake_cl


# ── 1. 核心不变式: 被 defer 的不许被改成别的工具 ────────────────────

@pytest.mark.parametrize("bad,would_become", [
    ("teach_start", "search_docs"),
    ("teach_end", "search_docs"),
    ("freeze_skill", "browser_fill"),
    ("browser_locate", "browser_click"),
    ("skill_publish", "wiki_search"),
])
def test_被defer的工具不会被改成另一个(patched, bad, would_become):
    arh, _ = patched
    agent = FakeAgent(VISIBLE)
    name = P + bad
    # 先确认桩里"上游会改错"这件事是真的 —— 否则这条测试什么都没测
    from difflib import get_close_matches
    hit = get_close_matches(name.lower(), VISIBLE, n=1, cutoff=0.7)
    assert hit, f"{bad} 在桩里根本不会被模糊匹配, 这条用例失效了"
    # 打了 patch 之后必须返 None
    assert arh.repair_tool_call(agent, name) is None


def test_没注册的幻觉名字照常被修(patched):
    """判据不能太宽。真的拼写错误 (未注册) 该修还得修。"""
    arh, _ = patched
    agent = FakeAgent(VISIBLE)
    # 未注册 → is_deferrable 返 False → 走上游原逻辑
    got = arh.repair_tool_call(agent, P + "browser_fil")   # 少一个 l
    assert got == P + "browser_fill"


def test_可见工具的大小写写错照常被修(patched):
    """P43 提升过的工具是 core, 不该被这个 patch 挡住。

    大小写变体在 registry 里查不到 → is_deferrable 返 False → 走上游原逻辑。
    """
    arh, _ = patched
    agent = FakeAgent(VISIBLE)
    got = arh.repair_tool_call(agent, (P + "browser_fill").upper())
    assert got == P + "browser_fill"


def test_守卫启动时上游给的一定是另一个工具(patched):
    """钉住"不需要同名放行分支"这个前提。

    第一版代码里有一层 `_norm_for_compare(repaired)==_norm_for_compare(name)`
    的放行, 变异测试打它跑绿 —— 那个分支到不了。前提是: 守卫只在名字**精确**
    命中 registry 且不可见时启动, 而上游几段精确匹配找的都是 valid_tool_names,
    必然落空。这条测试把这个前提钉住, 前提变了就红。
    """
    from difflib import get_close_matches
    agent = FakeAgent(VISIBLE)
    for bad in ["teach_start", "freeze_skill", "skill_publish"]:
        name = P + bad
        assert g._is_deferred_but_registered(agent, name), f"{bad} 该被守卫认领"
        hit = get_close_matches(name.lower(), VISIBLE, n=1, cutoff=0.7)
        assert hit and hit[0] != name, (
            f"上游对 {bad} 给出的是 {hit} —— 如果哪天它能给出同名变体, "
            "就要重新考虑加回同名放行分支"
        )


def test_已经可见的工具不受影响(patched):
    arh, _ = patched
    agent = FakeAgent(VISIBLE)
    n = P + "wiki_search"
    assert arh.repair_tool_call(agent, n) == n


# ── 2. 错误消息要指向 tool_call ──────────────────────────────────

def test_被defer时错误消息指向tool_call(patched):
    _, cl = patched
    msg = cl._invalid_tool_name_error_content(P + "freeze_skill", VISIBLE)
    assert P + "freeze_skill" in msg
    # ★ 判据要钉到"说清用哪个机制", 不能只断言 tool_call 这个词出现过 ——
    #   下面那行例子里本来就有它, 只断言子串的话"把说明句删掉"这个变异跑绿。
    assert "invoke it via `tool_call`" in msg, msg
    assert 'tool_call(name="' in msg, "还要给一个能照抄的调用形式"
    # ★ 不能说 "does not exist" —— 它存在, 只是这轮没发给它。
    #   8/19 模型就是照着这句话去列表里另找一个的。
    assert "does not exist" not in msg
    assert "exists but was not sent" in msg
    # 明确禁止替换, 否则模型还是会去挑一个看得见的
    assert "Do NOT substitute" in msg


def test_普通的不存在工具还是走原消息(patched):
    _, cl = patched
    msg = cl._invalid_tool_name_error_content("totally_made_up", VISIBLE)
    assert "does not exist" in msg
    assert "tool_call" not in msg


# ── 3. 幂等 / 容错 (patch 装两次、依赖缺失都不能炸) ────────────────

def test_装两次是noop(patched):
    arh, _ = patched
    first = arh.repair_tool_call
    g.install()
    assert arh.repair_tool_call is first, "重复装载不该再包一层"


def test_tool_search模块不存在时退回上游行为(monkeypatch):
    """老版本 hermes 没有 progressive disclosure —— 不能因此崩, 也不能乱拦。"""
    agent = FakeAgent(VISIBLE)
    monkeypatch.setitem(sys.modules, "tools", type(sys)("tools"))
    monkeypatch.delitem(sys.modules, "tools.tool_search", raising=False)
    assert g._is_deferred_but_registered(agent, P + "freeze_skill") is False


def test_tool_search关掉时不误拦(patched):
    """`tools.tool_search: {enabled: off}` → 所有 MCP 工具都可见。

    这时 is_deferrable_tool_name 对它们仍然返 True (它不看可见性), 全靠
    `if name in valid: return False` 那道早退挡住。少了它, 一个**明明发给了模型**
    的工具会被当成"被 defer", 拒绝修复它的大小写笔误。
    """
    agent = FakeAgent(VISIBLE | {P + "freeze_skill", P + "teach_start"})
    assert g._is_deferred_but_registered(agent, P + "freeze_skill") is False
    assert g._is_deferred_but_registered(agent, P + "teach_start") is False


def test_agent没有valid_tool_names时不炸(patched):
    class Bare:
        pass
    assert g._is_deferred_but_registered(Bare(), P + "freeze_skill") in (True, False)
