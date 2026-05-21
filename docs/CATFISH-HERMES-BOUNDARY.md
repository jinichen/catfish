# Catfish — Hermes 责任边界 (反越界原则)

> **状态**: 强制纪律. 任何 PR / 设计提议越界 = 不收.
>
> **写作背景**: 5/17 04:40 凌晨, catfish + 鲶鱼一起评估"memory 系统是否完成"时, 鲶鱼建议加 `catfish_memory_compress` / `catfish_memory_dedupe` / "压缩前提取 memory hook" 等. 鸿波 5 次抓到鲶鱼**越界 hermes 责任**: hermes 已经有 LCM / context 压缩 / memory 工具, catfish 不该自己再写一套. 当晚固化这条边界, 防未来再"凡事 catfish 全包".
>
> **关联**: `PRIVACY-PRINCIPLES.md` (员工数据不上传中心), `RCA-MEMORY-PLUMBING-20260516.md` (5/16 6h RCA — catfish 跟 hermes 接口 contract 断了 6 周没察觉, 反向证明边界清晰的重要性).

---

## 一句话

**catfish 只做 hermes 没有的事. hermes 有的, catfish 不重写.**

具体:
- **hermes 干**: agent 核心 (memory / todo / context / dispatch / LLM 路由)
- **catfish 干**: 包装 hermes 给员工 (UI / 入口 / 路由教学 / 政企合规 / SaaS 化)

---

## 责任表 (按层划分)

### 1. Memory 系统

| 子项 | hermes | catfish |
|---|---|---|
| memory 工具 (add/replace/remove/search) | ✅ memory_tool.py | ❌ 不重写 |
| MemoryStore in-memory 模型 | ✅ class MemoryStore | ❌ 不重写 |
| 写盘 / atomic_replace / file lock | ✅ memory_tool.py:434 _write_file | ❌ 不重写 |
| char_limit / dedupe / LCM 压缩 | ✅ 各类 store + ContextCompressor + autocompress | ⚠️ **5/17 加的 dedupe / compress 是越界**, 周一 audit 是否砍 |
| memory 写盘路径 (~/.hermes/memories/) | ✅ get_memory_dir() | ❌ 不变 |
| Dashboard 看 / 删 memory entry | ❌ hermes 没 UI | ✅ HermesMemoryCard.tsx (catfish 责任) |
| 注入到 LLM system prompt | ❌ hermes CLI 自己注 (AIAgent 内部) | ✅ catfish gateway 走 Provider 注入 (我们 SaaS 路径不走 hermes CLI) |
| target=user vs target=memory 路由教学 | ✅ memory_tool docstring | ✅ SOUL.md 解释给 catfish 员工 (扩 hermes 默认教学) |
| 跨 session 检索 (search) | ✅ memory(action=search) | ❌ 不重写 |
| 上下游 LLM provider 切换 | ❌ hermes 不管 | ✅ catfish gateway (LiteLLM) |

**catfish 越界过的事 (5/17 凌晨 + 上午)**:
- ❌ `catfish_memory_dedupe`: 自己用 jieba 写 Jaccard 找重复 — hermes 有 ContextCompressor 含语义摘要可复用
- ❌ `catfish_memory_compress`: 自己写"砍最老 N 条" 压缩 — hermes 有内置 compressor 已 ship
- ❌ "压缩前提取 memory hook" (一度想加进 hermes plugin): 改 hermes 主对话路径, 风险高且 hermes 已经在压
- ❌ **`catfish-autocompress` plugin (5/17 早 9:00 砍)** — 240 行 plugin 干一件事: 把 hermes ContextCompressor 默认 75% 阈值改成 50%. **hermes 0.14 已经把默认值改成 50%** (`agent/context_compressor.py:407` 写死 `threshold_percent: float = 0.50`), plugin 等于 no-op (50% 替 50%). 5/8 之后 9 天没启用都没事 = 证据. 鸿波 5/17 早 09:00 拍板: "hermes 有压缩, catfish 还压缩干嘛?" — 同 5/17 04:55 砍 dedupe/compress 一个套路.

