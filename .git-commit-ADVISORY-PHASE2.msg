BL-MANIFESTO-ADVISORY-PHASE2: DB 持久化 + admin CRUD + admin UI

按 docs/ADVISORY-FEED-SPEC.md Phase 2 完整 ship. 中央 admin 现在能 publish /
revoke advisory, 客户端 6h 内 pull 拿到. 跟 catfish-central-manifesto 公理 4
"中央 API 物理无能" 一致 — 中央 publish, 客户端 pull, 没 push.

## 改动 (5 新 + 4 改)

### 1. Alembic migration (新)

central/llm-gateway/alembic/versions/20260607_005_advisories.py:
  - CREATE TABLE advisories (跟 spec §6.1)
  - ix_advisories_published (DESC for feed ordering)
  - ix_advisories_active (partial WHERE revoked_at IS NULL)

### 2. advisory_db.py CRUD module (新)

跟 facts_db.py 同 _use_pg() pattern. 区别: advisory 是**中央 publish 内容**
不是员工数据, PG 失败不走 jsonl 兜底, 直接 fail loud (admin 看错误自己 fix).

  - pg_list_active() — feed.json 用 (未 revoked + 未 expired)
  - pg_list_all(include_revoked) — admin dashboard 用
  - pg_get(id)
  - pg_insert(advisory, published_by) — sysadmin publish
  - pg_revoke(id, revoked_by) — 软删 (改 revoked_at, 不真 drop)

### 3. advisory_router.py (大改)

prefix 从 /api/advisory → /api (让 admin endpoint 也在 /api/admin/* 一致).
endpoint path 完全兼容 (客户端 FEED_PATH = /api/advisory/feed.json 不变).

新增:
  - POST /api/admin/advisory (sysadmin only)
    * pydantic AdvisoryPublishReq + AdvisoryTargetReq + RemediationActionReq
    * 校验 id 格式 / severity / category enum
    * RuntimeError (PG 未配) → 503
    * unique violation → 409
  - DELETE /api/admin/advisory/:id (sysadmin only) — 软删
  - GET /api/admin/advisory (admin / sysadmin) — list 全部含 revoked

GET /api/advisory/feed.json 改:
  - 读优先级: PG > yaml fallback
  - PG 配了但表空 → fallback yaml (seed / demo / 平滑迁移)
  - PG 没配 → 走 Phase 1 yaml mode

### 4. test_advisory.py (+8 个 Phase 2 test)

mock-based (sandbox 无 PG):
  - require_sysadmin RBAC (admin/manager/employee 全拒)
  - advisory id pattern (CATFISH-ADV-YYYY-NNN)
  - use_pg env check
  - pg_insert without DB_URL raises (fail loud)
  - pg_list_active without DB returns empty
  - feed fallback yaml when PG empty
  - feed prefers PG when has data

### 5. central/web admin UI (2 新 + 1 改)

central/web/src/lib/advisory.ts (新, 76 行):
  - 走 api.ts Bearer auth (跟 facts.ts 同模板)
  - advisoryApi: list / publish / revoke

central/web/src/routes/admin/AdvisoryPage.tsx (新, 320 行):
  - sysadmin only (RoleGate)
  - 列表 table (含 revoked, severity 色 + status pill)
  - "+ 发布 advisory" 表单 (id auto-gen / severity / category / title /
    description / recommendation / target.skill / skill_version_pattern /
    catfish_version_pattern / expires datetime-local)
  - Revoke 按钮 (confirm 后 DELETE)

central/web/src/routes/AdminPage.tsx (+4 行):
  - import AdvisoryPage
  - Route path="advisory" 注册
  - NavTile 🛡 "Advisory 管理 (sysadmin)"

## 跟 manifesto 公理 4 关系

- ✓ pull-based feed (客户端拉) — 中央**没** push-to-device API
- ✓ admin 只能 publish/revoke, **不能** force-install / disable on device
- ✓ revoke 是软删 (改 revoked_at), 审计可见. 不会让员工"突然 advisory 消失"
  → manifesto 哲学: 员工本机看到的 advisory 状态是稳定的
- ✓ sysadmin only (publish/revoke 严级) — 普通 admin 都不能改全员看到的 advisory

## verify

- python3 -m py_compile 4 file 全过
- web tsc --noEmit 全过
- pytest sandbox 无 catfish_gateway 包, 鸿波本地 verify
- alembic upgrade head 跑迁移后 advisory_router 自动用 DB
- 鸿波 admin UI: /admin/advisory 看 list, 点"+ 发布" 表单 ship 完整流程

## Phase 3 BL (留下次)

- POST /v1/advisory/ack — 员工自愿上报处理状态 (默认关)
- GET /admin/advisory/:id/stats — 聚合统计 (处理率 / no_response_count /
  by_department) — admin dashboard
- 客户端"自动上报"开关 (设置 → 隐私)

## 9 file 改动

- migration: 1 新
- gateway: 2 新 (advisory_db) + 2 改 (router + test)
- web: 2 新 (lib + admin page) + 1 改 (AdminPage)

跟前 7 份战略 / 战术 doc + Phase 1 ship 一起联动 — manifesto 公理 4
完整产品落地.
