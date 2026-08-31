# 鲶鱼 · 测试规范 (Testing Specification)

> **版本**: v1.0 (2026-06-06 创建)
> **维护**: 鸿波
> **目的**: 定义全项目测试约定、分层、CI matrix、coverage 目标。新模块加测试时按这份规范。
>
> **跟其它 doc 的关系**:
> - `docs/TEST-PLAN-20260606.md` — 针对性测试 plan (marathon ship 后回归验证)
> - 本 spec — 通用规范, 每个 module 测试都遵循

---

## 0. 当前测试统计 (2026-06-06 audit baseline)

### Python (175 个 test 文件)

| 模块 | test 数 | CI 跑? | 备注 |
|---|---|---|---|
| `central/llm-gateway` | 78 | ✓ | 主测试覆盖 (~1300+ test case) |
| `edge/tool-bridge` | 42 | ✓ (3 E2E ignore) | 含 adapter / sandbox / mcp_client |
| `central/identity-server` | 9 | ✗ | OAuth grant / OIDC discovery |
| `central/mcp-registry` | 4 | ✗ | MCP server 注册 + manifest 拉取 |
| `edge/local-search` | 4 | ✗ | hermes 0.13 vector search |
| `edge/email-agent` | 4 | ✗ | Foxmail .box parser + macOS adapter |
| `edge/hermes-plugins/catfish-memory` | 4 | ✗ | 5 kind 路由 + prefetch |
| `edge/companion-app/src-tauri/tests` | 3 | ✗ | parse_file_test.py + 其它 |
| `central/secret-broker` | 2 | ✗ | edge tool key 管理 |
| `edge/journal-agent` | 2 | ✗ | journal 维度抽取 |
| `edge/hermes-plugins/catfish-xcatfish-user` | 2 | ✗ | P14 chinese alias / 透传 |
| `central/skills-hub` | 1 | ✗ | skill register/install/search |

**事实**: CI 只跑 **2/12** Python 模块, **10 个模块没自动跑测试**, 完全靠开发者本地手跑。

### TypeScript (8 个 `*.test.ts`)
- `companion-app/src/lib/*.test.ts` (4): promiseCheck, me, sessionGroup, linkify_paths
- `companion-app/src/store/*.test.ts` (4): recmode, email, queue, auto_continue
- **CI 没跑** vitest, package.json `scripts` 没 `test` 入口

### Rust (25 个 .rs 含 inline `#[cfg(test)]`)
- `companion-app/src-tauri/src/commands/*.rs` 内 `mod tests` (tasks_history, health, advisor_cache, profile, ...)
- **CI 没跑** `cargo test`, ci.yml 只跑 `vite build`

### Coverage
- **从未跑过** `pytest --cov` 或 `vitest --coverage` 或 `cargo llvm-cov`
- **不知道真实覆盖率**

---

## 1. 测试分层 (Test Pyramid)

### Layer 0 · 静态检查 (Type/Lint)
- Python: `mypy` (推荐但当前未启用) / 各 module pyproject.toml 内 ruff
- TypeScript: `npx tsc --noEmit` (CI 已跑)
- Rust: `cargo clippy --all-targets -- -D warnings` (CI 未跑)

**目标**: 0 error, 0 warning. CI 必过。

### Layer 1 · 单元测试 (Unit Tests)
- **定义**: 测一个函数 / 一个 class 方法, 无 IO, 无外部依赖, mock 所有 boundary
- **命名**: 文件 `test_<module>.py` / `<module>.test.ts` / 同文件内 `mod tests`
- **位置**:
  - Python: `<package>/tests/test_<module>.py`
  - TypeScript: `<src>/<module>.test.ts` (跟源码同目录, vitest 默认)
  - Rust: `mod tests { ... }` inline 在 .rs 文件末尾
- **耗时**: 单个 test <100ms, 整 module 跑 <1 分钟
- **mock**: 用 `unittest.mock` (Python), `vi.mock` (vitest), `mockall` (Rust)

### Layer 2 · 集成测试 (Integration Tests)
- **定义**: 多个模块协作, 真 SQLite / 真 socket / 真 HTTP, 但不调真 LLM, 不连真上游 API
- **命名**: `test_<feature>_integration.py` 或同目录加 `@pytest.mark.integration`
- **位置**: 跟 unit 同目录 `tests/`, 用 marker 区分
- **耗时**: 单个 <5s, 整套 <5 分钟
- **依赖**: 真 SQLite (in-memory 或 tmp file), 真 socket, 但 LLM provider mock (`mock_litellm_completion` fixture)

### Layer 3 · E2E (End-to-End)
- **定义**: 真启动多个 process (hermes daemon + tool-bridge + Companion + catfish-gateway), 真 LLM 调用, 真用户操作
- **命名**: `test_e2e_<scenario>.py` 或 `e2e/<scenario>.spec.ts`
- **位置**:
  - Python: 各模块 `tests/test_e2e_*.py` (跟 spec 同 module)
  - 或顶层 `tests/e2e/` 跨 module 全链路 (未来加)
