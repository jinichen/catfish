#!/usr/bin/env bash
# BL-Q3-FACT P0 MVP 全 4 天 sync 脚本 (5/10 夜).
#
# Day 1 后端已 push (上一次手动). 这次推 Day 2/3/4: catfish-web UI + approve
# 接通 + demo 资产.

set -e
cd "$(dirname "$0")/.."

if [ -f .git/index.lock ]; then
    rm -f .git/index.lock
fi

git fetch origin main 2>&1 | tail -3
BEHIND=$(git rev-list --count HEAD..origin/main)
if [ "$BEHIND" -gt 0 ]; then
    echo "⚠ 本地落后远程 $BEHIND 个 commit, 先 git pull --rebase"
    exit 1
fi

# ── Commit #1: Day 2 + Day 3 (web UI + approve/reject 接通) + PG 统一 ──
echo "════════════════════════════════════════"
echo "Commit #1: FACT Day 2+3 — catfish-web UI + SkillsHub publish + PG 4 张表"
echo "════════════════════════════════════════"
git add central/web/src/lib/facts.ts
git add central/web/src/lib/api.ts
git add central/web/src/routes/admin/FactsPage.tsx
git add central/web/src/routes/AdminPage.tsx
git add central/llm-gateway/src/catfish_gateway/facts_router.py
# BL-Q3-FACT PG 统一 (5/10): 4 张表 + facts_db.py PG layer
git add central/llm-gateway/alembic/versions/20260510_002_fact_patch_system.py
git add central/llm-gateway/src/catfish_gateway/facts_db.py

git commit -m "BL-Q3-FACT P0 MVP Day 2+3 (5/10): catfish-web /admin/facts UI + SkillsHub approve 接通

Day 2 — catfish-web UI:
- lib/facts.ts (NEW): API client + 10 个 TypeScript schema (FactMeta/FactPoint/SkillImpact/SkillPatch/FactDetail/FactAuditEvent 等)
- lib/api.ts: + postFormData() (multipart 上传)
- routes/admin/FactsPage.tsx (NEW, ~600 行): 三视图 (列表 + 上传 + 详情)
  * 列表页: 已上传 fact 卡片 + StatusBadge + 一行 5 字段 (文件/大小/上传者/受影响数/patch 数)
  * 上传表单: 文件 + 标题 + 生效日期, 上传完自动 navigate 到详情 + auto-analyze
  * 详情页: meta + LLM 摘要 + 事实点列表 (含原文引用 details) + 受影响 skill (confidence 颜色编码) + patches (diff 红绿对照 + 完整改后内容 details) + audit log
- routes/AdminPage.tsx: 加 facts/* 路由 + NavTile '📋 政策同步 (FACT)'

Day 3 — approve/reject 真接通 SkillsHub:
- facts_router.py: + POST /api/facts/{id}/patches/{idx}/approve
                   + POST /api/facts/{id}/patches/{idx}/reject
  * approve: 强制改 SKILL.md frontmatter version 为 <原 v>.fact-<id 前 8 位>
            multipart POST 到 skills-hub /skills/<ns> 发布新版本
            标 patch status=approved + 改 fact meta (全 approve 时改 fact status=approved)
  * reject: 标 status=rejected, 不发布
  * _bump_version_in_skill_md: 正则替换 frontmatter version (有 fence/无 fence 都处理)
- FactsPage.tsx PatchCard: 替换原 alert 占位为真调 approvePatch/rejectPatch,
  二次确认弹窗 (告知 version 命名规则), 弹成功消息显示新版本号
- 状态显示: pending/approved/rejected 不同样式 + 已采纳显示发布版本号

PG 统一 (跟 mcp-registry / skills-hub / identity / gateway 4 个 service 一致):
- alembic/versions/20260510_002_fact_patch_system.py (NEW): 4 张表
  fact_changes / fact_skill_impacts / fact_skill_patches / fact_audit
  + 12 个索引 (按 ts / status / skill / confidence / user / action)
  + CASCADE 删除 (fact 删了所有 impact + patch + audit 跟着删)
- facts_db.py (NEW, ~430 行): PG 主存储层
  * pg_upsert_fact / pg_write_facts_json / pg_replace_impacts / pg_replace_patches
  * pg_update_patch_status / pg_audit (PG writers, 失败兜底 jsonl)
  * pg_list_facts / pg_get_fact_detail / pg_get_patches (PG readers, 返 None 兜底)
- facts_router.py 双写: 写永远先 jsonl 再 PG (失败不阻塞业务),
  读优先 PG (SQL 一次性 + 索引快), PG 不可用 fallback jsonl

部署:
  CATFISH_DB_URL=postgresql://... alembic upgrade head
  (会 apply 20260510_002 migration, 跟现有 quota_events / gateway_audit 同库)
"

# ── Commit #2: Day 4 demo 资产 ──
echo
echo "════════════════════════════════════════"
echo "Commit #2: FACT Day 4 — 5/14 demo 资产 (mock 政策文件 + 3 个 sample skill + 演示脚本)"
echo "════════════════════════════════════════"
git add docs/samples/bl-q3-fact-demo/

git commit -m "BL-Q3-FACT Day 4 (5/10): 5/14 demo 资产 (mock 政策 + 3 sample skill + 7 步剧本)

docs/samples/bl-q3-fact-demo/:
- README.md: 演示前置准备 + 预期结果 + 故障排查 + 回收清理
- DEMO-SCRIPT.md: 5/14 现场 7 步演示剧本 (8-12 分钟), 含:
  * 0:00-1:30 铺垫: 关键提问打开客户痛感
  * 1:30-3:30 现状: LearningCard + 14 天追踪 + sysadmin 审计
  * 3:30-7:00 主菜: 上传 → 提取事实点 → 找受影响 → 生成 patch → 采纳发布
  * 7:00-9:00 升华: 为啥只有 catfish 能做 (通用 LLM / 企业知识库 各自为啥不行)
  * Q&A: 6 个典型客户问题 + 救场剧本 3 个
- policy-purchase-revision.md: mock '公司采购流程修订 v3.2' (5 条事实变更, 含 raw_quote)
- skill-send-purchase-request.md: sample skill #1 (预期命中, confidence 95%, 5万→3万阈值)
- skill-quarterly-budget-review.md: sample skill #2 (预期命中, confidence 75%, 2 周→3 周报送)
- skill-vendor-onboarding.md: sample skill #3 (v2.0 已对齐 ISO 27001, 预期不命中, 演 FACT 不误报)

演示叙事核心:
1. 通用 LLM 永远卡训练 cutoff 不可能跟上贵司变化
2. 企业知识库厂家没'白盒可执行 skill'层, 改不了员工日常工具
3. catfish 站中间真空: 既是员工日常工具又是白盒 skill 体系
4. 白盒可审计 = 央企合规过得了的唯一 continual learning 方案
"

# ── push ──
echo
git log origin/main..HEAD --oneline
echo
read -p "确认 push? [y/N] " yn
case $yn in
    [Yy]*) git push origin main && echo "✅ done" ;;
    *) echo "❎ 取消" ;;
esac
