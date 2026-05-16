#!/usr/bin/env bash
# BL-PPT-SKILLS (5/15 鸿波 '现在就要支持杂志风 PPT')
#
# 把归藏 (op7418/guizang-ppt-skill) 装到 catfish 真扫的 skills 目录,
# 让 catfish skills_catalog inject 能看到它, LLM 自动触发.
#
# **关键修正 (5/15 16:00)**:
# 之前我装到 ~/.hermes/skills/creative/, 但 catfish 的 discover_skills() 不扫
# ~/.hermes/skills, 它扫的是 catfish 项目仓库内 skills/department/ 所在目录.
# 真正路径: ~/person_task/catfish/skills/creative/guizang-ppt-magazine/
#
# 输出: HTML (单文件横向滑动). 不是 .pptx — 杂志风是 HTML-only 路径.
#
# 用法:
#   bash ~/person_task/catfish/scripts/install_guizang_ppt_skill.sh

set -e

RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; CYAN=$'\033[36m'; RST=$'\033[0m'

ok()   { echo "${GREEN}[OK]${RST} $*"; }
info() { echo "${CYAN}[INFO]${RST} $*"; }
fail() { echo "${RED}[FAIL]${RST} $*" >&2; exit 1; }
warn() { echo "${YELLOW}[WARN]${RST} $*"; }

echo ""
echo "${YELLOW}━━━ BL-PPT-SKILLS · 装归藏 PPT skill 进 catfish ━━━${RST}"

SRC="$HOME/.claude/skills/guizang-ppt-skill"
# 真路径: catfish 仓库内 (跟 department/eis-checkin 等同级)
# discover_skills 扫这里, 不扫 ~/.hermes/skills/
DST_DIR="$HOME/person_task/catfish/skills/creative"
DST="$DST_DIR/guizang-ppt-magazine"

# Step 1: 源在不在
if [ ! -d "$SRC" ]; then
    warn "源不存在: $SRC"
    info "先 clone 归藏到 ~/.claude/skills/:"
    info "  mkdir -p ~/.claude/skills && cd ~/.claude/skills"
    info "  git clone https://github.com/op7418/guizang-ppt-skill.git"
    exit 1
fi
if [ ! -f "$SRC/SKILL.md" ]; then
    fail "$SRC 不含 SKILL.md, 不是合法 skill 目录"
fi
ok "源在: $SRC (含 SKILL.md)"

# Step 2: 目标目录
mkdir -p "$DST_DIR"
ok "目标目录: $DST_DIR"

# Step 3: 已装? 先备份再覆盖
if [ -d "$DST" ]; then
    BACKUP="$DST.bak-$(date +%Y%m%d_%H%M%S)"
    warn "目标已存在, 备份到: $BACKUP"
    mv "$DST" "$BACKUP"
fi

# Step 4: 清理之前装错地方的 (~/.hermes/skills/creative/)
WRONG_DST="$HOME/.hermes/skills/creative/guizang-ppt-magazine"
WRONG_DST_DISABLED="$HOME/.hermes/skills/creative/guizang-ppt-magazine.disabled"
for wrong in "$WRONG_DST" "$WRONG_DST_DISABLED"; do
    if [ -d "$wrong" ]; then
        warn "清理之前装错地方: $wrong (catfish 不扫这, 留着浪费 disk)"
        rm -rf "$wrong"
    fi
done

# Step 5: 复制 (用 cp 不是 symlink, 改归藏不影响 catfish)
cp -R "$SRC" "$DST"
ok "已复制: $SRC → $DST"

