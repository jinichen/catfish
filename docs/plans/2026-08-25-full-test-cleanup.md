# Full Test Cleanup Implementation Plan

**Goal:** 清理本轮发现的全部有效测试失败，让 Gateway、Tool Bridge、Companion 全量测试在隔离环境下稳定通过。

**Scope:** 修复内部 HTTP 调用受系统代理污染、Companion Email store mock API 过期、Tool Bridge 测试受本机归档状态污染的问题；删除已经随 A2A federation 下线而失效的旧 E2E 测试，并同步测试文档。

---

## Task 1: 固化失败用例

- 为 localhost LLM Gateway 调用增加恶意/损坏代理环境回归用例。
- 保留 EmailTab 自循环测试的业务断言，仅补齐真实 store 的 `getState` 契约。
- 让 native result 测试显式关闭本地归档，避免读取开发机配置。

## Task 2: 修复有效缺陷

- Tool Bridge 调用本机 Gateway 时禁用环境代理继承。
- Gateway 的 NO_PROXY 预检容忍从 shell/配置复制进来的智能引号。
- 修复 Companion Email store mock。

## Task 3: 删除废弃覆盖

- 删除依赖已下线 `a2a_self_register` 的 `test_fed25_e2e_chain.py`。
- 从当前测试规范移除该测试；历史 CHANGELOG 保留。

## Task 4: 全量验证

- Gateway 全量 pytest。
- Tool Bridge 全量 pytest。
- Companion 全量 Vitest、TypeScript build、Rust check。
- `bash scripts/check_file_sizes.sh --strict` 与 diff whitespace 检查。
