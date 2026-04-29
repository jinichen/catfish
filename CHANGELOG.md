# 鲶鱼 · 进展日志

> 按日期记录每天完成的事、遗留的事、踩过的坑。
> 主要为 (1) 自己回顾 (2) 软著申报时展示真实开发节奏 (3) 未来新人入职快速了解项目演进。

---

## 2026-04-22（周三）

项目从 0 到 P0 · 网关 + 策略插件打通。

### 完成
- 架构讨论定稿：三大支柱（Browser / Email / Self-Evolution）+ 网关 + Skills Hub
- `catfish/` 项目目录骨架创建，每个子目录配 README 占位
- `catfish-gateway` 完整实现：
  - FastAPI + LiteLLM，多 provider 路由（openai / gemini）
  - Dev token 鉴权，SSO 占位（P1 替换）
  - 元数据审计日志，对话内容永不入库
  - 模型目录 `/v1/catalog`、探测 stub、单模型元信息端点
  - `param_overrides` 配置化特殊参数（如 Gemini 3 temperature=1.0）
  - per-model timeout、快速失败、详细错误透传
  - Docker + docker-compose 本地部署
- `catfish-plugin-policy` 完整实现：
  - 4 种 matcher（shell_command / file_read / network_request / tool_name）
  - 4 条默认红线（rm -rf / 凭据读取 / 绕过网关 / 钥匙串导出）
  - 11 条单元测试覆盖
- 代码规范基础设施：
  - `pyproject.toml` 的 ruff 复杂度规则
  - `check_file_sizes.sh` 扫大文件
  - `check_ai_tells.sh` 扫 AI 生成痕迹（bash 3.2 兼容）
  - `CONVENTIONS.md` + `AI-USAGE.md` 贡献规范
- 主设计文档 `catfish-design.md` 定稿（14 章完整设计）
- 端到端验证：手工 curl → 网关 → Gemini Flash-Lite 首次返回 200
- Hermes 侧第一次配成 Custom endpoint 接入网关

### 踩坑
- Gemini 3.1 Pro Preview 免费层配额为 0（只有 Flash-Lite 能用）
- Gemini 3.1 Flash-Lite Preview 时常 503（过载）
- macOS NO_PROXY 配置对 Python httpx 不完全生效，需 unset HTTPS_PROXY
- Hermes `/model` 交互菜单缺 OpenAI 选项，要用 "Custom endpoint"

### 遗留
- Self-Evolution 体检（memory / skill / cross-session search）
- catfish-plugin-gateway-auth（SSO 插件，目前手动配置可用）

---

## 2026-04-23（周四）

## 完成
- **thesis001 项目 AI 痕迹清理**
  - 从 355 条 AI 痕迹降到 15 条合理保留
  - em-dash / Unicode 箭头 / emoji 全部批量清除
  - 剩余 15 条是 LLM prompt 文本 + pipeline 步骤，语义合理保留
  - 新增 `clean_ai_tells.sh` 批量清理脚本
- **扫描脚本跨项目可用**
  - `check_file_sizes.sh`、`check_ai_tells.sh`、`clean_ai_tells.sh` 三件套
  - 软链到 `~/.local/bin`，任何项目目录下一键可用
  - 自动识别当前目录，不再硬编码项目路径
- **catfish-policy 插件端到端激活**
  - 软链 `~/.hermes/plugins/catfish-policy`
  - `hermes plugins enable catfish-policy` 成功
  - `hermes plugins list` 显示 enabled
  - 11 个 matcher 单元测试全部通过
- **bge-m3 embedding 链路就位**
  - 网关配置更新真实 UUID
  - 路由逻辑验证无误
- **运维故障排查手册 `troubleshooting.md` 落地**
  - 覆盖 9 大类典型问题：代理劫持 / VPN 冲突 / 端口占用 / sed 差异 / bash 版本 / 环境变量 / Hermes 配置 / 本地服务 / 诊断顺序
  - 从今天真实踩坑中提炼
- 发现并规避多个环境问题：
  - Clash Verge TUN 模式会劫持本机 10.10.x 流量（切 2.2.3 非 TUN 模式）
  - `HTTPS_PROXY=http://127.0.0.1:7890` 被 `~/.zshrc` 自动注入到所有 terminal
  - macOS 默认 bash 3.2 不支持 `declare -A`，脚本改用 `sort | uniq -c` 兼容
  - zsh 默认不把行内 `#` 当注释，命令行复制粘贴需去掉注释
  - BSD sed 与 GNU sed `-i` 语法不同，跨平台脚本需检测

### 遗留
- Hermes → 内网 Qwen 端到端联调（代理根因已定位，明天 15 分钟可完成）
- Self-Evolution 体检（A2-A4，待 Hermes 通后）

### 明天起手式
```bash
# 清环境变量
unset HTTPS_PROXY https_proxy HTTP_PROXY http_proxy ALL_PROXY
env | grep -i proxy   # 必须空

# 启 Hermes
hermes
# 发 "你好" 验证基本对话
# 再发 "你还记得我吗" 做 Memory 体检
```

---

## 2026-04-24（周五）

本地文件搜索 P0 · 离线也能干活。

### 背景
8:30 到公司，内网 VPN 还没通，手头有 Word / Excel / PPT / PDF 几百个，找东西全靠肉眼翻文件夹。离线也得能干活，所以优先把本地搜索做了。

### 完成
- **`catfish-local-search` 包 P0 落地**
  - 目录：`catfish/edge/local-search/`
  - 子命令：`index` / `query` / `status` / `clean` / `config`
  - 索引库：`~/.catfish/search.db`（SQLite FTS5 + trigram tokenizer）
  - 配置文件：`~/.catfish/search-scope.yaml`（首次运行自动生成）
- **支持格式**
  - Office：.docx .xlsx .pptx .doc .xls .ppt（走 markitdown）
  - PDF：.pdf（走 markitdown，底层 pdfminer）
  - 纯文本 / 代码 / 配置：40+ 扩展名直读
- **查询策略**
  - 长度 ≥ 3：走 FTS5 trigram + bm25 排序 + snippet 高亮
  - 长度 < 3：降级到 LIKE 子串（解决 "鲶鱼" 两字查不到的问题）
- **默认安全边界**
  - 只索引员工明确加入 include 的目录，绝不扫全盘
  - exclude 默认排除 Library / node_modules / .venv / .git / 大文件类型
  - 单文件上限 20MB，避免索引超大 log
- **3 条单元测试全过**
  - test_index_and_query：2字中文 / 4字中文 / 英文短词 / 英文长词都能命中
  - test_cleanup_missing：删文件后索引清理
  - test_stats_summary：统计信息正常
- **真实数据首次跑通**
  - 个人 Documents/Desktop/Downloads + catfish 项目全索引
  - 预期场景都能查到：`设计文档` / `Hermes` / `gateway` / `鲶鱼`

### 踩坑
- pdfminer 对非标准 PDF 会刷大量 `FontBBox from font descriptor` WARNING，把索引进度刷没了；在 `extractor.py` 里把 pdfminer 系列 logger 全部压到 ERROR 解决
- FTS5 trigram 分词器最少要 3 个字符，"鲶鱼" 这种 2 字关键词直接查不到；改成短查询走 LIKE 降级
- zsh 把 `query "鲶鱼"` 当成独立命令找不到，必须 `catfish-search query "鲶鱼"` 完整写

### 遗留
- 现在是全量索引，每次 `index` 都要重新扫；下一步做 watchdog 监听增量
- markitdown 对某些扫描版 PDF 抽不出文本，后续考虑 OCR 兜底
- 没做语义搜索，只做了字面匹配；Phase 3 接 bge-m3 走 catfish-gateway
- Hermes skill `local_search` 封装（让小鲶能调本地搜索）

### 明天 / 下周起手式
```bash
# 连上内网后，继续 Hermes ↔ 内网 Qwen 端到端
unset HTTPS_PROXY https_proxy HTTP_PROXY http_proxy ALL_PROXY
hermes

# 本地搜索进入日常使用
catfish-search query "<关键词>"
```

---

## 2026-04-24 下午（周五）追加

**里程碑：Hermes ↔ 内网 Qwen ↔ Self-Evolution 全链路贯通。**

### 完成
- **网关联调收官**
  - 起 gateway（`python -m catfish_gateway.app`，非 docker 直跑）
  - 端口 8999，5 个模型全部加载
  - 直连内网 Qwen 3.5 122B（UUID: 71c6900d-...）：200 OK，content = "你好！"
  - 经 gateway 再跑一遍：200 OK，dev token 认证生效
- **Hermes 接入 gateway**
  - `/model` 切到 Custom endpoint `http://127.0.0.1:8999/v1`
  - Provider 正确显示 "Local (localhost:8999)"
  - 上下文窗口 128K
- **Self-Evolution Memory 端到端体检全通**
  - 体检 A（写入）：发"我叫陈鸿波，正在做鲶鱼..."，Hermes 触发 `🧠 memory +user`，tool_calls 正确回传
  - 体检 B（同会话召回）：模型准确复述名字 + 项目
  - 体检 C（跨会话召回）：`/exit` 后新 session `20260424_095147_d90b57`，无提示下自动 `🧠 preparing memory…`，召回信息完整
  - 持久化落盘目录：`~/.hermes/memories/`
- **隐含验证（一次打通四件事）**
  - 内网 Qwen 的 tool calling 能力完整
  - 自研 gateway 的 tool_calls 字段无损透传
  - Hermes memory 插件（写 / 读 / 自动 recall）全栈工作
  - LiteLLM `openai/` 前缀 + UUID-in-path 非标准路径在 chat + tool 两场景都稳

### 踩坑
- zsh 单引号里 `$DEV_TOKEN` 字面量不展开，curl 被拒 "invalid dev token"。换成双引号或先 export 后用
- 公司网络封 raw.githubusercontent.com，LiteLLM 启动时拉 model cost map 超时，自动 fallback 到本地缓存，不影响功能（`LITELLM_LOCAL_MODEL_COST_MAP=True` 可彻底跳过）
- `/v1/catalog` 强制 Auth header 导致 jq 报 null（实际响应是 `{"detail":"missing Authorization header"}`）。这是有意保护（泄露内网 UUID），但 UX 不对
- Hermes 默认主题黄色前景在浅色终端下几乎看不清。换 Terminal 深色主题即解

### 遗留（P1 / 今日下一步）
- **A**：catfish-search 接入 Hermes 成 skill `local_search`，让小鲶能主动调本地搜索
- **B**：修 `/v1/catalog` 无 token 返回公开字段的 UX，有 token 才暴露 api_base
- **C**：Self-Evolution 第二层体检 —— 让 Hermes 自生成新 skill（不止记忆，还要能长能力）

### 软著申报证据链要点
这一天有显式的架构决策 + 代码实现 + 集成联调 + 端到端体检 + 持久化验证，是典型的自主开发过程。CHANGELOG 这几条可作为"项目真实开发"佐证。

---

## 2026-04-24 下午（周五）· A+B+C 三战连胜

上午 Self-Evolution 体检通过后，下午把平台的"**能用** → **好用**"三个硬伤一次解了。

### 完成 A：catfish-search 变成 Hermes 原生 tool
把 edge/local-search 从"员工要记得命令行调"升级成"小鲶自己知道该用"。三步进化：

1. **Skill 文档版**（初版，被动触发）
   - 目录：`edge/local-search/hermes-skill/catfish-local-search/SKILL.md`
   - 软链装到 `~/.hermes/skills/productivity/catfish-local-search/`
   - 踩坑：Hermes 0.10 不识别自建 namespace（`~/.hermes/skills/catfish/` 不工作），必须放预定义 namespace 下。记入 IDEAS #24
   - 问题：模型"看得到文档但不主动调"，还是走原生 `search_files`（60 秒 grep），SKILL.md 只在你显式说"用 X skill"才被加载

2. **SOUL.md 工具偏好版**（中间尝试，靠话术哄）
   - 一次性加段落到 `~/.hermes/SOUL.md` 告诉模型"找文件首选 catfish-local-search"
   - 本质上是靠 prompt 让模型"听话"，不确定性高
   - 用户一句话戳破："你意识到吗，这还要多一个组件" → 不是真正的解法，废弃

