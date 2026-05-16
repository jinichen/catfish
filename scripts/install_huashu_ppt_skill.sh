#!/usr/bin/env bash
# BL-HUASHU-PPT (5/15 鸿波 '把花叔也装一下, 试下效果')
#
# 把花叔 (alchaincyf/huashu-design) 装到 catfish 的 skills 目录, 跟归藏并列.
# 用户先 clone 到 ~/.claude/skills/huashu-design, 本脚本复制 + 加 catfish wrapper.
#
# 装到: ~/person_task/catfish/skills/creative/huashu-design/
#
# 用法:
#   # 1. 先 clone:
#   mkdir -p ~/.claude/skills && cd ~/.claude/skills
#   git clone https://github.com/alchaincyf/huashu-design.git
#   # 2. 再装:
#   bash ~/person_task/catfish/scripts/install_huashu_ppt_skill.sh
#
# 跟归藏 (install_guizang_ppt_skill.sh) 同套路, 区别:
#   - 跳过 assets/bgm-*.mp3 (27MB BGM, 视频导出才用, 大多数 PPT 任务不需要)
#   - frontmatter triggers 区分: 花叔主"商务设计/品牌/动画/反AI", 归藏主"杂志风/瑞士风"
#   - skill_path: creative/huashu-design (跟 GitHub repo 同名)

set -e

RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; CYAN=$'\033[36m'; RST=$'\033[0m'

ok()   { echo "${GREEN}[OK]${RST} $*"; }
info() { echo "${CYAN}[INFO]${RST} $*"; }
warn() { echo "${YELLOW}[WARN]${RST} $*"; }
fail() { echo "${RED}[FAIL]${RST} $*" >&2; exit 1; }

echo ""
echo "${YELLOW}━━━ BL-HUASHU-PPT · 装花叔 design skill 进 catfish ━━━${RST}"

SRC="$HOME/.claude/skills/huashu-design"
DST_DIR="$HOME/person_task/catfish/skills/creative"
DST="$DST_DIR/huashu-design"

# ─── Step 1: 检测源 ───────────────────────────────────────

if [ ! -d "$SRC" ]; then
    warn "源不存在: $SRC"
    info "先 clone 花叔仓库:"
    info "  mkdir -p ~/.claude/skills && cd ~/.claude/skills"
    info "  git clone https://github.com/alchaincyf/huashu-design.git"
    info ""
    info "然后再跑这个脚本."
    exit 1
fi

if [ ! -f "$SRC/SKILL.md" ]; then
    fail "$SRC 不含 SKILL.md, 不是合法 skill 目录"
fi

ok "源: $SRC"
SRC_SIZE=$(du -sh "$SRC" 2>/dev/null | awk '{print $1}')
info "源大小: $SRC_SIZE (含 BGM, 装时会跳)"

# ─── Step 2: 备份旧版 + 准备目标 ──────────────────────────

mkdir -p "$DST_DIR"

if [ -d "$DST" ]; then
    BACKUP="$DST.bak-$(date +%Y%m%d_%H%M%S)"
    warn "目标已存在, 备份到: $BACKUP"
    mv "$DST" "$BACKUP"
fi

# ─── Step 3: 复制 (rsync 跳 BGM + .git) ──────────────────

ok "复制中 (跳 BGM mp3 + .git)..."
rsync -a \
    --exclude='assets/bgm-*.mp3' \
    --exclude='.git' \
    --exclude='.gitignore' \
    --exclude='node_modules' \
    --exclude='package-lock.json' \
    "$SRC/" "$DST/"

DST_SIZE=$(du -sh "$DST" 2>/dev/null | awk '{print $1}')
ok "复制完成: $DST ($DST_SIZE, 跳过 27MB BGM)"

# ─── Step 4: 改 frontmatter 加 catfish 约定字段 ───────────

info "改 SKILL.md frontmatter (加 catfish triggers/kind/version)..."

python3 << PYEOF
import re
from pathlib import Path

skill_md = Path("$DST/SKILL.md")
text = skill_md.read_text(encoding="utf-8")

