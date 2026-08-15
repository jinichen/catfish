"""插件侧打网关的调用必须带 catfish_source 归属标记 (8/15 晚)。

# 病历

8/15 白天修完聊天链路的归属之后, 晚上再查账本:

    来源                          次数   合计token   占比
    (无标记)                       96    499,510    40.0%   ← 又是最大一栏
    companion-advisor              10    466,523    37.4%
    companion-chat                  3    146,192    11.7%
    companion-phishing-scan        11    126,040    10.1%

第一反应是"白天那个 agent loop 续轮丢 source 的老病没修干净"。**数据对不上**:
无标记每次输入才 4,787 token, 而 agent loop 续轮带着越滚越大的历史,
该是 4 万量级 (对比 companion-chat 每次 48,660)。

按用户对账把范围锁死了:

    client:hermes-cli   107 次 = 无标记 96 + phishing 11   ✓
    chenhongbo@ffcs.cn   15 次 = advisor 10 + chat 3 + profile 1 + transform 1  ✓

96 次全在服务身份名下, 一次都不在员工名下 —— 那就不是"员工发起、中途丢标",
而是**根本没人打过标**。查下去是七处插件自己的 LLM 调用:

    catfish_memory_llm.py   × 4   总结 / 蒸馏 / wiki 分析 / wiki 生成
    catfish_memory_merge.py × 1   wiki 合并
    memory_enforce.py       × 1   记忆路由分类
    recmode/aggregator.py   × 1   RecMode 聚合

白天那刀只覆盖了 Companion 的聊天链路, 从来没碰到这些。

# 名字为什么是 `plugin:xxx`

不是新编的。网关 metrics.py:274 的注释 5/17 就写了:

    默认 'unknown' 不写字段减少噪音, 显式标的 (companion / plugin:xxx) 才记

约定早在, 三个月没人实现。前缀分开"员工端应用发的" (companion-*) 和
"插件后台发的" (plugin:*), 看一眼账本就知道钱花在哪一侧。

# 这个文件钉三件事

  1. 五个记忆调用点各自的 source 名字没被改掉 / 删掉
  2. with_source 的分隔符逻辑 —— URL 自带 query 时必须用 `&`
  3. 不许有新的调用点绕过 with_source 直接 post 裸 _gateway_url()

第 2 条是这次真正容易错的地方: `_gateway_url()` 的第一优先级
`CATFISH_GATEWAY_INTERNAL_URL` 是**客户 IT 填的完整 URL**, 可以自带 query。
直接拼 `?` 会拼出 `...?a=b?catfish_source=x` —— 后半段被当成前一个参数的值,
标记静默丢失, 而账本里只是"又多了一条无标记"。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_DIR))

from catfish_memory_gateway import with_source  # noqa: E402

#: 五个记忆链路调用点 → 它们该打的 source 名。
#: 改名字要来这里改 —— 改了账本里的历史数据就对不上了, 是个该有人点头的动作。
EXPECTED = {
    ("catfish_memory_llm.py", "_call_summarize_llm"): "plugin:memory-summarize",
    ("catfish_memory_llm.py", "_call_distill_llm"): "plugin:memory-distill",
    ("catfish_memory_llm.py", "_call_analysis_llm"): "plugin:memory-wiki-analysis",
    ("catfish_memory_llm.py", "_call_generation_llm"): "plugin:memory-wiki-generation",
    ("catfish_memory_merge.py", "_call_merge_llm"): "plugin:memory-wiki-merge",
}


def _sources_in(fname: str) -> dict[str, str]:
    """从源码里读出 {函数名: with_source 的第二个参数}。"""
    tree = ast.parse((_DIR / fname).read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                    and sub.func.id == "with_source" and len(sub.args) == 2
                    and isinstance(sub.args[1], ast.Constant)):
                out[node.name] = sub.args[1].value
    return out


@pytest.mark.parametrize("key,expected", sorted(EXPECTED.items()))
def test_每个调用点的source名字没变(key, expected):
    fname, func = key
    got = _sources_in(fname).get(func)
    assert got == expected, (
        f"{fname}::{func} 的 source 是 {got!r}, 期望 {expected!r}。\n"
        "改名字账本里的历史数据就对不上了 —— 确实要改的话顺手改这张表。"
    )


# ── with_source 的分隔符 ────────────────────────────────────


def test_干净URL用问号():
    u = with_source("http://gw/v1/chat/completions", "plugin:memory-summarize")
    assert u == "http://gw/v1/chat/completions?catfish_source=plugin:memory-summarize"


def test_自带query的URL必须用and号():
    """★★★ `CATFISH_GATEWAY_INTERNAL_URL` 是客户 IT 填的完整 URL, 可以自带 query。

    用 `?` 拼会拼出 `...?tenant=dahua?catfish_source=x` —— parse 出来
    tenant 的值变成 `dahua?catfish_source=x`, 而 catfish_source 这个 key
    根本不存在。表现就是"这条又没打上标", 没有任何报错。
    """
    u = with_source("http://gw.corp/v1/chat/completions?tenant=dahua",
                    "plugin:memory-summarize")
    q = parse_qs(urlparse(u).query)
    assert q.get("tenant") == ["dahua"], f"原有参数被冲掉了: {q}"
    assert q.get("catfish_source") == ["plugin:memory-summarize"], f"标记没挂上: {q}"


def test_source名字里的冒号能原样解析():
    """`plugin:xxx` 带冒号。RFC 3986 允许 query 值里出现 `:`, 但值得钉一下 ——
    哪天有人"顺手"改成 URL 编码, 账本里就会变成 plugin%3Amemory-summarize,
    跟历史数据对不上。"""
    u = with_source("http://gw/v1/chat/completions", "plugin:memory-distill")
    assert parse_qs(urlparse(u).query)["catfish_source"] == ["plugin:memory-distill"]


# ── 防新增漏网 ──────────────────────────────────────────────


@pytest.mark.parametrize("fname", ["catfish_memory_llm.py", "catfish_memory_merge.py"])
def test_不许直接post裸的gateway_url(fname: str):
    """★★★ 防再出现无标记的调用点。

    新加一个 LLM 调用时最自然的写法就是抄旁边一行 `_gateway_url()` —— 这条拦住它。
    """
    tree = ast.parse((_DIR / fname).read_text(encoding="utf-8"))
    bad = []
    for sub in ast.walk(tree):
        if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                and sub.func.attr == "post" and sub.args
                and ast.unparse(sub.args[0]) == "_gateway_url()"):
            bad.append(sub.lineno)
    assert not bad, (
        f"{fname} 第 {bad} 行直接 post 了裸的 _gateway_url() —— "
        "包一层 with_source(_gateway_url(), \"plugin:xxx\"), 否则这条调用在网关账本里"
        "会落进「(无标记)」栏, 而那一栏 8/15 晚上占了全部 token 的 40%。"
    )


def test_with_source不从helpers拿():
    """⚠ 这条记的是一个当场炸过的坑。

    第一版把 `with_source` 加进了 llm.py 从 catfish_memory_helpers 的 import 行,
    直接 ImportError:

        cannot import name 'with_source' from partially initialized module
        'catfish_memory_helpers' (most likely due to a circular import)

    因为 helpers 在**文件末尾**回指 llm.py —— llm.py 被加载时 helpers 只初始化了
    一半, 它顶部 re-export 过的名字拿得到, 后加的拿不到。

    catfish_memory_gateway 是 base 侧, 不 import llm.py, 直接拿没有环。
    """
    for fname in ("catfish_memory_llm.py", "catfish_memory_merge.py"):
        tree = ast.parse((_DIR / fname).read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if (isinstance(n, ast.ImportFrom) and n.module
                    and "catfish_memory_helpers" in n.module):
                names = {a.name for a in n.names}
                assert "with_source" not in names, (
                    f"{fname} 从 helpers 拿 with_source —— 会撞循环导入。"
                    "改成 from catfish_memory_gateway import with_source。"
                )