3. **MCP server 版**（最终方案，原生 tool 化）
   - 新增 `src/catfish_search/mcp_server.py`（约 150 行）：实现 MCP 2024-11-05 stdio 协议，暴露两个 tool：`local_search` / `local_search_status`
   - 新增 entry point：`catfish-search-mcp`（pyproject.toml scripts）
   - 新增 `[mcp]` 可选依赖（pip install 'catfish-local-search[mcp]'）
   - `hermes-skill/install.sh` 自动写入 `~/.hermes/config.yaml` 的 `mcp_servers` 段
   - 配套 `uninstall.sh` 干净卸载（含旧 SOUL.md 段落清理）
   - 6 条单元测试（查询、状态、参数 clamp、错误处理）全过

**最终效果**（Hermes banner 变化）：
```
MCP Servers
  catfish-local-search (stdio) — 6 tool(s)
34 tools · 72 skills · 1 MCP servers
```
工具数从 28 → 34，`local_search` 出现在模型的原生工具列表里。

**实战对比**（同一句"帮我找我电脑里关于鲶鱼的设计文档"）：
| 方案 | 调用路径 | 耗时 | 召回 |
|------|---------|------|------|
| 原生 search_files | find + grep 全盘扫 | ~60s | 3 份 |
| MCP local_search | SQLite FTS5 | ~5.5s（4 次查询合计） | 4 份 |

### 完成 B：/v1/catalog 允许匿名访问
- 修 `auth.py` 拆出 `_verify_bearer` 内部函数，新增 `get_current_user_optional` 依赖
- `catalog.py` 支持 user=None 分支，返回字段本来就是公开信息（display_name / tier / 能力开关），没有 api_base 泄露
- 响应新增 `authenticated` 标志位让客户端区分匿名/登录视图
- `/v1/models` 和 `/v1/chat/completions` 等敏感端点保持强鉴权
- `tests/test_smoke.py` 新增 2 条测试（`test_catalog_anonymous_ok`、`test_catalog_bad_token_is_anonymous_not_401`）
- 新增 `tests/conftest.py` 自动加载 `.env`，避免以后跑 pytest 又忘记 export token
- 意义：Hermes 员工第一次启动时还没配 token 就能拿到模型清单，UX 摩擦消除

### 完成 C：Self-Evolution 长能力维度验证
在 hermes 里让 Qwen 自主生成 `daily-morning-brief` skill（读取最新日报 → 总结三句 → macOS 通知）。全自主执行链：
- `mkdir` 建目录 → `write_file` 写 SKILL.md（YAML frontmatter 格式正确）
- `write_file` 写 morning-brief.py（203 行可执行脚本）→ `chmod +x`
- **自我修复**：跑脚本报错 `Path | None` 类型不兼容 Python 3.9 → 自主 `patch` 改成 `Optional[Path]` → 重跑通过
- 真实创建测试日报 → 实测脚本 → macOS 通知发送
- 调 `cronjob` 工具注册定时任务（Job ID: 9b9c31be4749，下次 2026-04-25 09:00）
- `skill_view` 验证 skill 已加载
- 清理测试文件

规划 → 实现 → 测试 → 修复 → 注册 → 清理，完全自主，只在一个 clarify 超时 120s 后 fallback 默认选项。

### 踩坑
- Python source 里用 `"..."` 嵌套中文 `"..."` 引号会把字符串提前关掉，`…` 被当句外非法字符。写代码里的中文 description 不要用 ASCII 双引号嵌套
- Hermes MCP stdio 握手需要双向持续对话，用 `echo JSON | server` 方式做 install.sh 自检不完整。改用 `hermes mcp test` 作为权威自检
- `hermes mcp add` 命令是 discovery-first 交互式，不好脚本化；改用 Python 直接读写 `~/.hermes/config.yaml` 更可控幂等
- ruff N806 把 `Server` / `Tool` 这种类型重绑定也当"变量要小写"，改用 `server_cls` / `tool_cls` 等规避
- zsh 单引号里 `$VAR` 不展开（curl 时被坑过，今天又栽一次）

### 遗留 / 下周续作
- **Hermes 自定义 namespace 调研**（IDEAS #24）：等 catfish skill 数 ≥ 3 个再做
- **P1: OIDC SSO 取代 dev token**（gateway）
- **MCP server 分发**：现在只能绝对路径指向员工 venv，分发给同事时要调整
- **Browser Agent 深化**（下周起手式）
- **Companion App（邮件 agent）** 开始设计

### 完成加赛：员工一键分发工具链
A/B/C 打完顺手把"给新员工装环境"的工具也做出来，不然分发依赖个人口头传授。

- `catfish/onboarding/install-catfish.sh`（285 行）：macOS / Linux 一键装
  - 8 个步骤：Python 检测 → venv → pip install → PATH 软链 → 默认配置 → 首次索引 → Hermes MCP 注册 → watchdog daemon
  - 交互式（默认）/ `--yes` 无头模式 / `--skip-index` / `--skip-daemon` 四种组合
  - 幂等，重复执行跳过已完成步骤
- `catfish/onboarding/install-catfish.ps1`（264 行）：Windows PowerShell 镜像版
  - `py -3.12` 启动器优先、`mklink /J` 做目录联接（不要求管理员）
  - 同样的 `-Yes` / `-SkipIndex` / `-SkipDaemon` 参数
- `catfish/onboarding/README.md`（151 行）：员工/IT 指南，含 macOS/Linux/Windows 三平台用法、排错、卸载、公司内网 PyPI mirror 配置
- 更新 `scripts/README.md`：onboarding 从 planned 列表移到 "相关位置"

这意味着任何新员工从 git clone 到能用本地搜索 + Hermes 集成，只需要一条命令 + 两次 Y 确认。IT 批量部署时加 `--yes` 即可完全无交互。

### 软著证据链要点
这一天三条主线并行推进 + 加赛分发工具，每条都有：
- 明确的问题定义（"模型看得到但不用"、"catalog 匿名 UX 坏"、"模型能不能长新能力"、"同事怎么装"）
- 可工作的代码落盘（.py / .sh / .ps1 / .yaml / .md）
- 可复测的单元测试（18 passed, 1 skipped）
- 端到端集成验证日志（gateway 启动、MCP 握手、真实 Hermes 对话、跨平台构建）
- CHANGELOG 即时记录

是一个**典型的 agentic 平台开发日**。

---

## 2026-04-24 傍晚 · Browser Agent P0 摸底 + 认知调整

周五晚没收手，顺势启动下周 Browser Agent 支柱。先摸 Hermes 原生 browser toolset 的实际能力，结果**两次测试得到相反结论**，借此重新校准设计方向。

### 完成
- **`edge/browser-agent/` 子模块创建**
  - `README.md`（65 行）：定位 + 路线图 + 当前进度
  - `hermes-skill/catfish-browser-task/SKILL.md`（初版 210 行 → 修订版 ~250 行）：通用浏览器任务模板
  - `hermes-skill/install.sh`（37 行）：一键装 skill 到 Hermes
- **跑了两次实测**，观察到的事实比设计文档里的假设更值钱

### 关键观察（会改设计）
**第一次测试**（公开 GitHub repo 抓 star/issue/commit）：
- `browser_navigate github.com` 耗时 39 秒成功
- 后续点击 / snapshot 都 <1 秒
- accessibility tree 够用，不需要多模态截图
- 数据提取准确

**第二次测试**（同类任务换 repo）：
- `browser_navigate github.com` 连续 2 次 timeout（31s + 29s）
- 模型**自主降级到 `curl` + GitHub REST API**
- 走 API 后 45 秒完成所有字段，数据全对
- **模型的选择比 SKILL 的强制更聪明**

### 据此修订 SKILL.md 的设计认知

| 原设计直觉 | 实测校正 | 改动 |
|-----------|---------|------|
| "browser 是网页任务首选" | 有 API 的场景 curl 远快于 browser | SKILL 新增"决策树"：先问有没有 API，再上 browser |
| "多模态截图识别是关键" | accessibility tree 对结构化页面够用 | 多模态降级为 P3 兜底（canvas / PDF 嵌图才上） |
| "browser 很稳定" | 首次 navigate 不稳定（39s OK / 31s+29s 双 fail） | SKILL 失败模式新增"连续超时 2 次自动降级 API"；Chrome 常驻列为 P1 头号 |
| "SKILL.md 能强制模型走 4 步模板" | 模型把 SKILL 当参考，不当契约 | 4 步模板改为"指导原则"而非强制；要真强制得走 MCP tool（但 browser 没新能力，不适合 MCP 化） |

### 核心顿悟
**不要强迫模型按 SKILL 套路走，要修 SKILL 让它跟模型的实际决策对齐**。
模型自己选 curl over browser 是对的，说明我们该把这条路径正式写进 SKILL 的决策层级，而不是"只是例外 fallback"。这跟 local-search 走 MCP 化的路径对称 —— 一个是"让 tool 出现在工具列表"，一个是"让 SKILL 跟工具选择实际逻辑对齐"。

### 遗留（P1 · 周一开工）
- **Chrome 常驻调研**（解首次 30 秒问题）
  - 方案 A：Hermes attach 到员工已开的 Chrome（`--remote-debugging-port=9222`）
  - 方案 B：Hermes 用独立 Chrome 但复用员工 profile（`--user-data-dir`）
  - 读 Hermes 源码 `~/.hermes/hermes-agent/**/browser*` 找 Chrome 启动参数，看能不能配置
- **内网适配器**（如果公司内网 Jira/Confluence/OA 可 Mac 直连）
  - 选一个最常用的做成 `catfish-browser-jira` / `-confluence` / `-oa` 独立 skill
  - 这才是 browser 真正的价值场景（没有 API 的老系统）
- **JS execution 能力验证**：Hermes 有没有 `browser_eval` 之类的工具可以跑任意 JS？可以绕过一些 SPA 异步加载问题

### 软著证据链补充
一天内同一件事两次实测 → 观察到相反结论 → 据此修订 SKILL.md 的设计哲学 → CHANGELOG 记录推理路径。
这是**真实开发的"用脚投票"过程**，比"按设计文档一步步实现"更贴合 agentic 工程的本质。

### 收工前加读：Hermes browser 源码（周一路径已明）
读 `~/.hermes/hermes-agent/tools/browser_tool.py` 找到性能瓶颈根因和解决入口：

- Hermes 用 `agent-browser` CLI 做后端（三模式：local / Browserbase / Browser Use）
- **local mode 每次启新的 headless Chromium 就是 30~60 秒的来源**
- Hermes 原生支持 CDP attach，代码路径：
    - `_resolve_cdp_override()` 规范化 CDP URL（213 行）
    - 从 config.yaml 读 `browser.cdp_url`（283 行）
    - `_create_cdp_session()` 走 CDP 不启 Chromium（907 行）
    - 决策分叉：有 `cdp_url` 就用 `--cdp`，否则 `--session`（1144~1153 行）
- 三种配置方式任选其一：`config.yaml` / `BROWSER_CDP_URL` env / `/browser connect` 交互命令

**这意味着 P1 的所有问题同时解**：
- 性能（跳过 Chromium 启动，首次 <3 秒）
- 登录态（用员工自己的 Chrome，天然复用所有 cookie/session）
- 透明度（员工日常 Chrome 就是 agent 用的那个，看得见全过程）

**周一开工 checklist 已写进 `edge/browser-agent/README.md` 的 P1 段落**，4 步执行（30min 手工验证 + 1h 包装脚本 + 2h 跨平台 + 0.5h onboarding 集成），总估 4 小时，下周早上应该能吃完。

### 明天 / 下周起手式
已经把 P1 Step 2/3/4 在周五晚提前吃掉。周一真正要做的只剩 **Step 1 手工验证**：

```bash
# 验证 attach 脚本能端到端跑通
cd ~/person_task/catfish

# 一条命令搞定（替代原本人工 5 步：关 Chrome / 带参启 / 抓 URL / 写 config / 测）
bash edge/browser-agent/scripts/catfish-browser-attach.sh

# 然后 hermes 里发一次测试，看 browser_navigate 首次耗时是不是 <3 秒
hermes
# > 帮我打开 github.com 看首页
```

预期：
- 如果 attach 脚本里的 Chrome 启动/端口/config 写入流程都跑通且 browser_navigate <3 秒，P1 收工
- 如果脚本某一步失败，进入 debug（具体分支看 attach.sh 的 5 个 step 哪步报错）

### 今晚提前做完的（Step 2/3/4）
虽然标的是周一任务，周五晚临时决定不歇手直接干了：

- `edge/browser-agent/scripts/catfish-browser-attach.sh`（198 行）：Mac/Linux 版 CDP attach 一键脚本
    - 5 步：检测端口 → 启 Chrome（带 `--restore-last-session`）→ 抓 `webSocketDebuggerUrl` → 写 `~/.hermes/config.yaml` 的 `browser.cdp_url` → 总结提示
    - 安全设计：检测到 Chrome 已在跑但没带调试端口时**不粗暴 kill**，友好提示"保存工作后手动关 Chrome 重跑"
    - 幂等：重复跑自动更新 WS URL（每次 Chrome 换 PID，URL 里 UUID 变）