- **耗时**: 单个 <30s, 整套 <15 分钟
- **执行**: **CI 不跑** (耗时 + 资源限制), **手动**或 **nightly** 跑
- **当前状态**: 2 个 `test_e2e_*.py` 已存在但 CI 里 ignored:
  - `edge/tool-bridge/tests/test_e2e_sandbox.py`
  - `edge/tool-bridge/tests/test_e2e_agent_workflow.py`

### Layer 4 · 手动验收 (Manual Acceptance)
- **定义**: 真用户操作 Companion / TUI, 验证 UX
- **位置**: `docs/TEST-PLAN-<date>.md` (e.g. `TEST-PLAN-20260606.md`)
- **触发**: 大 marathon ship 后, 商用部署前, 真客户验收
- **执行**: 鸿波本机, 按 plan case 顺序跑, paste 截图 + log

---

## 2. 命名约定 (Naming Conventions)

### Python (pytest)
| 项 | 规范 | 例 |
|---|---|---|
| test 文件 | `test_<module_or_feature>.py` | `test_facts_router.py` |
| test 函数 | `def test_<scenario>():` | `def test_facts_router_lazy_skills_hub_url():` |
| test 类 | `class Test<Component>:` | `class TestApprovalGuard:` (可选, 复杂 case) |
| 异步 test | 同步函数 + `@pytest.mark.asyncio` 或 `asyncio_mode = "auto"` (推荐, 已用) | - |
| Fixture | `<name>_fixture` 或直接名词 | `mock_litellm`, `tmp_facts_dir` |
| Marker | `@pytest.mark.<layer>` (`unit`/`integration`/`e2e`/`slow`) | `@pytest.mark.integration` |

### TypeScript (vitest)
| 项 | 规范 | 例 |
|---|---|---|
| test 文件 | `<module>.test.ts` | `promiseCheck.test.ts` |
| describe block | `describe("<Component>", () => {...})` | `describe("promiseCheck", ...)` |
| it block | `it("should <verb>", () => {...})` | `it("should detect promise-only without tool call", ...)` |
| 异步 test | `it("...", async () => {...})` | - |

### Rust (cargo test)
| 项 | 规范 | 例 |
|---|---|---|
| inline test | 文件末尾 `mod tests { use super::*; #[test] fn ... }` | - |
| integration | `<crate>/tests/<feature>.rs` | (Companion 目前无 integration test 文件) |
| async test | `#[tokio::test]` | `#[tokio::test] async fn test_tool_bridge_call() {...}` |

---

## 3. Fixture / Mock 规范

### 共享 fixture 位置
- **module 级**: `<module>/tests/conftest.py` (现已有 6 个: catfish-memory / catfish-xcatfish-user / email-agent / llm-gateway / mcp-registry / secret-broker)
- **跨 module**: **没有, 也不需要** — 各 module 独立 conftest, 不共享 (catfish 原则: 模块自洽)

### Mock LLM (最常用)
**Python (gateway)**:
```python
@pytest.fixture
def mock_litellm_completion(monkeypatch):
    """mock litellm.acompletion 返回固定 chunk."""
    async def fake_completion(**kwargs):
        return MockStreamResponse(chunks=["hello", " world"])
    monkeypatch.setattr("litellm.acompletion", fake_completion)
```

**TypeScript (Companion)**:
```ts
vi.mock("./chat", () => ({
  streamChat: vi.fn().mockResolvedValue({
    onDelta: vi.fn(),
    onDone: vi.fn(),
  }),
}));
```

### Mock HTTP (上游 API)
- Python: `respx` (httpx 配套) 或 `responses` (requests 配套)
- TypeScript: `msw` (mock service worker) 或直接 `vi.spyOn(global, "fetch")`

### Mock 文件系统 (state.db / config 文件)
- Python: `tmp_path` fixture (pytest 内置)
- TypeScript: `memfs` 包

### 真 SQLite
- **integration test** 用 SQLite **in-memory** (`:memory:`) 或 **tmp file**
- 不要真改 `~/.hermes/state.db` (污染 dev 环境)

---

## 4. CI Matrix

### 现状 (`.github/workflows/ci.yml`)
- **3 个 job**: tool-bridge / gateway / companion (tsc + vite build)
- **只跑 Linux** (ubuntu-latest)
- **不跑** 其它 10 个 Python 模块, 不跑 vitest, 不跑 cargo test

### 目标 matrix (P0 必扩)

