# 鲶鱼 · 代码规范

> **适用对象**：所有写鲶鱼代码的人（包括 AI 协作者）
>
> **核心哲学**：架构纪律 > 个人发挥

---

## 1. 文件与函数尺寸

### 量化上限

| 层级 | 目标 | 硬上限 | 超了怎么办 |
|---|---|---|---|
| 函数 | 30 行 | 80 行 | 抽成多个小函数 |
| 类 | 200 行 | 400 行 | 拆职责 |
| 文件 | 300 行 | 500 行 | 按关注点拆成多个文件 |
| 目录 | 10 文件 | 20 文件 | 拆子目录 |

**不是死规则**：
- 配置文件（YAML / JSON / toml）、schema、模板、测试数据不受限
- 一段紧密耦合的算法可以适度放宽
- **拆得过早比大文件更坏**——边界浮现出来再拆

### 自动检查

```bash
# 每周跑一次，列出所有 ≥500 行的源文件
bash catfish/scripts/check_file_sizes.sh

# CI 里强制（≥800 行则失败）
bash catfish/scripts/check_file_sizes.sh --strict
```

---

## 2. 复杂度与静态检查

每个 Python 模块 `pyproject.toml` 必须配置 ruff 并启用以下 lint：

```toml
[tool.ruff.lint]
select = ["E", "F", "I", "W", "PL", "C90", "N", "UP", "B"]

[tool.ruff.lint.pylint]
max-statements = 50
max-branches = 12
max-returns = 6
max-args = 8

[tool.ruff.lint.mccabe]
max-complexity = 10
```

**Commit 前**：
```bash
ruff check src/ tests/
ruff format src/ tests/
```

**CI 里**：
```bash
ruff check --output-format=github src/ tests/
```

---

## 3. 拆分的时机

### 该拆的信号
- ✅ 打开文件找功能要滚超过一屏
- ✅ 两个及以上互不相关的关注点挤在一起
- ✅ Code review 时评论总说"这段能不能单独看"
- ✅ 测试要 mock 文件里大半内容
- ✅ 多人在同一文件改到 merge 冲突
- ✅ 加一个小功能要修 >50 行

### 别拆的信号
- ❌ 只为了"看起来小"（premature abstraction）
- ❌ 拆完要用 6 个文件描述一个紧密耦合的逻辑
- ❌ 拆完每个文件只有 3 行
- ❌ 暂时不清楚拆的边界在哪

**原则**：**让边界自然浮现，再顺势拆**。

---

## 4. 目录职责（防止堆成"杂物箱"）

| 目录 | 放什么 | **不放** 什么 |
|---|---|---|
| `central/` | 共享中央服务 | 员工本地组件 |
| `edge/` | 员工本地跑的东西 | 中央服务 |
| `plugins/` | Hermes 插件（hook 生命周期） | 独立应用 |
| `connectors/` | 内部系统 MCP server（独立进程） | Hermes 工具 |
| `skills/` | Hermes skill 包 | 代码库 |
| `installer/` | 员工装鲶鱼的工具链 | 业务代码 |

**不要**建 `utils/`、`helpers/`、`common/` —— 这是"杂物箱"的预兆。每个工具放进**它最相关的模块**。

---

## 5. AI 协作专项规则

当使用 LLM 辅助写代码时（Claude Code / Cursor / Aider / Hermes 等）：

1. **每加一个大功能前**，先说明："这加进去后文件会变多长？该不该先拆？"
2. **Edit 工具用多了后**，每周扫一次 `check_file_sizes.sh`
3. **达到 500 行设硬警戒线**：下次再动之前必须先拆
4. **拒绝"到处改"的建议**：如果 LLM 建议改 10 个地方做一个事，通常说明抽象错了，停下来重新想

**软著合规**（硬要求）：AI 生成代码必须经人工审阅改写后提交，详见 [`AI-USAGE.md`](AI-USAGE.md)。
提交前三件套自检：

```bash
bash catfish/scripts/check_ai_tells.sh     # 扫 AI 痕迹
bash catfish/scripts/check_file_sizes.sh   # 扫文件大小
ruff check src/                            # 静态检查
```

**反面教材**：Hermes 的 `run_agent.py` 一个文件 10,700 行。我们不要学。

---

## 6. 命名

- **模块/文件**：snake_case
- **类**：PascalCase
- **函数/变量**：snake_case
- **常量**：UPPER_SNAKE_CASE
- **私有**：前置下划线 `_private_fn`

**文件名应反映内容**：
- ✅ `gateway.py`、`model_catalog.py`、`rate_limiter.py`
- ❌ `utils.py`、`helpers.py`、`misc.py`

---

## 7. 注释与文档字符串

- **为什么 > 是什么**：代码写的是"是什么"，注释写"为什么这么写"
- **公开函数/类必须有 docstring**
- **复杂算法附一句"参考 XXX"或算法名**
- **禁止**：`# this is a function` 这种废话注释

```python
# 不好
def compute(x):
    # compute something
    return x * 2

# 好
def amplify_signal(signal: int) -> int:
    """Amplify signal by 2x.

    Signal amplification is applied before denoising (see ADR-003).
    """
    return signal * 2
```

---

## 8. 错误处理

- **永远不吞异常**：至少 log 一下
- **error message 要带上下文**：不只是 "failed"，要说"failed to X because Y"
- **用户可见的错误要面向用户**（HTTPException detail），内部错误写 traceback
- **上游错误要透传**（参考 catfish-gateway 的 `error_type` 字段做法）

---

## 9. 测试

- **业务逻辑**必须有单元测试
- **网关路由** 必须有冒烟测试
- **大重构** 先写测试保证行为不变，再改代码
- **测试文件名**：`test_<module>.py`
- **集成测试** 放 `tests/e2e/` 或 `tests/smoke/`

---

## 10. 提交信息

```
<type>(<scope>): <短描述>

<详细说明>
<为什么这么做>
<影响范围>
```

`type`：feat / fix / refactor / test / docs / chore / perf
`scope`：gateway / companion / policy / skills-hub / ...

例子：
```
feat(gateway): add per-model timeout config

Some preview models (Gemini 3) have cold-start > 60s.
Added upstream.timeout to UpstreamConfig, defaults to 60,
Gemini previews set to 180 in models.yaml.

Affects: catfish/central/llm-gateway/
```

---

## 11. 活文档

本规范在经验中迭代。**发现新的反模式** → 加进本文件 + 更新 `check_file_sizes.sh` 或 `pyproject.toml`。

**发现规则不合理** → PR 修改本文件，说明为什么。

---

**要点**：**纪律不是对人的约束，是对 bug 的防御工事**。