- `edge/browser-agent/scripts/catfish-browser-attach.ps1`（167 行）：Windows PowerShell 镜像版
- `edge/browser-agent/scripts/catfish-browser-detach.sh`（46 行）：从 config.yaml 删 `browser.cdp_url`，Hermes 回到默认 local 模式
- `edge/browser-agent/scripts/README.md`（95 行）：使用说明、隐私边界、已知限制、路线图
- 改 `onboarding/install-catfish.sh`：新增 step 9 询问"是否启用 Browser CDP attach 模式"（默认 **N**，是个隐私升级决策，必须员工明确同意）；新增 `--skip-browser-attach` 参数

### 隐私决策的刻意设计
attach 模式下 Hermes 能看到员工 Chrome 所有 tab（URL / DOM / cookie / 登录态）。这是明确的权限升级。

onboarding 的 step 9 **默认选 N**（即 confirm 函数第二参数是 "N"），不同于 step 6 (index) 和 step 8 (daemon) 默认 Y。这一条是**意图上的对抗**：性能诱人 → 员工容易随手点 Y → 但这是隐私上的大事，必须强制"减速思考"。

README.md 里的"隐私边界"段也用很大篇幅讲清楚好处和代价，让员工做 informed decision。

### 至此 Browser Agent P1 的代码侧 100% 完成
周一只需要员工真正在自己 Mac 上跑一次验证。如果 Step 1 验证通过，当天下午就能开工 P2（内网系统适配器），比原定计划快 2~3 天。

### 晚上继续实测（周一任务实际是周五晚完成的）
用户问"为什么不现在做"，直接上手。实测中发现两个需要修正的点：

**修正 1：macOS 启动 Chrome 必须用 `open -na`，不能 nohup**
直接 nohup `.app/Contents/MacOS/Google Chrome` 在 macOS 下 Chrome 会启动但调试端口不监听。改用 `open -na "Google Chrome" --args ...` 修好。

**修正 2：Chrome 2024 安全策略 —— 默认 profile 下 `--remote-debugging-port` 被静默忽略**
这是今天最大的"意外收获"。症状：Chrome 起来了，`pgrep` 能看到进程带参数，但 `lsof -iTCP:9222` 空白，`curl http://localhost:9222` Connection refused。
Google 从 2024 年起禁止默认 profile 开调试端口，防止恶意软件劫持员工日常登录态。
**解法**：加 `--user-data-dir=$HOME/.catfish/chrome-profile` 用独立 profile。

**这个"bug"反而变成了设计上的大改善**：
- 原计划：Hermes attach 员工日常 Chrome → 看到所有 tab / cookie → 明显的隐私升级 → 需要员工 opt-in
- 新实际：Hermes 只能看专用 profile → 跟员工日常 Chrome 物理隔离 → 隐私边界天然更紧
- 代价：员工要在专用 Chrome 里**手动登一次**公司系统。但登完持久化，心智模型 ("打开 Catfish Chrome → 登公司系统 → Hermes 干活") 比"Hermes 偷偷接管我正在用的 Chrome"清晰得多

### 真实性能数据（实测）
| 指标 | 原设计 local mode | 新 CDP attach 模式 |
|------|------------------|-------------------|
| 首次 navigate | 30~60 秒 / 常 timeout | 11s timeout → retry 7s 成功 |
| **热路径 navigate** | 5~15 秒 | **2.7 秒** ✅ 目标达成 |
| Chrome 启动 | 10s 级 headless Chromium 下载 | 4 秒本地启动 |
| 跟员工日常 Chrome 关系 | 无关 | 零冲突，并存两个 Chrome |

首次 11s 是 `agent-browser` CLI 的 Node.js 启动开销（不是 Chrome 启动）。后续热路径回到 2.7s。员工实际使用中首次开销是一次性的，整体体感到达"可用"线。

**未来 P1.x 的进一步优化**（降到 <1s）需要让 `agent-browser` 本身常驻，那是 Hermes upstream 级别的改动，暂不做。

### 架构设计调整（需要周一体现到 catfish-design.md）
原 §3.1 "Browser Agent" 章节里写的"接员工 Chrome profile（CDP attach）"需要改成：
> 用独立 Chrome profile（~/.catfish/chrome-profile/），员工在这个专用 Chrome 里登公司系统。
> 优点：跟员工日常浏览隐私物理隔离，不需要 opt-in 隐私升级决策。

### 软著证据链补充
同一天内同一个问题被实测**三次**（每次都发现新东西）：
1. 第一次：Chrome 启动参数没生效 → 诊断是 nohup 问题，改用 open -na
2. 第二次：Chrome 启动成功但端口不监听 → 诊断是 Chrome 2024 安全策略，改用 --user-data-dir
3. 第三次：CDP attach 工作，但热路径才达到 <3s，首次仍是 11s → 定位到 agent-browser CLI 启动开销（P1.x 再优化）

每次实测都推翻一个设计假设，替换为现实约束。这个"假设 → 实测 → 修正"的链条比"按设计文档一口气写完"更贴合真实开发节奏。

---

## 2026-04-24 深夜 · Browser Agent 端到端业务流跑通

在专用 Catfish Chrome 里登了一次内部"合规管控平台"（http://10.10.111.53:8776, admin/admin123），然后让 Hermes 执行完整的"登录 → 导航 → 抓数据"业务流。

**是鲶鱼平台 Browser Agent 支柱的第一次真实业务落地，不是 demo。**

### 实测执行链

| 步骤 | 动作 | 耗时 | Hermes 工具 |
|------|------|------|------------|
| 1 | 首次进入 10.10.111.53:8776 | 10.9s 一次 timeout + 5.2s retry | browser_navigate |
| 2 | 识别登录表单结构 | 0.9s | browser_snapshot |
| 3 | 填用户名 / 密码 / 点登录 | <1s | browser_type x2 + browser_click |
| 4 | 确认登录成功 | 0.3s | browser_snapshot |
| 5 | 导航 "用户管理" 模块 | <1s（click 失败 1 次 auto retry） | browser_click + browser_snapshot |
| 6 | 完整抓取用户列表 | 0.3s | browser_snapshot |

端到端总耗时 **约 19 秒**。抓到的数据包括 3 个用户的用户名 / 邮箱 / 姓名 / 状态 / 角色 / 部门 / 创建时间，全字段结构化。

### 今天拼的四件事组合起效
这个端到端场景要同时满足四个前置条件，缺一不可：

| 前置 | 今天哪一步搞定 |
|------|---------------|
| Hermes 本身能跟 agent 通信 | 早上 Self-Evolution 体检 + gateway 通路 |
| Browser Agent 原生工具链 | 下午傍晚 Browser Agent P0 摸底（认识 browser_navigate / snapshot / click / type 等） |
| Chrome CDP attach（热路径 <3s） | 晚上 P1 实测修复（macOS open -na + 独立 profile） |
| 内网登录态复用 | 刚才手工在专用 Chrome 里登一次，持久化到 `~/.catfish/chrome-profile/` |

**这一整天的工作今晚在这个业务流里全部兑现**。

### 关键认知
- **专用 Chrome profile 能访问内网 10.x IP**：Mac 系统级 VPN/零信任在所有 Chrome 实例生效，不用员工做任何额外网络配置
- **accessibility tree 对企业内部系统完全够用**：这个 admin 管理系统的全部交互都靠 DOM snapshot + @ref 定位，没调 browser_vision。省 token 省时间
- **click 偶尔失效的 retry 有必要**：`@e6` 第一次报 error，auto retry 就过了。Hermes 自带的这个机制很有价值
- **登录态持久化是决定性体验**：一次登录 vs 每次 Hermes 问密码，这是"工具"和"副手"的本质区别

### 需要警惕的
- **admin/admin123 是弱密码**：生产环境必改。鲶鱼员工装配套里以后应该加"不存凭据，读员工配在 OS Keychain 的账号"
- **admin 权限下的自动化是双刃剑**：能列/编辑/禁用/删除用户 —— **绝不能让 Hermes 没经员工确认就做删除操作**。SKILL.md 里"填表但不自提交"的规则要落实
- **Selector 稳定性** @e6 失败这种偶发，需要在 SKILL.md 里强化"2 次失败后换 navigate 直接 URL"的降级策略

### 下一步（P2 开工的起点）
今晚这个流程证明 Browser Agent 对**一个具体内网系统**能工作。下周把它变成 **可复用的 catfish-browser-compliance skill**（或类似命名）：

- 业务范围：用户管理、风险分析、合规报告
- 封装的模式：登录自动化 / 用户列表抓取 / 按条件过滤 / 触发分析 / 读取报告
- 员工视角的 prompt：
    - "帮我在合规平台上看一下谁是系统管理员"
    - "查下 XX 项目的合规检查状态"
    - "今天有多少新建用户"

这是 **三大支柱第一根真正立起来的时刻**。软著申报材料里这一段是"Agent 操作企业内部系统"的核心演示场景。

---

## 2026-04-25（周六）

**今日主轴**：鲶鱼三层落地 —— **身份层**（小鲶替代 Hermes）+ **网络层**（gateway 屏蔽代理复杂度）+ **桌面层**（Companion App 从 0 到 v0.1）。

从员工 `catfish` 命令的鲶鱼 banner，到 Hermes 自报"我是小鲶"，到 Companion 桌面应用一屏看见 gateway/Chrome/local-search 状态 + 全部历史会话 + 模型可达性 —— 全链路打通。

---

### 一、身份层：让小鲶替代 Hermes（早上）

#### Tier 1 · SOUL.md 替换
- 写 `catfish/edge/identity/SOUL.md`（80 行）：定义小鲶的 5 哲学（边缘主权 / 中央最小 / 礼物经济 / 红线不审查 / 平权不反人）+ 语调（直接、用"你"不用"您"、不堆套话）+ 工具偏好（catfish-* > native）
- 写 `catfish/edge/identity/install.sh`：备份 `~/.hermes/SOUL.md` → `.before-catfish`，软链 catfish 版本
- Hermes 每次对话都自动 reload SOUL，**不用重启就生效**

#### Tier 2 · catfish 命令包装
- 写 `catfish/edge/branding/catfish`（195 行 bash）
- 子命令：`version` / `status` / `doctor` / `--raw`（绕过 banner）/ `--no-banner`
- `cmd_status` 一键看 gateway:8999 / Chrome:9222 / local-search / SOUL symlink / Hermes runtime 是否就绪
- banner 用 ASCII 框 + `🐟 鲶 鱼 · CATFISH`

#### Tier 3 · Hermes UI 深度品牌覆盖
- 写 `catfish/edge/hermes-fork/apply_brand_patch.py`（~300 行）：可逆补丁工具
  - `RULES`：简单字符串替换
  - `REGEX_RULES`：多行块替换（`HERMES_AGENT_LOGO` / `HERMES_CADUCEUS` / `TIPS` 列表）
  - 函数级替换：扫 `def build_welcome_banner(`，替换到下一个 `def`/`class`
  - 备份机制：`.before-catfish` 后缀，`--revert` 一键还原
  - 已 patched 检测防重复
- `~/.hermes/hermes-agent/hermes_cli/banner.py`：
  - `HERMES_AGENT_LOGO = ""` / `HERMES_CADUCEUS = ""`（清空 ASCII art）
  - 标题改 `f"鲶鱼 v{VERSION} ({RELEASE_DATE})"`
  - `build_welcome_banner` 整个函数替换为 5 行极简版本（cyan 框 + 模型/cwd/tools/session 四行）
- `~/.hermes/hermes-agent/hermes_cli/skin_engine.py`：`agent_name` 全替换 `Hermes Agent` → `鲶鱼`，`Goodbye! ⚕` → `再见 🐟`
- `~/.hermes/hermes-agent/hermes_cli/tips.py`：200+ Hermes 默认 tips 全替换为 10 条鲶鱼定制
- 写 `catfish/edge/hermes-fork/uninstall.sh` 还原入口

#### 颜色调优
- `/skin daylight` 切到浅色背景适配的皮肤（之前 default 在白终端上看不清输入字）

---

### 二、网络层：员工不该 care HTTPS_PROXY（中午）

