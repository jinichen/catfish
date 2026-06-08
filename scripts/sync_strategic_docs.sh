#!/bin/bash
# BL-STRATEGIC-DOC-SYNC Phase 1 (6/7 鸿波 audit 后 ship).
#
# 把核心战略 / 设计 doc 从 catfish git repo 拷到 ~/.catfish/strategic_docs/,
# 加 frontmatter 让 catfish-memory plugin 的 _render_strategic_docs 自动 inject.
#
# 跟 wiki/concepts/ 分开 (避免污染 distill 链).
#
# # 用法
#
#   ./scripts/sync_strategic_docs.sh
#
# 重复跑安全 (overwrites).
#
# # 选哪些 doc
#
# 不是 docs/ 下全部, 只挑**真核心战略 / 架构 / 哲学**, 不上运营记录 / 日报 / sprint log.
#
# 当前 7 份 (audit 后挑的):
#   1. CATFISH-CENTRAL-MANIFESTO.md  - 4 公理 + API 边界
#   2. MOAT-ASSESSMENT.md             - 7 Powers 战略 audit
#   3. PATENT-LANDSCAPE.md            - 5 个 patent 方向
#   4. PATENT-EXAMINER-AUDIT.md       - 审查员视角校准
#   5. CAPABILITY-GAPS.md             - 商业化必备能力
#   6. ADVISORY-FEED-SPEC.md          - advisory 系统设计
#   7. EMPLOYEE-SELF-SERVE-TOOLS-SPEC.md - 10 个员工自助工具
#   8. SANDBOX-DEPLOY.md              - 4 层沙盒架构

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DOCS_SRC="${REPO_DIR}/docs"
DOCS_DST="${HOME}/.catfish/strategic_docs"

mkdir -p "$DOCS_DST"

# 拷文件清单 (你想加 / 删, 改这个数组)
DOCS=(
  "CATFISH-CENTRAL-MANIFESTO.md"
  "MOAT-ASSESSMENT.md"
  "PATENT-LANDSCAPE.md"
  "PATENT-EXAMINER-AUDIT.md"
  "CAPABILITY-GAPS.md"
  "ADVISORY-FEED-SPEC.md"
  "EMPLOYEE-SELF-SERVE-TOOLS-SPEC.md"
  "SANDBOX-DEPLOY.md"
)

NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
COUNT=0

for doc in "${DOCS[@]}"; do
  src="${DOCS_SRC}/${doc}"
  dst="${DOCS_DST}/${doc}"

  if [[ ! -f "$src" ]]; then
    echo "⚠  跳过: $src 不存在"
    continue
  fi

  # 检查 src 是否已有 frontmatter (避免重复加)
  if head -1 "$src" | grep -q '^---$'; then
    # src 已有 frontmatter, 直接拷
    cp "$src" "$dst"
  else
    # src 没 frontmatter, 加一份
    # 提取 doc 标题 (第一个 # 标题, 没有 fallback 文件名)
    title=$(grep -m 1 '^# ' "$src" | sed 's/^# //' || basename "$doc" .md)

    {
      echo "---"
      echo "type: strategic_doc"
      echo "title: ${title}"
      echo "source: catfish/docs/${doc}"
      echo "synced_at: ${NOW}"
      echo "---"
      echo ""
      cat "$src"
    } > "$dst"
  fi

  COUNT=$((COUNT + 1))
  echo "✓ ${doc}"
done

echo ""
echo "完成: ${COUNT} 份 doc → ${DOCS_DST}"
echo ""
echo "下一步:"
echo "  1. 重启 hermes daemon (让 catfish-memory plugin 重 load)"
echo "  2. catfish chat 任意一句, _render_strategic_docs 就会 inject"
echo "  3. 想验证, 跑 dump prompt 工具看 system prompt 含 '📘 战略 / 设计 doc' 段"