**周一 audit 决定**: 这 4 件是 catfish 越界, 全砍 (5/17 凌晨砍 3 个工具暴露 + 上午砍 plugin 源码). 留代码作 git 历史, LLM 看不到.

**catfish-autocompress 砍后的真实路径**:
1. `~/.hermes/config.yaml` 改 `engine: compressor` (hermes 内置)
2. `edge/hermes-plugins/catfish-autocompress/` 目录删除
3. task #82 BL-HERMES-014-UPGRADE-STEP2 → deleted (没东西要 audit 了)
4. HERMES-014-UPGRADE-RUNBOOK 里"重新部署 catfish-autocompress" 段落删
5. 想自定义阈值? 直接配 hermes config.yaml (hermes 0.14 + 后续版本本应支持, 走 hermes 路径)

### 2. Context / Session 系统

| 子项 | hermes | catfish |
|---|---|---|
| session lifecycle (start / end / fork) | ✅ AIAgent / cli.py | ❌ 不重写 |
| state.db (sessions / messages 表) | ✅ hermes 写 | ✅ catfish session_write.rs 同时写 (员工本机, 文件锁 WAL) |
| FTS5 / 历史搜索 | ✅ session_search_tool.py | ⚠️ catfish 加了 catfish_search_sessions (跨 session) 跟 hermes session_search 互补 (5/5 注释明确"优先用 catfish_search_sessions") |
| context 压缩 (ContextCompressor) | ✅ agent.context_compressor | ⚠️ catfish 包装 catfish-autocompress plugin (调阈值 50%, hermes 默认是 70%) — 这是"调参不重写", 合理 |
| 主动 starter (proactive) | ❌ hermes 没 | ✅ catfish 自家 (ProactiveCard + gateway endpoint) |

### 3. Tool dispatch

| 子项 | hermes | catfish |
|---|---|---|
| tool registry | ✅ tools/registry.py | ❌ 不重写 |
| dispatch | ✅ registry.dispatch(name, args, **kw) | ✅ catfish-tool-bridge 包装 (5/16 BL-MEMORY-BRIDGE-STORE 修了 store 注入断 6 周) |
| 工具 schema 兼容 | ✅ hermes 工具 | ✅ catfish 加 catfish_* native 工具 (本地能力 hermes 没有的) |

### 4. LLM 调用

| 子项 | hermes | catfish |
|---|---|---|
| LLM SDK | ✅ hermes CLI 走 LiteLLM | ✅ catfish gateway 走 LiteLLM (catfish 自家 fallback 链, 不走 hermes CLI 那条) |
| 模型选 / 切换 | ❌ hermes CLI 启动时定 | ✅ catfish 跑时切 (BL-FALLBACK-TOGGLE 默认关 auto-fallback) |

---

## 决策原则 (catfish 该做 vs 不该做)

新功能落 catfish 前先问 3 个问题:

1. **hermes 有没有同等能力?** → grep `~/.hermes/hermes-agent/tools/` + `agent/` + `plugins/`
   - 有 → 不做. 直接复用 hermes
   - 没有 → 继续问 #2
2. **这是不是 hermes 该做但还没做?** (例如更好的 LCM)
   - 是 → 给 hermes 提 PR / issue, **不**在 catfish 写一套 (除非急用 fallback)
   - 否 → 继续问 #3
3. **这是不是 catfish SaaS 包装层独有的事?** (UI / 入口 / 政企合规 / 多员工)
   - 是 → catfish 做
   - 否 → 重新评估, 可能是 hermes 责任

---

## Skill 边界 (5/21 加, 业务 skill 库重叠扫描后定)

写新 catfish skill (放在 `skills/department/` 或 `skills/business/`) 前必须先扫 hermes 自带 skill, 防重叠:

```bash
ls ~/.hermes/hermes-agent/skills/          # 25 大类目录
grep -ri "<关键词>" ~/.hermes/hermes-agent/skills/ --include="*.md" | head
```

