BL-CATFISH-MANIFESTO clean-up: 3 个 ❌ 违反 endpoint reframe

跟 docs/CATFISH-CENTRAL-MANIFESTO.md (6/7 ship) 联动. manifesto 公理 4
"中央 API surface 物理无能" 落地到具体 endpoint clean-up.

audit 现有 catfish 中央 API surface 37 个 endpoint, 28 合规 (75.7%), 6 灰色
(16.2%), 3 违反 (8.1%). 本次 clean 3 违反:

## 1. POST /admin/users/:email/lock (identity-server)

不删功能, 加 docstring + response 字段明确 reframe:

  - 这是"撤销中央 SSO seat", 不是"远程禁用员工本机 catfish"
  - 员工本机 catfish + BYO API key 完全不受影响 (响应 affects_local_data: false)
  - 类似 GitHub admin "revoke seat" — 撤 license seat 不动本机代码

response 新加:
  - affects_local_data: false (admin UI 显给 IT 看)
  - scope: "central_sso_seat_only"

## 2. POST /admin/users/:email/reset-password (identity-server)

加 deprecated marker + docstring 标 BL-MANIFESTO-RESET-FLOW (长期改 admin
trigger reset link + 员工自助改密码, admin 不再能直接 set 密码).

短期保留 — 实际触发场景通常是"员工忘记密码 + 找 admin 帮忙重置", 员工主动
请求 admin 协助. 但行为本身跟公理 3 有摩擦, deprecated 出来等后续重构.

response 新加:
  - affects_local_data: false
  - scope: "central_sso_password_only"
  - deprecated: true
  - deprecated_note: 说明长期改造方向

## 3. /api/tool-archives/* (llm-gateway)

最严重的违反 — gateway 中央 archive 工具结果. 5/22 已经做了 edge 端重构
(catfish-tool-bridge tool_archive_local.py), gateway HTTP API 是死代码,
但还 mount 在 fastapi 上. 本次彻底断 wiring:

  - app.py: 删 tool_archive_router include (HTTP /api/tool-archives/* 不再 mount)
  - app.py: 删 summary_worker lifespan 启动 (5/22 后没新 archive 进 PG)
  - archiver.py: prepare_tool_messages **永远走 truncate**, 删 env=1 PG fallback
    * 原来 env=1 能回滚老 PG archive (debug 用) — 彻底删, 是 ops 后门
    * 现在物理上 prepare_tool_messages **永不写 PG**, 只能 truncate 硬切
    * 这是 manifesto 公理 4 "API surface 物理无能" 的直接落地

dead code 文件留 git history (router.py / db.py / reader.py / summary_worker.py
/ archiver.py PG fallback path), 后续 BL-MANIFESTO-CLEAN-DEAD 整套 rm. 本次
只断 wiring + 删 ops 后门, 不动 file (减 risk).

## test 更新

central/llm-gateway/tests/test_tool_archive.py:
  - 删 test_prepare_enabled_archives (老期望 env=1 走 PG, 现在不可能)
  - 删 test_prepare_disabled_falls_back_to_fix41 (跟新 test 重叠)
  - 改 test_prepare_default_phase6a_disables_pg_archive →
    test_prepare_default_truncates (default 行为 unchanged)
  - 加 test_prepare_always_truncates_never_archives — 强 guarantee 即使 env=1
    也走 truncate (防 ops 手抖回滚)
  - 加 test_prepare_env_rollback_blocked — 防 ops 设 "true" 试图回滚

## verify

- python3 -m py_compile 全过 (4 个 file)
- sandbox 无 catfish_gateway 包, pytest 没法跑, 鸿波本地 pytest verify

## 资金 / 时间 / 风险评估

- 工作量: 1 小时
- risk: 低 (router 死代码 5/22 之后没员工 caller, env=1 是 ops 后门没正常 traffic)
- 收益: 高 (manifesto 公理 4 落地, 减 8.1% 违反到 5% 以下; reset-password
  deprecated 出来等后续 BL 完整改造)

剩余 6 个 ⚠ 灰色 endpoint (admin user CRUD / skill DELETE) 走后续 BL 单独
review + reframe, 不在本次 scope.
