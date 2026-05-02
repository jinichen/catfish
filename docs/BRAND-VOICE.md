# 品牌声音 · BRAND-VOICE.md

**给所有写鲶鱼对外文案的人 (开发者 / 设计 / GTM)**.
对外 = 员工能看到 + 客户演示能看到.

---

## 核心原则

**鲶鱼是产品, 底层依赖是实现细节. 跟员工 / 客户的所有触点上, 永远不暴露内部术语.**

像 Apple 不会在 iCloud UI 上写"基于 OpenStack" — 即使技术上是那样.

---

## 红线 — 对外**绝对不**说

| ❌ 不说 | ✅ 改说 |
|---|---|
| `hermes` / `Hermes` | 鲶鱼 / 小鲶 |
| `hermes-agent` | 鲶鱼平台 |
| `~/.hermes/state.db` | 对话历史 / 本地数据 |
| `system prompt` | 身份档案 / 启动配置 |
| `skill_manage` (工具名) | "学习工作流" / "自动抽象 skill" |
| `memory_save` / `memory_recall` (工具名) | "记下来" / "回忆起来" |
| `tool-bridge` (技术组件名) | "工具桥" / "工具网关" (内部用) |
| `litellm` / `OpenAI SDK` | (不提) |
| `model_tools.py` 等内部模块名 | (不提) |
| `JSONL` / `unix socket` 等技术术语 | (不提, 用人话) |

---

## 鲶鱼 vs Catfish (五一 sprint 5/2 加)

**主名 = 鲶鱼 (中文场景), 副名 = Catfish (技术术语 / 国际场景)**.

### 用法

| 场景 | 用 | 例 |
|---|---|---|
| 客户对话 / 销售物料 / 演讲口语 | **鲶鱼** | "鲶鱼帮你写汇报" / "鲶鱼是企业 LLM 中台" |
| Companion UI 中文文案 | **鲶鱼 / 小鲶** | "跟小鲶说话…" / "鲶鱼平台" |
| PPT 封面 / 文档标题 | **鲶鱼 (Catfish)** | "陈鸿波 / 鲶鱼平台 (Catfish)" |
| 工程代码 / 包名 / 路径 | **catfish** (英文) | `catfish-gateway` / `~/.catfish/` |
| 工具名 / API | **catfish_xxx** (英文) | `catfish_run_skill` / `catfish_a2a_ask` |
| Phase 3 产品名 | **Catfish Federation** (专有名) | "12 个月 ship Catfish Federation" |
| Companion .app 标题 | **鲶鱼 Companion** | (window 顶部) |

### 红线 — 对外中文叙述里**绝对不**单独说 "catfish"

| ❌ 不说 | ✅ 改说 |
|---|---|
| "用 catfish 干活" | "用鲶鱼干活" |
| "catfish 帮你..." | "鲶鱼帮你..." / "小鲶帮你..." |
| "关掉非 catfish 窗口" | "关掉非鲶鱼窗口" |
| "catfish 中央 gateway" | "鲶鱼中央 gateway" |
| "Catfish 平台是一个..." | "鲶鱼平台是一个 (catfish-gateway 中央网关 + ...)" |

**规则**: 中文叙述里出现孤立 catfish 字样 = 错. 写代码模块/路径/工具名时保留英文 = 对.

### 触发场景的安全规则

写文案时心里默念: "客户读这一句, 会不会觉得在看技术文档?". 觉得太技术 → 改鲶鱼.

---

## 触点分类 — 哪些是"对外"?

### 必须遵守 (员工/客户能直接看到)

- ✅ Companion 任意 tab 的所有可见文字 (button label, tooltip title, 卡片标题, 描述文字)
- ✅ 错误提示给员工看的内容 (`friendly_upstream_error()` 输出)
- ✅ 模型回复里描述自己时 (但 SOUL.md 应该已经定了"你是小鲶, 不是 hermes")
- ✅ CLI 工具输出 (`catfish doctor` / `catfish status` 等)
- ✅ 文档面向客户 / 员工 (SETUP / USER GUIDE)
- ✅ skill 描述 (员工 / 客户能看到 skill 列表)

### 可以保留 hermes (内部, 不外露)

- ⚠️ 源代码注释 / docstring (开发者参考, 可以说"这个对应 hermes state.db")
- ⚠️ 架构文档 (POSITIONING / STRATEGY / ARCH 等内部讨论)
- ⚠️ git commit message (开发者上下文)
- ⚠️ 测试代码 / 内部 log
- ⚠️ BACKLOG.md / PR description

## 已知踩过坑 (2026-04-28)

3 处 UI 文案露馅, 客户演示前 1 周才发现. 修法见 commit `feat(brand): 删 UI 里 hermes 字样`:

1. `LearningCard.tsx` 教育性 footer: "小鲶在用 hermes 的 memory tool ..." → 删 hermes / skill_manage
2. `ChatSidebar.tsx` 终端按钮: "⌘ 在终端开 hermes" → "⌘ 在终端开鲶鱼"
3. `ServicesCard.tsx` why 字段: "暴露 hermes 60+ 工具" → "暴露 60+ 工具给小鲶"

## 检查清单 (写完代码 / 文案前过一遍)

```
□ git grep -i "hermes" 在 src/ 下没有 UI 可见的字样了吗?
□ tooltip / title attribute 里没有内部术语吗?
□ button label / 卡片标题用的是员工能懂的话吗?
□ 错误提示让员工知道"做什么", 不是让员工读 stack trace 吗?
□ SOUL.md 模型自我介绍的 "你是小鲶" 没破吗?
```

## 测试纪律 (可加 CI grep, P2)

```bash
# 应该 0 命中
git grep -ni "hermes" \
  src/tabs/ src/components/ \
  | grep -v "^[^:]*\.\(test\|spec\)" \
  | grep -vE "^[^:]+:\s*(\* |//|/\*\*)" \
  | grep -vE ".tsx?:\d+:\s*\*"
```

如果 CI 命中 → fail build, 强制开发者改.

---

## 为啥这事重要

**客户视角**:
- 看到 "hermes" → "你们用别人开源项目? 那我直接装 hermes 就行, 找你们干啥?"
- 看到 `skill_manage` → "啥意思? 太技术了"
- 看到 `~/.hermes/state.db` → "系统在我电脑上写了什么文件? 安全吗?"

**鲶鱼差异化在 catfish layer** (gateway / SOUL / identity / 凭据安全 / Companion / 工程纪律), 不在 hermes 底层. 露 hermes 反而**模糊掉**我们的真正价值.

**不是"骗"客户**: 如果客户问"底层用了什么开源项目", 你坦诚说基于 hermes-agent + LiteLLM, 没问题. 但**不主动**在 UI 文案里暴露.
