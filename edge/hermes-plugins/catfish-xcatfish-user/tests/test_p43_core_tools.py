"""P43: catfish 高频工具提升为 hermes 核心 —— 不变式测试。

这次改动的三个失效方式都是**静默**的, 所以每一条都要有测试钉住:

  1. 名字前缀写错 → patch 加进去的名字永远匹配不上真实注册名, 工具照样被 defer,
     而日志会照常打印 "P43 ✓ 4 个工具提升为核心"。
  2. 用 extend 而不是重新绑定 → catfish 工具混进 hermes 的 CLI / cron /
     telegram toolset, 而且 discord 因为用了 `+` 不跟着变, 结果不一致。
  3. 两处名单不同步 → 只改一处就等于没改 (见 tools_sanitizer_constants.py
     里 always-on 那段注释)。

前两条历史上都真发生过: `MCP_CATFISH_PREFIX` 单/双下划线写错, 从 5/25 写下来
一次都没匹配过; 而它本身就是为了修「always-on 匹配不上 MCP 包装名」而加的。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import plugin_core_tools as pct  # noqa: E402


# ── 前缀必须跟 hermes 的约定一致 ─────────────────────────────────

def test_prefix_uses_double_underscore_delimiter():
    """hermes 的 MCP 注册名是 mcp__<server>__<tool>, 两处都是双下划线。

    单下划线是历史上真踩过的坑 —— 写错了 patch 不报错, 只是永远不生效。
    """
    assert pct._MCP_PREFIX.startswith("mcp__"), pct._MCP_PREFIX
    body = pct._MCP_PREFIX[len("mcp__"):]
    assert body.endswith("__"), f"server 段后面要接双下划线: {pct._MCP_PREFIX}"
    assert "-" not in pct._MCP_PREFIX, (
        "server 名里的 `-` 要按 hermes 的 sanitize_mcp_name_component 换成 `_`"
    )


def test_promoted_names_are_fully_prefixed():
    """必须写 MCP 全名。裸名在 classify_tools 里匹配不上 —— 全链路不剥前缀。"""
    assert pct.PROMOTED_TOOL_NAMES, "提升名单不能为空"
    for full in pct.PROMOTED_TOOL_NAMES:
        assert full.startswith(pct._MCP_PREFIX), full
        bare = full[len(pct._MCP_PREFIX):]
        assert bare.startswith("catfish_"), f"{full} 剥完前缀应是 catfish_*"


def test_reminders_reader_is_promoted_directly():
    """系统待办是员工高频入口，不能要求模型先猜到 tool_search。"""
    assert (
        f"{pct._MCP_PREFIX}catfish_list_reminders"
        in pct.PROMOTED_TOOL_NAMES
    )


def test_reminders_writer_is_promoted_directly():
    """创建提醒也必须直接可见，防止模型只写会话 todo 后假报成功。"""
    assert (
        f"{pct._MCP_PREFIX}catfish_create_reminder"
        in pct.PROMOTED_TOOL_NAMES
    )


def test_email_handoff_readers_are_promoted_directly():
    """邮件交接给出的 email_id 必须能在当前终端直接读全文和附件。"""
    for tool in ("catfish_email_read", "catfish_email_attachment"):
        assert f"{pct._MCP_PREFIX}{tool}" in pct.PROMOTED_TOOL_NAMES


def test_prefix_matches_hermes_convention_if_hermes_present():
    """能读到 hermes 源码时, 直接跟它的 MCP_TOOL_NAME_PREFIX 对。

    hermes 没装就跳过 —— 这条是"能验就验", 不是硬依赖。
    """
    mcp_tool = Path.home() / ".hermes/hermes-agent/tools/mcp_tool.py"
    if not mcp_tool.exists():
        pytest.skip("本机没有 hermes-agent 源码")
    src = mcp_tool.read_text(encoding="utf-8", errors="replace")
    m = re.search(r'MCP_TOOL_NAME_PREFIX\s*=\s*"([^"]+)"', src)
    assert m, "hermes 里找不到 MCP_TOOL_NAME_PREFIX —— 上游可能改了命名机制"
    assert pct._MCP_PREFIX.startswith(m.group(1)), (
        f"hermes 用 {m.group(1)!r}, 我们拼的是 {pct._MCP_PREFIX!r} —— 对不上"
    )


# ── patch 必须重新绑定, 不能原地改 ───────────────────────────────

class _FakeToolsets:
    """最小替身: 复现 toolsets.py 里「5 个 toolset 持同一个 list 对象」的结构。"""

    def __init__(self):
        self._HERMES_CORE_TOOLS = ["read_file", "write_file", "memory"]
        # 464-487 行那几个 toolset 定义, 模块加载时就求值, 持有同一对象
        self.TOOLSETS = {
            "hermes-cli": {"tools": self._HERMES_CORE_TOOLS},
            "hermes-cron": {"tools": self._HERMES_CORE_TOOLS},
        }


def _run_patch_against(fake, monkeypatch):
    monkeypatch.setitem(sys.modules, "toolsets", fake)
    pct._patch_p43_promote_catfish_core_tools()


def test_patch_rebinds_and_does_not_mutate_shared_list(monkeypatch):
    """核心不变式: `_HERMES_CORE_TOOLS` 换成新对象, 那几个 toolset 不受影响。

    用 extend 的话这条会红 —— 而 extend 的后果是 catfish 工具混进 hermes 的
    CLI / cron toolset, 那是完全不同的执行路径。
    """
    fake = _FakeToolsets()
    original_obj = fake._HERMES_CORE_TOOLS
    original_len = len(original_obj)

    _run_patch_against(fake, monkeypatch)

    assert fake._HERMES_CORE_TOOLS is not original_obj, "必须重新绑定, 不能原地改"
    assert len(original_obj) == original_len, (
        "原 list 被改了 —— hermes-cli / hermes-cron 会跟着变"
    )
    for ts in fake.TOOLSETS.values():
        assert len(ts["tools"]) == original_len, f"toolset 被污染: {ts['tools']}"
    for full in pct.PROMOTED_TOOL_NAMES:
        assert full in fake._HERMES_CORE_TOOLS


def test_patch_is_idempotent(monkeypatch):
    fake = _FakeToolsets()
    _run_patch_against(fake, monkeypatch)
    after_first = list(fake._HERMES_CORE_TOOLS)
    _run_patch_against(fake, monkeypatch)
    assert fake._HERMES_CORE_TOOLS == after_first, "重复 install 不该重复追加"


def test_patch_bails_out_when_structure_changed(monkeypatch):
    """hermes 哪天把 _HERMES_CORE_TOOLS 换成 frozenset —— 不硬改, 记日志退出。"""
    class _Changed:
        _HERMES_CORE_TOOLS = frozenset({"read_file"})

    fake = _Changed()
    monkeypatch.setitem(sys.modules, "toolsets", fake)
    pct._patch_p43_promote_catfish_core_tools()          # 不该抛
    assert fake._HERMES_CORE_TOOLS == frozenset({"read_file"}), "结构不认识时不该动它"


# ── 两处名单必须同步 ─────────────────────────────────────────────

def _gateway_constants_src() -> str | None:
    here = Path(__file__).resolve()
    for parent in here.parents:
        p = (parent / "central/llm-gateway/src/catfish_gateway"
                     / "tools_sanitizer_constants.py")
        if p.exists():
            return p.read_text(encoding="utf-8")
    return None


def _gateway_sanitizer_src() -> str | None:
    here = Path(__file__).resolve()
    for parent in here.parents:
        p = parent / "central/llm-gateway/src/catfish_gateway/tools_sanitizer.py"
        if p.exists():
            return p.read_text(encoding="utf-8")
    return None


def test_提升名单必须含catfish_browser否则BLFIX4是死的():
    """★★★ 8/13 → 8/17 静默失效四天的那件事。

    gateway BL-FIX4 (5/8) 的规矩是"tools 里出现 catfish_browser_* 就丢掉
    hermes 自带的 12 个 browser_*", 两族不并存。它的判据是
    `_has_catfish_browser_tools(tools)` —— **扫入参数组**。

    8/13 tool_search 上线, 所有 catfish_* 默认被 defer, 数组里一个
    catfish_browser_* 都没有 → 判据恒为 False → 去重再没触发过。

    没有任何报错。去重不触发只表现为"少丢了点东西": 每轮多 6,872 token,
    而且跑内网系统用的是没打过 FIX3/FIX9 补丁的 hermes 那一族。

    这条钉住那个耦合: **只要 gateway 还拿"数组里有没有"当判据, 提升名单里
    就必须至少留一个 catfish_browser_*。** 两个条件一起查 —— 哪天 gateway
    改成看可达性了, 这条会因为前半截失效而提醒你回来删掉它。
    """
    src = _gateway_sanitizer_src()
    if src is None:
        pytest.skip("同 checkout 下找不到 gateway sanitizer")

    uses_array_probe = "_has_catfish_browser_tools(tools)" in src
    if not uses_array_probe:
        pytest.skip(
            "gateway 已经不用「扫数组」当 BL-FIX4 判据了 —— 这条测试的前提没了, "
            "回来确认新判据是什么, 然后删掉或改写本条"
        )

    promoted_browser = [n for n in pct.PROMOTED_TOOL_NAMES if "catfish_browser_" in n]
    assert promoted_browser, (
        "提升名单里一个 catfish_browser_* 都没有, 而 gateway 的 BL-FIX4 判据是"
        "「入参数组里有没有 catfish_browser_*」—— tool_search 会把没提升的全 defer, "
        "于是那条去重恒不触发, hermes 自带的 12 个 browser_* 白占 6,872 token/轮。"
        "这正是 8/13 到 8/17 静默失效的四天。"
    )


def test_gateway_always_on_covers_promoted_tools():
    """P43 提升的工具必须同时进 gateway 的 ALWAYS_ON_TOOLS。

    只改一处的后果:
      · 只改 P43   → 工具到得了 gateway, 但名额紧时会被 cap 砍
      · 只改 gateway → 仍被 tool_search defer, 压根到不了 gateway
    两种都表现为"改了没效果"。
    """
    src = _gateway_constants_src()
    if src is None:
        pytest.skip("同 checkout 下找不到 gateway 常量文件")
    m = re.search(r"ALWAYS_ON_TOOLS: frozenset\[str\] = frozenset\(\{(.*?)\n\}\)",
                  src, re.S)
    assert m, "ALWAYS_ON_TOOLS 结构变了"
    always_on = set(re.findall(r'^\s*"([a-z_0-9]+)"', m.group(1), re.M))
    missing = [
        full[len(pct._MCP_PREFIX):]
        for full in pct.PROMOTED_TOOL_NAMES
        if full[len(pct._MCP_PREFIX):] not in always_on
    ]
    assert not missing, f"这些 P43 提升的工具不在 gateway ALWAYS_ON 里: {missing}"


def test_gateway_mcp_prefix_matches_ours():
    """gateway 剥前缀用的常量必须跟这里拼出来的一致, 否则 is_always_on 恒 False。"""
    src = _gateway_constants_src()
    if src is None:
        pytest.skip("同 checkout 下找不到 gateway 常量文件")
    m = re.search(r'MCP_CATFISH_PREFIX = "([^"]+)"', src)
    assert m, "MCP_CATFISH_PREFIX 不见了"
    assert m.group(1) == pct._MCP_PREFIX, (
        f"gateway 侧是 {m.group(1)!r}, plugin 侧是 {pct._MCP_PREFIX!r} —— "
        "对不上的话 always-on 对 MCP 包装名全部失效 (5/25 就是这么坏的)"
    )


def test_gateway_cap_leaves_room_for_promoted_tools():
    """cap 要放得下 29 个 hermes 核心 + 3 个 bridge + 提升的这几个。

    29 / 3 是 8/10 shape dump 的实测值 (n_tools=32)。cap 不够的话, 提升进来的
    工具会在 gateway 被砍掉 —— 又是一次"改了没效果"。
    """
    src = _gateway_constants_src()
    if src is None:
        pytest.skip("同 checkout 下找不到 gateway 常量文件")
    m = re.search(r"^DEFAULT_MAX_TOOLS = (\d+)", src, re.M)
    assert m, "DEFAULT_MAX_TOOLS 不见了"
    cap = int(m.group(1))

    # 8/17: 提升 catfish_browser_* 之后这个式子要多算一步。
    #
    # BL-FIX4 (tools_sanitizer.py:309 判据) 一看到数组里有 catfish_browser_*
    # 就丢掉 hermes 自带的 12 个 browser_*, 而**那一步在 cap 之前**
    # (丢弃在 :365, cap 在 :474)。所以真正到 cap 那一步的比到达 gateway 的少 12。
    #
    # 不把这一步算进来的话, 这条会在"其实放得下"的时候误报 —— 8/17 加 6 个
    # 浏览器工具时就是这么红的 (29+3+11=43 > 40, 而真实是 31)。
    _HERMES_BROWSER = 12   # api-server profile 里的 browser_* 个数, 见
                           # ~/.hermes/hermes-agent/toolsets.py 的 hermes-api-server
    promoted_browser = [n for n in pct.PROMOTED_TOOL_NAMES if "catfish_browser_" in n]

    need = 29 + 3 + len(pct.PROMOTED_TOOL_NAMES)
    detail = f"29 hermes 核心 + 3 bridge + {len(pct.PROMOTED_TOOL_NAMES)} 个提升的"
    if promoted_browser:
        need -= _HERMES_BROWSER
        detail += f" − {_HERMES_BROWSER} 个被 BL-FIX4 丢掉的 hermes browser_*"
    assert cap >= need, f"cap={cap} 放不下 {need} 个 ({detail})"
