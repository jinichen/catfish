# projects/ — 客户业务项目层

> **状态**：🟢 规范已定（BL-PROJECTS-LAYER 2026-07-27 鸿波拍板）
>
> **定位**：基于鲶鱼底座建构的**客户业务项目资产**。规划 / 方案 / 知识库结构 / 业务 skill。
> 不是技术代码，不是部署配置。

---

## 为什么有这一层

鲶鱼是多客户产品。一个客户项目会产生两类东西：

| 类型 | 放哪 | 例子 |
|---|---|---|
| **部署配置** | `delivery/<客户>-<阶段>/` | docker-compose · config-overlay · setup.sh · 部署 SOP |
| **业务资产** | **`projects/<项目>/`** (本层) | 产业规划 · 申报材料 · 知识库 schema · 业务 skill |

`delivery/` 已存在（`dahua-poc/` 是活例子），管"怎么把鲶鱼装到客户环境"。
本层管"客户业务上要做什么、怎么做"。

**不建分支**：分支是给代码演进用的（feature/hotfix），客户项目长期分支必然与 main 分叉，merge 地狱。
**不建独立仓库**：会跟 `delivery/` 分裂（部署在 catfish、业务在别处），且每个项目要重配 CI / 权限。
**加目录层**：跟 `delivery/` 同构，天然共享底座演进，打客户包时整层 exclude。

---

## 目录结构约定

```
projects/
├── README.md              本文件（规范）
└── <项目名>/
    ├── README.md          项目概览 · 阶段 · 负责人 · 里程碑（必须）
    ├── .gitignore         排除真业务数据（必须）
    ├── docs/              规划 / 方案 / 申报材料 / 会议纪要
    ├── deck/              PPT 源码 + 输出
    ├── knowledge-schema/  知识库结构定义（**只 schema · 不含真数据**）
    ├── skills/            项目专属 skill（通用的沉淀回 catfish `skills/`）
    └── data-schema/       业务数据结构定义（合同 / 权益 / 评级 字段）
```

按需建，不必每个目录都有。

---

## 军规 · 4 条边界

### 1 · 真业务数据不进 git

| 进 git | 不进 git |
|---|---|
| 知识库**结构**（字段定义 / 分类体系 / 关系模型） | 知识库**真数据**（认证要求全文 / 企业名录 / 合同条款） |
| 数据 schema（合同有哪些字段） | 真合同 / 客户名单 / 员工信息 |
| skill 定义 + 方法论 | skill 跑出来的业务结果 |
| PPT 源码 + 脱敏输出 | 含客户机密的版本 |

真数据走 `~/.catfish/` （员工本机）或客户自己的存储。
跟 `delivery/dahua-poc/.gitignore` 同思路 — **生成产物和敏感内容一律不进 git，source of truth 才 track**。

### 2 · 通用能力沉淀回 catfish

项目里做出来的 skill，若别的客户也能用：

```
projects/<项目>/skills/xxx/     →  抽象脱敏  →  catfish skills/shared/xxx/
```

判断标准：**去掉客户特定信息后还有价值** → 沉淀。
反例：`daosheng-认证申报`（含稻壳纤维特定参数）不沉淀；`出口认证材料清单生成`（通用方法）沉淀。

### 3 · 每个项目必须有 README

至少写清：

- **定位**：这个项目是什么，客户是谁
- **阶段**：当前在哪一期，下一个里程碑
- **负责人**
- **跟 delivery/ 的关系**：有没有对应的部署配置目录
- **状态**：🟢 进行中 / 🟡 规划中 / 🔴 暂停 / ⚫ 归档

免得半年后忘了这目录干什么的。

### 4 · 打客户包时整层 exclude

`projects/` 是内部资产，**不进客户交付 tar**。
跟 `delivery/*/config-overlay/` 现在的做法一致（那边 README 明写"打 tar 时 overlay 目录 exclude"）。

打包脚本需显式 `--exclude=projects/`。

---

## 新项目起步流程

```bash
cd ~/person_task/catfish/projects
mkdir -p <项目名>/{docs,deck,knowledge-schema,skills}

# 1 · 写 README（照抄 daosheng/README.md 结构改）
# 2 · 写 .gitignore（照抄 daosheng/.gitignore 改）
# 3 · 若需要部署配置 → 另建 delivery/<客户>-<阶段>/
```

---

## 现有项目

| 项目 | 客户 | 状态 | delivery/ 对应 |
|---|---|---|---|
| `daosheng/` | 稻生万物（谷千合） | 🟡 规划中 · P1 期方案已出 | 暂无（等落地阶段建） |

---

## 跟其他层的关系

```
catfish/
├── central/      技术 · 中央服务（gateway / identity / wiki-hub / skills-hub）
├── edge/         技术 · 边缘（companion / tool-bridge / identity / hermes-plugins）
├── skills/       技术 · 通用 skill 库（跨客户复用）
├── plugins/      技术 · hermes 插件
├── delivery/     交付 · 客户部署配置（docker-compose / config-overlay / SOP）
└── projects/     业务 · 客户项目资产（本层）
```

**依赖方向**：`projects/` 用 `skills/` 和技术层的能力，反向不依赖。
技术层改动不该因为某个项目而破坏通用性 — 客户特定需求走 `SOUL_<CUSTOMER>.md` / `config-overlay` / 项目专属 skill 三条既有路径。