**决策树**:

| hermes 有同类? | catfish 做法 |
|---|---|
| ✅ 有, 功能 80%+ 重叠 | **不写 skill**. 写 cookbook (SKILL.md 教 LLM 自己 chain hermes 工具), 跟 `catfish-journal/SKILL.md v0.2.0 跨 source` 同模式 |
| ⚠️ 有但走云端 API (Teams / Google / Microsoft Graph), catfish 要走本地 / 国内系统 | **重定位**, 明确标注差异. skill 名加前缀 (例: `local-meeting-minutes` 区别于 hermes `teams-meeting-pipeline`), 不抢同一个 trigger 词 |
| ❌ 没有, 中国政企本地化业务 | **写 catfish skill**, 但渲染层/工具基础设施**只复用 hermes 提供的** (`python-docx` / `python-pptx` / `pymupdf` / `openpyxl` / `markitdown` 等), 不重复造轮子 |

**当前 catfish skill 边界 verdict (5/21 audit)**:

| catfish skill | hermes 对应 | verdict |
|---|---|---|
| leadership-briefing (4 段公文 .docx) | `ocr-and-documents` (给 python-docx 工具底座) | ✅ 不越界. catfish = 中国政企模板 + 方正小标宋 + 双 backend 错别字 + LLM 接业务数据 |
| weekly-report (.xlsx 周报) | `google-workspace` (云端 Sheets) | ✅ 不越界. catfish = 本地 openpyxl 政企表格模板 |
| project-approval (项目立项 .docx) | `ocr-and-documents` (工具底座) | ✅ 不越界. catfish = 立项模板 + 4 段语义 |
| qualification-export (资质导出) | 无 | ✅ 写 skill, 政企特定数据 |
| ~~meeting-minutes~~ (会议纪要) | `productivity/teams-meeting-pipeline` | ⚠️ **不写 skill**, 改 cookbook 教 LLM chain (BL-I3.1 视频抽音轨 + hermes ocr-and-documents 渲染). 砍 1 周工作量到 1-2 天 |
| annual-summary (年终汇报) | 无 (数据源 `~/.hermes/employee_journal/` 是 catfish 5/20 ship 的 journal-agent) | ✅ 写 skill |
| procurement (采购单 .xlsx) | 无 | ✅ 写 skill |

**Skill 命名反模式** (容易跟 hermes 撞 trigger 词):
- `meeting-*` (撞 `teams-meeting-pipeline`)
- `pdf-*` / `document-*` (撞 `ocr-and-documents` / `nano-pdf`)
- `slides-*` / `ppt-*` / `deck-*` (撞 `powerpoint`)
- `email-*` (撞 `email/himalaya`)
- `obsidian-*` / `notion-*` / `linear-*` / `airtable-*` (各自撞 hermes 同名 skill)

加前缀 `catfish-` 或本地化关键词 (`zh-` / `gov-` / `enterprise-`) 显示差异. 实在没差异化 → 不写 skill, 写 cookbook.

### Skill 路径 + Curator 边界 (5/21 加)

**hermes Curator** (auxiliary model, 后台 archive 不活跃 skill) 的 `is_agent_created()` 判定:

```python
def is_agent_created(skill_name: str) -> bool:
    """Whether *skill_name* is neither bundled nor hub-installed."""
    off_limits = _read_bundled_manifest_names() | _read_hub_installed_names()
    return skill_name not in off_limits
```

含义: 只动 `~/.hermes/skills/` 里**既不是 bundled 也不是 hub-installed** 的 skill. 推论:

| catfish skill 落哪 | Curator 行为 |
|---|---|
| `~/.catfish/skills/<ns>/<name>/` (本机, catfish 自家路径) | ❌ Curator 看不到, 不动 |
| `~/.hermes/skills/<ns>/<name>/` + 通过 `catfish_skill_install` 走 hub | ✅ 进 `_read_hub_installed_names()` 列表 → Curator 永不动 |
| `~/.hermes/skills/<ns>/<name>/` 但**未**通过 hub (例: 手动 cp / 教学产物误落) | ⚠️ 被判 agent-created → 30 天没用就被 archive (可恢复但流程难) |