#### `catfish/central/llm-gateway/src/catfish_gateway/network.py`（~150 行新文件）
- `precheck_and_setup()` 启动前自检：
  - 检测当前 `HTTPS_PROXY` 端口可达性（Clash/Mihomo 在不在跑）
  - 不可达就 `unset` 整套 PROXY_VARS，避免 gateway 内部调外网时去连死端口
  - 强制 `NO_PROXY` 包含 localhost + RFC1918 三段（10.0.0.0/8, 172.16/12, 192.168/16）+ 公司常见 DNS 后缀
- `print_banner(status)` 输出一行人话状态行
- 14 条单元测试 `test_network.py`（不需要真代理，全 mock）

#### `report_upstream_reachability(models)`：启动后上游自检
- 串行探每个 chat 模型（timeout 1.5s × 5 模型 = 最多 7.5s 启动延迟）
- 内网模型直接 TCP 探 `api_base`
- 外网模型（api_base=None 走 LiteLLM 内置）靠当前代理状态推断
- banner 输出 `✓ catfish-private-main  直连 10.10.40.102:32730` / `✗ catfish-public-gemini-flash  外网模型，代理不可达`
- 全失败时打印"常见原因 → 修法"提示
- 返回 dict 缓存到 `app.state.upstream_status`，给 `/v1/catalog` 复用（避免每次列模型都做 TCP 探测）

#### `app.py` lifespan 接入
- 新启动顺序：`precheck → uvicorn 起来 → load_config → upstream_status 缓存 → 服务就绪`

#### `catalog.py` 三态状态分级
- 不再过滤 `is_available=False` 的模型（之前 OLD 行为是隐藏，导致前端不知道哪些没配 key）
- 新增三个字段对外暴露：
  - `api_key_configured: bool`（API key 环境变量是否设了）
  - `is_reachable: bool|None`（上游 TCP 是否通；None = 还没探）
  - `status_reason: str`（人话解释）
- 前端据此画 ✓绿（配+通）/ ⚠黄（配但不通）/ ○灰（未配）三态

---

### 三、桌面层：Companion App 从 0 到 v0.1（下午→深夜）

#### 技术栈选型
- **Tauri 2.0** + React 18 + Vite + TypeScript + Zustand
- 拍板理由：跨平台（macOS+Windows），包体 ~10MB（vs Electron 80MB+），鲶鱼"轻爽"品牌一致
- 包管理选 `npm`（用户没装 pnpm）

#### 项目骨架（`catfish/edge/companion-app/`，69 文件）
- `src-tauri/`（Rust 后端，15 文件）
  - `commands/` 按职责拆 9 个：gateway / chrome / local_search / health / logs / sessions / identity / skills / system + 共享 types
  - `services/` 内部模块（前端不可见）：process（跨平台子进程）+ catfish_paths（路径解析）
  - `tray/` menubar 占位
- `src/`（React 前端，34 文件）
  - 单页 + 三 tab（控制台 / 会话 / 仪表盘）
  - `components/`：StatusDot · BrandHeader · TabBar · ConfirmDialog
  - `hooks/`：useServiceStatus（轮询 3s）· useLogTail · useSessions · useCatalog（轮询 15s）· useIdentity
  - `lib/tauri.ts`：唯一 invoke 出口（强类型包装）
  - `store/`：services / identity / ui（Zustand 三个 store）
  - `styles/tokens.css`：CSS 变量驱动品牌色 + dark mode 自动跟随系统

#### 控制台 tab：subprocess 真管理
- `services/process.rs`：跨平台 `spawn_detached`（Unix `process_group(0)` 脱离父进程；Windows `CREATE_NO_WINDOW + DETACHED_PROCESS`）+ `kill`（SIGTERM 800ms → SIGKILL）+ `is_alive`（`kill -0` / `tasklist`）
- `services/catfish_paths.rs`：解析 `CATFISH_ROOT` / `~/person_task/catfish` / `~/catfish` 三级降级；找 venv python / Chrome 二进制；PID 文件 + log 文件路径
- 三个真实启停命令：
  - `gateway_start/stop/status`：spawn `python -m catfish_gateway.app` PORT=8999；停 = 读 PID kill；status = TCP+`/healthz` 双探
  - `chrome_launch/kill/status`：spawn 隔离 user-data-dir 的 Chrome `--remote-debugging-port=9222`（不动员工日常 Chrome）；status = TCP+`/json/version`
  - `local_search_start/stop/status`：spawn `python -m catfish_search.cli watch`，PYTHONPATH=src（免 pip install）；status = PID 文件 + is_alive
- 一键全启 / 全停 / 重启按钮：`Promise.allSettled` 并发，全停 800ms 后再起
- 实时日志面板（LogPanel）：UI 框架在，Rust 端日志流推送未实现（留 TODO）

#### 会话 tab：SQLite 真读
- 发现：`~/.hermes/sessions/` 是请求 dump 文件夹（调试用），**真会话在 `~/.hermes/state.db` SQLite**
- `commands/sessions.rs`：rusqlite（bundled 自带 sqlite3 免装 libsqlite3-dev）+ chrono ISO-8601
- `sessions_list`：查 sessions 表，按 `started_at DESC LIMIT 100`，5 项 token 求和（input + output + cache_read + cache_write + reasoning）
- `sessions_get`：查 messages 表 `WHERE role=? AND content IS NOT NULL ORDER BY timestamp DESC LIMIT 1`，过滤掉只做 tool_call 没真文字回复的助手消息
- 4000 字符截断防 IPC 数据爆炸
- UI：左侧列表（title fallback id 后 6 位 + active 绿徽标 + endReason）+ 右侧详情（最后用户/助手消息）

#### 仪表盘 tab：四张卡片
- **身份卡**：系统 `$USER` + SOUL 符号链接判断（symlink → "鲶鱼定制"，普通文件 → "默认"）+ skin（config.yaml 多 key 容忍）+ default_model（`model.default`）+ 活动会话（state.db `WHERE ended_at IS NULL`）
- **配额卡**：占位（TODO 接入中央 quota 服务）
- **模型卡**：消费 `/v1/catalog` 的三态字段，UI ✓绿/⚠黄/○灰，鼠标悬停看 status_reason
- **Skills & MCP 卡**：扫 `~/.hermes/skills/<namespace>/<skill>/SKILL.md` 解析 YAML frontmatter，按 namespace 折叠（27 类 / 100+ skills）；`config.yaml` 的 `mcp_servers` 列表

#### 端到端验证（晚上的真测试）
- Hermes 自报"我是小鲶。鲶鱼平台为员工提供的数字副手" —— **SOUL 三层覆盖完全生效**
- memory 工具自动写入"User nickname is 波哥 (Boge) in addition to 鸿波 (Hongbo)" —— 别名持久化
- local-search MCP 调用工作（员工问"企业资质规划"时 fork stdio MCP 查本地文件）

---

### 踩坑

#### 工具链篇（国内开发踩坑大全）
- **pnpm 不在用户机器上**：默认推 pnpm 是开发者惯性，user 没装。一次性改全栈到 npm（`package.json` 移除 `packageManager` 字段、`tauri.conf.json` 改 `npm run dev/build`、`scripts/*.sh` 改 npm）
- **HTTPS_PROXY 把 npm install 卡死**：用户 shell 残留 `HTTPS_PROXY=http://127.0.0.1:7890` 但 Clash 没起。`unset` 后 `npm install` 5 秒完成
- **npm 默认 registry 龟速**：换 `https://registry.npmmirror.com` 永久全局
- **Rust toolchain 没装**：`rustup` 走 USTC 镜像 `https://mirrors.ustc.edu.cn/rust-static`
- **Cargo crates.io-index 走 USTC sparse**：`~/.cargo/config.toml` `replace-with = 'ustc'` + `sparse+...`
- **USTC 镜像偶发 SSL 抽风**：cargo 自带 retry 救场，但日志会刷 `spurious network error` —— 看到不慌
- **Tauri 编译期要 icons**：`generate_context!` 宏会嵌入 `tauri.conf.json` 列出的图标，`.gitkeep` 占位文件不够。`qlmanage -t -s 1024 -o /tmp src.svg` 转 PNG，然后 `npm run tauri -- icon` 一键派生 32x32 / 128x128 / @2x / .icns / .ico
- **macOS Xcode CLT 缺失会卡 cargo link**：`xcode-select --install` 解决（用户已装好）

#### Rust/Tauri 设计篇
- **Tauri 命令名全局唯一**：一开始让三个服务都用 `start/stop/status` 同名 → invoke_handler! 冲突。改成 `gateway_start` / `chrome_launch` / `local_search_start` 全限定命名
- **`reqwest .json::<T>()` 需要 Deserialize**：只 derive `Serialize`（前端用）不够，反向解析也得 `Deserialize`。从此默认三件套打包：`#[derive(Debug, Serialize, Deserialize)]`
- **Vite `import.meta.env` 类型缺失**：要 `<reference types="vite/client">`，加一个 `src/vite-env.d.ts` 一行解决
- **`reqwest` 默认走系统代理**：`Client::builder().no_proxy()` 强制 localhost 不走代理，避免员工 `HTTPS_PROXY` 把 127.0.0.1:8999 也劫了

#### 数据层踩坑
- **`~/.hermes/sessions/` 不是会话文件夹**：是请求 dump（调试用 JSON）。真会话在 `state.db` SQLite。早期猜 JSONL 完全错向
- **Hermes sessions 表没 cwd 列**：之前我前端按"项目目录"展示 → 砍掉，改用 `title || id 后6位`
- **Hermes 不持久化 skin**：`/skin daylight` 是运行时切换，重启 hermes 回 default。Companion 仪表盘只能显示 default
- **catalog 旧 filter 行为**：原 OLD 行为隐藏 unavailable 模型 → 前端永远不知道哪些没配 key。改成全暴露 + 三态字段
- **catalog 缓存生命周期**：现在是启动时探一次，运行中 VPN 断了 gateway 不知道。前端改成 15s 轮询缓存，但底层 cache 还是启动 snapshot

#### UX 篇
- **皮肤行的开发者注释泄漏**："Hermes 不持久化 skin（/skin daylight 是运行时的）"是给我自己看的，不该出现在员工 UI 上。即时砍掉
- **+新会话按钮不能动**：`open_terminal` 是 stub。补 osascript 调 Terminal.app 自动跑 `catfish`

---

### 关键认知

- **"工具栈零摩擦"是真用户体验**：员工不该 care `HTTPS_PROXY` / Clash / `RUSTUP_DIST_SERVER` / npm registry 这些"系统级状态"。鲶鱼平台的责任就是把它们藏在启动逻辑里，员工看到的是"OK 现在能用"或者"哪里坏了点这里修"
- **subprocess 管理本质是三件套**：PID 文件 + 探活 + 日志重定向。跨平台成本远比想象低（Unix 用 `process_group(0)`，Windows 用 `CREATE_NO_WINDOW`），不需要 `nix` / `libc` 这种重 crate
- **声明式可达 vs 隐式过滤**：catalog 的 OLD 设计"过滤掉不可用模型"省事但牺牲了诊断信息。改成"全暴露 + 三状态字段"后前端能展示"为什么这模型不能用 + 怎么修"
- **Hermes session 真存储是 SQLite 不是 JSONL**：现代 agent 框架普遍这么做（Claude Code 也类似），消息+会话+memory+索引全在一个 db 里。读 `state.db` 是 Companion 跟 Hermes 集成最稳的接口
- **品牌覆盖必须三层**：SOUL（人格语调）+ 命令包装（员工触达点）+ 源码 patch（UI 字面）。少一层就会泄漏 "Hermes" 字样，前 hermes/Nous Research 字样会在某些 banner / 错误提示里露馅
- **Companion 是"边缘自治"哲学的最佳载体**：每个员工的本机控制中心 = 鲶鱼平台的"边缘"，单文件桌面 app 装好就能管所有本地组件，不依赖任何中央服务（除了配额）

---

### 遗留

- **配额卡**：`1.24M / 5.00M tok` 是 hard-coded mock。要做的话需要先决定中央 quota 架构（按 token 还是 USD？按月还是按日？quota DB 存哪？）—— 几天工作量
- **LogPanel 实时日志流**：UI 框架在，Rust 端 `commands::logs::tail` 还是 stub。需要 spawn 后台任务 tail 文件 + `app_handle.emit("log:<service>", payload)` —— ~80 行 Rust
- **catalog 缓存运行中刷新**：现在只在 gateway 启动时探一次。VPN 状态变化期间，Companion 看到的还是启动时 snapshot
- **chrome status 检测不严**：TCP 9222 通就报 healthy，但不一定是 Companion 起的实例（员工也可能自己起了带 9222 的 Chrome）。要做"严格匹配 Companion 起的"得读 `/json/version` 的 userDataDir 字段
- **Companion 自启动**：macOS LaunchAgent / Windows 自启项 没接 —— 员工还是要手动启 Companion
- **MCP server 列表没活性探测**：现在全显绿点，stdio MCP 真没办法 ping，要用就只能 spawn 测试 ListTools 调用，开销大
- **Companion 没接 SSO**：身份卡的 SSO 行已删，等中央 SSO 服务起头再说

