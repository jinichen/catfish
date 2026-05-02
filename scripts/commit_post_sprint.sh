#!/bin/bash
# 五一 sprint 5/2 ~ 5/5 收尾 commit 脚本
#
# 沙盒里没法直接写 .git, 所以从主机 Mac 终端跑这个.
#
# 用法:
#   cd ~/person_task/catfish
#   bash scripts/commit_post_sprint.sh

set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"

echo "=== Step 1/3: RBAC + Quota (5/2 + 5/3) ==="
git add \
  central/identity-server/src/catfish_identity/users.py \
  central/identity-server/tests/test_users.py \
  central/llm-gateway/src/catfish_gateway/rbac.py \
  central/llm-gateway/src/catfish_gateway/quota.py \
  central/llm-gateway/tests/test_rbac.py \
  central/llm-gateway/tests/test_quota.py \
  docs/RBAC-DESIGN.md \
  docs/QUOTA-DESIGN.md \
  docs/BACKLOG.md \
  scripts/sprint_e2e_test.sh

git commit -m "feat(rbac+quota): 五一 sprint 5/2 + 5/3 — RBAC 三角色 + Quota 三维滑动窗口

5/2 RBAC (BL-D8):
- catfish_identity.users: 加 role + managed_departments + effective_role()
  * 默认 role=employee, role=manager 必须配 managed_departments 列表
  * 兜底 role: 邮箱 ceo/admin@ → admin, hr/finance/it@ → manager, 其他 → employee
- catfish_gateway.rbac: Permission Enum + ROLE_PERMISSIONS 矩阵
  * 8 个权限维度
  * 3 角色: employee / manager (+team_view 本部门) / admin (全权)
  * require_permission() FastAPI 依赖, 401 (未登) / 403 (无权)
- 测试: test_rbac.py 16 个用例

5/3 Quota (BL-D9):
- catfish_gateway.quota: QuotaConfig + sliding window sqlite store
  * 三维: per_user (minute+day) / per_model (day) / per_department (day)
  * yaml 配置: defaults + overrides 覆盖, 0 表示不限
  * check_quota() 任一维度超就拒, friendly_quota_message 提示切到 catfish-private-main
  * estimate_tokens(text): 4 字符 ≈ 1 token, 最低 1000
- 测试: test_quota.py 17 个用例, 滑动窗口 1 分钟边界 + override

文档:
- docs/RBAC-DESIGN.md
- docs/QUOTA-DESIGN.md
- docs/BACKLOG.md: 标 BL-D8 D9 D17 为 🔵 MVP

测试: gateway 67 通过"

echo
echo "=== Step 2/3: PG 中央数据库 (5/4) ==="
git add \
  .gitignore \
  central/identity-server/pyproject.toml \
  central/identity-server/src/catfish_identity/db.py \
  central/identity-server/src/catfish_identity/app.py \
  central/identity-server/src/catfish_identity/registry.py \
  central/identity-server/src/catfish_identity/users.py \
  central/identity-server/config/database.yaml.example \
  central/identity-server/tests/test_pg_integration.py

git commit -m "feat(pg): 五一 sprint 5/4 — 中央数据库迁 PostgreSQL (BL-D17)

catfish-identity + gateway 共享 PG 实例 + 不同表:
- catfish-identity: users, registry_agents
- gateway: quota_events, gateway_audit (gateway 那边后续 ship)

切换逻辑 (db.py db_url() 优先级):
1. env CATFISH_DB_URL  (跨进程统一最优先, docker-compose / k8s 用)
2. config/database.yaml 的 url 字段
3. 都没 → yaml/sqlite fallback (单测 / 单机 dev 模式)

新增:
- catfish_identity.db: asyncpg 池 + init_schema (CREATE TABLE IF NOT EXISTS)
- config/database.yaml.example: 模板, 真 yaml 进 .gitignore (含密码)
- catfish_identity.app: lifespan hook 启动时 init_schema → seed_pg_from_yaml_if_empty
  → reload_from_pg (优先级反转: PG 在了就用 PG, 否则 yaml)
- users.py: reload_from_pg + seed_pg_from_yaml_if_empty
- registry.py: 双 backend, _load/_save 自动选 PG 或 yaml

测试: test_pg_integration.py 3 个真 PG 测 (CATFISH_TEST_DB_URL 没设跳过)
依赖: asyncpg>=0.29.0"

echo
echo "=== Step 3/3: 浮窗 UI Cmd+Shift+Space (5/5) ==="
git add \
  edge/companion-app/src-tauri/Cargo.toml \
  edge/companion-app/src-tauri/capabilities/default.json \
  edge/companion-app/src-tauri/src/lib.rs \
  edge/companion-app/src/App.tsx \
  scripts/commit_post_sprint.sh

git commit -m "feat(浮窗): 五一 sprint 5/5 — Cmd+Shift+Space 全局召唤鲶鱼浮窗

设计:
- 任何 app 里按 Cmd+Shift+Space → 鲶鱼浮窗居中弹出 + 置顶 + 抢焦
- 已弹出再按一次 → 收起 (toggle 行为)
- Esc (前端 App.tsx) → 隐藏 (输入态不抢, 让组件用)

实现:
- Cargo.toml: tauri-plugin-global-shortcut = '2.0'
- src/lib.rs: 注册 plugin + setup hook 注册 shortcut + handler 切换 visibility
- capabilities/default.json: global-shortcut:default + allow-register/unregister
- App.tsx: useEffect 加 keydown 监听, Esc 调 getCurrentWindow().hide()

容错:
- 快捷键被其他 app 占用 → log warn 不挂掉 app
- handler 内 window 操作全部用 let _ = ... 忽略错误
- unminimize() 处理最小化态"

echo
echo "=== 完成. 推送到 GitHub: ==="
echo "  git push origin master"
echo
echo "=== 后续手动验证 (Mac 上跑): ==="
echo "  cd edge/companion-app"
echo "  npm run tauri:dev    # 起 dev 看 log: '已注册全局快捷键 Cmd+Shift+Space → 召唤鲶鱼浮窗'"
echo "  # 然后切到任意其他 app, 按 Cmd+Shift+Space, 鲶鱼应该居中弹出"