**纪律**:
- catfish 教学产物 (BL-LEARN-RECMODE) **默认落 `~/.catfish/skills/`**, 不进 `~/.hermes/skills/`. 详见 `docs/LEARN-RECMODE-DESIGN.md § 7.4`
- catfish_skill_install 装的全部走 hub 路径, 自动进 `_read_hub_installed_names()`
- 永不**手动 cp / 软链** skill 到 `~/.hermes/skills/`, 必失踪 (Curator 静默 archive)

### 双 Hub 关系 (5/21 加, 必读)

**关键事实**: hermes 和 catfish **各有一个 Skills Hub**, 解决**不同需求**, 不重叠不越界. 但两个 Hub 的客户端实现现在是平行两套, 是技术债.

#### Hermes Skills Hub (上游自带, 2972 行 in `~/.hermes/hermes-agent/tools/skills_hub.py`)

```
HUB_DIR = ~/.hermes/skills/.hub/   (lock.json / quarantine / audit / taps / index-cache)

SkillSource(ABC) 5 个具体 source:
  ├─ OptionalSkillSource  — hermes 自带可选 skill
  ├─ GitHubSource         — 公网任何 GitHub repo
  ├─ ClawHubSource        — ClawHub 公网社区
  ├─ LobeHubSource        — LobeHub 公网社区
  └─ (留座位给 CatfishHubSource, 现在空着 — Q3 技术债)

Trust: TRUSTED_REPOS = {openai/skills, anthropics/skills, huggingface/skills} 白名单
       skills_guard.py verdict ∈ {safe, caution, dangerous} → INSTALL_POLICY 拦截

CLI: hermes skills search / install / publish / tap add <repo>
     hermes skills publish <path> --to github --repo <owner/repo>   ← 公网发布
```

**面向**: 个人开发者从公网找 skill, 公开发布到 GitHub.

#### Catfish Skills Hub (5/2 ship, `central/skills-hub/`)

```
Hub server (中央 FastAPI):
  ~/.catfish-hub/<ns>/<name>/<version>/  +  PG (skills_versions / skills_audit)
  鉴权: OAuth + RBAC + 部门隔离 + 审核流 ⬜ (manager publish → admin approve, 1 周 backlog)
  Audit: SHA256 校验 + BL-D2 6 类凭据扫描拒上传 + 谁 publish/install jsonl

客户端 (员工本机, tool-bridge):
  catfish_skill_publish — 走 OAuth + 凭据扫描 + 上传中央
  catfish_skill_install — 中央 hub URL pull + 本机 dry-run + dedup
  ⚠️ 自己写了一套, 没接 hermes SkillSource ABC — Q3 技术债
```

**面向**: 企业内员工分享部门 skill 但不让出公司, manager/admin 治理.

#### 不重叠的根因

| 维度 | Hermes Hub | Catfish Hub |
|---|---|---|
| 数据源 | 公网 GitHub + 公网社区 | 企业内部中央 server |
| Trust | GitHub 仓库白名单 | 企业 OAuth + RBAC |
| 公私 | 完全公开 (互联网可见) | 内网私有 |
| 客户内网能用 | ❌ 通常封 GitHub | ✅ 自托管 |
| 解决的问题 | 个人开发者效率 | 企业 know-how 治理 |

按 BOUNDARY 决策原则: catfish Hub 落在"政企合规 + 多员工"独有性, 不算越界. ✓

#### 双 Hub 共存的 4 条纪律