# 提取已有 frontmatter
m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
if not m:
    print("⚠️ SKILL.md 没 yaml frontmatter, 不加 catfish 字段 (跳过)")
    raise SystemExit(0)

fm = m.group(1)
body = text[m.end():]

extra = []
if "kind:" not in fm:
    extra.append("kind: instructional")
if "version:" not in fm:
    extra.append('version: "1.0.0"')
if "deprecated:" not in fm:
    extra.append("deprecated: false")
if "triggers:" not in fm:
    # 花叔触发词 — 跟归藏区分开:
    #   归藏: 杂志风/瑞士风/PPT/slides/deck/演讲稿/幻灯片
    #   花叔: 商务PPT/品牌设计/视觉/动画/反AI slop/MP4/原型
    # 共同词 (PPT/slides) 都有, 让 agent 看 description 区分
    extra.append("""triggers:
  - 花叔
  - 商务PPT
  - 商务幻灯片
  - 品牌设计
  - 视觉设计
  - 设计稿
  - 设计风格
  - 原型
  - 动画
  - MP4
  - 视频解说
  - 海报
  - 信息建筑
  - 反AI
  - design
  - prototype
  - showcase""")

if extra:
    new_fm = fm + "\n" + "\n".join(extra)
    new_text = "---\n" + new_fm + "\n---\n" + body
    skill_md.write_text(new_text, encoding="utf-8")
    print(f"✅ 已加 {len(extra)} 个 catfish 字段")
else:
    print("✅ frontmatter 已含 catfish 字段, 不动")
PYEOF

# ─── Step 5: 加 catfish script.py wrapper ────────────────

info "加 catfish_run_skill 调用入口 (render_huashu_design 函数)..."

cat > "$DST/script.py" <<'PYEOF'
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
PYEOF

ok "已加 catfish script.py wrapper (render_huashu_design 入口)"

# ─── Step 6: 验证 ────────────────────────────────────────

echo ""
info "skill 结构:"
ls -la "$DST" | head -15
echo ""

info "SKILL.md frontmatter:"
head -25 "$DST/SKILL.md"
echo ""

# 跑 script.py 验证 render_huashu_design 入口
info "测 script.py 入口..."
TEST_OUT=$(echo '{"input":"做商务 PPT 介绍公司","topic":"test","output_format":"html"}' | python3 "$DST/script.py" 2>&1 | head -5)
if echo "$TEST_OUT" | grep -q '"ok": true'; then
    ok "script.py 测通 (render_huashu_design 返 ok=true)"
else
    warn "script.py 测试输出有点怪:"
    echo "$TEST_OUT"
fi

# ─── Step 7: 下一步提示 ──────────────────────────────────

echo ""
echo "${YELLOW}━━━ 下一步 ━━━${RST}"
echo ""
echo "1. 重启 gateway 让 skills_catalog 重扫:"
echo "   ${CYAN}pkill -f catfish_gateway${RST}"
echo "   ${CYAN}cd ~/person_task/catfish/central/llm-gateway && python -m catfish_gateway.app${RST}"
echo ""
echo "2. Companion **新开对话** 试一句, 对比花叔 vs 归藏:"
echo "   ${CYAN}'做一份花叔风的商务 PPT 介绍我们公司'${RST}     ← 花叔触发"
echo "   ${CYAN}'做一份杂志风 PPT 介绍我们公司'${RST}             ← 归藏触发"
echo ""
echo "3. agent 同时看到两个候选 skill, 按 description 选:"
echo "   - ${CYAN}creative/huashu-design${RST}        多流派设计, 反 AI slop"
echo "   - ${CYAN}creative/guizang-ppt-magazine${RST} 杂志/瑞士排版 PPT"
echo ""
echo "4. 5/15 实盘验证: Qwen 122B instruction-following 弱, 花叔指令更长 = 更难"
echo "   听话. Gemini 2.5 Pro 这俩都能驾驭. 客户保密走 Qwen 时 mileage may vary."
echo ""
ok "装完: $DST"
ok "(catfish skills root: $(dirname $DST_DIR) — 跟 department/ 同级)"
