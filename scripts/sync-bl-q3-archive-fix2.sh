#!/usr/bin/env bash
# BL-Q3-ARCHIVE fix2 — summary_worker 用 chat 同款模型 (5/11).
#
# 鸿波: '不可能再用小模型, summary 直接用 chat 同款模型'.
#
# 私有部署下 token 不要钱, 用 chat 同款模型 = 数据走向一致 + GPU 同 shard +
# 不用维护 catalog tool_summarizer tag.

set -e
cd "$(dirname "$0")/.."

if [ -f .git/index.lock ]; then
    rm -f .git/index.lock
fi

git fetch origin main 2>&1 | tail -3 || echo "(fetch 失败, 继续)"

git add central/llm-gateway/alembic/versions/20260511_004_tool_archives_origin_model.py
git add central/llm-gateway/src/catfish_gateway/tool_archive/db.py
git add central/llm-gateway/src/catfish_gateway/tool_archive/archiver.py
git add central/llm-gateway/src/catfish_gateway/tool_archive/summary_worker.py
git add central/llm-gateway/src/catfish_gateway/app.py
git add scripts/sync-bl-q3-archive-fix2.sh

git commit -m "BL-Q3-ARCHIVE fix2 (5/11): summary_worker 用 chat 同款模型 (origin_model)

鸿波 5/11 反问: '不可能再用小模型, 直接用 chat 同款模型'.

原设计假设了'摘要应该用小便宜模型', 但这是 SaaS 思维 — 鸿波私有部署 (122b
在 10.10.40.102 内网), token 边际成本 0, 用小模型省的是寂寞. 真正合理的是
让 summary 跟 chat 走同一个模型 — 数据走向一致, GPU 同 shard, 不用维护
catalog tool_summarizer tag.

改动 (5 文件):

1. alembic 20260511_004 — tool_archives 表加 origin_model 列 (VARCHAR 128, nullable)

2. db.py upsert_archive + _pg_get + pick_unsummarized 全链路加 origin_model
   字段 (jsonl 兜底同步)

3. archiver.archive_tool_messages / prepare_tool_messages 接收 origin_model
   参数, 写入 db row

4. app.py chat_completions 调 prepare_tool_messages 时传 model_name (经过
   vision / tool-capable reroute 后的最终模型名)

5. summary_worker._summarize_one 候选选择新顺序:
   a. origin_model (如果 catalog 里 + 可达) 顶到首位
   b. catalog 'tool_summarizer' tag 模型 (没人加 tag 时空)
   c. 兜底 — 任意 chat + private 优先

   保留 fallback chain 是为了 origin_model 不可达时 (例: 切了 catalog)
   仍能继续, 不让摘要全卡.

实测 (sandbox):
  archive 写: origin_model='qwen_v3_5_122b_a10b' 正确记下
  jsonl / PG / pick_unsummarized 全链路读得到

42 单测全过 (没改 test 侧, archive 测试用的是默认 None origin_model 路径).

部署:
  cd central/llm-gateway && alembic upgrade head
  # 重启 gateway 即生效, 下次 chat 触发的 archive 会带 origin_model
"

if [ -n "$(git status --porcelain)" ]; then
    echo "⚠ 还有 untracked / unstaged 改动."
fi

echo
git log origin/main..HEAD --oneline
echo
read -p "确认 push? [y/N] " yn
case $yn in
    [Yy]*)
        git push origin main && {
            echo "✅ done"
            echo
            echo "mac 接下来 (两条):"
            echo "  alembic upgrade head    # 加 origin_model 列"
            echo "  # 重启 gateway"
        }
        ;;
    *) echo "❎ 取消." ;;
esac