# Step 6: 加 catfish-style script.py wrapper
# 跟 leadership-briefing 等同模式 (script.py 是 catfish 调用约定).
# 归藏 skill 是指令型 — script.py 只返 SKILL.md 内容给 LLM 用 read_file / write_file
# 自己生成 HTML, 不真在 sandbox 跑 python.
#
# **关键 (5/15 修)**: 入口函数必须叫 `render_*` (catfish_tools._find_render_function
# 扫前缀). 之前用 `def run()` 撞 "skill 没正确暴露入口" 错.
cat > "$DST/script.py" <<'PYEOF'
"""BL-PPT-SKILLS catfish wrapper for 归藏 PPT skill.

归藏 (op7418/guizang-ppt-skill) 是**指令型** skill — 真生成 HTML 的工作由
LLM 自己完成 (read_file 读 template → 填内容 → write_file). 这个 script.py
是 catfish_run_skill 调用入口, 按 catfish 约定暴露 ``render_*`` 函数.
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


def _normalize_style(style: str) -> str:
    s = (style or "A").strip().upper()
    if s in ("A", "杂志", "MAGAZINE", "MAG"):
        return "A"
    if s in ("B", "瑞士", "SWISS"):
        return "B"
    return "A"


def _list_files(directory: Path, pattern: str) -> list[str]:
    if not directory.exists():
        return []
    return sorted(str(p) for p in directory.glob(pattern))


def render_guizang_magazine(
    input: str = "",
    topic: str = "",
    style: str = "A",
    page_count: int = 7,
    output_dir: str = "",
    **kwargs,
) -> dict:
    """生成杂志风 / 瑞士风 HTML PPT (归藏 skill, 指令型)."""
    root = Path(__file__).parent
    skill_md_path = root / "SKILL.md"
    instruction = (
        skill_md_path.read_text(encoding="utf-8")
        if skill_md_path.exists()
        else ""
    )

    templates = _list_files(root / "assets", "*.html")
    references = _list_files(root / "references", "*.md")

    style_chosen = _normalize_style(style)
    topic_seed = topic.strip() if topic else (input.strip()[:20] if input else "")
    topic_safe = _safe_topic(topic_seed, fallback="ppt")

    out_root = Path(output_dir).expanduser() if output_dir else (
        Path("~/.catfish/output").expanduser()
    )
    date_str = datetime.now().strftime("%Y%m%d")
    style_tag = "杂志风" if style_chosen == "A" else "瑞士风"
    output_target = out_root / f"{topic_safe}_{style_tag}_{date_str}.html"

    preferred_template = None
    for t in templates:
        name = Path(t).name.lower()
        if style_chosen == "B" and "swiss" in name:
            preferred_template = t
            break
        if style_chosen == "A" and "swiss" not in name and name.endswith(".html"):
            preferred_template = t
            break
    if preferred_template is None and templates:
        preferred_template = templates[0]

    # 5/15 鸿波实盘 lesson: agent 误用 execute_code 跑 string replace +
    # 假设模板有 {{TITLE}} jinja 占位符 + 自造路径. 措辞写**铁律**, 不模糊.
    summary_lines = [
        "归藏 PPT skill 是**指令型** — 这条 tool 调用本身**不产生 HTML 文件**.",
        "真 HTML 要你接力执行: read_file 读模板 → 自己拼 → write_file 输出.",
        "",
        "📌 这次任务参数:",
        f"   主题: {topic_safe}",
        f"   风格: {style_tag} (style={style_chosen})",
        f"   目标页数: {page_count}",
        "   ⚠️  输出路径 (必须用这个, 不要自己造):",
        f"      {output_target}",
        "",
        "📋 接力 3 步 — 严格按下面顺序, 每步一个 tool_call:",
        "",
        f"   Step 1: read_file('{preferred_template}')",
        "           拿到 ~860 行 HTML 骨架. 模板含 <head><style>...</style></head>",
        "           + 空 <body> + WebGL/Motion 脚本. 全套样式都在 head 里.",
        "",
        "   Step 2 (可选, 大致看版式选型): read_file 1-2 份 reference",
    ]
    for r in references[:2]:
        summary_lines.append(f"      - {r}")
    summary_lines.extend([
        "",
        f"   Step 3: write_file('{output_target}', <完整 HTML 字符串>)",
        "           写法: 保留 step 1 读到的 <head>...</head> 原封不动,",
        f"           **重写 <body>** 塞 {page_count} 个 <section class='slide'>...",
        "           每页 1 个 section, 16:9 比例, 内容按 input 拆.",
        "",
        "           ⚠️  **严禁**:",
        "           ❌ 用 execute_code 跑 string.replace('{{TITLE}}', ...) — "
        "模板没 jinja 占位符, replace 全不命中.",
        "           ❌ 用 execute_code 自己造路径 + 自己生成. 浪费 token 还出错.",
        "           ❌ 自己改 output_target (已经 gateway 算好, 必须按这个).",
        "           ✅ 正确做法: write_file 工具直接传完整 HTML 字符串, 1 个 tool_call 搞定.",
        "",
        "📝 用户原始需求 (填进 body 的内容):",
        f"   {input or '(空)'}",
        "",
        "⚠️  HTML 内容约束:",
        "   - 单文件 HTML, CSS 内联在 <head> (从模板继承), 不依赖外部 JS",
        "   - 横向翻页: 每页 .slide div, 16:9, 左右键导航 (模板已带 JS)",
        "   - 杂志风 = 大字号 + 黑白 + 强排版; 瑞士风 = 网格 + 单一主色",
        "",
        f"Step 3 完成后, 回员工: '✅ PPT 已生成: {output_target}, 双击在浏览器打开看效果'.",
    ])

    return {
        "ok": True,
        "files": [],
        "summary": "\n".join(summary_lines),
        "instruction": instruction,
        "instruction_path": str(skill_md_path),
        "templates": templates,
        "preferred_template": preferred_template,
        "references": references,
        "style_chosen": style_chosen,
        "style_label": style_tag,
        "topic": topic_safe,
        "page_count": page_count,
        "output_target": str(output_target),
        "is_instructional": True,
    }


if __name__ == "__main__":
    args_json = sys.stdin.read().strip()
    args_dict = json.loads(args_json) if args_json else {}
    result = render_guizang_magazine(**args_dict)
    print(json.dumps(result, ensure_ascii=False, indent=2))
PYEOF
ok "已加 catfish script.py wrapper (render_guizang_magazine 入口)"

# Step 7: 验
echo ""
info "skill 结构:"
ls -la "$DST" | head -10
echo ""

info "SKILL.md frontmatter:"
head -10 "$DST/SKILL.md"
echo ""

# Step 8: 提示下一步
echo "${YELLOW}━━━ 下一步 ━━━${RST}"
echo ""
echo "1. 重启 gateway 让 skills_catalog 重扫 (新 skill 进 catalog):"
echo "   ${CYAN}pkill -f catfish_gateway${RST}"
echo "   等 Companion 自动重起, 或自己 cd central/llm-gateway 起."
echo ""
echo "2. Companion 试一句:"
echo "   ${CYAN}'用 creative/guizang-ppt-magazine skill 做企业资质使用情况分析,${RST}"
echo "   ${CYAN} 杂志风, 7 页, 含核心数据/Top 5/未调用分布/重点关注/优化建议'${RST}"
echo ""
echo "3. agent 应该:"
echo "   - skills_catalog 里看到 creative/guizang-ppt-magazine"
echo "   - 调 catfish_run_skill('creative/guizang-ppt-magazine', {...})"
echo "   - 拿到 SKILL.md 指令 + template 路径"
echo "   - read_file 读 template, write_file 输出 HTML"
echo ""
echo "4. open ~/.catfish/output/资质使用情况分析*.html 浏览器看真效果"
echo ""
ok "装完了. 装到: $DST"
ok "(catfish skills root: $(dirname $DST_DIR) — 跟 department/ 同级)"
