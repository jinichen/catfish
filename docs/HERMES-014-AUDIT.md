# Hermes 0.14 影响面审计 (catfish 视角)

> ⚠️ **P3.5.161 archived (7/3 鸿波)** — 本 doc 里 **族 A `_audit_unknown_tools` +
> `_KNOWN_BUILTIN_TOOLS` 部分**已在 P3.5.161 全删. hermes v0.18 registry:395-408
> 上游本身防 override, catfish 侧冗余. 保留**族 B `X-Catfish-Source` 追踪**
> (ctx.llm bypass 独立 CVE, 未删). 详见 CHANGELOG P3.5.161.

**Release**: `v0.14.0` (tag `v2026.5.16`) — "The Foundation Release"
**发布**: 2026-05-16
**catfish 升级**: 2026-05-17 上午 (鸿波客户机 pilot)
**审计**: 2026-05-17 早 09:30

## 头条数字

- 808 commits / 633 merged PRs / 1393 files changed / 165,061 insertions
- 545 issues closed (**12 P0**, **50 P1**)
- 215 community contributors

---

## 1. catfish 集成的 6 个 hermes 内部 API 表面

catfish 不是 hermes 客户, 是 **hermes 的 sibling project** — catfish-tool-bridge 在客户机跟 hermes 同 venv, 通过 `sys.path` 注入直接 import hermes 内部 module。 这种集成方式 0.14 升级仍 100% 兼容, 但每次升级要 audit 这 6 个表面:

| catfish 文件 | 用的 hermes API | 0.14 状态 |
|---|---|---|
| `edge/tool-bridge/bootstrap.py` | `import model_tools` (触发链式注册) | ✅ 兼容, model_tools.py 在 0.14 仍是 repo root |
| `edge/tool-bridge/bootstrap.py` | `from tools import registry` | ✅ 兼容 |
| `edge/tool-bridge/adapter.py:155` | `from tools.todo_tool import TodoStore` | ✅ 兼容 (module 名未变, tool name 改 `todo`) |
| `edge/tool-bridge/adapter.py:209` | `from tools.memory_tool import MemoryStore` | ✅ 兼容, `__init__(memory_char_limit, user_char_limit)` 签名未变 |
| `edge/hermes-fork/apply_brand_patch.py:281` | `from tools.mcp_tool import get_registered_mcp_servers` | ⚠️ 未实测 |
| `edge/hermes-plugins/catfish-autocompress/` | `from agent.context_compressor import ContextCompressor` + `from agent.context_engine import ContextEngine` | ❌ **deprecate** (5/17 砍, hermes 0.14 默认值采纳我们想要的 50%) |

实测确认 `ContextCompressor.__init__` 签名: `model, threshold_percent, protect_first_n, protect_last_n, summary_target_ratio, quiet_mode, summary_model_override, base_url, api_key, config_context_length, provider, api_mode` — 13 个 kwargs 跟 0.13 一致。

`ContextEngine` ABC 方法: `compress, get_status, get_tool_schemas, handle_tool_call, has_content_to_compress, name, on_session_end, on_session_reset, on_session_start, should_compress, should_compress_preflight, threshold_percent, threshold_tokens, update_from_response, update_model` — 0.14 多了 `has_content_to_compress` / `get_status` / `update_model`, 老接口仍在。

---

## 2. tool name 变化 (catfish 实施层最重要)

hermes 0.14 客户机 `registry.get_all_tool_names()` 实测 **71 个 tool**, 跟 0.13 列表的对比:

### 改名 (catfish ALWAYS_ON_TOOLS / dept allowed_tools 受影响)