#### Job 1: Python all modules
```yaml
strategy:
  matrix:
    module:
      - central/llm-gateway
      - central/skills-hub
      - central/mcp-registry
      - central/secret-broker
      - central/identity-server
      - edge/tool-bridge
      - edge/local-search
      - edge/journal-agent
      - edge/email-agent
      - edge/hermes-plugins/catfish-memory
      - edge/hermes-plugins/catfish-xcatfish-user
```

每个 module 独立跑, 失败不影响其它 (`fail-fast: false`).

#### Job 2: TypeScript vitest
```yaml
- name: vitest
  working-directory: edge/companion-app
  run: npx vitest run --coverage
```

#### Job 3: Rust cargo test
```yaml
- name: cargo test
  working-directory: edge/companion-app/src-tauri
  run: cargo test --all-features
```

#### Job 4: Companion tsc + vite build (已有, 保持)

#### Job 5: cross-platform (P1 加)
```yaml
strategy:
  matrix:
    os: [ubuntu-latest, macos-latest]
```

只对 Companion (Tauri Mac app) 加 macOS runner. Python 跨平台差异小, 不加 macOS (省 CI 时间).

---

## 5. Coverage 目标

### Baseline (待跑, 暂不知数据)
- **Python**: `pytest --cov=src --cov-report=html --cov-report=term-missing`
- **TypeScript**: `vitest run --coverage`
- **Rust**: `cargo llvm-cov --workspace --html`

### 目标 (从 baseline 出发, 不一刀切)
| 模块类型 | Coverage 目标 | 备注 |
|---|---|---|
| Core gateway / tool-bridge | **≥80%** | 商用部署前必达 |
| Hermes plugins | **≥70%** | monkey-patch 比较 fragile, test 兜底 |
| Edge agents (email/journal/local-search) | **≥60%** | 业务逻辑为主 |
| Skills | **≥50%** | 主要靠 manual + e2e |
| Companion Rust | **≥60%** | Tauri command 是稳定 API |
| Companion TypeScript | **≥40%** | UI 主要靠 vitest + 手动 |

### 不强制 100%
- 不强求纯 UI 组件 100% (React 组件测试 ROI 低)
- 不强求 vendored / generated code (如 OpenAPI client)

---

## 6. 测试什么 (按层分)

### Unit (Layer 1) 必测
- **纯函数**: 参数 → 返回, 边界 case, 异常 path
- **类方法**: 状态转换, 副作用 mock
- **数据 schema 校验**: Pydantic / TypeScript Zod
- **正则 / parser**: 多边界输入 + happy path + 错误输入

### Integration (Layer 2) 必测
- **DB schema 迁移** (alembic): up + down 跑通
- **HTTP endpoint** (FastAPI): 真 TestClient, 真 status code, 真 body
- **跨模块 dispatch**: tool-bridge adapter → mock hermes → assert payload
- **RBAC**: 不同 role + dept + scope 组合, 全权限矩阵 ≤8 combo 测完
- **session 持久化**: 写盘 → 读回, 跨 process 一致

### E2E (Layer 3) 应测 (CI 不跑, 手动 / nightly)
- **chat 完整流**: send → LLM stream → tool dispatch → result → next round
- **approval 闭环** (marathon 修的): button 弹 → 点 → hermes 解 block → tool 真跑
- **Wiki 三态搜索**: title BM25 / 全文 BM25 / 语义 (BGE-M3 ONNX)
- **跨电脑迁移**: BL-F4 数据迁移工具 (商用前必测)

### Manual (Layer 4) 必跑
- 每次大 marathon ship 后, 跑 `TEST-PLAN-<date>.md`
- 商用部署前, 跑 BL-E18 跨 2 台真机测试
- 客户验收前, 客户场景定制 plan

---

## 7. 测试新模块 checklist (从 0 加测试)

新模块 (e.g. 加一个 hermes plugin) 加测试时按这步骤:

```
1. ✓ 在 module 下建 tests/ 目录
2. ✓ 加 pyproject.toml `[tool.pytest.ini_options]` 段:
       testpaths = ["tests"]
       asyncio_mode = "auto"  # 如有 async
3. ✓ 加 tests/conftest.py (共享 fixture, 可选)
4. ✓ 命名 test_<feature>.py + def test_<scenario>():
5. ✓ Layer 1 unit 必加, Layer 2 integration 推荐
6. ✓ 跑通: PYTHONPATH=src python -m pytest tests/ -v
7. ✓ 加进 .github/workflows/ci.yml matrix
8. ✓ 跑 coverage: pytest --cov=src
9. ✓ 给 module README 加 "## Testing" section
```

---

## 8. 写好测试的原则

### DO
- ✓ **Arrange-Act-Assert** 三段式, 一目了然
- ✓ **一个 test 测一件事** (单一职责)
- ✓ **test 名描述行为**, 不描述实现 (`test_user_login_returns_jwt` 而非 `test_login_function`)
- ✓ **fixture 化重复 setup** (`tmp_db` `mock_llm`)
- ✓ **断言要严格** — `assert resp.status_code == 200`, 不只 `assert resp.ok`
- ✓ **失败信息有用** — `assert x == y, f"expected y={y}, got {x}"`