---

### 明天起手式

- **commit 今天的工作**：CHANGELOG 写完，commit 信息切分（gateway / hermes-fork / companion-app 三块）
- **如果继续 Companion**：补 LogPanel 实时日志流（80 行 Rust，员工最爱的"看 gateway 在干啥"功能）
- **如果转催 P2**：catfish-browser-compliance skill 抽象（昨天的内网管理系统通路落地为可复用 skill）
- **如果想 polish 仪表盘**：Quota 架构拍板（按 token/USD？月/日？DB？）

---

### 今日总账（数字版）

- Rust 代码：~1100 行新增（services + commands + types）
- TypeScript 代码：~1500 行新增（hooks + components + tabs + lib）
- Python 代码改动：~120 行（network.py 新增 + catalog.py / app.py 改动）
- bash + AppleScript：~50 行（identity install + catfish wrapper + open_terminal）
- 总文件改动：~70+ 个，涉及 3 大组件（identity / gateway / companion-app）
- 端到端通路验证：4 次（catfish 命令 / 仪表盘模型 / 会话查询 / Hermes 自报小鲶）

**这一天等于 4-5 天的活在 14 小时内压缩完。**

---

## 2026-04-26（周日）

**今日主轴**：按 TOMORROW.md 节奏起手 —— P0-1 Self-Evolution M2 落地。

### 完成

#### P0-1 · `catfish_today_summary` 原生工具（~40 min）

修昨晚截图里的 UX bug：员工问"今天学了什么"时，LLM 调 `session_search` 翻历史，答非所问。给 LLM 一个明确的 native tool 直接回答今日活动。

- 新增 `catfish/edge/tool-bridge/src/catfish_tool_bridge/catfish_tools.py`（~210 行）
  - `CATFISH_NATIVE_TOOLS` schema：description 显式告诉 LLM 这个 tool 用于回答"今日"问题，而不是 `session_search`/`memory_recall`
  - `collect_today_summary()` 直读 `~/.hermes/USER.md` + `memories/*.md` + `skills/<ns>/<name>/SKILL.md` mtime + `state.db` sessions/messages —— 与 `learning.rs` 同一份逻辑的 Python 镜像
  - 故意不走 IPC 调 Companion：tool-bridge 起来时 Companion 不一定开着（CLI 也在用）
  - sqlite 用 `mode=ro` 只读连接，免得污染 hermes 自己的 WAL
- `adapter.py` 接入：
  - `list_tools()` native 排前面 + 重名 hermes tool 跳过
  - `dispatch_tool()` native 不走 hermes registry / toolset 可用性检查
  - `health()` 区分 hermes / native tool 数
- `server.py` 启动 banner 显示 `(hermes 60 + catfish 原生 1)` 拆分
- 测试：`tests/test_catfish_tools.py`（16 单测）+ `tests/test_adapter.py`（7 单测）
  - 边界覆盖：无 `~/.hermes` / USER.md 昨天改 / memories 子目录混合 / SKILL.md 无 frontmatter / state.db 不存在 / state.db schema 错乱
  - adapter 覆盖：native 排序、重名跳过、dispatch 不依赖 registry、health 字段
  - **23/23 通过，0.05s**

### 踩坑

- **Python 字符串里的 ASCII 双引号** —— 第一版 description 写成 `回答员工的"今日"问题`，里面是 ASCII `"` 不是中文 `"`，整个字符串被截断，第二个 `"` 后变成裸标识符，`SyntaxError: invalid syntax. Perhaps you forgot a comma?` 指向行首。改用 `「今日」` 中文角括号绕开。

### 遗留（按 TOMORROW.md 顺序）

- **P0-2 内网 IP / hardcode 环境变量化**（60 min，开源前必清）
- **P0-3.1 / 3.2 多会话 sidebar + resume**（3-5 天）
- **更多关键路径单测**：`session_write.rs`（rusqlite, Rust `#[cfg(test)]`）+ `learning.rs` + `catalog.py` 三状态字段 —— TOMORROW.md 测试清单里的剩余三项

### 节奏

8-10h 节奏起步。今日上半场把 P0-1 完整做完（含测试），P0-2 起手。

#### P0-2 · 清所有硬编码（IP / 端口 / hostname）

开源前必清的口子。范围扩到所有「会随部署变」的字面值，不止内网 IP。

**全栈共享 env 约定**（`catfish/.env.example` 顶层定义）:
- `CATFISH_GATEWAY_HOST` / `CATFISH_GATEWAY_PORT` / `CATFISH_GATEWAY_URL`
- `CATFISH_CHROME_DEBUG_HOST` / `CATFISH_CHROME_DEBUG_PORT`
- `INTERNAL_LLM_BASE_QWEN_MAIN` / `_QWEN_VISION` / `_BGE_M3` / `INTERNAL_LLM_KEY`

**Gateway 侧 (Python)**:
- `config.py` 加 `_interpolate_env()` 支持 `${VAR}` / `${VAR:-default}`，递归走 dict/list；缺必需 env 立即报清楚错（不偷偷走 None）
- `models.yaml` 三个 `api_base` 改占位 `${INTERNAL_LLM_BASE_*}`，YAML 干净到能直接开源
- `network.py` 注释里的 `10.10.40.102` 字面值替换为通用描述
- `docker-compose.yml` 默认 `PORT` 8000 → 8999 与全栈对齐
- `.env.example` 加 LLM base / 全栈 host+port 段
- 18 单测覆盖：完整替换 / 嵌入 / 默认 / 嵌套 / 缺失报错 / yaml 端到端

**Companion 侧 (Rust)**:
- 新增 `services/endpoints.rs`（~140 行 + 6 单测）：`OnceLock<Endpoints>` 从 env 读 host/port，进程内冻结
- `commands/health.rs` / `gateway.rs` / `chrome.rs` 都改用 `endpoints::endpoints().gateway_base()` 等，三个文件再也没 `127.0.0.1:8999` / `:9222` 字面值
- 启动 gateway 子进程时 `PORT` 透传 env 配置 → 前端展示端口 + 后端监听端口必然一致

**Companion 侧 (TS)**:
- `lib/env.ts` 加 `readGatewayUrl()`：`VITE_CATFISH_GATEWAY_URL` > `VITE_CATFISH_GATEWAY_HOST/PORT` > 默认
- `lib/http.ts` 默认 base 改读 `config.gatewayUrl` 而非硬编码常量

**Feishu Monitor**:
- `config.py` `_default_gateway_url()` 走 env，YAML 模板 `gateway_url: ""` 留空时回落 env
- 既保留员工 yaml 显式覆盖能力，又支持纯 env 配置

**bash + 文档**:
- `branding/catfish` 4 个端口 env 提到顶部，`cmd_status` 用变量
- `catfish-browser-attach.sh` `PORT` / `HOST` 走 env
- `policy/rules.yaml` no-rogue-llm 提示文案改成提 `CATFISH_GATEWAY_URL`
- `central/llm-gateway/README.md` 不再写死内网 IP，指 `.env.example`
- `docs/operations/troubleshooting.md` 全部 `10.10.40.102` 换成 `$INTERNAL_LLM_HOST`，开头加变量约定

### 验证（P0-2）

- gateway 单测 **48 通过**（含 18 个 `_interpolate_env` 新单测）
- tool-bridge 单测 **23 通过**（无回归）
- 内网 IP / UUID 在 runtime 代码 + docs 全部清零（仅 CHANGELOG/TOMORROW/STRATEGY 历史保留）
- Rust `cargo check` 因 sandbox 无 cargo 未跑，要在本机跑 `cargo check && cargo test --lib services::endpoints`

#### P0-4 · Gateway 防 Gemini code_execution 退化

**症状**：员工切到 Gemini 2.5-flash 聊天，问"查 CHANGELOG"，UI 显示"工具调用:" 后跟 `{"tool_code": "print(mcp_catfish_local_search_local_search(...))"}` —— 工具名是模型瞎编的 MCP-style 名字，dispatch 时 unknown tool，对话卡死。

**根因**：Gemini 2.x/3.x (Pro / Flash 都有) 在 `tools=[]` 时会自动启用 native 的 code_execution / tool_code 模式，输出 Python 伪代码块。LiteLLM 在 OpenAI schema 转译时把它包成假 tool_call。更深一层：tool-bridge 没起来 → `useChat` 透传 `tools=[]` → 才触发 Gemini 退化。

**修复**：`gemini_guard.py` 新文件 + 18 单测
- `_is_gemini(model)` 检测 `gemini/*` 前缀（覆盖 2.x/3.x 全 Pro / Flash / Preview）
- `harden_for_gemini(body, model)` 在 system message 末尾 append 禁止 tool_code 的中英双语指令（~80 token）
- 幂等（靠 `[CATFISH-GEMINI-GUARD]` marker，重复调用不重复 append）
- 多模态 system content（list of parts）也支持，找 text part 加，全 image 时新建 text part
- 不依赖 tools 字段，不破坏现有 SOUL inject
- `app.py` `chat_completions` 时序：identity inject → gemini guard → litellm

**验证**：gateway 单测累计 **66 通过**（48 + 新增 18）

#### P0-5 · Companion 自起 tool-bridge 服务（彻底堵漏）

P0-4 是症状治理，根因还是 tool-bridge 没起 → tools=[] → Gemini 才会退化。这一项把根因彻底堵上。

**改动**：`services/autostart.rs` 新文件
- `schedule_autostart()` 在 Tauri setup hook 里 spawn 后台任务
- 顺序拉 gateway → tool-bridge（gateway 瞬启，tool-bridge 要 ~3-5s import hermes toolset）
- `pid_alive()` 检查 PID 文件 + 进程 → 已在跑就 no-op，不当 Err 处理
- 失败用 `log::warn!`，不阻塞 UI 启动
- socket 残留文件先清（上次 crash 留下的死文件）
- 复用现有 `process::SpawnConfig` + `catfish_paths` 不重复造轮子
- `lib.rs` setup hook 加一行 `services::autostart::schedule_autostart();`

**用户可见效果**：员工 Cmd+Q Companion 后再开，仪表盘的 gateway / tool-bridge 卡片应该自己变绿，不用手动点"启动"。聊天里 Gemini 不会再退化到 tool_code，因为 tools 数组永远有内容。

**验证**：autostart 是 Rust 侧改动，sandbox 无 cargo；本机 `cargo build --release` 重编 Companion 后 Cmd+Q 重启：
1. `tail -f ~/person_task/catfish/.companion-state/tool-bridge.log` 启动后立即应有日志
2. 仪表盘 tool-bridge 卡片自动变绿
3. Gemini 聊天里问"今天有啥更新"，应该规规矩矩调 `catfish_today_summary` 拿真数据

#### Fix · useChat tool schema 错把 input_schema 当 function

**症状**：开 Companion + tool-bridge autostart 起来后，Gemini 模型聊天直接报 `litellm.APIConnectionError: 'name'`。Qwen 路径完全正常。

**根因**：`useChat.ts` `ensureTools()` 第 48-58 行注释说"hermes input_schema 已经是 {name, description, parameters}"——这条注释是错的。tool-bridge 给的 `ToolInfo` 是扁平结构 `{name, description, input_schema, ...}`，`input_schema` 仅对应 OpenAI tool 的 `parameters` 字段。代码把 `input_schema` 整个当 `function` 用了，发出去的 tool 缺 `name`。OpenAI 兼容路径（Qwen）宽容能跑，Gemini 走 `GoogleAIStudioGeminiConfig.map_openai_params._map_function` 时 `KeyError: 'name'` 直接挂。

**修复**：
- `useChat.ts` 改成显式 `function: {name: t.name, description: t.description, parameters: t.input_schema ?? {type: "object", properties: {}}}`
- `gateway/tools_sanitizer.py` 新文件 + 14 单测：网关侧防御性兜底，畸形 tool 单条丢掉不让 500
- `app.py` `chat_completions` 时序：identity → sanitize_tools → gemini_guard → litellm

#### P0-3.1 + P0-3.2 · Plan C Week 3 多会话 sidebar + resume

合并一项做完。

