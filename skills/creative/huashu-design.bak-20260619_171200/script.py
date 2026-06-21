"""catfish wrapper for 花叔 huashu-design skill.

花叔是**指令型** skill — 真活由 agent 接力执行. 这个 script.py 是
catfish_run_skill 调用入口, 按 catfish 约定暴露 render_huashu_design 函数,
返回 SKILL.md 指令 + 资源路径告诉 LLM 下一步怎么做.

跟归藏 (creative/guizang-ppt-magazine) 的对比:
- 归藏: 单 HTML, 杂志风/瑞士风 PPT, 横向翻页, 单一模板
- 花叔: 多形态 (HTML/PPTX/MP4/PDF), 5 流派设计顾问, 21 份 references drill-down,
        Junior Designer 工作流 (placeholder → 迭代), 反 AI slop 评审, 动画 pipeline.

入口函数: render_huashu_design (catfish_tools._find_render_function 扫前缀 render_).
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

_TOPIC_SAFE_RE = re.compile(r"[\\/:*?\"<>|\r\n\t]+")


def _safe_topic(topic: str, fallback: str = "untitled") -> str:
    s = (topic or "").strip()
    if not s:
        return fallback
    s = _TOPIC_SAFE_RE.sub("_", s)
    return s[:60] or fallback


def _list_files(directory: Path, pattern: str, max_count: int = 10) -> list[str]:
    if not directory.exists():
        return []
    return sorted(str(p) for p in list(directory.glob(pattern))[:max_count])


def render_huashu_design(
    input: str = "",
    topic: str = "",
    output_format: str = "html",
    output_dir: str = "",
    **kwargs,
) -> dict:
    """生成花叔风格 design skill 指令.

    Args:
        input:         用户原始需求 (例如 '做商务 PPT 介绍我们公司')
        topic:         主题 (用于文件名)
        output_format: html (默认, 含 PPT/原型/动画) / pptx / mp4 / pdf
        output_dir:    输出目录, 默认 ~/.catfish/output

    Returns:
        catfish 标准格式 + 接力指令.
    """
    root = Path(__file__).parent

    # ── SKILL.md (指令全文) ──
    skill_md_path = root / "SKILL.md"
    instruction = (
        skill_md_path.read_text(encoding="utf-8")
        if skill_md_path.exists()
        else ""
    )

    # ── 资源 ──
    references = _list_files(root / "references", "*.md", max_count=21)
    demos = _list_files(root / "demos", "*.html", max_count=8)
    assets_jsx = _list_files(root / "assets", "*.jsx", max_count=10)
    scripts = _list_files(root / "scripts", "*.js", max_count=10)
    scripts += _list_files(root / "scripts", "*.mjs", max_count=10)

    # ── 归一化 ──
    output_format = (output_format or "html").lower().strip()
    if output_format not in ("html", "pptx", "mp4", "pdf", "svg"):
        output_format = "html"

    topic_seed = topic.strip() if topic else (input.strip()[:20] if input else "")
    topic_safe = _safe_topic(topic_seed, fallback="design")

    # ── 输出路径 ──
    out_root = Path(output_dir).expanduser() if output_dir else (
        Path("~/.catfish/output").expanduser()
    )
    date_str = datetime.now().strftime("%Y%m%d")
    output_target = out_root / f"{topic_safe}_花叔风_{date_str}.{output_format}"

    # ── 指令文本 ──
    summary_lines = [
        "花叔 design skill 是**指令型** — 这条 tool 调用本身**不产生文件**.",
        "真活要你接力执行: 读 SKILL.md + references → 按 5 流派设计 → write_file 输出.",
        "",
        "📌 这次任务参数:",
        f"   主题: {topic_safe}",
        f"   输出格式: {output_format}",
        "   ⚠️  输出路径 (必须用这个, 不要自己造):",
        f"      {output_target}",
        "",
        "📋 接力步骤 — 严格按顺序:",
        "",
        f"   Step 1: read_file('{skill_md_path}')",
        "           60KB 主文档. 含 5 流派设计哲学 (Pentagram/Field.io/Kenya Hara/...)",
        "           + 核心资产协议 (强制下载真 logo/截图, 不靠记忆)",
        "           + Junior Designer 工作流 (placeholder → 迭代)",
        "           + 反 AI slop 规则 (禁紫渐变/emoji 图标/Inter 当 display/...)",
        "",
        "   Step 2 (按需): read_file 相关 references — 21 份 drill-down 文档:",
    ]
    # 列出最相关的 references (按 input 关键词匹配前几份)
    for r in references[:6]:
        ref_name = Path(r).stem
        summary_lines.append(f"      - {ref_name} ({r})")
    if len(references) > 6:
        summary_lines.append(f"      ... 共 {len(references)} 份, 看任务需要 drill 哪份")

    summary_lines.extend([
        "",
        f"   Step 3 (按需): demos/ 下有 {len(demos)} 个 demo HTML 看风格参考",
        "",
        "   Step 4: 按 input 真做内容, write_file 输出最终产物:",
        f"           write_file('{output_target}', <完整产物>)",
        "",
        "           ⚠️  严禁:",
        "           ❌ 不读 SKILL.md / references 直接瞎做",
        "           ❌ 用 emoji 当图标 / 用 Inter 当 display 字体 / 紫蓝渐变背景",
        "             (反 AI slop 是花叔灵魂, 违反 = 不专业)",
        "           ❌ 自己改 output_target",
        "           ✅ 按 SKILL.md 的 5 流派选 1 个, 严守该流派规范",
        "",
        "📝 用户原始需求:",
        f"   {input or '(空)'}",
        "",
    ])

    if output_format == "pptx":
        summary_lines.append(
            "📦 PPTX 输出: 用 scripts/html2pptx.js — 先生成 HTML, "
            "再用这脚本转可编辑 PPTX (DOM → 真 PPT text frame, 非图片)."
        )
    elif output_format == "mp4":
        summary_lines.append(
            "🎬 MP4 输出: scripts/render-video.js + tts-doubao.mjs — 先 HTML, "
            "再渲染 25fps + BGM (BGM 已被本脚本跳过, 需 1 处单独 下载 bgm-*.mp3)."
        )

    summary_lines.append("")
    summary_lines.append(
        f"完成后, 回员工: '✅ 已生成: {output_target}'."
    )

    return {
        "ok": True,
        "files": [],
        "summary": "\n".join(summary_lines),
        "instruction": instruction,
        "instruction_path": str(skill_md_path),
        "references": references,
        "demos": demos,
        "assets_jsx": assets_jsx,
        "scripts": scripts,
        "output_format": output_format,
        "topic": topic_safe,
        "output_target": str(output_target),
        "is_instructional": True,
    }


if __name__ == "__main__":
    args_json = sys.stdin.read().strip()
    args_dict = json.loads(args_json) if args_json else {}
    result = render_huashu_design(**args_dict)
    print(json.dumps(result, ensure_ascii=False, indent=2))
