"""project-approval skill — 项目立项 / 立项申请 .docx 生成.

# 设计

复用 leadership-briefing 的整套渲染层 (blocks / 字体 / 页脚 / 附件 CSV / typo_check).
区别只在 SKILL.md 里的语义化 4 段定义 (背景/方案/风险/进度), 渲染代码 100% 共享.

未来如果有第 4 个公文 skill, 把 leadership-briefing 的 render 移到
skills/_shared/docx_render/ 各 skill 都 import. 现在 2 个 skill, 直接复用更省事.

# 入口

  render_project_approval(*, title_lines, sections, attachments=None,
                          important_phrases=None, output_path=None) → dict

  返回跟 leadership-briefing 一致:
    { "docx": ..., "attachments": [...], "files": [...], "audit": "" }
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

# 复用 leadership-briefing 的整套渲染 + typo_check.
# 走 importlib.spec_from_file_location 避免 'script' 模块名跟自己重名 (双 skill 都叫 script.py).


def _load_leadership_render():
    """动态加载 leadership-briefing/script.py 拿 render_briefing 函数."""
    leadership_script = (
        Path(__file__).resolve().parent.parent / "leadership-briefing" / "script.py"
    )
    spec = importlib.util.spec_from_file_location(
        "_leadership_briefing_script", leadership_script
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 {leadership_script}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.render_briefing


_render_briefing = _load_leadership_render()


def render_project_approval(
    *,
    title_lines: list[str],
    sections: list[dict[str, Any]],
    attachments: list[dict[str, Any]] | None = None,
    important_phrases: list[str] | None = None,
    output_path: str | None = None,
) -> dict[str, Any]:
    """生成项目立项 .docx (+ 可选 CSV 附件).

    结构跟 leadership-briefing 完全一致 (4 段 + blocks 模式). 调用 leadership-briefing
    的 render_briefing, 只是文件命名 / 标题语义不同.

    Args:
        title_lines: 标题行 (居中加粗), 一般 1-2 行 ("关于 X 项目的立项申请" + "申请人: X 部门")
        sections: 4 段 (背景/方案/风险/进度), 每段 { "heading": str, "blocks": list[dict] }
        attachments: CSV 附件, 每个 { "name": "附件1-XXX", "header": [...], "rows": [...] }
        important_phrases: 红字高亮的关键词 (金额/日期/重大风险)
        output_path: 输出路径. 没指定走 ~/.catfish/outputs/YYYY-MM-DD/HHMMSS_<title>/

    Returns:
        { "docx": "/path/主.docx",
          "attachments": ["/path/附件1.csv", ...],
          "files": [所有路径合并],
          "audit": "/path/.audit.json" | "" }
    """
    return _render_briefing(
        title_lines=title_lines,
        sections=sections,
        attachments=attachments,
        important_phrases=important_phrases,
        output_path=output_path,
    )


__all__ = ["render_project_approval"]
