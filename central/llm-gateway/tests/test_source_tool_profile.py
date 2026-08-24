"""BL-ADVISOR-NATIVE-LEAK (8/24) —— source profile 到底砍没砍东西。

# 为什么这个文件是新建的

`_filter_by_source_profile` 5/22 写下, 到 8/24 为止**一个测试都没有**。它这三个
月里其实什么都没做: 判据 `name.startswith("catfish_")` 认不出真实注册名
`mcp__catfish_tools__catfish_*`, 所有 catfish 工具都从「非 catfish_ 前缀不动」
那条溜过去了。没测试 + 判据错 = 静默空转, 谁也不知道。

8/24 的事故让它现形: advisor 拿着 execute_code 连打 13 次, 上游返 400
"Repetitive tool calls", 员工看到「千问一直出错」。

# 这里每条测试钉的是什么

红线侧 (advisor 是参谋不是代理, CATFISH-ADVISOR-DESIGN.md:55「任何级别都不代行」):
  advisor 手上不许有 execute_code / write_file / patch / process / delegate_task。
  这是**能力边界**, 不是 prompt 约定 —— 跟 catfish_email_create_draft 那边同一个
  思路: 模型手上没这个工具, 想调也调不到。

不误伤侧 (同样重要, 砍过头 advisor 就干不了活了):
  员工正常会话 (companion-chat / unknown) 一个工具都不许少;
  员工自装的 MCP 不许被 catfish profile 砍;
  advisor 自己的业务工具和 always-on 的知识库检索一族必须还在。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from catfish_gateway.tools_sanitizer import (  # noqa: E402
    _filter_by_source_profile,
)
from catfish_gateway.tools_sanitizer_constants import (  # noqa: E402
    ALWAYS_ON_TOOLS,
    DEFERRED_TOOL_BRIDGES,
    MCP_CATFISH_PREFIX,
    SOURCE_NATIVE_TOOLS,
    SOURCE_TOOL_PROFILES,
)

ADVISOR = "companion-advisor"


def _tool(name: str) -> dict:
    return {"type": "function", "function": {"name": name, "parameters": {}}}


def _names(tools: list[dict]) -> set[str]:
    return {t["function"]["name"] for t in tools}


#: 8/24 实盘 advisor 那一发请求里工具的真实形态 —— catfish 侧全是 MCP 包装名
#: (agent.log 查证: catfish_check_compliance 26/26、catfish_draft_email_reply
#: 10/10 都带 mcp__catfish_tools__ 前缀, 裸名一次没出现)。测试用假名字很容易
#: 把这个 bug 测没了, 所以这里照抄真名。
REAL_WORLD_TOOLS = [
    # hermes 原生 —— 事故元凶在这一族
    _tool("execute_code"),
    _tool("write_file"),
    _tool("patch"),
    _tool("process"),
    _tool("delegate_task"),
    _tool("read_file"),
    _tool("search_files"),
    _tool("memory"),
    _tool("todo"),
    _tool("clarify"),
    _tool("web_search"),
    _tool("web_extract"),
    _tool("web_crawl"),
    _tool("skill_view"),
    _tool("skills_list"),
    # hermes 0.20 渐进式披露的三个桥 —— 被 defer 的 67 个 catfish 工具全靠它们
    _tool("tool_search"),
    _tool("tool_describe"),
    _tool("tool_call"),
    # catfish 业务工具 (advisor profile 白名单里的), MCP 包装名
    _tool(f"{MCP_CATFISH_PREFIX}catfish_check_compliance"),
    _tool(f"{MCP_CATFISH_PREFIX}catfish_draft_email_reply"),
    # catfish always-on (知识库检索一族, 8/13 鸿波要求必须能用)
    _tool(f"{MCP_CATFISH_PREFIX}catfish_wiki_search"),
    _tool(f"{MCP_CATFISH_PREFIX}catfish_today_summary"),
    # 不在 advisor profile 白名单里的 catfish 工具
    _tool(f"{MCP_CATFISH_PREFIX}catfish_style_fingerprint_refresh"),
    # 员工自己装的 MCP
    _tool("mcp__github__create_issue"),
]


# ─────────────────────────────────────────────────────────────────────
# 1. 红线: advisor 不许握有"能改变世界"的原生工具
# ─────────────────────────────────────────────────────────────────────
def test_advisor拿不到execute_code():
    """本次事故的正主。

    这条红了 = advisor 又能跑代码了 —— 后台无人值守的参谋握着 shell,
    既撞「任何级别都不代行」红线, 也会重演 8/24 的 400 死循环。
    """
    kept, dropped = _filter_by_source_profile(REAL_WORLD_TOOLS, ADVISOR)
    assert "execute_code" not in _names(kept)
    assert "execute_code" in dropped


@pytest.mark.parametrize(
    "tool_name",
    ["execute_code", "write_file", "patch", "process", "delegate_task",
     "memory", "todo", "read_file", "search_files"],
)
def test_advisor拿不到任何执行或写入类原生工具(tool_name):
    """参谋只出建议。凡是能改变世界 / 能替员工动手的, 一个都不给。

    注意这些**全都在 ALWAYS_ON_TOOLS 里** —— 所以这条测试同时钉死了
    "原生分支必须排在 always-on 之前"。顺序写反的话 always-on 会抢先
    放行, 白名单等于没写。
    """
    assert tool_name in ALWAYS_ON_TOOLS, (
        f"{tool_name} 不在 ALWAYS_ON_TOOLS 里了 —— 这条测试的前提变了, "
        "先确认上游改了什么再改测试"
    )
    kept, _ = _filter_by_source_profile(REAL_WORLD_TOOLS, ADVISOR)
    assert tool_name not in _names(kept)


def test_三条后台source除了桥没有别的原生工具():
    """profile / briefing-card / email-scheduler 都是无人值守批处理, 没有员工在
    屏幕前, 给执行类工具的风险比 advisor 还高。

    桥 (tool_search/describe/call) 是例外 —— 不给桥它们够不着自己的业务工具,
    见 test_业务工具被defer的source必须留着三个桥。桥本身到不了 core 工具。
    """
    for src in ("companion-profile", "companion-briefing-card",
                "companion-email-scheduler"):
        kept, _ = _filter_by_source_profile(REAL_WORLD_TOOLS, src)
        leaked = {
            n for n in _names(kept)
            if not n.startswith(("catfish_", "mcp__")) and n not in DEFERRED_TOOL_BRIDGES
        }
        assert not leaked, f"{src} 漏了原生工具: {leaked}"


# ─────────────────────────────────────────────────────────────────────
# 2. 不误伤: 砍过头 advisor 就干不了活
# ─────────────────────────────────────────────────────────────────────
def test_advisor保留反问和查证能力():
    """clarify = 信息不足时问员工 (而不是瞎猜着给建议);
    web_* = 合规/政治敏感判断要能查政策原文。

    web_* 掉了不只是"少个工具": BL-WEB-ALWAYS-ON (5/25) 记过, 模型拿不到
    web_search 会改用 browser 抓页面, 慢 30 倍贵 30 倍。
    """
    kept, _ = _filter_by_source_profile(REAL_WORLD_TOOLS, ADVISOR)
    got = _names(kept)
    for n in ("clarify", "web_search", "web_extract", "web_crawl"):
        assert n in got, f"advisor 丢了 {n}"


def test_advisor的业务工具和知识库检索都还在():
    """MCP 包装名必须被认出来 —— 这是 bug 1 的正面证据。

    归一化写错的话, 这些包装名会被当成"原生裸名"送进 native 白名单比对,
    一个都不在里面, 全被砍 —— advisor 当场变成空手参谋。
    """
    kept, _ = _filter_by_source_profile(REAL_WORLD_TOOLS, ADVISOR)
    got = _names(kept)
    for n in ("catfish_check_compliance",      # profile 白名单
              "catfish_draft_email_reply",     # profile 白名单
              "catfish_wiki_search",           # always-on (8/13)
              "catfish_today_summary"):        # always-on (5/23)
        assert f"{MCP_CATFISH_PREFIX}{n}" in got, f"advisor 丢了 {n}"


def test_员工自装的MCP不被catfish_profile砍():
    """5/22 原意保留: 那是员工装的 plugin, 不归 catfish profile 管。

    归一化只 strip catfish-tools 自己的前缀 —— 别家的 mcp__ 名字过不了 strip,
    要是没先挡一道, 会被当成"原生裸名"误砍。
    """
    kept, _ = _filter_by_source_profile(REAL_WORLD_TOOLS, ADVISOR)
    assert "mcp__github__create_issue" in _names(kept)


def test_不在表里的source一个工具都不少():
    """员工正常会话走这条 (companion-chat / unknown / plugin:*)。

    这条是本次改动的**爆炸半径上限**: 只要它绿, 改动就碰不到员工日常聊天。
    """
    for src in ("companion-chat", "unknown", "plugin:toolbridge-recmode", ""):
        kept, dropped = _filter_by_source_profile(REAL_WORLD_TOOLS, src)
        assert kept == REAL_WORLD_TOOLS, f"source={src!r} 被动了"
        assert dropped == []


# ─────────────────────────────────────────────────────────────────────
# 3. 归一化: 包装名和裸名必须同命
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("bare", [
    "catfish_check_compliance",            # profile 白名单内 → 都该留
    "catfish_style_fingerprint_refresh",   # 白名单外 → 都该砍
    "catfish_wiki_search",                 # always-on → 都该留
])
def test_MCP包装名与裸名结果一致(bare):
    """bug 1 的直接钉子。

    这两种名字是同一个工具的两种暴露方式, 判决必须相同。老代码里包装名走
    「非 catfish_ 前缀不动」、裸名走白名单比对 —— 同一个工具两种命运,
    而线上**只出现包装名**, 所以白名单实际从未生效。
    """
    kept_bare, _ = _filter_by_source_profile([_tool(bare)], ADVISOR)
    kept_mcp, _ = _filter_by_source_profile(
        [_tool(f"{MCP_CATFISH_PREFIX}{bare}")], ADVISOR,
    )
    assert bool(kept_bare) == bool(kept_mcp), (
        f"{bare} 裸名 kept={bool(kept_bare)} 但包装名 kept={bool(kept_mcp)} —— "
        "同一个工具两种命运, 归一化没做对"
    )


def test_白名单外的catfish工具确实被砍():
    """正面确认白名单**真的在起作用** —— 不是"全放行"混过去的。

    没有这条的话, 把过滤逻辑整个删成 `return tools, []` 上面那些
    "不误伤"测试照样全绿。
    """
    kept, dropped = _filter_by_source_profile(REAL_WORLD_TOOLS, ADVISOR)
    leaked = f"{MCP_CATFISH_PREFIX}catfish_style_fingerprint_refresh"
    assert leaked not in _names(kept), "advisor 不该有画像刷新工具"
    assert leaked in dropped


# ─────────────────────────────────────────────────────────────────────
# 4. 两表一致性: 漏加表项要当场红, 不许静默
# ─────────────────────────────────────────────────────────────────────
def test_两表key必须一一对应():
    """SOURCE_TOOL_PROFILES 里的每条 source, 在 SOURCE_NATIVE_TOOLS 里都必须
    显式出现 (哪怕空集)。

    漏加的后果是静默走 `.get(src, frozenset())` 默认值 —— 把该 source 的原生
    工具全砍。那是"改了没效果"的反面: 改了有**意外**效果, 而且不报错。
    以后加新 source 时这条会拦住。
    """
    assert set(SOURCE_TOOL_PROFILES) == set(SOURCE_NATIVE_TOOLS), (
        f"两表 key 对不上: 只在 profile 表 = "
        f"{set(SOURCE_TOOL_PROFILES) - set(SOURCE_NATIVE_TOOLS)}, "
        f"只在 native 表 = {set(SOURCE_NATIVE_TOOLS) - set(SOURCE_TOOL_PROFILES)}"
    )


def test_桥名单必须正好是hermes那三个():
    """独立钉死三个名字, **不从 DEFERRED_TOOL_BRIDGES 自己派生**。

    为什么单列一条: 下面那条一致性测试拿同一个常量既当"要求"又当"供给"
    (`DEFERRED_TOOL_BRIDGES - native`), 是个自我循环 —— 常量本身漏了一个名字,
    差集照样是空, 测试照样绿。变异实测: 从常量里删掉 tool_call, 23 条全过。

    真事: 少了 tool_call, advisor 能 tool_search 搜到工具、能 tool_describe 看
    schema, 就是**调不动** —— 半截路, 而且不报错。

    名字来源 hermes tools/tool_search.py:59
        BRIDGE_TOOL_NAMES = frozenset({TOOL_SEARCH_NAME, TOOL_DESCRIBE_NAME, TOOL_CALL_NAME})
    """
    assert DEFERRED_TOOL_BRIDGES == {"tool_search", "tool_describe", "tool_call"}


def test_业务工具被defer的source必须留着三个桥():
    """砍了桥 = 那条 source 当场变空手, 而且**测不出来** —— 上面所有"不误伤"
    测试用的都是直接放进 tools 数组的假工具, 全绿。

    真事: hermes 0.20 起 catfish 78 个工具除 P43 提升的 11 个外全被 defer,
    只能走 tool_search → tool_describe → tool_call。advisor 的 8 个业务工具里
    7 个在这一族。

    这条测试跨仓读 P43 的 _PROMOTE 名单 (跟 test_p43_core_tools.py 同款做法):
    profile 白名单里只要有 P43 名单外的工具, 该 source 就必须有桥。以后往
    profile 白名单加新工具时, 这条会替你想起来问一句"它需要桥吗"。
    """
    promote_py = (
        Path(__file__).resolve().parents[3]
        / "edge/hermes-plugins/catfish-xcatfish-user/plugin_core_tools.py"
    )
    if not promote_py.exists():
        pytest.skip(f"跨仓文件不在 (CI 可能只 checkout 了 central/): {promote_py}")

    import re
    src = promote_py.read_text(encoding="utf-8")
    m = re.search(r"^_PROMOTE\s*=\s*\((.*?)^\)", src, re.S | re.M)
    assert m, "plugin_core_tools.py 里找不到 _PROMOTE —— 上游改了结构, 先看清楚再改测试"
    promoted = set(re.findall(r'"(catfish_[a-z_]+)"', m.group(1)))
    assert promoted, "_PROMOTE 解析出来是空的 —— 十有八九是正则没对上语法"

    for src_name, catfish_tools in SOURCE_TOOL_PROFILES.items():
        deferred = catfish_tools - promoted
        if not deferred:
            continue
        native = SOURCE_NATIVE_TOOLS[src_name]
        missing = DEFERRED_TOOL_BRIDGES - native
        assert not missing, (
            f"{src_name} 的业务工具 {sorted(deferred)} 被 hermes defer, "
            f"必须靠桥才够得着, 但它的 native 白名单缺了 {sorted(missing)} —— "
            "这条 source 会变成空手, 且不会报错。"
        )


def test_桥够不到execute_code是hermes保证不是我们的():
    """记一笔边界, 免得以后有人以为"给了 tool_call 等于给了全部工具"。

    hermes tools/tool_search.py 的 parse 里:
        if not is_deferrable_tool_name(name):
            return None, {}, f"'{name}' is not a deferrable tool. ..."
    而 is_deferrable_tool_name 对 _HERMES_CORE_TOOLS 一律返 False。
    execute_code / write_file / patch / process 都是 core → 桥调不动。

    所以 advisor 拿到桥之后, 可达集合 = 被 defer 的工具 = catfish 业务工具那族,
    不含任何执行类原生工具。这里只断言我们这侧没把 core 工具误塞进桥名单。
    """
    assert not (DEFERRED_TOOL_BRIDGES & ALWAYS_ON_TOOLS), (
        "桥名单跟 always-on 撞了 —— 桥本身应该是 hermes core 之外的东西"
    )
    for src_name, native in SOURCE_NATIVE_TOOLS.items():
        dangerous = native & {"execute_code", "write_file", "patch", "process",
                              "delegate_task", "memory"}
        assert not dangerous, f"{src_name} 的 native 白名单里混进了执行类工具: {dangerous}"


def test_native表里不许出现catfish工具名():
    """两张表分工: native 管 hermes 原生裸名, profile 管 catfish_*。
    混进来说明作者搞错了表, 而且会静默失效 (catfish_* 根本不查 native 表)。"""
    for src, names in SOURCE_NATIVE_TOOLS.items():
        bad = {n for n in names if n.startswith(("catfish_", "mcp__"))}
        assert not bad, f"{src} 的 native 白名单里混进了 catfish 工具: {bad}"
