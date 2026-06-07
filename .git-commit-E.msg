E1-E5 ship: taste-skill 5 类 UI 改造 (banner+toolcall+wiki 全闭环)

E1: 安装 Leonxlnx/taste-skill (~/.claude/skills/ via npx skills add)
    skill redesign-existing-projects = 5 大类 audit (typography/color/
    layout/interactivity/content) + 7 步 fix priority.

E2: approval banner 改造 (ChatPanel.tsx + globals.css .approval-banner*)
  ✓ CSS class + brand 墨青 single accent
  ✓ filled primary + 文字链 secondary + ghost destructive (3 层 weight)
  ✓ submittingChoice state + spinner per-button
  ✓ 60s 倒计时 aria-live polite
  ✓ enter animation + focus-visible 3px ring

E3: ChatToolCall 改造 (ChatToolCall.tsx + globals.css .toolcall*)
  ✓ 4px left-bar 状态 accent (单边色块替全 4 边)
  ✓ lowercase italic section label (typography 降权)
  ✓ ▶ rotate 90deg transition + open state
  ✓ keyboard a11y: role=button + Enter/Space + aria-expanded
  ✓ ApprovalBtn → .toolcall__approval-btn--primary/--always/--deny
    (brand 墨青 + muted + status-err, 删 ✓/✗ emoji prefix)

E4: WikiTab + WikiTree 改造 (WikiTab/Tree.tsx + globals.css .wikitab* .wiki-*)
  ✓ 3 pane bg shift (elevated / cream / elevated) + inset shadow 替 1px border
  ✓ pill segmented control for mode + kind toggle
  ✓ Group caret rotate 90deg transition + keyboard a11y
  ✓ single brand 墨青 accent

E5: WikiPreview + WikiGraph 改造 + ChatToolCall ApprovalBtn brand fix
  WikiPreview:
    ✓ metadata card elevation shadow (替 1px border)
    ✓ kind emoji 🧑📐💬 → .wiki-kind-badge--entity/concept/query
      (brand 一致 chip — 墨青 / 暖橙 / 灰青)
    ✓ tag chip bg-cream 无 border, hover → brand 墨青
    ✓ wikilink .wiki-preview__wikilink (brand 墨青) + --dangling (status-err)
    ✓ 编辑/保存 复用 .approval-banner__btn-primary/__btn-link (跟 E2 一致)
    ✓ textarea focus-visible 3px ring (跟 wiki-search-input 一致)
    ✓ 变更历史 caret rotate (跟 .wiki-group 一致, pattern 复用)
    ✓ 1px dashed → 渐变 1px gradient (subtle 替 hairline)
    ✓ RelatedRecommend shadow elevation (替 1px border + 删 📊 emoji)
  WikiGraph:
    ✓ node color brand 一致 (entity 墨青 #0E5F66 / concept 暖橙 #F47B3D
      / query 灰青 #6B8589 low-saturation, 同 cyan 系)
    ✓ selected #ff3366 粉红 → #1A8A95 cyan-bright (brand 高亮版)
    ✓ header inset shadow + .wiki-graph__legend-dot (替 1px border)
    ✓ empty 🕸️ emoji → circle + 文案 (Phosphor 风替 cliche emoji)

CI: tsc --noEmit 全过. globals.css +935 行 class 群, components -552 行 inline style.
