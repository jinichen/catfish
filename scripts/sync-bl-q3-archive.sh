#!/usr/bin/env bash
# BL-Q3-ARCHIVE — tool message archive + 摘要双层 (5/11 鸿波 "现在就做").
#
# 替代 BL-FIX41 硬切: lossless 保留 + LLM 主动 catfish_read_tool_archive
# 召回中段. 跟 FIX41 共存 (灰度 env 开关), archive 写挂自动降级 FIX41.

set -e
cd "$(dirname "$0")/.."

if [ -f .git/index.lock ]; then
    rm -f .git/index.lock
fi

git fetch origin main 2>&1 | tail -3 || echo "(fetch 失败, 继续 — 离线 commit)"

# ── 新增 / 修改文件 (按模块顺序) ──

# Alembic migration
git add central/llm-gateway/alembic/versions/20260511_003_tool_archives.py

# 新 tool_archive 包 (8 文件)
git add central/llm-gateway/src/catfish_gateway/tool_archive/__init__.py
git add central/llm-gateway/src/catfish_gateway/tool_archive/archiver.py
git add central/llm-gateway/src/catfish_gateway/tool_archive/db.py
git add central/llm-gateway/src/catfish_gateway/tool_archive/features.py
git add central/llm-gateway/src/catfish_gateway/tool_archive/prompts.py
git add central/llm-gateway/src/catfish_gateway/tool_archive/reader.py
git add central/llm-gateway/src/catfish_gateway/tool_archive/router.py
git add central/llm-gateway/src/catfish_gateway/tool_archive/summary_worker.py

# 改动: app.py 接入 / internal_models 加 tool_summarizer use_case
git add central/llm-gateway/src/catfish_gateway/app.py
git add central/llm-gateway/src/catfish_gateway/internal_models.py

# 单测
git add central/llm-gateway/tests/test_tool_archive.py

# tool-bridge 加 catfish_read_tool_archive 工具
git add edge/tool-bridge/src/catfish_tool_bridge/catfish_tools.py
git add edge/tool-bridge/src/catfish_tool_bridge/read_tool_archive.py

# SOUL.md 教育段
git add edge/identity/SOUL.md

# 设计文档
git add docs/CATFISH-Q3-ARCHIVE-DESIGN.md

# sync 脚本自己
git add scripts/sync-bl-q3-archive.sh

git commit -m "BL-Q3-ARCHIVE (5/11): tool message archive + 摘要双层 (替代 FIX41 硬切, lossless)

修 context overflow 真根因 — FIX41 硬切丢中段是损失, 改 archive 完整保留 +
LLM 主动召回. 鸿波 5/11 '直接上 Q3 不要考虑别的', 提前 4 天从 5/15 拉到 5/11.

架构 (设计文档 docs/CATFISH-Q3-ARCHIVE-DESIGN.md 18 段 + FAQ):

  ① tool message > 4KB → 写 PG (lossless 全文 + 14 天保留)
  ② prompt 替换成: ref + 头 500B + 尾 500B + (异步 haiku) 摘要
  ③ LLM 看到 [已归档: archive_ref=...] 自行调
     catfish_read_tool_archive(ref, grep / line_range) 拿中段
  ④ summary_worker 后台 5s 扫表, 调 gateway loopback chat 走
     tool_summarizer use_case (private haiku tier, 不动用户配额)

新增模块 (central/llm-gateway/src/catfish_gateway/tool_archive/):

  archiver.py        主流程 + ref 计算 + session_id 派生
  db.py              PG 主 + jsonl 兜底 (跟 facts_db/mcp-registry 同模板)
  prompts.py         替换文本模板 + 摘要 prompt (反幻觉规则)
  reader.py          grep ±5 行上下文 / line_range / max_bytes
  router.py          /api/tool-archives/{read,gc,<ref>}
  summary_worker.py  asyncio 后台 worker + 候选切换
  features.py        env 灰度开关 (默认开, fallback FIX41)

Alembic: 20260511_003 — tool_archives 表 + 5 索引

接入:
  - app.py prepare_tool_messages 替换 truncate_tool_messages
  - app.py lifespan 启动 summary_worker
  - app.py 挂 tool_archive_router
  - internal_models KNOWN_USE_CASES 加 'tool_summarizer'
  - tool-bridge catfish_tools.py 加 catfish_read_tool_archive schema + 分发
  - tool-bridge read_tool_archive.py (~120 行, 跟 skill_publish 同款 OAuth)
  - SOUL.md 加 '看到 [已归档: archive_ref=...] 怎么办' 6 条铁律段

单测: 31 个全过 (tests/test_tool_archive.py)
  - 阈值边界 / 消息数保持 / ref 幂等 / 不污染原 list
  - 替换文本 3 态 (ready / pending / failed)
  - reader grep / line_range / max_bytes 三模式
  - db jsonl 兜底 upsert/get/pick_unsummarized/update_summary
  - features 全局开关 / 白名单 / threshold env
  - prepare 整合 (enabled archive / disabled 降级 FIX41)
  - session_id 派生 3 路径 (conversation_id / messages hash / 当天日期)

实测 (sandbox, 鸿波 demo 前夜真实场景):
  199 messages / 63 tool_msgs / 258KB → 112KB
  省 146KB ~36K tokens, 消息数完全保留
  跟 FIX41 硬切效果相当, 但全部 lossless 可召回

灰度开关 (env):
  CATFISH_TOOL_ARCHIVE_ENABLED        默认 true (鸿波 '现在就做')
  CATFISH_TOOL_ARCHIVE_USERS          白名单 (空=全员)
  CATFISH_TOOL_ARCHIVE_THRESHOLD      触发阈值字节, 默认 4000
  CATFISH_TOOL_ARCHIVE_SUMMARY        摘要开关, 默认 true
  CATFISH_TOOL_ARCHIVE_FALLBACK_FIX41 archive 写挂降 FIX41, 默认 true
  CATFISH_TOOL_ARCHIVE_RETENTION_DAYS retention 天数, 默认 14
  CATFISH_TOOL_ARCHIVE_DIR            jsonl 兜底目录

部署后:
  1. cd central/llm-gateway && alembic upgrade head — 建 tool_archives 表
  2. 重启 gateway — summary_worker 自启
  3. 重启 Companion/tool-bridge — 加载 catfish_read_tool_archive
  4. 看 gateway log:
     'BL-Q3-ARCHIVE: 归档 N 条 tool message, 省 XXXXX 字节 (~XXK tokens)'
     'summary_worker ref=... ok model=...'

未做 (P1, demo 之后):
  - /admin/archives UI 页面
  - 敏感 archive 标记 (短 retention)
  - PG → S3 迁移 (上云时)
  - archive 向量检索 (Q4 GRAPH)
"

if [ -n "$(git status --porcelain)" ]; then
    echo "⚠ 还有 untracked / unstaged 改动, 跳过. 这次只关心 Q3 archive."
fi

echo
echo "ahead commits:"
git log origin/main..HEAD --oneline
echo
read -p "确认 push? [y/N] " yn
case $yn in
    [Yy]*)
        git push origin main && echo "✅ pushed — 接下来 mac 上跑:"
        echo "  cd central/llm-gateway && alembic upgrade head"
        echo "  # 重启 gateway + Companion 即生效"
        ;;
    *) echo "❎ 取消. 后续: git push origin main" ;;
esac
