#!/usr/bin/env bash
# BL-ARCH1 + BL-ARCH2 全段 sync 脚本 (5/10).
#
# 鸿波: "同步代码吧". sandbox 删不掉残留 .git/index.lock, mac 端跑这个就行.
#
# 拆 2 个 commit (跟 BACKLOG 阶段对齐):
#   1. BL-ARCH1 P0+P1+P2+P3: catfish-web 中央门户 + 用户管理 + sysadmin RBAC + LOGO
#   2. BL-ARCH2 + fix1~4:    Companion 瘦身 + WebPortalLink + Tauri shell.open +
#                            webUrl yaml + localStorage + AuthCallback setMe + 心跳

set -e

cd "$(dirname "$0")/.."
ROOT=$(pwd)
echo "→ catfish 根目录: $ROOT"

# 1. 清残留 lock (sandbox 跑过几次 git status, mac 这边可能还卡着)
if [ -f .git/index.lock ]; then
    echo "→ 清 .git/index.lock"
    rm -f .git/index.lock
fi

# 2. 验当前 branch
BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [ "$BRANCH" != "main" ]; then
    echo "⚠ 当前 branch=$BRANCH, 不是 main. 中止." >&2
    exit 1
fi

# 3. Commit #1: BL-ARCH1 全段 (P0+P1+P2+P3)
echo
echo "════════════════════════════════════════"
echo "Commit #1: BL-ARCH1 全段 (P0+P1+P2+P3)"
echo "════════════════════════════════════════"

# 中央 web (P0+P1+P3, 整个新项目)
git add central/web

# identity-server (P1: 用户管理 8 字段 + admin_router + alembic; D6 fix1/2)
git add central/identity-server/.env.example
git add central/identity-server/alembic/versions/20260510_002_users_admin_fields.py
git add central/identity-server/src/catfish_identity/admin_router.py
git add central/identity-server/src/catfish_identity/app.py
git add central/identity-server/src/catfish_identity/users.py
git add central/identity-server/config/users.yaml

# llm-gateway (P1: admin_proxy /api/admin/*; P2: sysadmin role 继承)
git add central/llm-gateway/src/catfish_gateway/admin_proxy.py
git add central/llm-gateway/src/catfish_gateway/app.py
git add central/llm-gateway/src/catfish_gateway/auth/base.py
git add central/llm-gateway/src/catfish_gateway/auth/dev_token.py
git add central/llm-gateway/src/catfish_gateway/rbac.py

# skills-hub (P2: sysadmin 通过 require_admin)
git add central/skills-hub/src/catfish_skills_hub/app.py

git commit -m "BL-ARCH1 (P0+P1+P2+P3, 5/10): catfish-web 中央门户 + 用户管理 + sysadmin

P0 (~2.5K 行 TS): catfish-web 新项目 — vite + react 18 + zustand + react-router +
oidc-client-ts. NavBar/RoleGate/Card 三套基础, 8 路由 (Home/Me/Skills/Mcp/Manager/
Audit/Admin/AuthCallback). 跨端 LOGO 用 catfish-logo.svg 跟 Companion 一致.

