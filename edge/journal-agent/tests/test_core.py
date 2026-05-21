"""catfish-journal core 单测.

跟 catfish/edge/companion-app/src-tauri/src/commands/journal.rs Rust 端单测
等价 — 任一端改 regex 必须两边都过.
"""

from __future__ import annotations

import pytest

from catfish_journal import core


# ── extract_todos ────────────────────────────────────────────────────


def test_extract_checkbox_unchecked():
    md = "- [ ] 给老李写汇报\n- [x] 已完成的事"
    out = core.extract_todos(md)
    assert len(out) == 1
    assert out[0].text == "给老李写汇报"
    assert out[0].source == "checkbox"
    assert out[0].line == 1


def test_extract_inline_todo_zh():
    md = "今天: 待办: 修复 P0 bug"
    out = core.extract_todos(md)
    assert len(out) == 1
    assert out[0].text == "修复 P0 bug"
    assert out[0].source == "inline"


def test_extract_section_tracked():
    md = (
        "## 2026-05-19 - 周一\n"
        "- [ ] 任务 A\n"
        "## 2026-05-20\n"
        "- [ ] 任务 B"
    )
    out = core.extract_todos(md)
    assert len(out) == 2
    assert out[0].section == "2026-05-19 - 周一"
    assert out[1].section == "2026-05-20"


def test_extract_completed_skipped():
    md = "- [x] 已完成\n- [ ] 待办"
    out = core.extract_todos(md)
    assert len(out) == 1
    assert out[0].text == "待办"


def test_extract_empty_journal():
    assert core.extract_todos("") == []


def test_extract_skip_inline_in_completed_checkbox():
    """`- [x] TODO: xxx` 不应被双抽 (已完成 checkbox + 行内 TODO 标记)."""
    md = "- [x] TODO: 已完成的事"
    out = core.extract_todos(md)
    assert out == []


# ── mark_todo_done ───────────────────────────────────────────────────


def test_mark_done_basic():
    md = "- [ ] 给老李写汇报\n- [ ] 修 P0 bug\n"
    out = core.mark_todo_done(md, 1, "给老李")
    assert "- [x] 给老李写汇报" in out
    assert "- [ ] 修 P0 bug" in out  # 别误伤


def test_mark_done_text_hint_mismatch():
    with pytest.raises(core.JournalEditError):
        core.mark_todo_done("- [ ] 任务\n", 1, "不存在")


def test_mark_done_already_done_rejected():
    with pytest.raises(core.JournalEditError):
        core.mark_todo_done("- [x] 已完成\n", 1, "已完成")


def test_mark_done_out_of_range():
    with pytest.raises(core.JournalEditError):
        core.mark_todo_done("- [ ] 只一行\n", 5, "只一行")


def test_mark_done_preserves_indent():
    """嵌套 list 缩进必须保留."""
    md = "  - [ ] 子任务\n"
    out = core.mark_todo_done(md, 1, "子任务")
    assert "  - [x] 子任务" in out


def test_mark_done_line_zero_rejected():
    with pytest.raises(core.JournalEditError):
        core.mark_todo_done("- [ ] x\n", 0, "x")


def test_mark_done_star_bullet():
    """* 跟 - 都是 markdown bullet."""
    md = "* [ ] 任务\n"
    out = core.mark_todo_done(md, 1, "任务")
    assert "* [x] 任务" in out


# ── delete_todo ──────────────────────────────────────────────────────


def test_delete_checkbox():
    md = "- [ ] 任务 1\n- [ ] 任务 2\n"
    new, deleted = core.delete_todo(md, 1, "任务 1")
    assert "任务 1" not in new
    assert "任务 2" in new
    assert "任务 1" in deleted


def test_delete_inline_todo():
    md = "## 段\nTODO: 内联任务\n"
    new, _ = core.delete_todo(md, 2, "内联任务")
    assert "内联任务" not in new
    assert "## 段" in new  # 段标题保留


def test_delete_non_todo_rejected():
    """不许删段标题 / 正文 (防 LLM 误删)."""
    md = "## 段标题\n正文\n"
    with pytest.raises(core.JournalEditError):
        core.delete_todo(md, 1, "段标题")
    with pytest.raises(core.JournalEditError):
        core.delete_todo(md, 2, "正文")


def test_delete_hint_mismatch():
    with pytest.raises(core.JournalEditError):
        core.delete_todo("- [ ] 任务\n", 1, "不匹配")