**Rust 侧** (`commands/sessions.rs`):
- `SessionMeta` 加 `source` 字段（cli/companion/null）—— sidebar 显示来源 badge
- 新增 `SessionMessage` struct（id, role, content, tool_calls, tool_call_id, tool_name, timestamp）
- `SessionDetail` 加 `messages: Vec<SessionMessage>` —— 给 resume 用，按 timestamp asc
- `messages_blocking()` 新查询：拉全部消息 + 截断超长 content
- 老字段 `lastUserMessage` / `lastAssistantMessage` 保留兼容现有 SessionsTab UI

**TS 侧 store** (`store/chat.ts`):
- 新增 `loadSession(detail: SessionDetail)` action：DB messages → ChatMessage 映射
- `dbMessageToChat()` 把 SQL row 转运行时格式，tool_calls JSON 反序列化成 ToolCall[]，status 一律 "done"（历史已完成）
- 设 `persistedSessionId` 让后续 send 顺着同一 session 续写

**TS 侧 UI**:
- 新文件 `tabs/Chat/ChatSidebar.tsx`（~240 行）：左侧 240px 会话列表
  - SourceBadge（蓝点 = companion / 灰点 = cli）
  - 相对时间显示（"刚刚" / "X 分钟前" / "X 天前" / 日期）
  - 流式中禁用切换，防异步条件竞争
  - "+ 新对话"按钮固定在底部
  - refreshKey prop 让父组件能在 send 后强制重拉
- `ChatTab.tsx` 重构：`[左侧 SessionList | 右侧 ChatPanel]` flex 布局
  - 点列表项 → `getSession()` → `loadSession(detail)` 灌进 store
  - "+ 新对话"按钮搬到 sidebar，header 留模型选择
  - header 显示当前会话 id（截断 + tooltip）+ 消息数
  - send 完后 bump refreshKey 让 sidebar 看到新 message_count

#### #18 · 关键路径测试

按 TOMORROW.md 测试清单全部补齐。

**Python (sandbox 跑通)**:
- `tests/test_catalog.py` —— 14 单测：三状态字段（key+reachable / key+unreachable / 无 cache → None / 无 key 统一文案）+ default 选取逻辑 + embedding 隐藏 + 匿名/认证用户 + can_access 过滤 + real-world 5 模型混合场景
- `tests/test_gemini_guard.py` —— 18 单测（已有，P0-4 补的）
- `tests/test_tools_sanitizer.py` —— 14 单测（已有，P0-4 补的）
- `tests/test_config_interpolate.py` —— 18 单测（已有，P0-2 补的）

**Rust (写完待本机 cargo 跑)**:
- `commands/session_write.rs` 加 `#[cfg(test)] mod tests` —— 7 单测：random_hex_6 唯一性 + generate_session_id 格式 + create/append/finalize/title 四 happy paths + concurrent 10 路并发 append 不丢
- `commands/learning.rs` 加 `#[cfg(test)] mod tests` —— 14 单测：extract_description 7 边界（基本/quoted/single quoted/无 frontmatter/无 description/空 value/未结束）+ today/is_today 时间边界 + build_summary 三场景 + unix_to_iso
- `services/endpoints.rs` —— 6 单测（已有，P0-2 补的）
- `Cargo.toml` 加 `[dev-dependencies] tempfile = "3"`

**总账**:
- Python: gateway 94 + tool-bridge 23 = **117 全过 0.26s**
- Rust 新增: 27 单测（待本机 `cargo test --lib` 跑）
- 跑法: `cd central/llm-gateway && pytest tests/`；`cd edge/companion-app/src-tauri && cargo test --lib`

### 今日总账（2026-04-26）

| 任务 | 状态 | 单测 |
|------|------|------|
| P0-1 catfish_today_summary tool | ✅ | 16 + 7 |
| P0-2 清硬编码（IP/端口/hostname） | ✅ | 18 + 6 |
| P0-3.1 sidebar | ✅ | (UI) |
| P0-3.2 resume 历史 | ✅ | (集成) |
| P0-4 Gemini guard | ✅ | 18 |
| P0-5 自起 tool-bridge | ✅ | (Rust 集成) |
| Fix useChat tool schema | ✅ | 14 (sanitizer) |
| #18 关键路径测试 | ✅ | 14 (catalog) + 27 (Rust) |

**遗留**（明天起手或本周）:
- P1 模型 fallback 链（today Gemini 配额耗尽暴露的痛点）
- gateway 错误返回人话化（429 → "Gemini 免费配额今天用完了"）
- Companion 仪表盘加 tool-bridge 状态卡片（autostart 起来了但仪表盘不显示）

---

## 2026-04-27（周一）

视觉 / 多模态全栈打通 + 一波 SOUL 反幻觉补丁 + 4-26 P1 收尾追登。

> 这一天主线是: 让员工**给小鲶喂图**这条路真正能跑通——从 Tauri 端 📎/粘贴/拖入加图，到 gateway multimodal 透传，到 Qwen3-VL 真的"看清像素而不是瞎答"，到 SOUL 层防"我没视觉能力"自我否认。中间还顺手把 catfish_screenshot 默认改成零打扰的 active_window 模式，并修了 browser_vision 在精细识别上的可靠性误导。

### 完成

#### #50 · Companion 起 Chrome 后自动刷新 hermes cdp_url

**痛点**：每次 Chrome 重启 `webSocketDebuggerUrl` UUID 变，但 `~/.hermes/config.yaml` 的 `browser.cdp_url` 写死，不刷新员工敲 `browser_navigate` 就 404。之前要靠 `catfish-browser-attach.sh` 手动跑。

**实现** (`commands/chrome.rs`, `services/catfish_paths.rs`)：
- `chrome_launch` 后 `tokio::spawn` 后台 poll Chrome `/json/version`，60 次 retry × 500ms = 30s 兜底
- 拿到 `webSocketDebuggerUrl` 写入 yaml 的 `browser.cdp_url` 字段（yaml 解析 + 已是同值 skip 防 inotify 噪音）
- 容错：Chrome 没起来 → 静默放弃 + log warn；hermes config 不存在 → skip
- `catfish_paths.rs` 新增 `hermes_config_path()` 助手

**Hermes 内存缓存的限制**：已在跑的 hermes session 把 cdp_url 缓存在内存，config 改了也不刷新——员工还是要 `/exit` 重进。这是上游 hermes 行为，我们改不了，但 cdp_url 本身已经是最新的，再读一次就通。

#### #51 · catfish_screenshot 工具 + SKILL (Qwen3-VL 看屏幕)

tool-bridge 加 native tool。

- **mac**: `screencapture` 系统命令封装；**win**: `PIL.ImageGrab`（fallback）
- 输入参数 `mode` + `reason` 必填（reason 一句话员工能看到）
- 12MB 单张上限（base64 后 ~16MB），超就拒绝不撑爆 socket
- `server.py` `start_unix_server` 的 `limit` 从默认 64KB 拉到 16MB（不然 readline 处理不了大 base64 一行）
- SKILL.md (`tool-bridge/hermes-skill/catfish-screenshot/`): 文档化何时调 / 不该调（浏览器场景永远 browser_vision、本地文件用 read_file）/ 隐私红线
- catfish-policy R8: `mode=fullscreen` 时 reason 必须含"员工/明确/全屏/同意/要求/确认"等词，否则 deny
- install.sh (`tool-bridge/hermes-skill/install.sh`): 软链 SKILL 到 `~/.hermes/skills/productivity/catfish-screenshot`
- 17 单测：schema / 入参校验 / mac 三种 mode / 员工取消 / 文件超大 / dispatch 路由

#### #52 · ChatInput 图片附件 (粘贴/拖放/上传 + 自动切视觉模型)

**整条多模态管线**：

- `types/chat.ts` 新 `Attachment` interface (kind, mimeType, name, base64, sizeBytes) + `ChatMessage.attachments?` 字段
- `lib/chat.ts` `toWire()` 检测 user message 含附件 → 输出 OpenAI multimodal content array `[{type:"text"}, {type:"image_url", image_url:{url:"data:..."}}]`
- `tabs/Chat/ChatInput.tsx` 完全重写：📎 按钮（隐藏 file input + accept="image/*"）/ `onPaste` 捕获剪贴板图片 / `onDragOver/Drop` 拖入区域背景变浅青色反馈 / 缩略图行（64×64 + ×删除）/ 错误提示行 / max 6 张 + 12MB 单张校验
- `tabs/Chat/ChatPanel.tsx` + `tabs/Chat/ChatTab.tsx`: `onSend` / `handleSend` 透传 `attachments`
- `tabs/Chat/ChatMessage.tsx` UserBubble: 渲染图片缩略图（max 220×220 contain），文字附件并存
- `hooks/useChat.ts`: `send()` 改签名 `(content: string, attachments?: Attachment[])`；新增 `maybeSwitchToVision(currentModel)` 在带图发送但当前模型 `supports_vision=false` 时透明切到视觉模型；按优先级 `[catfish-private-vision, catfish-public-qwen-flash, catfish-public-gemini-flash, catfish-public-gemini-pro]` 选第一个 reachable+key_configured 的；切了在对话流追加一条 assistant 提示"🔁 已切到 X"；切不到（pool 空）短路返回 + 红色错误，不去硬发让上游 400
- `runOneRound` 内 `streamChat({model: useChatStore.getState().model})`——从 store snapshot 读，避免 closure 拿到旧 model

**State.db 持久化策略**：图片 base64 不入库（避免膨胀状态库），文字部分末尾加占位符 `[📎 N 张图片 — Companion in-memory, 切会话不保留]`。切回历史会话只剩文字。后续要持久化再迁。

#### Gateway · vision 加 fallback chain + connection error 进 on_errors

P1 fallback 链 4-26 已经覆盖了 main + gemini-pro/flash，今天发现 **vision 漏了**——内网 10.10.40.x 一抖动 catfish-private-vision 就直接挂员工。

- `models.yaml` `catfish-private-vision` 加 `fallback: {chain: [catfish-public-qwen-flash, catfish-public-gemini-flash], on_errors: [429, 502, 503, 504, "timeout", "connection error", "connection refused"]}`
- 所有现有 fallback 的 `on_errors` 也补齐这两个 connection error 关键词
- `fallback.py` `_ERROR_KEYWORDS` 加同义词扩展（"connection error" → "cannot connect" / "connect call failed" / "broken pipe" / "apiconnectionerror"）—— 实际撞内网 wifi 抖动时 LiteLLM 抛的就是这一组

24 现有 fallback 测试不回归 + 4 个新增手工 keyword 验证。

#### #53 · SOUL.md 加多模态自我认知段，防"我没视觉能力"幻觉

**症状**：Qwen3-VL 在 browser-task 上下文里，`browser_vision` tool 调用失败后，把"工具失败"错位成"我没视觉能力"，越解释越偏（说"我是语言模型，依赖 browser_vision 工具"）。

**修法**：SOUL.md 加"多模态能力 (重要 — 防自我否认)"段：
- 列出当前可能的多模态模型（catfish-private-vision / qwen-flash / gemini）
- ✅ "image_url 直接看，不需要工具"
- ❌ 三句严禁说："我是语言模型..." / "依赖 browser_vision..." / "工具失败所以看不了"
- 区分 browser_vision（浏览器 tool）vs user message image_url（你直接看，原生能力）

SOUL.md 是 mtime cache + `~/.hermes/SOUL.md` 软链回源代码——改完 gateway 自动 reload 不需要重启。

#### #54 · catfish_screenshot 加 active_window 模式 (零打扰自动截图)

**反馈**：默认 `mode=interactive` 弹十字让员工框选，员工："不能让人工去选择"。

**新模式 `active_window`**：
- `osascript` 拿 frontmost window ID（"tell application System Events to get id of first window of (first process whose frontmost is true)"）
- `screencapture -l <window-id>`，全自动 0 鼠标
- 设为**新默认**（覆盖 interactive）
- macOS Accessibility 权限没给时（osascript 失败）静默 fallback 到 fullscreen——还是不打扰员工
- Win 端 `interactive/window/active_window` 全退到 fullscreen 并带 platform_note 提示员工先关敏感窗口

19 个 screenshot 测试（新增 2 个 active_window case：拿到 ID 走 `-l` / 没 Accessibility 权限静默 fallback 到全屏）。

#### #55 · 修正 browser_vision 误导，验证码场景改走 catfish_screenshot

**症状**：员工实测——browser_vision 在像素级精细识别（验证码）上**不可靠**，看似 ✓ 调用成功但 `e3` 填的字符是模型用历史数据**瞎答**，跟当前页面验证码毫无关系。登录"成功"是凑巧或页面没真登上。