P1 (~1.7K 行): identity-server users 加 8 字段 (locked / locked_at / locked_by /
deleted_at / created_by / last_login_at / password_changed_at /
must_change_password) + users_audit 表 + 9 admin endpoints + RBAC (sysadmin > admin
> manager > employee 继承). 防自锁 / 软删除 / bcrypt 12. gateway /api/admin/*
反代 (跟 hub_proxy / mcp_proxy 同模板). catfish-web /admin/users 完整 CRUD +
/admin/system sysadmin only (服务状态 / 操作审计 / 危险操作).

P2 (sysadmin 继承 admin 全权, 修 鸿波 \"是不是第三段没完成的问题?\"):
- gateway User.is_admin() 改 role in (admin, sysadmin), 加 is_sysadmin()
- gateway rbac.ROLE_PERMISSIONS 加 sysadmin: set(Permission)
- gateway has_permission_for_department / require_self_or_department_admin 同款
- gateway dev_token._user_from_dev tier=admin 也含 sysadmin
- skills-hub require_admin 同款扩 sysadmin
(oidc.py 不需要改, role 直接从 IdP claims 透传)

P3 (LOGO 跨端统一, 修 鸿波 \"LOGO 要统一\"):
- catfish-web public/ 复制 catfish-logo.svg / -avatar.svg / -mascot.svg (Companion 同 source)
- index.html favicon /catfish.svg (404) → /catfish-logo.svg
- NavBar / HomePage 改 <img src=/catfish-logo.svg> 替 🐟 emoji
- HomePage isManagerOrAdmin / isAdmin 加 sysadmin (P2 同款继承漏修)
- HomePage 加 sysadmin 专属 \"🔐 系统管理\" tile

D6 fix1/2 (依赖配套):
- identity-server CORS (浏览器 PKCE flow 必须)
- identity-server _load_dotenv (跟 mcp-registry / skills-hub 同款隐性 bug,
  pyproject 写了 python-dotenv 但 app.py 没调)

users.yaml chenhongbo tier admin → sysadmin (PG 5/9 seed 配套 SQL UPDATE).

验证: 浏览器 PKCE 登录 chenhongbo → NavBar 出 🔐 系统 + sysadmin badge → /admin/users CRUD 全行 → /audit fetchGlobalAudit 正常返聚合 (P2 修后).

实际节奏: 原计划 5/15 起 3 周 ARCH1 ship, 鸿波 5/10 \"现在就做\" → 一天 ~4.5K
行 ship, 提前 21 天."

# 4. Commit #2: BL-ARCH2 全段 (Companion 瘦身 + 4 fix)
echo
echo "════════════════════════════════════════"
echo "Commit #2: BL-ARCH2 全段 (Companion 瘦身 + fix1~4)"
echo "════════════════════════════════════════"

git add edge/companion-app/src-tauri/capabilities/default.json
git add edge/companion-app/src-tauri/gen/schemas/capabilities.json
git add edge/companion-app/src-tauri/src/commands/endpoints.rs
git add edge/companion-app/src-tauri/src/services/endpoints.rs
git add edge/companion-app/src/lib/env.ts
git add edge/companion-app/src/lib/markdown.tsx
git add edge/companion-app/src/lib/me.ts
git add edge/companion-app/src/tabs/Dashboard/DashboardTab.tsx
git add edge/companion-app/src/tabs/Dashboard/WebPortalLink.tsx

git commit -m "BL-ARCH2 (Companion 瘦身 + WebPortalLink + fix1~4, 5/10): 客户端瘦身, 管理类去 web

砍 7 张管理类卡 (从 DashboardTab import 移除, .tsx 文件保留待 ARCH3 / 回滚):
  ❌ McpRegistryCard / SkillsHubCard       → web /mcp /skills 浏览
  ❌ SkillAuditCard / AuditCard            → web /admin /audit 跨员工
  ❌ DepartmentQuotaCard / DepartmentAuditCard → web /manager
  ❌ AdminGlobalCard                       → web /admin

留 14 张 \"我的\" 视角卡 (全员看, 离线友好):
  今日:    Proactive + Tasks
  我自己:  Identity + AgentPrefs
  我的画像:Relation + MemoryHistory + UserProfile + StyleFingerprint + Feedback
  服务:    Services + Quota + Catalog + SkillsMcp(我装的) + Curator
  学习:    Learning + SkillRevision

新加 WebPortalLink banner (~250 行): 顶部 \"去中央门户 →\" 按 role 过滤锚点
(/me / /skills / /mcp / /manager / /audit / /admin / /admin/system).

fix1 (Tauri webview 吞 <a target=_blank>):
- WebPortalLink + lib/markdown.tsx onClick preventDefault → @tauri-apps/plugin-shell.open()
- src-tauri/capabilities/default.json 显式加 shell:allow-open

fix2 (硬编码 + sessionStorage 关 tab 丢 + 白屏):
- src-tauri/src/services/endpoints.rs Endpoints + EndpointsYaml 加 web_url/host/port
- src-tauri/src/commands/endpoints.rs RuntimeEndpoints 多返 web_url
- companion-app/src/lib/env.ts config.webUrl + bootstrapEndpoints 接 web_url
- catfish-web/src/lib/auth.ts userStore + stateStore 都改 localStorage
- catfish-web/src/App.tsx AuthCallback 在 navigate 之前主动 fetchMe + setMe;
  主 useEffect 依赖加 me; useAuthStore 用选择器订阅
- catfish-web/src/lib/me.ts Role 加 sysadmin (跟 lib/admin.ts 对齐)
- catfish-web/src/vite-env.d.ts 新建 (修 import.meta.env 类型)

fix3 (webUrl prod fallback 错跑 gateway → 全部 404, 鸿波截图锁定):
- companion-app/src/lib/env.ts 删 if(env.DEV) ... readGatewayUrlBuildTime() 分支,
  默认始终 localhost:5173, prod 客户必须 yaml 显式配
- src-tauri/src/services/endpoints.rs 删 \"gateway 远程 → web 同 host\" 推导

fix4 (catfish-web 没起 → Safari \"无法连接\" 无声失败):
- central/web/vite.config.ts host: 0.0.0.0 + strictPort: true
- WebPortalLink.tsx 加 pingWeb() 心跳 (15s, mode=no-cors timeout=2s)
- offline 时 banner 变红, 链接灰掉, cursor=not-allowed, 不让员工点了看 Safari 报错
- 提示文字: \"未运行 (http://localhost:5173) — 启动: cd central/web && npm run dev\"

视图分层去掉: manager/admin 也走 web 看管理, Companion 不再 role 分支 (只 banner 按 role 过滤).

鸿波诊断功劳: \"链接全部无效\" → fix1 Tauri webview / \"硬编码 + 重登 + 白屏\" → fix2 三连击 / \"全部失效\" + 截图 → fix3 一图锁定 / \"还是一样的\" + 截图 → fix4 vite 没起."

# 5. CHANGELOG / BACKLOG / .gitignore (单独一个 commit, 不夹在功能里)
echo
echo "════════════════════════════════════════"
echo "Commit #3: 文档同步 (CHANGELOG + BACKLOG + .gitignore)"
echo "════════════════════════════════════════"
git add CHANGELOG.md docs/BACKLOG.md .gitignore
git add scripts/sync-bl-arch.sh
git commit -m "docs: 5/10 BL-ARCH1+ARCH2 全段 CHANGELOG + BACKLOG 标 ✅ + nohup.out gitignore

CHANGELOG 加 4 段 (5/10 下午/夜晚):
- BL-ARCH1 P0+P1 + BL-ARCH2 一天合计 ~4.5K 行 (提前 21 天)
- BL-ARCH2 fix1~4 (Tauri shell.open / webUrl yaml / localStorage / 白屏 / 心跳)
- BL-ARCH1 P2 sysadmin 全链路继承 admin
- BL-ARCH1 P3 LOGO 跨端统一 + sysadmin tile

BACKLOG §M BL-ARCH1/ARCH2 标 ✅ 5/10, 时间线对比原 3周+2天 vs 实际一天.

.gitignore 加 nohup.out (skills-hub / mcp-registry 启动脚本会留 runtime log,
当前 central/skills-hub/nohup.out untracked 占位置).

scripts/sync-bl-arch.sh 加 (本脚本)."

# 6. push
echo
echo "════════════════════════════════════════"
echo "Push origin main"
echo "════════════════════════════════════════"
git log --oneline -5
echo
read -p "确认 push 三个 commit 到 origin/main? [y/N] " yn
case $yn in
    [Yy]*) git push origin main ;;
    *) echo "❎ 取消 push, commit 已本地 ship, 后续手动 git push." ;;
esac

echo
echo "✅ done"