### DON'T
- ✗ **不要在 test 里写复杂逻辑** (test 里有 if / for 是 smell)
- ✗ **不要 mock 自己 module 内部** (mock 应该在 boundary 上)
- ✗ **不要依赖 test 顺序** (每个 test 必独立, 重排顺序仍通)
- ✗ **不要 sleep** (`time.sleep(1)` 等条件 — 用 `tenacity` retry 或 event-based 等)
- ✗ **不要 hit 真 LLM / 真外网** (CI 慢 + flaky + cost), 整体设计 inject 接口让 test 能 mock

---

## 9. 失败处理

### CI 失败 (red CI)
1. 看 GitHub Actions log 找 stack trace
2. 复现本地: `cd <module> && pytest tests/test_xxx.py::test_yyy -v`
3. 修 + commit + push
4. **绝不能** 用 `@pytest.mark.skip` / `--ignore` 绕过失败 test (除非真坏 test, 那就删它), 用 `@pytest.mark.xfail` 标已知失败要修

### Flaky test (偶尔失败)
- 加 retry: `@pytest.mark.flaky(reruns=3)` (pytest-rerunfailures 插件)
- 但**优先**找出真因 (race condition / time-dependent / external dep), 修而不是绕

### Coverage 降了
- PR 模板加一行 "coverage 变化": `+2.3% / -0.8% / no change`
- 商用部署前**强制**: 主模块 coverage 不降, 降了 PR 拒收

---

## 10. 下一步 (这份 spec 落地的 BL)

### Sprint 1 (这周, ~3 天)
| Task | 估时 | 产出 |
|---|---|---|
| 跑 coverage baseline (Python + TS + Rust) | 0.5 天 | 真覆盖率数据进本 spec §0 |
| CI 扩 matrix 跑全部 12 Python 模块 | 0.5 天 | ci.yml 更新 |
| CI 加 vitest job + cargo test job | 0.5 天 | ci.yml 更新 |
| 补关键 module test gap (覆盖率 <30% 的) | 1 天 | test 数 +20% |

### Sprint 2 (下周, ~3 天)
| Task | 估时 | 产出 |
|---|---|---|
| E2E approval flow 自动化 (Tauri WebDriver / Playwright) | 1.5 天 | `tests/e2e/test_approval_flow.py` |
| E2E Wiki Phase 2 自动化 | 1 天 | `tests/e2e/test_wiki_search.py` |
| CI 加 macOS runner (Companion 真平台) | 0.5 天 | `os: macos-latest` matrix |

### Sprint 3 (商用部署前)
| Task | 估时 | 产出 |
|---|---|---|
| 性能基准 (concurrent users / LLM latency / Wiki search latency) | 1 天 | `benchmarks/` 目录 + 报告 |
| Security penetration test (越权 / XSS / RBAC bypass) | 1 周 | `tests/security/` 套件 |
| BL-E18 跨 2 台真机测试 | 1 周 | 客户 IT 接入文档 + acceptance |

---

## 附录 A · 已忽略的 E2E test (CI 里 `--ignore`)

| 文件 | 原因 | 怎么跑 |
|---|---|---|
| `edge/tool-bridge/tests/test_e2e_sandbox.py` | 需要真启动 sandbox 子进程 | 本地: `pytest test_e2e_sandbox.py` |
| `edge/tool-bridge/tests/test_e2e_agent_workflow.py` | 需要真 hermes daemon | 本地启 hermes 后跑 |
| `central/llm-gateway/tests/test_a2a_jwt.py` | 需要 a2a 协议依赖 | 本地装完整 deps 后跑 |

---

## 附录 B · 现有 conftest.py 内容速查

每个 module 的 conftest.py 提供了:

| Module | 主要 fixture |
|---|---|
| `central/llm-gateway` | TestClient / mock auth provider / mock litellm / tmp config |
| `central/mcp-registry` | tmp manifest dir / mock MCP server |
| `central/secret-broker` | tmp secret store / mock vault |
| `edge/email-agent` | tmp .box / mock IMAP |
| `edge/hermes-plugins/catfish-memory` | tmp memory dir / mock session_db |
| `edge/hermes-plugins/catfish-xcatfish-user` | mock APIServerAdapter / mock pre_tool_call ctx |

新模块加 conftest 时, 至少提供:
- 一个 tmp dir fixture (替代真盘写)
- 一个 mock LLM fixture
- 一个 mock auth context fixture (如需要)

---

**这份 spec 是 living document** — 改了 / 加了规范, 直接编辑这文件, commit message 写 `docs: TESTING-SPEC update <reason>`.