| 0.13 老名 | 0.14 新名 | catfish 改动 |
|---|---|---|
| `shell` / `bash` | `terminal` | `_ALWAYS_ON_TOOLS` 改; dept seed 改 |
| `edit_file` | `patch` | 同上 |
| `search` / `grep` / `list_dir` | `search_files` | 同上 |
| `todo_tool` | `todo` | catfish-tool-bridge module 路径 `tools.todo_tool` 未变, 只 tool name 改 |
| `screenshot` / `vision` | `browser_snapshot` / `browser_get_images` / `vision_analyze` | 从 ALWAYS_ON 移除 (太具体不算底座) |

### 新加 (release notes 重点)

| 0.14 新 tool | 用途 | catfish 影响 |
|---|---|---|
| `x_search` (#26763) | X (Twitter) 搜索, OAuth-or-API-key | dept seed 默认不放 sales / legal |
| `video_generate` | unified pluggable video gen | sales / legal 不放 |
| `computer_use` (#21967) | cua-driver, **非 Anthropic-only** | RBAC 假设过时, 需重审 dept |
| `browser_console` (#23226) | 180x faster CDP | 默认放 engineering |
| `kanban_*` (11 个) | multi-agent Kanban (release headline) | 放 engineering / legal (协作场景) |

### 内置 plugin discovery 新加 (catfish-tool-bridge 启动 log 看到 23 plugin / 18 enabled)

- **image_gen providers**: openai / openai-codex / xai
- **video_gen providers**: fal / xai
- **web search providers**: brave-free / ddgs / exa / firecrawl / parallel / searxng / tavily

这些 plugin 注册成 tool 也算 71 之列。 客户 hermes config 装哪些 plugin 决定 tool 表面真实大小。

### catfish 配套已做

- `tools_sanitizer.py` 的 `_KNOWN_BUILTIN_TOOLS` 扩到 71 个真名 (commit `a944fd6`)
- `_ALWAYS_ON_TOOLS` 改 0.14 真名 (commit `a944fd6`)
- alembic migration 006 修 sales/legal dept seed (commit `a944fd6`)

---

## 3. 12 个核心 0.14 改动对 catfish 的影响

按 catfish 关注度排:

### 🔴 必须反应的 (5/17 已 ship)

**(1) `tool_override` plugin flag (#26759)** — plugin manifest 字段, 客户安装的 hermes plugin 能**重命名 built-in tool**。 例: 把 `catfish_browser_open` 重命名成 `catfish_run_skill` (dept-allowed)。 LLM 看后者, 实际调前者。 RBAC 被绕。

- **catfish 反应**: BL-RBAC-DAY4-HARDENING (#75 ship) — `tools_sanitizer._audit_unknown_tools()` 加 `_KNOWN_BUILTIN_TOOLS` 白名单 detect 陌生 tool 名 + log WARN (不 drop, 避免误杀)
- 详情见 `docs/RBAC-PLUGIN-THREAT-MODEL.md`

**(2) `ctx.llm` plugin API (#23194)** — plugin context API, plugin 能**用自己凭证 / base_url 直接发 LLM call**, 完全绕开 catfish-gateway。

- **catfish 反应**: BL-RBAC-DAY4-HARDENING (#75 ship) — `X-Catfish-Source` header. Companion 标 `companion`, plugin ctx.llm 默认 `unknown`. audit jsonl 看 unknown 占比 = bypass 嫌疑。 真正堵需要客户 IT firewall 出口锁回 catfish-gateway, 这是 Ops 不是代码事。

**(3) `huggingface/skills` default tap (#26219)** — 每个 hermes 装机自动看到 HF 外部 skill index。

- **catfish 反应**: BL-RBAC-DAY5 (#67 pending) — schema 设计要加 `source/namespace` 维度 (`bundled:*` / `hf:*` / `github:*` / `local:*`), 不只 name 白名单

**(4) `/handoff` live session transfer (#23395)** — agent active session 切 model/persona/profile, 不丢 messages / tool history / context。

- **catfish 反应**: catfish 5 维 memory injection 是 per-session 绑定 (`BL-MEMORY-UNIFIED-INJECT`)。 handoff 切 model 时 memory provider 是否 re-bind? 0.14 notes 没说。 BL-RBAC-DAY8 (#70) E2E 测覆盖。

**(5) `computer_use` 非 Anthropic (#21967)** — 不再 Anthropic-only, 任何 provider 都能跑 computer_use。

- **catfish 反应**: dept allowed_models 跟 allowed_tools 的隐含关联失效, dept admin 应该重审 sales / legal 是否该有 `computer_use` 权限。 BL-RBAC-DAY7 (#69) admin UI 应该提示。

### 🟡 应该接的新机会 (P1)

**(6) `hermes proxy` OpenAI-compatible local proxy (#25969)** — 把 Claude Pro / ChatGPT Pro / SuperGrok OAuth 订阅暴露成 localhost OpenAI 端点。

- **catfish 反应**: BL-HERMES-PROXY-CHAIN (#73 pending) — 反向链路, catfish-gateway 后端挂 `hermes proxy` 当 OAuth subscription 提供者。 员工拿公司 gateway 调员工自己 Claude Pro 订阅, 企业关键卖点。

**(7) `transform_llm_output` plugin hook (0.13 #21235, 0.14 沿用)** — plugin 在 LLM 输出落 conversation 前 reshape/filter。

- **catfish 反应**: 客户可写 hermes plugin 做二次脱敏 / 内容过滤, 减少 catfish-gateway 耦合。 没紧迫, 文档化即可。

**(8) `HERMES_SESSION_ID` env var to agent tools (#23847)** — hermes 原生暴露 session_id 给工具。

- **catfish 反应**: BL-HERMES-013-SESSION-KEY (#74 pending) — 长期能简化 catfish tool-bridge 的 Store 注入 plumbing。 当前 tool-bridge 工作良好, P2。

**(9) Cross-session 1h Claude prompt cache (#23828)** — Anthropic / OpenRouter / Nous Portal 共享 1h prefix cache。

- **catfish 反应**: BL-CACHE-AUDIT (#76 ship) — 5 维 inject 顺序重排 (stable 前 unstable 后) + `cache_control: ephemeral` marker, AuditTransform 抓 `cache_read_tokens` / `cache_creation_tokens` 进 jsonl。 部署后看一周 cache hit rate, 目标 > 70%。 客户 Claude 调用每个省 ~45% input cost (1.5K / 3K 假设)。

### 🟢 客户自动沾光 (catfish 不动)

**(10) Two new messaging platforms (LINE + SimpleX Chat)** + **Microsoft Teams 端到端**: 22 平台总数。

**(11) Debloating wave (`pip install hermes-agent`) + lazy-install**: 客户装机简单。

**(12) Sessions survive restarts (#21192)**: gateway 重启不丢 session, 跟 BL-TODO-STORE-PERSIST 正交。

---

## 4. 实测踩过的 4 个坑 (5/17 鸿波客户机 pilot)

### 坑 1: PyPI 0.14 wheel 还没推

```
$ pip install hermes-agent==0.14
ERROR: Could not find a version that satisfies the requirement hermes-agent==0.14
       (from versions: 0.13.0)
```

5/17 早 09:00 实测, PyPI 上 `hermes-agent` 最新 wheel 仍是 `0.13.0` (5/14 upload)。 Nous Research 的 release process **GitHub tag → PyPI wheel 滞后 1-7 天**。 0.14 GitHub release notes 写的 "`pip install hermes-agent && hermes`" 是**未来 default 路径**, 当前必须 git source 装。

**结论**: catfish 集成长期都走 git clone path (`~/.hermes/hermes-agent/`), `find_hermes_agent_path()` 找 `model_tools.py`。 PyPI 装的话子模块在 `site-packages/`, sys.path 注入失效 → `BL-HERMES-014-LAZY` 错。

### 坑 2: 错 venv (Python 3.12 vs 3.11.14 黑洞)

客户机有**两个** venv:
- `~/.hermes/venv/` (Python 3.12) — 用户之前 `pip install hermes-agent` 自己建的, **跟 hermes 实际跑无关**
- `~/.hermes/hermes-agent/venv/` (Python 3.11.14) — hermes install.sh 装的, `which hermes` 指向这个的 entry point

升级时**激活错 venv** 装 hermes 等于扔垃圾堆, `hermes --version` 仍报老版本。

**结论**: 升级前必须确认:
```bash
deactivate
source ~/.hermes/hermes-agent/venv/bin/activate
which python   # 应显 ~/.hermes/hermes-agent/venv/bin/python (3.11.14)
```

### 坑 3: `git checkout v2026.5.16` 抹 catfish-autocompress plugin

升级前 catfish-autocompress 装在 `~/.hermes/hermes-agent/plugins/context_engine/catfish-autocompress/` (hermes 源码目录, 不是用户目录)。 `git checkout v2026.5.16` 把不在 tag 里的文件 clean 掉, plugin 整目录被抹。

5/17 上午顺手发现 — hermes 0.14 默认 `threshold_percent=0.50` 跟 catfish-autocompress 想要的阈值一致, plugin 变 no-op, **直接砍**。

**结论**: 客户自定义 hermes plugin 必须装**用户目录** (`~/.hermes/plugins/`), 升级不被抹。 但 hermes 0.14 plugin discovery 是否扫用户目录还需 audit (BL-HERMES-014-UPGRADE-STEP2 deleted)。

### 坑 4: catfish brand patch (cli.py, banner.py, main.py 等) 跟 hermes 升级冲突

`git checkout v2026.5.16` 报:
```
error: Your local changes to the following files would be overwritten by checkout:
cli.py / hermes_cli/banner.py / hermes_cli/main.py / hermes_cli/skin_engine.py / 
hermes_cli/tips.py / rl_cli.py / ui-tui/src/components/appLayout.tsx
```

7 个文件被 `edge/hermes-fork/apply_brand_patch.py` 改过 ("Hermes" → "鲶鱼"), 不能直接 checkout。

**结论**: 升级流程 `git stash push` 保存 brand patch → checkout → `git stash pop` 接回。 如果 patch 跟 0.14 新代码撞 (`apply_brand_patch.py` 的 regex 可能不匹配 0.14 新文本), 需要重 apply。 5/17 实测 stash pop 自动接成功, patch 跟 0.14 兼容。

---

## 5. catfish 配套 commit 清单 (5/17 一天)

按时间顺序:

| commit | 内容 |
|---|---|
| `c163dab` | BL-CACHE-AUDIT — 5 维 inject 顺序重排 + cache_control marker + AuditTransform 透传 cache tokens |
| `81ddd90` | BL-RBAC-DAY4-HARDENING + BL-HERMES-014-LAZY + 升级 runbook v1 |
| `4e80456` | runbook 警告 PyPI 0.14 wheel 还没推 |
| `a944fd6` | tools_sanitizer 71 真名 + sales/legal seed migration 006 |
| `56b20de` | runbook 加 catfish-autocompress re-deploy 段 (后续 f2684f8 推翻) |
| `f2684f8` | BL-CATFISH-AUTOCOMPRESS-KILL — 砍 plugin (hermes 0.14 默认 50% 阈值采纳) |

---

## 6. 还要做的 (P0 → P3)

### P0 (这周内)

- **BL-CACHE-VERIFY** — 部署后跑 2 条 Claude chat, audit jsonl 验 `cache_read_tokens > 0`。 没命中 = inject 顺序仍有动态内容混进 stable 段
- **BL-RBAC-DAY5 (#67)** — allowed_skills schema 加 source/namespace 维度 (huggingface/skills tap 已 ship, 我们刚装 frontend-slides 是 github skill, 真实场景已具备)

### P1 (下周)

- **#75 follow-up** — Companion 加 X-Catfish-Source: companion header, 否则 audit 全是 unknown
- **#73 BL-HERMES-PROXY-CHAIN** — catfish-gateway 后端挂 hermes proxy 当 OAuth subscription provider (企业卖点)
- **#69 BL-RBAC-DAY7** — catfish-web /admin/access UI, dept admin 真能改 RBAC
- **#72 BL-SEC-TOCTOU-AUDIT** — 镜像 hermes 0.14 关 12 P0 安全 patterns

### P2

- **#68 BL-RBAC-DAY6** — visible_rooms per-dept (catfish-web 房间 RBAC)
- **#70 BL-RBAC-DAY8** — E2E + 文档 + 客户接入手册

### P3

- **#47 BL-NEMOTRON-XML-TOOLCALL** — LiteLLM 兼容
- **#74 BL-HERMES-013-SESSION-KEY** — `HERMES_SESSION_ID` env var 替代 tool-bridge Store 注入可行性 (tool-bridge 工作良好, 不紧)
- **#78 BL-HERMES-014-P0-MIRROR** — 拉 hermes P0 closures 镜像 (公开 advisory 页 #24253 被删, 拉不全)

---

## 7. 升级影响面比例 (回答 "升级 0.14 catfish 能直接用很多特性吗")

实际数 0.14 release notes 重头条 (12 P0 closures + 20 Highlight bullets):

- **60% 客户 hermes 端 LLM 行为自动沾光** — CDP / 冷启 / TUI / /handoff / clarify UI / 9 skills / cron / messaging 平台 / debloating。 catfish 中央层不参与, 客户升级体感直接涨。
- **25% catfish 可接的新接入** — hermes proxy / providers as plugins / transform_llm_output / HERMES_SESSION_ID / x_search / Grok / cache。 不接也不坏, 接了多一个能力。
- **15% 必须 catfish 改的** — tool_override (#75 已修) / HF skills (#67 待做) / computer_use 假设过时 / lazy install / cron contextvars。 不改要么 RBAC 出洞要么静默坏。

5/17 已经把 15% 必修中的核心 (#75 + #77 + tools_sanitizer + dept seed migration) ship 完。 剩下的是 #67 Day 5 schema 还要做。

---

## 8. 长期纪律 (跟 BOUNDARY 文档绑)

升级 0.14 教会我们一件事 (`docs/CATFISH-HERMES-BOUNDARY.md`):

**hermes 每次大版本升级会带走 catfish 自家越界写的东西**:
- 0.14 升级直接砍了 catfish-autocompress (`agent/context_compressor.py:407` 默认 50% 采纳我们想要的阈值)
- 0.14 的 unified `memory` 工具继续延续, 我们 `catfish_remember` / `catfish_memory_*` 在 5/16 凌晨刚砍

**下次升 0.15 应该自问**:
1. catfish 当前所有"自家工具"里, 哪些 hermes 0.15 已经做了?
2. 我们 catfish-tool-bridge 的 6 个 hermes internal API 还在不在?
3. dept seed 数据用的 tool name 还存在不?

把这三问写进 BL-RBAC-DAY8 (#70) 的客户接入手册末段, 作 catfish 团队季度回顾用。

---

## Sources

- [Hermes Agent v0.14.0 release page](https://github.com/NousResearch/hermes-agent/releases/tag/v2026.5.16)
- [hermes-agent PyPI JSON](https://pypi.org/pypi/hermes-agent/json) (实证 PyPI wheel 仍 0.13.0)
- 客户机实测 5/17 早 08:00-10:00 (鸿波本机, hermes 0.14 升级 + 71 tool name 验证 + 4 个坑)
- `docs/CATFISH-HERMES-BOUNDARY.md`
- `docs/RBAC-PLUGIN-THREAT-MODEL.md`
- `docs/HERMES-014-UPGRADE-RUNBOOK.md`