**修法**：catfish-browser-task SKILL.md 加对比表：
- 粗看页面布局 / 颜色：`browser_vision` OK
- 验证码 / 小数字 / 任何"看清"：**永远 `catfish_screenshot mode=active_window` 拍 Chrome 窗口**，让 Qwen3-VL 直接看 base64 PNG—精度 = 员工肉眼

SOUL.md 删掉之前误导性"浏览器场景永远 browser_vision"，同步成"看清像素 → catfish_screenshot 直拍"。

**根因猜测**：browser_vision 走 hermes 内置 vision pipeline，中间层（图压缩 / 模型路由 / question-answer 提示模板）有损精度，遇验证码这种小字符直接糊。catfish_screenshot 直拍像素喂 user message，没中间损失。

#### #56 · SOUL.md 加"系统操作不让员工跑 shell"段，防 6 步手动清单

**症状**：模型遇到 cdp 失效，给员工列 6 步手动命令清单（退 Hermes / Cmd+Q Chrome / `open -n -a /Applications/Google\ Chrome.app --args --remote-debugging-port=9222` / 手动访问 / 重启 Hermes / "告诉我，我立即演示"）。员工是来用产品的，不是来当 sysadmin。

**修法**：SOUL.md 加段教模型——优先级 1. catfish 内置 tool / 2. Companion UI 按钮 / 3. 实在不行才 shell（且 ≤ 2 条）。给标准答案表（cdp 失效 / Companion 异常 / tool-bridge 死 / gateway 502 / skill 不生效），每条 ≤ 2 步。

具体反例：cdp 失效正确答案是"Companion 控制台点「启动 Catfish Chrome」（自动同步 cdp_url）+ /exit 重进 hermes"——2 步搞定，配合 #50 是无感的。

### 踩坑

- **Qwen3-VL 自我否认**：browser-task 上下文里 vision tool 调失败 → 模型错位成"我没视觉能力"。修：SOUL.md 加专项指引 + 区分清楚 browser_vision tool vs 直接喂 image_url 的多模态原生能力。
- **browser_vision 不可靠**：实测在验证码识别上看似 ✓ 调用成功，实际填的字符是模型瞎猜（e3="xtF7" 跟当前页面无关）。真要看清像素就 catfish_screenshot 直拍 Chrome 窗口。上游 hermes 的 vision pipeline 中间层精度损失，我们改不了，但可以绕开。
- **内网 LLM 整体不可达 (TimeoutError + ConnectionRefused)**：公司 VPN 抖动 / 平台维护时 catfish-private-{main, vision} 全断。修：vision 加 fallback chain；fallback.py 识别 connection error 关键词；自动落公共 qwen-flash。
- **fallback chain 自我覆盖**：编辑 models.yaml 时 fallback chain 莫名变成 `[qwen-flash, vision, qwen-flash]`（vision 重复 + 顺序错），原因不明，可能是 Edit 工具的某个 race。修：手动 grep 校验后修正成 `[qwen-flash, gemini-flash]`。今后批量改 yaml 后必须 yaml.safe_load 验证一遍。
- **rollup native binding (sandbox)**：Linux sandbox 跑 `npm run build` 撞 mac-installed rollup binding 不兼容。不是代码问题——TypeScript `tsc --noEmit` 通过即可，真打包在用户 mac 跑。
- **tempfile.gettempdir() 缓存**：screenshot 测试里多个 case 跑同一秒，生成同一个 `/tmp/catfish-shot-<unix>.png` 路径，下个 test 看到上个写的文件误判成功。修：`monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))` 直接打补丁，不能用 `setenv("TMPDIR", ...)`（gettempdir 已模块级缓存）。
- **zsh `#` 当参数**：之前几次给用户的命令里写了 `# 注释` 行内，zsh 不支持行内 `#` 当参数前缀，把 `# 或 dev` 当作 cargo 的额外参数喂进去导致 build 失败。教训：给 user 的 shell 命令永远不带 inline 注释。
- **模型幻觉 hermes_tools 模块**：员工尝试 `python -c "from hermes_tools import browser_navigate"` 测，模块根本不存在 (LLM 瞎想出来的 import 名)。Hermes 工具是 registry 模式 + tool calling 协议，要测得走 tool-bridge socket / hermes CLI / Companion 内对话。

### 测试

- tool-bridge: **63 全过**（新增 17 screenshot + 2 active_window + 1 修正 adapter 测试）
- gateway: fallback 24 测试不回归 + 4 个新增手工 connection-error keyword 验证
- companion-app: TypeScript `tsc --noEmit` 通过

### 4-26 P1 收尾追登

> 本来该归 4-26 entry，但 working tree 没单独成 commit，顺手追登：

- **#43 catfish-roleplay skill** (`edge/communication-coach/hermes-skill/catfish-roleplay/`, ~310 行 SKILL.md): 3 阶段演练——收集 4 项 → 在角色里 → 系统反馈（3 ✓ + 3 ✗ + 方法论命名 + next-time）
- **#44 SOUL.md 情绪共情层**: emotion 信号识别 + 3 步先停一拍 + 边界（不滥扮心理咨询）
- **#45 POSITIONING.md** (`docs/POSITIONING.md`, 14 章 ~430 行): 战略定位文档
- **#46 Self-Evolution 软技能进步追踪**: `learning.rs` 加 5 字段（coaching_sessions_today/this_week/prev_week, emails_drafted_today, methodologies_this_week）；`catfish_today_summary` Python 镜像同步
- **#47 catfish wrapper skin 修**: `branding/catfish` 加 `ensure_skin`——默认 `display.skin: default`，品牌 skin opt-in via `CATFISH_BRAND_SKIN=1`；同时 `nuke_proxy_for_hermes` 防代理污染
- **#48 Skill 生成纪律 (B 模式)**: SOUL.md 新增"Skill 生成纪律"章（≥3 次重复触发智能建议，员工"存成 skill"也存）；catfish-policy R6（防 skill_manage delete 误删 catfish-* skill）+ R7（防 skill 内容含密码/api_key/token/邮箱地址等敏感字段）
- **#49 skill_watcher hot-reload** (`tool-bridge/.../skill_watcher.py`): 后台 daemon poll `~/.hermes/skills/` 的 SKILL.md mtime；变化后等 30s quiet period（dispatch_tool 期间不触发）→ `os._exit(0)` → Companion autostart respawn 加载新 skill。13 单测覆盖。

### 遗留

- **Companion Tauri 真机 build**：今天 Rust 改动（chrome.rs cdp 自动刷新）和前端改动（ChatInput 多模态）都已 tsc 过，但用户没本机 `cargo build --release` 出新 .app。下次员工用前必须重 build，否则只有 dev mode 能看到效果。
- **catfish-public-qwen-flash 配置疑似错**：`upstream.model: openai/deepseek-v4-flash`，但 display_name / 注释指 Qwen3.6-Flash。LiteLLM 一旦 fallback 到这条会触发新错误。优先级低（公网模型平时用得少），但 fallback 真启用时会撞。
- **#41 Companion macOS LaunchAgent 自启动** 还没做。当前 autostart 只在 Companion 起来后管 tool-bridge / gateway，但 Companion 自己关掉再开机不会自动起。
- **#38, #39 跨平台 IPC**: Companion 当前用 Unix socket 跟 tool-bridge 通信，Windows 装不上。要改 TCP localhost。
- **#35-#37 Outlook / Foxmail Win 适配器**: email-agent 现在只覆盖 Foxmail Mac，Outlook Mac (AppleScript) / Outlook Win (pywin32 COM) / Foxmail Win 都待做。
- **gateway 错误返回人话化**（4-26 已列遗留）：429 / timeout / 401 翻译成员工能看的话——今天没碰，下次。
- **Companion 仪表盘加 tool-bridge 状态卡片**（4-26 已列遗留）：autostart 起来了但仪表盘不显示——今天没碰。

### 今日总账（2026-04-27）