# ── add_todo ─────────────────────────────────────────────────────────


def test_add_empty_journal():
    out = core.add_todo("", "新任务")
    assert "- [ ] 新任务" in out


def test_add_existing_no_section():
    out = core.add_todo("既有\n", "新任务")
    assert "既有" in out
    assert "- [ ] 新任务" in out
    # 顺序: 旧的在前, 新的在后
    assert out.index("既有") < out.index("新任务")


def test_add_to_existing_section():
    md = "## 2026-05-20\n- [ ] 老任务\n## 2026-05-21\n- [ ] 明天\n"
    out = core.add_todo(md, "新任务", section="2026-05-20")
    # 新任务在 5-20 段内, 5-21 段之前
    today_idx = out.index("2026-05-20")
    tomorrow_idx = out.index("2026-05-21")
    new_idx = out.index("新任务")
    assert today_idx < new_idx < tomorrow_idx


def test_add_section_not_exists_creates():
    """section 不存在 → 文末新建该 section."""
    md = "## 老段\n- [ ] 老任务\n"
    out = core.add_todo(md, "新任务", section="新段")
    assert "## 新段" in out
    assert "- [ ] 新任务" in out
    # 新段在文末
    assert out.index("## 新段") > out.index("老段")


def test_add_empty_text_rejected():
    with pytest.raises(core.JournalEditError):
        core.add_todo("既有\n", "   ")


def test_add_strips_text():
    out = core.add_todo("", "  带空白  ")
    assert "- [ ] 带空白" in out
    assert "  带空白" not in out


# ── 幂等性 (BL-CATFISH-TODO-SYNC v0.1.7) ────────────────────────────


def test_add_idempotent_skip_if_exists():
    """同 text 已存在 → 不重复加, 返原 content."""
    md = "## 今天\n- [ ] 给老李写汇报\n"
    out = core.add_todo(md, "给老李写汇报")
    # 内容不变 (没新增一行)
    assert out == md
    # 仍只有一条 - [ ] 给老李写汇报
    assert out.count("- [ ] 给老李写汇报") == 1


def test_add_idempotent_skip_with_section():
    """同 text 已存在 + 指定 section → 不重复加."""
    md = "## 2026-05-20\n- [ ] 任务 A\n"
    out = core.add_todo(md, "任务 A", section="2026-05-20")
    assert out == md
    assert out.count("- [ ] 任务 A") == 1


def test_add_idempotent_completed_also_skipped():
    """text 已存在但已 [x] 完成 → 也跳过 (不重新加未完成版).
    catfish-todo-sync sync 时遇到 completed 不会重新生 - [ ] 行."""
    md = "## 历史\n- [x] 已完成的事\n"
    out = core.add_todo(md, "已完成的事")
    assert out == md
    assert "- [ ] 已完成的事" not in out


def test_add_different_text_not_skipped():
    """text 不同 → 正常追加."""
    md = "- [ ] 任务 A\n"
    out = core.add_todo(md, "任务 B")
    assert "- [ ] 任务 A" in out
    assert "- [ ] 任务 B" in out
    assert out.count("- [ ]") == 2


def test_add_partial_match_not_skipped():
    """部分匹配不算 (e.g. journal 有 "任务 A 详细" 不阻止 add "任务 A")."""
    md = "- [ ] 任务 A 详细描述\n"
    out = core.add_todo(md, "任务 A")
    # 应该新增, 不被前缀匹配误判
    assert "- [ ] 任务 A 详细描述" in out
    assert "- [ ] 任务 A\n" in out


def test_add_idempotent_strips_then_compares():
    """text 前后空白被 strip 后比对 — '  任务 A  ' 跟 '任务 A' 一样."""
    md = "- [ ] 任务 A\n"
    out = core.add_todo(md, "  任务 A  ")  # 前后带空白
    assert out == md  # strip 后跟已有匹配, 跳过


# ── done=True 历史补写 (BL-CATFISH-TODO-SYNC v0.1.9, 5/20) ──────────


def test_add_done_writes_checked_box():
    """done=True → 写 `- [x] xxx` 而非 `- [ ] xxx`."""
    out = core.add_todo("", "已完成历史", done=True)
    assert "- [x] 已完成历史" in out
    assert "- [ ] 已完成历史" not in out


def test_add_done_existing_unchecked_skipped():
    """journal 已有 `- [ ] X` (未完成) → done=True 调 add 也跳过 (幂等覆盖, 不重复加).
    实际 caller 应先调 done 而非 add --done, 但万一传错也不破."""
    md = "- [ ] 任务 A\n"
    out = core.add_todo(md, "任务 A", done=True)
    assert out == md  # 已存在跳过, 不变 [x]


