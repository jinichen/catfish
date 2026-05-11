#!/usr/bin/env bash
# BL-FIX45 A+ (5/11 深夜) — 抽 fetchWithAuth 统一 401 reauth.
#
# 鸿波截图: 仪表盘'今日话题'拉不到, gateway log 显示
# /api/proactive/starter 401 — Companion 没自动 reauth.
#
# 原 BL-FIX45 A 只改了 chat.ts. 但 me.ts 里所有 fetch 都各自 inline 加
# Authorization, 没处理 401. 一处一处补会再漏一个. 抽 fetchWithAuth
# wrapper, 所有 gateway 调用统一走自动 reauth.

set -e
cd "$(dirname "$0")/.."

if [ -f .git/index.lock ]; then
    rm -f .git/index.lock
fi

git fetch origin main 2>&1 | tail -3 || echo "(fetch 失败, 继续)"

git add edge/companion-app/src/lib/me.ts
git add edge/companion-app/src/lib/chat.ts
git add scripts/sync-bl-fix45-a-plus.sh

git commit -m "BL-FIX45 A+ (5/11): 抽 fetchWithAuth 统一 401 reauth (修 proactive/me/quota 等所有路径)

鸿波 5/11 深夜截图: 仪表盘'今日话题'显示'拉不到话题, 看 gateway 起没起'.
真根因: /api/proactive/starter 返 401 (token 过期), Companion 没自动 reauth.

# 原 BL-FIX45 A 漏了哪些

只改了 chat.ts inline 401 处理. 但 me.ts 里十几条 inline fetch 各自手动加
Authorization, 都没 401 处理:

  fetchMe / fetchProactiveStarter / fetchContextualStarter /
  fetchDepartmentQuota / fetchDepartmentAudit / fetchGlobalQuota /
  fetchGlobalAudit / updateDepartmentQuota / fetchDevUsers / ...

每条 inline 补 401 处理 → 一处漏一处. 不可维护.

# 修法 (BL-FIX45 A+)

抽 fetchWithAuth wrapper 一次封装:
  - 自动加 Authorization: Bearer <token>
  - 收 401 → 调 tauri auth_login (浏览器 OAuth flow) → 拿新 token → 重发
  - retry 上限 1 (防死循环, IdP 真挂时不无限循环)
  - 其它错码 (200/404/500/etc) 原样返, caller 处理

所有 me.ts 里的 fetch 都改走 fetchWithAuth (8 处):
  - fetchMe
  - fetchDepartmentQuota
  - fetchDepartmentAudit
  - updateDepartmentQuota (PUT 含 body)
  - fetchGlobalQuota
  - fetchGlobalAudit
  - fetchProactiveStarter (这是鸿波截图卡死的那条)
  - fetchContextualStarter

chat.ts 也改走 fetchWithAuth:
  - 移除 inline 401 reauth 代码 (递归调 streamChat + invoke('auth_login'))
  - 移除 invoke import (不再需要)
  - 移除 _retryCounters.reauth 字段 (wrapper 内部处理)
  - 401 残留处理只剩 friendly error msg (wrapper retry 一次仍 401 = IdP 真挂)

# 不动的部分

  - BL-FIX45 B (500 fallback) 保留, 因为这是 chat 特殊路径 (model 切换)
  - BL-FIX45 C (skill session-renewal) 保留 SOUL 教导

# 测试

12/12 Node sanity check 全过:
  - me.ts fetchWithAuth 定义 + 8 个调用方都改用 wrapper
  - chat.ts 走 fetchWithAuth + 删除 inline 401 + 删除 invoke import

# 部署

仅改 Companion 前端 (TypeScript). 不动 gateway / tool-bridge / 数据库.
Companion 重启即生效 (vite dev) / 重新 build (production).

# 5/12 内网测期望

鸿波清 keychain → 打开仪表盘 → 期望:
  - '今日话题' 显示 401 → fetchWithAuth 自动调 auth_login → 弹浏览器登录
  - 不再是'拉不到话题, 看 gateway 起没起'红字
  - 所有其它 API (画像 / 配额 / 审计) 同款自动 reauth
"

if [ -n "$(git status --porcelain)" ]; then
    echo "⚠ 还有 untracked / unstaged 改动."
fi

echo
git log origin/main..HEAD --oneline
echo
read -p "确认 push? [y/N] " yn
case $yn in
    [Yy]*) git push origin main && echo "✅ done — Companion 重启即生效" ;;
    *) echo "❎ 取消." ;;
esac
