"""同一实体不该被拆成多个文件。

# 这组测试守的是什么 (8/4 鸿波 "为什么会被拆成多个")

实测鸿波机器 255 条里 **21 组同 title 多文件, 共 50 个文件 (20%)**:

    「高新技术企业认定」 × 5   gaoxinjishu-qiye-rending / gaoxinjishu-qiye-ren-ding
                              / gaoxin-jishu-qiye-rending / gaoxinjishuqiyerending
                              / gao-xin-ji-shu-qi-ye-ren-ding
    「中电福富」        × 3   zhongdianfufu / zhongdian-fufu / zhong-dian-fu-fu

三个原因叠在一起:

1. Generation prompt 的 slug 规则自相矛盾 (helpers:335 老版):
     "slug = 中文转拼音**首字母** (e.g. 陈鸿波 → chenhongbo, 中电福富 → zdff)"
   说首字母, 例子一个首字母一个全拼。LLM 只能猜, 实测产出第三种 (全拼, 分词
   每次不同)。

2. _call_generation_llm 只喂 analysis, LLM 不知道已有哪些条目, 每次从零造。

3. _write_wiki_files 只做 `target.exists()` 精确路径匹配, 变体路径不同 → 新建。

而 Companion UI 那条路 (wiki_write.rs::find_normalized_collision) 有规范化重名
检测, 13/21 组它本来就能拦住 —— 又是两条产线只有一条设防。

三条都修了, 这组测试钉住第 3 条 (确定性兜底) 和第 2 条 (清单真的给了 LLM)。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from catfish_memory_helpers import (
    _existing_wiki_index,
    _normalize_slug_for_dedup,
    _redirect_to_existing_equivalent,
    _write_wiki_files,
)


def _fm(title: str, body: str = "正文。") -> str:
    return f"---\ntype: entity\ntitle: {title}\ntags: [x]\n---\n\n# {title}\n\n{body}\n"


@pytest.mark.parametrize(
    "a,b",
    [
        ("zhongdianfufu", "zhongdian-fufu"),
        ("zhongdian-fufu", "zhong-dian-fu-fu"),
        ("fudaisong", "fu-dai-song"),
        ("ITSS-Yunwei", "itssyunwei"),
        ("a_b.c", "abc"),
    ],
)
def test_normalize_treats_variants_as_same(a, b):
    assert _normalize_slug_for_dedup(a) == _normalize_slug_for_dedup(b)


def test_normalize_keeps_different_entities_apart():
    """「中电福富」和「中电福富信息科技有限公司」是两个东西, 不能合。"""
    assert _normalize_slug_for_dedup("中电福富") != _normalize_slug_for_dedup(
        "中电福富信息科技有限公司"
    )


def test_variant_write_updates_existing_file(tmp_path):
    """核心: 第二次用变体 slug 写 → 更新已有文件, 不新建。"""
    ents = tmp_path / "wiki" / "entities"
    ents.mkdir(parents=True)
    (ents / "zhongdianfufu.md").write_text(_fm("中电福富", "第一版。"), encoding="utf-8")

    _write_wiki_files(tmp_path, {"wiki/entities/zhong-dian-fu-fu.md": _fm("中电福富", "第二版。")})

    files = sorted(p.name for p in ents.glob("*.md"))
    assert files == ["zhongdianfufu.md"], f"变体建了新文件: {files}"
    txt = (ents / "zhongdianfufu.md").read_text(encoding="utf-8")
    assert "第二版" in txt, "新内容没写进去"
    assert "第一版" in txt, "旧内容该以 legacy 注释留底 (P18 merge)"


def test_genuinely_new_entity_still_created(tmp_path):
    """别矫枉过正 —— 真的新实体必须能建。"""
    ents = tmp_path / "wiki" / "entities"
    ents.mkdir(parents=True)
    (ents / "zhongdianfufu.md").write_text(_fm("中电福富"), encoding="utf-8")

    _write_wiki_files(tmp_path, {"wiki/entities/beijingfufu.md": _fm("北京福富")})
    assert (ents / "beijingfufu.md").exists()


def test_no_cross_kind_merge(tmp_path):
    """entity 和 concept 同名不该互相合并 —— 只在同目录里找等价。"""
    (tmp_path / "wiki" / "entities").mkdir(parents=True)
    (tmp_path / "wiki" / "concepts").mkdir(parents=True)
    (tmp_path / "wiki/concepts/zizhi-liucheng.md").write_text(_fm("资质流程"), encoding="utf-8")

    rel = _redirect_to_existing_equivalent(tmp_path, "wiki/entities/zizhiliucheng.md")
    assert rel == "wiki/entities/zizhiliucheng.md", "跨目录被误合了"


def test_exact_hit_unchanged(tmp_path):
    ents = tmp_path / "wiki" / "entities"
    ents.mkdir(parents=True)
    (ents / "x.md").write_text(_fm("X"), encoding="utf-8")
    assert _redirect_to_existing_equivalent(tmp_path, "wiki/entities/x.md") == "wiki/entities/x.md"


# ─────────────────────────────────────────────────────────────
# 清单必须真的给到 LLM —— prompt 里承诺了就得兑现
# ─────────────────────────────────────────────────────────────


def test_existing_index_lists_slug_and_title(tmp_path):
    (tmp_path / "wiki" / "entities").mkdir(parents=True)
    (tmp_path / "wiki" / "concepts").mkdir(parents=True)
    (tmp_path / "wiki/entities/zhongdianfufu.md").write_text(_fm("中电福富"), encoding="utf-8")
    (tmp_path / "wiki/concepts/zizhi-liucheng.md").write_text(_fm("资质申报流程"), encoding="utf-8")

    idx = _existing_wiki_index(tmp_path)
    assert "entity/zhongdianfufu ← 中电福富" in idx
    assert "concept/zizhi-liucheng ← 资质申报流程" in idx
    assert "必须复用" in idx, "清单要带上复用要求, 否则 LLM 只当参考"


def test_existing_index_empty_when_no_wiki(tmp_path):
    assert _existing_wiki_index(tmp_path) == ""


def test_existing_index_truncates(tmp_path):
    ents = tmp_path / "wiki" / "entities"
    ents.mkdir(parents=True)
    for i in range(12):
        (ents / f"e{i}.md").write_text(_fm(f"实体{i}"), encoding="utf-8")
    idx = _existing_wiki_index(tmp_path, limit=5)
    assert "已截断" in idx


# ─────────────────────────────────────────────────────────────
# title 判据 —— 比 slug 规范化更强, 覆盖"中文名 vs 拼音名"
# ─────────────────────────────────────────────────────────────


def test_same_title_different_slug_merges(tmp_path):
    """8/4 实测剩下的 8 组: title 一样但 slug 规范化后仍不等。

    高新资质申报 vs gaoxin-zizhi-shenbao / 陈秀平 vs chenxiuping —— title 是员工
    在 UI 上唯一看得见的东西, 两条 title 完全相同的条目他根本分不出是两个东西。
    """
    ents = tmp_path / "wiki" / "entities"
    ents.mkdir(parents=True)
    (ents / "gaoxin-zizhi-shenbao.md").write_text(_fm("高新资质申报", "第一版。"), encoding="utf-8")

    _write_wiki_files(tmp_path, {"wiki/entities/高新资质申报.md": _fm("高新资质申报", "第二版。")})

    files = sorted(p.name for p in ents.glob("*.md"))
    assert files == ["gaoxin-zizhi-shenbao.md"], f"title 相同却建了新文件: {files}"
    txt = (ents / "gaoxin-zizhi-shenbao.md").read_text(encoding="utf-8")
    assert "第二版" in txt and "第一版" in txt


def test_title_check_reads_new_content_not_disk(tmp_path):
    """回归钉子: 第一版拿 Path 去读那个**还不存在**的新文件, title 永远是空,
    判据一次都不触发而且不报错。必须从传进来的 content 里取。"""
    from catfish_memory_helpers import _title_of_content, _redirect_to_existing_equivalent
    assert _title_of_content(_fm("中电福富")) == "中电福富"

    ents = tmp_path / "wiki" / "entities"
    ents.mkdir(parents=True)
    (ents / "zdff.md").write_text(_fm("中电福富"), encoding="utf-8")
    # slug 完全不等价 (zdff vs 中电福富), 只能靠 title 判据
    rel = _redirect_to_existing_equivalent(
        tmp_path, "wiki/entities/中电福富.md", _fm("中电福富")
    )
    assert rel == "wiki/entities/zdff.md", rel


def test_different_title_not_merged(tmp_path):
    ents = tmp_path / "wiki" / "entities"
    ents.mkdir(parents=True)
    (ents / "a.md").write_text(_fm("中电福富"), encoding="utf-8")
    rel = _redirect_to_existing_equivalent(
        tmp_path, "wiki/entities/b.md", _fm("中电福富信息科技有限公司")
    )
    assert rel == "wiki/entities/b.md", "不同 title 被误合了"


def test_no_frontmatter_file_still_deduped(tmp_path):
    """第三种形态: 一条有 frontmatter, 一条是纯 markdown。

    8/4 实测 信息安全中心.md 完全没 frontmatter (232 字节), 按 frontmatter title
    分组会漏掉它 —— 但 UI 上它显示的就是"信息安全中心", 跟另一条一模一样。
    判据必须用**员工看得见的名字** (title 或 fallback 文件名), 跟读侧一致。
    """
    ents = tmp_path / "wiki" / "entities"
    ents.mkdir(parents=True)
    (ents / "信息安全中心.md").write_text("# 信息安全中心\n\n纯 markdown, 没有 frontmatter。\n",
                                          encoding="utf-8")
    rel = _redirect_to_existing_equivalent(
        tmp_path, "wiki/entities/xinxi-anquan-zhongxin.md", _fm("信息安全中心")
    )
    assert rel == "wiki/entities/信息安全中心.md", rel