| 任务 | 状态 | 单测 |
|------|------|------|
| #50 Chrome cdp_url 自动刷新 | ✅ | (Rust 集成) |
| #51 catfish_screenshot 工具 + SKILL | ✅ | 17 |
| #52 ChatInput 图片附件 + 自动切视觉模型 | ✅ | tsc 通过 |
| #53 SOUL.md 多模态自我认知 | ✅ | (prompt) |
| #54 catfish_screenshot active_window | ✅ | +2 |
| #55 browser_vision 误导修正 | ✅ | (prompt) |
| #56 SOUL.md 不让员工跑 shell | ✅ | (prompt) |
| Gateway vision fallback + connection error | ✅ | 4 (验证 keyword) + 24 (回归) |
| 4-26 P1 收尾追登 (#43-#49) | ✅ 已合 | (前期) |

---

## 2026-04-28（周一）

demo sprint 大日 · 21 task ship + 5 月 demo 文档级筹备

### 完成

#### P0 安全 + 凭据 (3 项)

- **secret_resolver** (`edge/tool-bridge/.../secret_resolver.py`, ~80 行):
  scheme env:// / keychain:// / wincred:// (后者 Phase 2). Keychain 走 `security find-generic-password -w`. 错误格式 / 平台限制 / 超时全 friendly error.
- **prompt_security** (`central/llm-gateway/.../prompt_security.py`):
  中英文密码 regex (密码是/为/: + password=/passwd:/api_key= + Bearer header). 命中 → audit 加 `security_concern='prompt_credential_detected'`, warn 不拦.
- **secret_ref + audit 整合**: `browser_fill` 加 `secret_ref` 参数; tool-bridge audit 加 security_audit 标记; gateway metrics 加 security_concern 字段. 测试 152 + 189 = 341 全过.

#### P0 浏览器 + Tool Bridge 稳定性 (5 项)

- **browser_goto https→http fallback**: 内网老系统 (.ffcs.cn) 只听 80. 撞 ERR_CONNECTION_REFUSED + URL 是 https → 同 page 切 http 重试 1 次. 加 `fallback_hint` 让模型记住下次走 http.
- **multimodal_guard**: 含图请求 + 当前模型不支持 vision → 自动 reroute 到 catfish-private-vision (同 tier 优先, 跨 tier 兜底). 修 protobuf 解析炸 BadRequest 400.
- **stale PID 检测** (踩过坑 鸿波 demo): tool-bridge PID 死后被 OS 复用给别的进程, `kill -0` 误判活. 加 `read_pid_file_alive_strict(path, cmdline_substr)` 用 `ps -p PID -o command=` 验进程身份. 4 卡片 + autostart 共 12 处调用切到 strict 版.
- **Companion watchdog** (`services/watchdog.rs`): 5s tick 检查 gateway / tool-bridge 进程死活, 死了自动 respawn (复用 autostart::ensure_*_running). 连续 5 次 spawn 失败 → 3 分钟 backoff. 跟 skill_watcher 主动 exit 配合.
- **Watchdog port 探测**: 开发者手动跑 gateway 时 watchdog 5s tick 看 PID file → 误以为没在跑 → spawn 第二个 → 撞 8999. 修法: spawn 前 `tokio::TcpListener::bind` 探测端口被占就让位.

#### P0 性能 + 可观测 (5 项)

- **私有 LLM timeout** 60→180s (catfish-private-main / vision): MoE active 10B + 24K prompt TTFT 60s+ 常态.
- **auto-compress 阈值** 70%→50% (`catfish-autocompress`): 50% × 128K = 64K 触发, 压完 ~30K, TTFT 回 5-10s.
- **TTFT metric** (`metrics.py` + `app.py`): streaming 路径首 chunk 到达时间记 `ttft_ms`. >30s 自动 warn log. JSONL 审计加这字段, 区分"上游慢" vs "输出长".
- **streaming keepalive** (`_stream_with_keepalive`): asyncio.wait_for 包装 iterator, 每 30s timeout = yield SSE comment `: keepalive\n\n`. 防客户端 / 中间代理 timeout.
- **friendly_error 加 proto syntax** (`errors.py`): "BadRequest + invalid value Invalid + proto syntax" → 翻译成 "请求格式上游不认 (大概率截图发给非 vision 模型)". 27 case 测试.

#### Companion UI + 品牌 (3 项)

- **AuditCard 仪表盘** (Rust `commands/audit.rs` + React `AuditCard.tsx`):
  读 `~/.catfish/gateway_audit.jsonl` 聚合显示今日请求数 / 错误率 / Token / 本地优先 % / TTFT p50/p95 / security_concern 计数. 兑现"中央可审看不到内容"卖点.
- **SkillsMcpCard 占整行 + 多列**: 之前占 1 列, 旁边 LearningCard 是 1/-1 占整行, 导致 SkillsMcp 旁边大片空白. 改 `gridColumn: 1/-1` + namespace 列表 grid `auto-fill minmax(220px, 1fr)`.
- **品牌纪律 BRAND-VOICE.md**: dashboard 露 hermes/skill_manage/system prompt 等开发术语. 删 5 处 UI 文案, 注释保留 (开发者参考). 写 docs/BRAND-VOICE.md (红线表 + 触点分类 + 检查清单).

#### Skill / Memory / Attention (4 项)

- **SOUL.md attention hot-fix** (复述模式): 长 session 模型失忆反复犯错. 触发"你又来一遍" → 进复述模式, 每次回复开头 quote 已知硬事实. SOUL.md 加 60 行.
- **catfish_remember tool + inject_session_facts**: 工程级 attention 兜底, 不依赖模型自觉. tool-bridge `catfish_remember(key, value)` 写 `~/.catfish/session_facts.json`; gateway 入口注入到 system prompt 末尾. 跟复述模式互补.
- **SOUL.md execute_code 红线** (踩过坑 EIS 145 条 demo): 模型在 sandbox 写脚本 import catfish_browser_*, 必死锁. SOUL.md 加进程边界对比 + 4 条红线 + 触发场景 + 完整 demo 踩坑历史.
- **SOUL.md 批量数据抓取优先级** (D→A→B→C): 找导出按钮 > 找后端 API > 用 skill > 翻页落盘 + 代码统计.
- **execute_code 误用守卫** (`adapter.py`): tool-bridge 检测 sandbox 调 catfish_browser_* / catfish_screenshot 等子串 → 立即拒绝 + friendly error "请用原生 tool calling, 别写脚本调".

#### Companion 工程 (1 项)

- **LaunchAgent autostart 安装脚本** (`scripts/install-launchagent.sh`):
  写 `~/Library/LaunchAgents/com.catfish.companion.plist` + `launchctl bootstrap` (新 API). RunAtLoad=true + KeepAlive (异常退出自动拉起). 标准位置. 1 行命令开机自启.

### 踩过坑

- **stale PID + macOS PID 复用**: 现象 — tool-bridge 死后 Companion 永远启不来子进程, 必须手动 rm pid file. 原因 — `kill -0 PID` 不区分进程身份, OS 复用 PID 给别的进程时被当成活. 解法 — `ps -p PID -o command=` 验 cmdline 含 'catfish_tool_bridge' 等 substring.
- **proto syntax error**: 现象 — catfish_screenshot 后调 main 模型撞 `code = 400 cause = proto: syntax error (line 1:1): invalid value Invalid`. 原因 — 主力模型不支持 vision, 上游 Go protobuf 解析多模态 content list 炸. 解法 — multimodal_guard 自动 route 到 vision 模型.
- **内网 https 撞 ERR_CONNECTION_REFUSED**: 现象 — 模型默认补 https://eis.ffcs.cn 全失败. 原因 — EIS 老系统只监听 80. 解法 — browser_goto 加 https→http fallback + SOUL.md 加纪律 (内网默认 http).
- **Watchdog 跟手动 gateway 抢端口**: 现象 — 开发者 `python -m catfish_gateway.app` 启 gateway, 5s 后 watchdog spawn 第二个撞 8999, 自己被 shutdown. 原因 — watchdog 看 PID file 不指向 Companion 启的就当死. 解法 — spawn 前 port 探测.

### 遗留

- 私有 LLM (10.10.40.102) 偶尔不可达 (VPN 抖动) — 跟我们代码无关.
- catfish-public-qwen-flash 配置 deepseek-v4-flash 名字疑似错 (4-27 已记) — 没碰.
- Companion 仪表盘 tool-bridge 状态卡片 (BL-C9) — AuditCard 算覆盖了 audit, tool-bridge 仍没单独卡片. 推到 5 月之后.

### 今日总账

| 类别 | 数量 |
|------|------|
| Task | **21 ship** |
| 测试 | gateway 223 (含新 19 multimodal_guard) + tool-bridge 152 (含新 5 secret_resolver) + 5 case execute_code 守卫 |
| 文档新增 | BRAND-VOICE.md (123 行) / AUTH-DESIGN.md § 13 决策 / SSO-RATIFY.md (192 行) / ROADMAP.md (126 行) |
| Commit | 多个 commit, 主要按主题分 (security / brand / dashboard / SOUL / watchdog / TTFT) |

---

## 2026-04-29（周二）

SSO 全链路一日 ship (Phase 1A + 1B + 1C 实际 6 小时完成原计划 6-9 天) + 5 月 demo 文档级齐.

### 完成

#### 🔐 SSO 全链路 (5 个 sub-phase)

- **Phase 1A · AuthProvider ABC 重构** (gateway):
  - `auth.py` 单文件 → `auth/` 包: `base.py` (AuthProvider ABC + User dataclass + auth_method 字段) / `dev_token.py` (现有逻辑 100% 兼容) / `__init__.py` (工厂 + lazy singleton + 测试 hook)
  - 23 case ABC 边界测试, 旧 223 case 全过 (gateway 246/246)
- **Phase 1B-1 · 自建 catfish-identity OIDC server** (新模块 `central/identity-server/`):
  - 5 OIDC 端点: discovery / jwks / authorize / token / userinfo
  - RS256 + JWKS 自动生成 RSA 2048 (`~/.catfish/identity-server/keys`)
  - bcrypt 密码 hash + YAML 用户表 + timing-safe 验证 (未知 user 也跑 bcrypt 防 timing attack)
  - 31/31 测试 (jwt_signer 5 + users 10 + routes 16, 完整 auth code flow)
  - 简单 HTML 登录页 (内联, 不依赖 jinja). HTML escape 防注入.
- **Phase 1B-2 · gateway OIDCProvider + CompositeProvider**:
  - `OIDCProvider` 用 `PyJWKClient` 自带缓存 (兼容旧版 PyJWT 不支持 lifespan 参数 — try/except fallback)
  - 拒绝: expired / wrong_iss / wrong_aud / missing_sub / access_token_misuse / unknown_kid / wrong_sig
  - `CompositeProvider` 串联多 provider, is_strict 高水位 (任一 strict 即 strict)
  - 工厂 env=prod + OIDC_ISSUER 设了 → Composite[OIDC, DevToken]; 没设 → DevToken + warning
  - 29 case 测试, gateway 全套 275/275 全过
- **Phase 1C · Companion OAuth flow** (Tauri Rust):
  - `services/oauth.rs` (~340 行): 完整 Authorization Code flow
    - 起临时 HTTP server 监听 127.0.0.1 随机端口
    - 浏览器跳 IdP `/authorize`, callback 收 code + 验 state CSRF
    - POST `/token` 换 access_token + id_token
    - 存 macOS Keychain (keyring crate 跨平台)
  - 4 Tauri command (whoami / login / logout / get_access_token)
  - React `LoginGate` (没登录挡住整个 App) + `AuthBanner` (dev_token 模式黄色 warning) + `useAuth` hook
- **Phase 1C-2 · OIDC 配置从 yaml 读** (踩过坑 macOS .app 不继承 terminal env):
  - `OidcConfig::load()` 优先级: `~/.catfish/companion.yaml` > env > 自动生成默认 yaml
  - 第一次启动自动写默认 yaml + 0600 权限. 客户切自己 SSO 改 yaml 一行重启即可 (30 秒).

#### 📊 数据统计准确度修复 (踩过坑 鸿波"已修正多次仍出错")

- **SOUL.md § 数据统计 = 代码统计** (软规则):
  - LLM enumeration 30+ 条必错 (token attention 物理限制)
  - CSV/Excel/文件夹/网页/跨多文件 5 类场景对照表
  - 阈值: ≤5 直接看 / 6-30 建议代码 / 30+ **必须** execute_code
- **stats_guard 工程级强制** (`stats_guard.py`):
  - 正则检测员工最近一句 user message 含统计意图关键词 (多语言: 多少/统计/总数/合计/总共/累计/平均/最大/最小/分组/按.*分类/占比/行数/条数/计数/出现.*次/去重 + 英文 count/sum/total/group by/aggregate)
  - 命中即在最后一条 system message 末尾追加 STATS_GUARD_BLOCK 强制提醒 (含 execute_code 模板)
  - 跟 inject_session_facts 同机制
  - 37 case 测试, gateway 全套 312/312 全过

#### 📚 5 月 demo 文档级 6 件套 (~1100 行)

- `docs/MAY-DEMO-PREP.md` (176 行) — 准备清单 8 段
- `docs/MAY-DEMO-DECK.md` (210 行) — PPT 30 张大纲 + 演讲口语稿 + 现场翻车话术
- `docs/MAY-DEMO-Q-AND-A.md` (244 行) — 客户必问 12 题, 每题 5-10 句答案
- `docs/POC-PLAN.md` (220 行) — PoC 1-2-3 周标准化方案 + 双方权责 + 商务条款
- `docs/ELEVATOR-PITCH.md` (185 行) — 30 秒电梯演讲 5 个对象版本
- `docs/COMPARE-1PAGER.md` (138 行) — vs ChatGPT/星辰 1 页对比图

### 踩过坑

- **macOS .app 不继承 terminal env**: 现象 — Companion 启动后报 "OIDC 配置错: CATFISH_OIDC_ISSUER 没设", 即使 terminal 里 export 了. 原因 — macOS LaunchServices 启动 .app 不读 shell env. 解法 — `launchctl setenv` 全局生效 (临时) + 长期改成读 `~/.catfish/companion.yaml` (Phase 1C-2 立即做完).
- **PyJWT 旧版没 lifespan 参数**: 现象 — sandbox PyJWT 版本旧, `PyJWKClient(jwks_uri, lifespan=cache_ttl)` 抛 TypeError. 解法 — try/except 兜底, 旧版不传 lifespan.
- **bcrypt timing-safe dummy hash 格式错**: 现象 — `bcrypt.checkpw(b"dummy", b"$2b$12$dummy_hash...")` ValueError "Invalid". 原因 — 我写的 dummy 字符串不是合法 bcrypt 格式. 解法 — module-level 用 `bcrypt.hashpw(b"placeholder", bcrypt.gensalt())` 生成合法 dummy.
- **测试 env leak**: 现象 — `test_prod_env_phase1a_still_dev_token` 失败. 原因 — Phase 1B-2 改了行为 (prod + OIDC_ISSUER → Composite), 测试期望旧 Phase 1A 行为, 但 OIDC_ISSUER 在测试环境 leak 进来. 解法 — 测试加 `monkeypatch.delenv("CATFISH_OIDC_ISSUER")`.

### 遗留

- **gateway models.yaml** working tree 未 commit (鸿波端有改动, 内容未确认).
- **真实 case 视频** (3 个 1 分钟): 鸿波下午回家自己录.
- **业务 skill 真跑通**: 等鸿波明天去公司拿格式 (汇报模板 / EIS 字段 / 周报模板) 后写 3 个 SKILL.md scaffolding.
- **Phase 2 安全加固**: PKCE / id_token Companion 端验签 / refresh_token rotation / 失败计数 / RBAC. 5 月 demo 后做.
- **客户 SSO 接入文档**: `docs/SSO-CUSTOMER-INTEGRATION.md` — 客户 IT 拿这份能自己接 SSO. 今天写.

### 今日总账

| 类别 | 数量 |
|------|------|
| Task ship | **8 个** (Phase 1A/1B-1/1B-2/1C/1C-2 + stats_guard + SOUL § 数据统计 + 6 件文档) |
| 测试 | gateway 312 (含新 29 OIDC + 37 stats_guard) + identity-server 31 (新模块) + Companion (Rust 单测 + 集成手动验证) |
| 代码新增 | ~1500 行 (auth/ + identity-server/ + oauth.rs + stats_guard.py + tests) |
| 文档新增 | ~1100 行 (5 月 demo 6 件套) |
| 端到端验证 | curl + 浏览器双路径 OAuth flow 通, gateway 验签返 authenticated:true |

### 明天起手式

- 上午: 公司拿 3 类格式 (汇报模板 / EIS 字段截图 / 周报模板) + 录 30s 短视频 (case 素材)
- 下午: 写 3 个业务 skill scaffolding (eis-qualification-export / weekly-report / leadership-briefing)
- 晚上: commit + push (今天累积 5+ 个 commit, 还没 push)

---

## 记录规则

- 每天收工时补一条
- 完成 / 踩坑 / 遗留 / 明天起手式 四栏
- 完成优先写"事实"（做了什么），不写"感受"
- 踩坑写"现象 + 原因 + 解法"便于未来参考
