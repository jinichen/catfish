"""写盘时 frontmatter 必须带开头的 `---`。

# 这组测试守的是什么 (8/4)

鸿波: "知识库里面还有很多条目是用拼音, 能修正吗"

真因不是文件名 —— UI 显示的是 frontmatter 里的 title (WikiTree.tsx:575/884 和
WikiGraph.tsx:223 都是 f.title)。而读侧 wiki_read.rs:81 的规则很硬:

    if !trimmed.starts_with("---") { return (String::new(), text); }

少了开头那行 `---` → 整个 frontmatter 当正文 → 一个字段都读不到:
  · title 读不到  → fallback 成文件名 → UI 上显示成拼音 slug
  · tags 读不到   → 标签筛选里消失
  · related 读不到 → 图里没有连线, 树里掉进"未分类"

8/4 实测鸿波机器: 255 条里 19 条是这样。文件里明明写着 `title: 高新资质申报`,
UI 上一直显示 `gaoxin-zizhi-shenbao`。全程零报错 —— 读侧遇到这种文件是"正常
返回一个 title=slug 的条目", 不是失败, 所以没有任何东西会喊。

写侧三条路只有一条保证了 `---`, 而唯一没校验的 P19 LLM merge 恰恰只在
**更新已有条目**时触发 —— 坏掉的正好都是老条目。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from catfish_memory_helpers import _ensure_frontmatter_fence, _write_wiki_files


GOOD = "---\ntype: entity\ntitle: 高新资质申报\ntags: [项目]\n---\n\n# 高新资质申报\n\n正文。\n"
# LLM 少打开头那行 —— P19 merge 的真实事故形态
MISSING_OPEN = "type: entity\ntitle: 高新资质申报\ntags: [项目]\n---\n\n# 高新资质申报\n\n正文。\n"


def test_repairs_missing_opening_fence():
    out = _ensure_frontmatter_fence("wiki/entities/x.md", MISSING_OPEN)
    assert out.startswith("---\n"), out[:60]
    assert out == GOOD.replace("\n\n正文。\n", "\n\n正文。\n")


def test_leaves_good_content_untouched():
    assert _ensure_frontmatter_fence("wiki/entities/x.md", GOOD) == GOOD


def test_leaves_plain_markdown_untouched():
    """没有 frontmatter 的纯正文是合法的 —— 不能瞎补。"""
    plain = "# 标题\n\n就是一段正文, 没有 frontmatter。\n"
    assert _ensure_frontmatter_fence("wiki/entities/x.md", plain) == plain


def test_leaves_keylike_prose_untouched():
    """第一行像 YAML 键, 但后面没有收尾 fence → 不是这一类, 别猜。"""
    prose = "note: 这是一句以冒号开头的正文\n\n后面没有任何 --- 分隔线。\n"
    assert _ensure_frontmatter_fence("wiki/entities/x.md", prose) == prose


def test_warns_when_repairing(caplog):
    """静默修好等于把上游问题藏起来 —— 必须出声。"""
    with caplog.at_level("WARNING"):
        _ensure_frontmatter_fence("wiki/entities/x.md", MISSING_OPEN)
    assert any("缺开头的 ---" in r.getMessage() for r in caplog.records), caplog.text


# ─────────────────────────────────────────────────────────────
# 端到端: 走真实写盘路径
# ─────────────────────────────────────────────────────────────


def _read_title(p: Path) -> str:
    """复刻读侧 (wiki_read.rs split_frontmatter + title fallback slug)。"""
    text = p.read_text(encoding="utf-8")
    trimmed = text.lstrip()
    if not trimmed.startswith("---"):
        return p.stem                      # ← 这就是"显示成拼音"的那条路
    after = trimmed[3:]
    end = after.find("\n---")
    if end < 0:
        return p.stem
    fm = after[:end].strip()
    for line in fm.splitlines():
        line = line.strip()
        if line.startswith("title:"):
            return line[len("title:"):].strip()
    return p.stem


def test_new_file_path_gets_fence(tmp_path):
    """新建路径: content 直接来自 LLM, 以前一点校验都没有。"""
    n_e, _ = _write_wiki_files(tmp_path, {"wiki/entities/gaoxin-zizhi-shenbao.md": MISSING_OPEN})
    assert n_e == 1
    f = tmp_path / "wiki/entities/gaoxin-zizhi-shenbao.md"
    assert _read_title(f) == "高新资质申报", (
        f"读侧拿到的 title 是 {_read_title(f)!r} —— 退化成文件名了, 说明 fence 没补上"
    )


def test_p19_llm_merge_path_gets_fence(tmp_path):
    """P19 LLM merge: 文件已存在 + 在 skip 集里 → 原样 overwrite。

    这是 8/4 那 19 个坏文件最可能的来源 —— 唯一完全没校验的路径, 而且只在
    更新已有条目时走到。
    """
    f = tmp_path / "wiki/entities/gaoxin-zizhi-shenbao.md"
    f.parent.mkdir(parents=True)
    f.write_text(GOOD, encoding="utf-8")

    _write_wiki_files(
        tmp_path,
        {"wiki/entities/gaoxin-zizhi-shenbao.md": MISSING_OPEN},
        skip_merge_paths={"wiki/entities/gaoxin-zizhi-shenbao.md"},   # 假装 LLM merge 处理过
    )
    assert _read_title(f) == "高新资质申报", (
        f"P19 路径写出来的文件读不出 title (拿到 {_read_title(f)!r}) —— "
        "这正是员工看到拼音的形态"
    )


def test_p18_regex_merge_path_still_ok(tmp_path):
    """P18 regex merge 本来就是对的, 加守卫别把它弄坏。"""
    f = tmp_path / "wiki/entities/x.md"
    f.parent.mkdir(parents=True)
    f.write_text(GOOD, encoding="utf-8")
    _write_wiki_files(tmp_path, {"wiki/entities/x.md": GOOD})   # 不在 skip 里 → 走 P18
    assert _read_title(f) == "高新资质申报"
