"""prefetch 里两段最大的东西要按相关性给, 不能定量倾倒 (8/15)。

# 病历: 一段每轮 2,222 token、并且藏起三分之二知识库的代码

查"一句话为什么要 4-5 万输入 token"时把 prefetch 逐段量了一遍,
`_render_wiki_summary` 是最大的一段。看代码发现它有三个毛病:

## 1. 列文件名, 不列标题

员工机器上 261 个 wiki 条目, **203 个是拼音 slug**
(`AAA-xin-yong-deng-ji-zheng-shu`), 而它们 frontmatter 里写着中文
`title: AAA信用等级证书`。

而 `resolve_wiki_ref` 的优先级是 **title 第一 (wiki_resolve.py:90),
slug 第四 (:107)**。也就是说旧写法一直在让 LLM 用最弱的那条解析路径,
同时把中文标题这个最有信息量的东西丢掉。

## 2. 不接 query, 按字母序砍到 50

168 个实体只露前 50 → **118 个永远看不见, 而且永远是同一批**。
C 开头的 `CCRC-tong-xin-wang-luo-...` 系列长期占坑,
`gaoxinjishuqiyerending` (高新技术企业认定) 永远进不来。

员工问高新认定, wiki 里明明有条目, LLM 却看不到。这不是省不省 token 的事,
是**功能缺陷**。

## 3. 废弃条目照列

30 个 `deprecated: true` 跟正主一起注进去 (`gaoxinjishu-qiye-rending` 自己
就是一个, 内容已并入 `gaoxinjishuqiyerending`) —— 既费 token, 又让 LLM 在
两份之间犹豫。

# strategic_docs 那边

排序本身是好的, 问题是**排完之后预算没花完就继续塞**。查"福富的资质情况"时
榜首只有 0.00338 (撞上专利审查报告), 照样注 ~1,600 token 进去。

# 这个文件钉什么

  1. 废弃条目不出现
  2. 显示的是 title, 不是 slug
  3. 相关条目能从字母序埋不到的地方被捞出来  ← 那个功能缺陷
  4. 没 query 时保持老行为 (字母序 cap 50), 不给 advisor 引回归
  5. query 全不命中时**退回字母序**, 不给一个空清单
  6. frontmatter 超过 head_bytes 时不许静默丢标题
  7. strategic_docs 榜首太低 → 折叠成一行提示 (而不是倒 7 篇)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_PLUGIN_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PLUGIN_DIR))

from catfish_memory import CatfishMemoryProvider  # noqa: E402
from catfish_memory_render import (  # noqa: E402
    _STRATEGIC_MIN_OVERLAP,
    _WIKI_CAP_PLAIN,
    _WIKI_CAP_QUERY,
    _overlap_coefficient,
    _query_token_set,
)
from wiki_resolve import load_nodes  # noqa: E402


@pytest.fixture
def provider():
    return CatfishMemoryProvider()


def _entity(home: Path, slug: str, title: str, *, kind: str = "entities",
            deprecated: bool = False, body: str = "内容") -> None:
    d = home / "wiki" / kind
    d.mkdir(parents=True, exist_ok=True)
    fm = [f"title: {title}", "type: entity"]
    if deprecated:
        fm.append("deprecated: true")
    (d / f"{slug}.md").write_text(
        "---\n" + "\n".join(fm) + "\n---\n\n" + body, encoding="utf-8"
    )


def _titles(rendered: str) -> list[str]:
    """只取 **实体 (N)** / **概念 (N)** 那两行里的 [[...]]。

    ⚠ 不能对整段做 findall —— 结尾那句说明文字里有个 `[[标题]]` 占位符
      (「chat 时引用员工 wiki 用 `[[标题]]` 精确链接」), 会被一起抓进来。
      第一版就是这么写的, 六条断言全挂在这个多出来的 '标题' 上。
    """
    out: list[str] = []
    for line in rendered.splitlines():
        if line.startswith(("**实体", "**概念")):
            out += re.findall(r"\[\[([^\]]+)\]\]", line)
    return out


# ── wiki_summary ────────────────────────────────────────────


def test_废弃条目不进清单(tmp_path, provider):
    """★★ 员工机器上 261 个里有 30 个 deprecated, 之前跟正主一起注。"""
    _entity(tmp_path, "gaoxinjishu-qiye-rending", "高新技术企业认定（旧）",
            deprecated=True)
    _entity(tmp_path, "gaoxinjishuqiyerending", "高新技术企业认定")
    got = _titles(provider._render_wiki_summary(tmp_path))
    assert got == ["高新技术企业认定"], f"废弃的那份还在: {got}"


def test_显示标题而不是拼音文件名(tmp_path, provider):
    """★★★ 203/261 的文件名是拼音, 标题是中文。

    resolve_wiki_ref 是 title 第一优先 (wiki_resolve.py:90), slug 第四 (:107)
    —— 给 slug 等于让 LLM 走最弱那条路, 还丢掉中文这个最有信息量的东西。
    """
    _entity(tmp_path, "AAA-xin-yong-deng-ji-zheng-shu", "AAA信用等级证书")
    got = _titles(provider._render_wiki_summary(tmp_path))
    assert got == ["AAA信用等级证书"], f"还在列拼音 slug: {got}"


def test_没有frontmatter时退回文件名(tmp_path, provider):
    """员工机器上 82 个条目没有 title —— 退回 slug, 不能变空。"""
    d = tmp_path / "wiki" / "entities"
    d.mkdir(parents=True)
    (d / "houjingyuan.md").write_text("没有 frontmatter 的裸文件", encoding="utf-8")
    assert _titles(provider._render_wiki_summary(tmp_path)) == ["houjingyuan"]


def test_相关条目能从字母序埋不到的地方捞出来(tmp_path, provider):
    """★★★ 这条就是那个功能缺陷的复现。

    造 60 个 C 开头的占坑条目 + 1 个排在最后的目标条目。
    字母序 cap 50 → 目标永远露不出来。按 query 排 → 第一个就是它。
    """
    for i in range(60):
        _entity(tmp_path, f"CCRC-tong-xin-{i:02d}", f"CCRC通信网络安全服务能力{i:02d}")
    _entity(tmp_path, "gaoxinjishuqiyerending", "高新技术企业认定")

    plain = _titles(provider._render_wiki_summary(tmp_path))
    assert "高新技术企业认定" not in plain, (
        "前置不成立: 字母序应该埋掉它, 这条测试才有意义"
    )
    ranked = _titles(provider._render_wiki_summary(tmp_path, query="高新技术企业认定"))
    assert ranked and ranked[0] == "高新技术企业认定", (
        f"按 query 排之后还是捞不出来: {ranked[:5]}"
    )


def test_没有query时保持老行为(tmp_path, provider):
    """★ advisor / 定时任务这些调用方没有 query, 不能给它们引回归。"""
    for i in range(80):
        _entity(tmp_path, f"e{i:03d}", f"实体{i:03d}")
    got = _titles(provider._render_wiki_summary(tmp_path))
    assert len(got) == _WIKI_CAP_PLAIN
    assert got == sorted(got), "没 query 时该是字母序"


def test_有query时cap更小(tmp_path, provider):
    """⚠ 第一版写的是 `len(got) == _WIKI_CAP_QUERY` —— **恒等式**。

    改常量的同时改了代码和期望, 做变异 (20→50) 时它绿着过去了。
    断言要钉住"关系", 不能钉住"跟被测代码同一个来源的数"。
    """
    for i in range(80):
        _entity(tmp_path, f"e{i:03d}", f"资质证书{i:03d}")
    plain = _titles(provider._render_wiki_summary(tmp_path))
    got = _titles(provider._render_wiki_summary(tmp_path, query="资质证书"))
    assert len(got) < len(plain), (
        f"有 query 时 ({len(got)}) 该比没 query 时 ({len(plain)}) 少 —— "
        "按相关性排的少数几个, 比按字母序排的一大堆有用"
    )
    assert len(got) <= 25, f"有 query 时还列了 {len(got)} 个"   # 故意写死, 不引常量


def test_query一个都不命中时退回字母序而不是空清单(tmp_path, provider):
    """★★ 员工打声招呼 ("在吗") 不该让整个 wiki 清单消失 ——
    那会让 LLM 以为知识库是空的, 比多花 token 糟。"""
    _entity(tmp_path, "fufu", "中电福富")
    got = _titles(provider._render_wiki_summary(tmp_path, query="zzz"))
    assert got == ["中电福富"], f"退回字母序失败: {got}"


def test_实体和概念分开列(tmp_path, provider):
    _entity(tmp_path, "fufu", "中电福富", kind="entities")
    _entity(tmp_path, "zizhi", "资质统筹原则", kind="concepts")
    out = provider._render_wiki_summary(tmp_path)
    assert "**实体 (1)**" in out and "**概念 (1)**" in out


# ── load_nodes 的头部读取 ───────────────────────────────────


def test_frontmatter超过head_bytes时不许静默丢标题(tmp_path):
    """★★★ 截断处切断 frontmatter → _fm_of 返空 → title 悄悄退化成 slug。

    没有任何报错, 表现只是"wiki 清单里突然冒出一堆拼音名"。
    load_nodes 必须发现切断了就整份重读。
    """
    d = tmp_path / "wiki" / "entities"
    d.mkdir(parents=True)
    padding = "\n".join(f"tag_{i}: {'x' * 60}" for i in range(40))   # 撑爆 head_bytes
    (d / "slugname.md").write_text(
        f"---\ntitle: 中电福富\n{padding}\n---\n\n正文", encoding="utf-8"
    )
    assert len(padding) > 256, "前置: padding 得真的超过下面那个 head_bytes"
    node = load_nodes(tmp_path, head_bytes=256)[0]
    assert node.title == "中电福富", (
        f"标题被截断吃掉了, 退化成 {node.title!r} —— 这正是要防的静默降级"
    )


def test_head_bytes够用时不读全文(tmp_path):
    """够用就别读全文 —— 261 个文件读全文是 1.1 MB / 62 ms, 每轮都付。"""
    d = tmp_path / "wiki" / "entities"
    d.mkdir(parents=True)
    (d / "x.md").write_text(
        "---\ntitle: 短标题\n---\n\n" + "正" * 100_000, encoding="utf-8"
    )
    node = load_nodes(tmp_path, head_bytes=4096)[0]
    assert node.title == "短标题"


def test_deprecated字段解析(tmp_path):
    _entity(tmp_path, "a", "甲", deprecated=True)
    _entity(tmp_path, "b", "乙")
    got = {n.slug: n.deprecated for n in load_nodes(tmp_path, head_bytes=4096)}
    assert got == {"a": True, "b": False}


def test_废弃条目仍然解析得到(tmp_path):
    """★★ deprecated 只影响"要不要主动推荐", 不影响解析 ——
    否则历史 `[[老名字]]` 链接会大面积断。"""
    from wiki_resolve import resolve_wiki_ref
    _entity(tmp_path, "old", "旧名字", deprecated=True)
    nodes = load_nodes(tmp_path, head_bytes=4096)
    assert resolve_wiki_ref("旧名字", nodes).kind == "hit"


# ── strategic_docs ──────────────────────────────────────────


def _doc(home: Path, name: str, body: str) -> None:
    d = home / "strategic_docs"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.md").write_text(body, encoding="utf-8")


def test_榜首太低时折叠成一行提示(tmp_path, provider):
    """★★★ 老行为是退回字母序, 把**全部**文档倒进去 —— 恰好在最没用的时候
    花最多的钱。

    折叠不等于够不着: catfish_search_docs 的 description 里本来就写着
    「当 system prompt 折叠区显示 'catfish strategic docs 还有 N 份'…必用」,
    而它是 P43 提升的核心工具, 每轮都在模型手里。
    """
    for i in range(7):
        _doc(tmp_path, f"专题文档{i}", "关于季度营收与人力编制的说明。" * 20)
    out = provider._render_strategic_docs(tmp_path, query="量子纠缠")
    assert "###" not in out, f"还在往里塞正文:\n{out[:300]}"
    assert "还有 7 份" in out
    assert "catfish_search_docs" in out, "得告诉 LLM 怎么把它们捞回来"


#: 一段字符很杂的长正文 —— 让 Jaccard 的并集足够大, 好造出"沾一点点边"的分数。
_LONG_BODY = (
    "营收编制流程审批预算部门季度年报计划执行考核指标绩效人员招聘培训发展"
    "战略目标市场客户产品研发测试上线运维监控告警日志分析报表汇总归档"
) * 10


def test_沾一点点边也要折叠(tmp_path, provider):
    """★★★ 这条才真正考到**闸门**。

    ⚠ 上面那条 (query 完全不沾边) overlap 恰好是 0, 阈值设成多少都会折叠 ——
      做变异时它挡不住。得让 overlap 严格大于 0 但小于阈值。

    真实对应的是 "福富的资质情况" 撞上 PATENT-EXAMINER-AUDIT 那种:
    overlap 0.10, 确实非零, 但一点用都没有。
    """
    for i in range(7):
        _doc(tmp_path, f"DOC-{i}", _LONG_BODY)

    # 先自证前置: overlap 确实落在 (0, 阈值) 之间, 否则这条测的是别的东西
    from catfish_memory_base import _read_text_safe
    head = _read_text_safe(tmp_path / "strategic_docs" / "DOC-0.md", 1500).strip()[:1200]
    q = _query_token_set("甲乙丙营")            # "营"命中正文, 甲乙丙不命中
    ov = _overlap_coefficient(q, _query_token_set("DOC-0") | _query_token_set(head))
    assert 0 < ov < _STRATEGIC_MIN_OVERLAP, (
        f"前置不成立: overlap {ov:.3f} 不在 (0, {_STRATEGIC_MIN_OVERLAP}) 里, "
        "这条就没在考闸门"
    )

    out = provider._render_strategic_docs(tmp_path, query="甲乙丙营")
    assert "###" not in out, f"沾一点点边就把 7 篇全塞进去了:\n{out[:300]}"
    assert "还有 7 份" in out


def test_闸门不受文档长度影响(tmp_path, provider):
    """★★★ 这条是 8/17 那个"换个环境会不会不匹配"的回归测试。

    第一版闸门是 `Jaccard >= 0.008`, 在员工机器上那 8 篇 (约 370 字) 量得
    好好的。但 Jaccard 的分母是**并集**, 随文档词汇量涨:

        命中内容一字不变, 只往文档里加互不相同的词
            正文  317 字   Jaccard 0.00805   ← 刚好过 0.008
            正文  717 字   Jaccard 0.00353   ← 完美命中被静默折叠
            正文 3017 字   Jaccard 0.00083

    **同一个完美命中摆动 275 倍。** 换台机器、文档写长点, 相关材料就没了,
    而且没有任何信号。

    这条钉住: 同样的命中内容, 文档从短到长, 判定必须不变。
    """
    import random
    CJK = [chr(c) for c in range(0x4E00, 0x4E00 + 3000)]
    random.seed(7)
    core = "护城河评估：数据不出端是核心壁垒。"

    for extra in (0, 300, 1500, 3000):
        d = tmp_path / f"len{extra}"
        _doc(d, "MOAT", core + "".join(random.sample(CJK, extra)))
        _doc(d, "OTHER", "".join(random.sample(CJK, 400)))
        out = provider._render_strategic_docs(d, query="护城河")
        assert "### MOAT" in out, (
            f"正文加到 {extra} 个杂词之后, 完美命中被折叠了 —— "
            f"闸门又依赖文档长度了:\n{out[:200]}"
        )


def test_命中时注入榜首_同时砍掉陪跑的(tmp_path, provider):
    """★★ 相对下限。

    ⚠ 第一版只断言 `"### MOAT" in out` —— 没说无关的**不该在**。
      做变异 (删掉相对下限那行) 时它绿着过去了。
      "该出现的出现了" 只是一半, 另一半是 "不该出现的没出现"。

    三篇的实测分 (query="护城河"):
        MOAT   0.03049  比值 1.000  → 留
        WEAK   0.00694  比值 0.228  → 砍 (非零, 但低于榜首的 25%)
        NOISE  0.00000  比值 0.000  → 砍
    WEAK 那篇是关键: 它分数非零, 只有相对下限拦得住它。
    """
    _doc(tmp_path, "MOAT",
         "护城河评估：数据不出端是核心壁垒，护城河由此建立。" * 3 + _LONG_BODY)
    _doc(tmp_path, "WEAK", _LONG_BODY + "沿河部署了监控节点。")
    _doc(tmp_path, "NOISE", _LONG_BODY)

    out = provider._render_strategic_docs(tmp_path, query="护城河")
    assert "### MOAT" in out, f"榜首没进去:\n{out[:300]}"
    assert "### WEAK" not in out, "非零但只有榜首 23% 的陪跑篇不该占 prompt"
    assert "### NOISE" not in out, "零分的更不该进"


def test_没有query时不折叠(tmp_path, provider):
    """★ query 空 = session 初始化 / advisor, 老行为是全注入, 不动它。"""
    for i in range(3):
        _doc(tmp_path, f"DOC{i}", "内容" * 50)
    out = provider._render_strategic_docs(tmp_path, query="")
    assert out.count("###") == 3


def test_闸门取值落在实测的空档里():
    """员工机器 8 篇的实测 overlap: 该留 0.57~1.00, 该折 0.00~0.20。

    阈值必须落在那段空档里。0.3/0.4/0.5 都全对, 取中间。
    """
    assert 0.20 < _STRATEGIC_MIN_OVERLAP < 0.57


def test_overlap_不随文档词汇量变化():
    """★★ 直接钉 helper 本身的性质, 不经过渲染。

    这是"可移植"的定义: 同一个 query、同一段命中内容, 材料词汇量变化 200 倍,
    overlap 必须一个字都不动。Jaccard 在同样条件下摆动 275 倍。
    """
    import random
    from catfish_memory_render import _jaccard_similarity
    CJK = [chr(c) for c in range(0x4E00, 0x4E00 + 3000)]
    random.seed(7)
    q = _query_token_set("护城河")
    core = "护城河评估：数据不出端是核心壁垒。"

    ovs, jacs = [], []
    for extra in (0, 300, 1500, 3000):
        d = _query_token_set(core + "".join(random.sample(CJK, extra)))
        ovs.append(_overlap_coefficient(q, d))
        jacs.append(_jaccard_similarity(q, d))

    assert len(set(ovs)) == 1 and ovs[0] == 1.0, f"overlap 变了: {ovs}"
    assert max(jacs) / min(jacs) > 50, (
        f"Jaccard 没有像预期那样大幅摆动 ({jacs}) —— 那这条测试的前提就变了, "
        "去看看 _query_token_set 是不是改了"
    )


# ── 拿真数据兜底 ────────────────────────────────────────────


def _catfish_home() -> Path:
    import os
    env = os.environ.get("CATFISH_HOME", "").strip()
    return Path(env).expanduser() if env else Path.home() / ".catfish"


_REAL = _catfish_home()


@pytest.mark.skipif(not (_REAL / "wiki" / "entities").is_dir(),
                    reason="真 wiki 只在员工机器上 (设 CATFISH_HOME 可跑)")
def test_真数据_高新认定捞得出来(provider):
    """★ 合成用例是我按理解搭的; 真数据才是判据。CI 上会 skip —— skip 不是通过。"""
    ranked = _titles(provider._render_wiki_summary(_REAL, query="高新技术企业认定"))
    assert ranked, "真数据上一个都没排出来"
    assert any("高新" in t for t in ranked[:3]), f"前三名里没有高新相关: {ranked[:5]}"


@pytest.mark.skipif(not (_REAL / "wiki" / "entities").is_dir(),
                    reason="真 wiki 只在员工机器上")
def test_真数据_废弃条目确实被挡掉(provider):
    nodes = load_nodes(_REAL, head_bytes=4096)
    dep = {n.title for n in nodes if n.deprecated}
    assert dep, "前置: 真数据里该有 deprecated 条目 (8/15 是 30 个)"
    shown = set(_titles(provider._render_wiki_summary(_REAL)))
    assert not (shown & dep), f"废弃条目漏进清单: {shown & dep}"