1. **catfish skill (业务 + 教学产物) 只走 catfish Hub publish**. **永禁** `hermes skills publish --to github` (会把内网信息泄到公网 GitHub)
2. **个人想从公网装 skill** → 直接走 hermes Hub (`hermes skills install`), catfish 不拦
3. **catfish Hub 不聚合 hermes 公网 source**. 客户内网通常无 GitHub 访问, 也不该把公网未审 skill 混进企业可信源
4. **CatfishHubSource ABC 实现** — Q3 技术债登记, 配合 Hermes 升级 sprint 一起做. 写完后 catfish_skill_install 砍掉, 走 hermes 标准 `hermes skills install --source catfish-hub` 链路 (~200 行客户端适配)

#### 三层路径 + 两 Hub 完整图

| 路径 | 谁管 | 用途 | Curator? |
|---|---|---|---|
| `~/.hermes/skills/` (bundled) | hermes | 自带 skill (productivity/powerpoint 等) | 不动 (bundled) |
| `~/.hermes/skills/.hub/` | hermes Hub | 从公网 source 装的状态 | 不动 (hub_installed) |
| `~/.hermes/optional-skills/` | hermes | 可选官方 skill | 不动 |
| `~/.hermes/skills/productivity/catfish-*` | catfish 历史 (5/12 BL-MM9-FREEZE) | 现 3 个业务 skill 落这 | ⚠️ 被判 agent-created, 方案 1 ship 后改走 external_dirs 或 pin 兜底 |
| `~/.catfish/skills/<ns>/<name>/` | catfish (方案 1) | **教学产物默认落这**, 通过 hermes config.yaml `skills.external_dirs` 注册让 registry 看到 | 不动 (external_dirs 外, Curator 不扫) |
| 中央 server `~/.catfish-hub/<ns>/<name>/<version>/` | catfish Hub | **publish 后中央副本** | 不动 (在 server) |
| `~/person_task/catfish/skills/department/<name>/` | catfish 工程 (git tracked) | **业务 skill 源码** (leadership-briefing 等) | 不动 (不在 `~/.hermes/`) |

---

### Skills Hub 隐私边界 (5/21 加, 防"自动共享")

**catfish 当前只有一个 Hub — 中央 Skills Hub**, **没有"本机 hub"概念**. publish 到 Hub = 上传中央 + 默认对全公司可见 (审核流 ⬜ 还在 backlog).

教学场景 (BL-LEARN-RECMODE) 录屏 + 语音可能含 PII / 内网信息 / 部门 know-how, 风险比一般 skill 高很多. 硬约束:

| 约束 | 反模式 |
|---|---|
| 教学 freeze 后**默认**落本机 `~/.catfish/skills/`, **不**自动 publish | freeze 末尾直接 publish |
| publish 必须员工**显式点按钮**, 不走 LLM tool call | LLM 自己调 `catfish_skill_publish` |
| publish 前跑 3 道扫描 (凭据 / PII / 内网 URL) 才发请求 | 跳扫描直接 POST |
| publish 弹窗明确警告"会被部门/全公司看到" | 静默 publish |

详见 `docs/LEARN-RECMODE-DESIGN.md § 7.4`. **#11 ⬜ "部门级 skill auto-推"** 是潜在自动共享风险点, ship 前必读这段.

---

## 反模式 (PR review / 设计提议时拦)

| 反模式 keyword | 警告 |
|---|---|
| "catfish 自己实现 X" 但 hermes 已经有 X | 越界 |
| "catfish 重写 hermes Y 因为 hermes Y 不够好" | 应该给 hermes 提 PR, 不是 catfish 包一层 |
| "在 hermes plugin 里加 catfish 专属 hook" | 改 hermes 内部 = 高风险 + 升级 hermes 时丢. 改 catfish 那层 |
| "catfish gateway 加一个 X 的 endpoint, X 是 hermes 也有的" | 同上, 应该走 hermes |
| "catfish 把 hermes 没暴露的内部 class 暴露给 LLM" | 应该给 hermes 提 PR 让它正式 expose, 不是 catfish 偷偷用 |
| 写新 catfish skill 没先 `ls ~/.hermes/hermes-agent/skills/` 扫一遍 | 5/21 加. 重叠风险点: `meeting-*` / `email-*` / `pdf-*` / `slides-*` / `notion-*` / `linear-*` 等 trigger 词易撞 |
| 新 skill 自己造渲染轮子 (重写 docx/pptx/xlsx 生成) 而不复用 hermes 提供的 python-docx / python-pptx / openpyxl | hermes `ocr-and-documents` + `powerpoint` 已经给了渲染底座. catfish 只做政企本地化模板 + LLM 接业务数据 |

