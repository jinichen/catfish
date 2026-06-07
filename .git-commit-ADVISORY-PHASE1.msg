BL-MANIFESTO-ADVISORY-PHASE1: Advisory Feed + Banner ship

按 docs/ADVISORY-FEED-SPEC.md Phase 1 完整 ship — 中央 publish + 客户端
pull-based banner. 取代主流 fleet 强制 push 模式. 跟 catfish-central-manifesto
公理 4 "中央 API 物理无能" 一致.

## 后端 (Python, 3 新 file + 1 改)

- central/llm-gateway/config/advisories.yaml — 静态 advisory 源 (2 个 demo)
- central/llm-gateway/src/catfish_gateway/advisory_router.py — yaml-based feed
  endpoint, ETag 304 fast path, public GET with SSO 鉴权
- central/llm-gateway/tests/test_advisory.py — 8 个 contract test
- central/llm-gateway/src/catfish_gateway/app.py — 加 include_router

API:
  GET /api/advisory/feed.json  (Bearer 鉴权)
  → 返 active advisory 列表 + metadata
  → 304 fast path: If-None-Match etag 一样
  → 静默优雅降级: yaml 缺失 / 解析失败 → 返 [] 不 panic

## 客户端 Rust (3 新 file + 2 改)

- src-tauri/src/commands/advisory.rs — Tauri commands (本机 SQLite state)
  - advisory_list_local_states / get_local_state
  - advisory_mark_shown / ack / snooze / dismiss
  - 本机 ~/.catfish/advisory_state.db (单表, schema 见 docstring)
- src-tauri/src/commands/mod.rs — pub mod advisory
- src-tauri/src/lib.rs — register 6 个新 command

设计:
  - **不**包 polling task — 前端 useEffect 处理, Rust 端纯 state ops
  - upload_consent 默认 false (Phase 2 加员工自愿上报 catfish-gateway)

## 客户端 React (5 新 file + 1 改)

- src/types/advisory.ts — Advisory / Feed / LocalState 接口
- src/lib/advisory.ts — fetchAdvisoryFeed + matchesAdvisory + shouldShowBanner +
  6 个 Tauri command wrapper. 含 semver-lite (catfish 0.15.x 类版本够用)
- src/hooks/useAdvisory.ts — 启动 + 6h interval polling, 返 banners[] +
  reload()
- src/components/AdvisoryBanner.tsx — 顶部 banner UI, 显最高 severity 那条
  (Phase 1, 多条堆叠 Phase 2 加)
- src/App.tsx — AppShell 顶部 `<AdvisoryBanner />` (AuthBanner 后)
- src/styles/globals.css — +107 行 .advisory-banner* class (E2 brand 一致)

UI 设计:
  - critical → 红色 + 不可永久 dismiss (24h 后重弹) + ⛔ icon
  - high → 红色 + ⚠ icon + 可 snooze
  - medium → 黄色 + ℹ icon + 可 dismiss
  - low / info → 不弹 banner (Phase 2 advisory list 页显)
  - 跟 .approval-banner (E2) 同 design system, focus-visible ring +
    slide-in animation

## 跟 manifesto 公理 4 关系

- ✓ pull-based (客户端拉 feed) — 中央**没** push-to-device API
- ✓ 员工有"忽略 / 稍后提醒"选项 — 中央**无法**强制处理
- ✓ 状态留本机 SQLite — 中央**不知道**员工处理与否 (Phase 2 加自愿上报)
- ✓ critical 24h 后重弹 — 客户端**本机**判断, 不是中央 push

## verify

- tsc --noEmit 全过 (修了 2 个小 warning: 未用 import + ts-expect-error)
- python3 -m py_compile 4 个新/改 file 全过
- cargo check sandbox 无 toolchain, 鸿波本地 build verify
- pytest sandbox 无包, 鸿波本地 `pytest tests/test_advisory.py` verify

## Phase 2 (BL)

- Admin POST /api/advisory (DB 持久化)
- 员工自愿 POST /v1/advisory/ack 上报
- 聚合统计 dashboard
- catfish UI 内 advisory list 页 (顶部菜单 → 安全)

## 11 file 改动 + 1602 行净增

(净增主要在 .advisory-banner CSS + advisory.rs + advisory.ts + AdvisoryBanner.tsx)

跟前 7 份战略 / 战术 doc 联动:
  - ADVISORY-FEED-SPEC.md → 本次实现
  - CATFISH-CENTRAL-MANIFESTO.md → 公理 4 落地证据
  - MOAT-ASSESSMENT.md → counter-positioning 具体产品形态