def test_add_done_existing_checked_skipped():
    """journal 已有 `- [x] X` → done=True 调 add 跳过 (不重复加)."""
    md = "- [x] 已完成的事\n"
    out = core.add_todo(md, "已完成的事", done=True)
    assert out == md
    assert out.count("- [x] 已完成的事") == 1


def test_add_done_to_section():
    """done=True + section: 加 `- [x]` 到指定段尾."""
    md = "## 历史\n- [x] 已完成 A\n## 当前\n- [ ] 进行中\n"
    out = core.add_todo(md, "已完成 B", section="历史", done=True)
    assert "- [x] 已完成 B" in out
    # 在 历史 段内, 当前 段之前
    assert out.index("已完成 B") < out.index("## 当前")


def test_add_done_to_new_section():
    """done=True + section 不存在 → 文末新建该 section + [x] 行."""
    md = "## 当前\n- [ ] 任务\n"
    out = core.add_todo(md, "历史完成", section="2026-05-20 已完成", done=True)
    assert "## 2026-05-20 已完成" in out
    assert "- [x] 历史完成" in out


def test_add_done_default_false_compat():
    """不传 done 参数 (老 caller) 仍写 `- [ ]` 行 — 兼容性."""
    out = core.add_todo("", "新任务")
    assert "- [ ] 新任务" in out
    assert "- [x] 新任务" not in out


# ── round-trip: 抽 → 改 → 抽 ──────────────────────────────────────


def test_roundtrip_mark_done_then_extract():
    """mark done 后, extract 不再返这条."""
    md = "## 今天\n- [ ] 给老李写汇报\n- [ ] 修 P0 bug\n"
    todos_before = core.extract_todos(md)
    assert len(todos_before) == 2

    target = next(t for t in todos_before if "老李" in t.text)
    new_md = core.mark_todo_done(md, target.line, target.text[:5])

    todos_after = core.extract_todos(new_md)
    assert len(todos_after) == 1
    assert "P0 bug" in todos_after[0].text


def test_roundtrip_add_then_extract():
    md = "## 今天\n- [ ] 老任务\n"
    new_md = core.add_todo(md, "新任务", section="今天")
    todos = core.extract_todos(new_md)
    assert len(todos) == 2
    assert any("新任务" in t.text for t in todos)
    assert any("老任务" in t.text for t in todos)


# ── is_priority 解析 (5/21 加) ───────────────────────────────────────


def test_priority_star_prefix():
    """⭐ 前缀识别 + text 去前缀."""
    out = core.extract_todos("- [ ] ⭐ 完成季度汇报")
    assert len(out) == 1
    assert out[0].is_priority is True
    assert out[0].text == "完成季度汇报"  # 去掉 ⭐


def test_priority_top_emoji_prefix():
    """🔝 前缀识别."""
    out = core.extract_todos("- [ ] 🔝 给老李回复")
    assert out[0].is_priority is True
    assert out[0].text == "给老李回复"


def test_priority_chinese_prefix():
    """'重点:' / '重点：' 中英文冒号都识别."""
    out = core.extract_todos("- [ ] 重点: 跑完整周报\n- [ ] 重点：写邮件")
    assert len(out) == 2
    assert all(t.is_priority for t in out)
    assert out[0].text == "跑完整周报"
    assert out[1].text == "写邮件"


def test_priority_default_false():
    """没前缀 → is_priority=False (向后兼容)."""
    out = core.extract_todos("- [ ] 普通任务\nTODO: 普通行内")
    assert len(out) == 2
    assert all(t.is_priority is False for t in out)


def test_priority_inline_todo():
    """行内 TODO: 也支持 ⭐ 前缀."""
    out = core.extract_todos("TODO: ⭐ 重点任务")
    assert len(out) == 1
    assert out[0].is_priority is True
    assert out[0].text == "重点任务"


def test_priority_mixed_sort_order():
    """priority + 普通 TODO 混合, extract 按行号顺序; 排序由 caller 做."""
    md = "- [ ] 普通 1\n- [ ] ⭐ 重点\n- [ ] 普通 2"
    out = core.extract_todos(md)
    assert [t.text for t in out] == ["普通 1", "重点", "普通 2"]
    assert [t.is_priority for t in out] == [False, True, False]