---

## 历史教训

**5/16 BL-MEMORY-PLUMBING-DIAG (6h RCA)**:
- hermes 升 0.13 时 memory_tool handler 从无参变成 `kw.get("store")`. catfish tool-bridge 没注 store → 静默拒 6 周.
- 教训: catfish 跟 hermes 的**接口 contract** 要在 `HERMES-UPGRADE-CHECKLIST.md` 显式守住. 升级必跑 `tests/test_memory_store_injection.py` 行为级 contract test.

**5/17 凌晨 BL-MEMORY-DEDUPE-COMPRESS (今晚 over-engineer)**:
- 我 (鲶鱼) 凌晨 03am 看 memory entries 觉得"该加去重 + 压缩工具", 没 grep hermes 现成的 ContextCompressor / autocompress plugin 就动手.
- 鸿波 5 次抓: 没核实 / 越界 / over-engineer / 没信 hermes / 复杂化.
- 教训: 凌晨疲劳期**禁止凭印象设计新功能**. 任何"加新工具"前先 3 问 (上面决策原则).

---

## 商业差异化

这条边界是 catfish 对客户的 USP 一部分:
- 客户问"你们跟 hermes 是啥关系", 答: catfish = SaaS 化包装 + 政企合规 + UI / 入口. hermes = agent 核心.
- 客户问"hermes 升级你们怎么办", 答: catfish-tool-bridge 是 adapter, hermes 升级我们跑 `HERMES-UPGRADE-CHECKLIST.md` 跟. 但**我们没动 hermes 内部**, 所以升级风险低.
- 砸边界 (catfish 改 hermes 内部) = 砸"升级跟随" 承诺.

---

**作者**: 鸿波 + 鲶鱼
**生效日期**: 2026-05-17 (凌晨 04:50)
**修订**: 需鸿波拍板, 不允许工程师 / AI 单方面松绑.

---

## 附录: 威胁模型 — OS 隔离才是边界 (BL-HERMES-014-P0-MIRROR 5/17)

镜像 hermes 0.14 #20317 ("Rewrite security policy around OS-level isolation as the boundary"). hermes 0.14 正式把"plugins 跟 hermes core 之间是同进程协作不是安全边界"写进官方威胁模型. catfish 同套规则:

1. **进程内不是安全边界**. catfish-gateway 进程里跑的所有代码 (含动态加载的 skill / plugin / hermes adapter) 都视为同信任域. 不能依赖 Python 沙箱 / import hook / monkey-patch 做安全控制 — 一个恶意 in-process plugin 可以拿到任意全局变量, 改任意函数, 读任意环境变量.
2. **OS / 容器 / 主机才是边界**. 想跨员工 / 跨租户隔离, 要靠:
   - 不同员工 = 不同 catfish-gateway 进程 / 不同 macOS user / 不同 Linux namespace
   - 不同租户 = 不同 docker container / 不同 VM
   - 网络层 (TLS + JWT) 是跨边界通信的唯一安全机制
3. **catfish 中央层 (FastAPI 进程) 内做的所有"RBAC 检查" 都是 quota / UX 控制, 不是隔离担保**. allowed_models / allowed_tools / allowed_skills 是为了控成本 + 防误操作, 不能拦下"恶意员工已经拿到进程内执行权"这种情况.
4. **想真隔离**: 客户多租户 = 一员工一进程 (systemd 单实例 / launchd 一员工一 service). 这是 SaaS 化包装的一部分, 客户 IT 升 Day 8 接入手册要写清楚.

跟 hermes 0.14 #20317 一致 — 别把进程内复杂代码当安全沙箱用.
