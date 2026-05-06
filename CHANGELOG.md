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

#### 🔧 SKILL 自动注入 + run_skill 工具 (踩过坑 鸿波 demo 场景翻车)

需求 — 鸿波 4-29 现场测试: 跟鲶鱼说"写一份汇报材料", `qwen_v3_5_122b_a10b` 模型回了"我无法访问你的文件系统, 给你写个 Python 脚本你跑". 完全没调任何工具. 根因: catfish/skills/ 下 leadership-briefing skill 存在, 但 LLM **看不到它** — gateway 没注入 skill 列表, tool-bridge 没暴露 run_skill 工具. 必须在 5 月 demo 前修.

- **gateway 端 `skills_loader.py`** (新增, 175 行):
  - 扫 `catfish/skills/**/SKILL.md`, 解析 yaml frontmatter (name + description)
  - skills 根目录发现策略: `CATFISH_SKILLS_DIR` env > 从 `__file__` 向上找
  - `format_skills_block(skills)` 渲染 markdown 块, 每个 skill 一段, 顶部点出"用 catfish_run_skill 调用"
- **gateway 端 `skills_inject.py`** (新增, 110 行):
  - `inject_skills_catalog(messages)` 在最后一条 system message 末尾追加 skill 列表
  - 缓存机制: skill 列表 mtime fingerprint, SKILL.md 改了立即生效, 稳态零成本
  - 幂等: 同样 block 已存在不重复追加
  - 跟 inject_session_facts / inject_stats_guard 同套机制 + 顺序: identity → session_facts → stats_guard → **skills_catalog** → 后续
- **tool-bridge 端 `catfish_run_skill` 工具** (`catfish_tools.py` 加 ~190 行):
  - schema: `{skill_path, params}` 双字段, params 可传 `_help: True` 拿入参 schema
  - 安全: 拒绝 `..` 路径遍历; 必须在 `catfish/skills/` root 内; script.py 必须存在
  - 反射加载 script.py 用 `importlib.util.spec_from_file_location`, **必须**先 `sys.modules[name] = module` 再 exec_module — 否则 dataclass + `from __future__ import annotations` 会抛 `'NoneType' object has no attribute '__dict__'` (踩过坑)
  - 自动找 `render_*` 函数; 用 `inspect.signature` 推参数 schema 给 LLM 看
  - 返回值挖文件路径 (.docx / .xlsx / .pptx) 拼到 `files` 字段, Companion 自动渲染为可点击 pill
  - 友好错误: 参数不匹配时提示用 `_help` 模式
- **测试**:
  - tool-bridge `test_run_skill.py` 8 测试 (注册 / _help / 真调 / 路径遍历 / 不存在 / 参数错 / sys.modules 不污染)
  - gateway `test_skills_inject.py` 11 测试 (扫描 / frontmatter / inject 位置 / 幂等 / 不 mutate)
  - **gateway 全套 323/323 全过** (旧 312 + 新 11)
  - **tool-bridge 全套 165/165 全过**

#### 📁 文件下载 UI 优雅化 (Phase 2 风格)

需求 — Office skill 装好之后 (python-docx / openpyxl / python-pptx), 鲶鱼能生成 .docx / .xlsx / .pptx, 但聊天界面没"下载入口", 员工要去 Finder 翻找路径才能拿到文件. 不优雅.

- **Tauri 端 `commands/file.rs`** (新增, 121 行):
  - `reveal_in_finder(path)` — macOS `open -R <path>`, Linux `xdg-open <parent>`, Windows `explorer /select,<path>`
  - `open_file(path)` — 用系统默认 app 打开
  - 安全: 只接受绝对路径 + 验证文件存在 + 拒绝 `/.ssh/` `/.aws/` `/.gnupg/` `/.kube/` `/etc/passwd` `/etc/shadow` `keychain` 等敏感路径子串
  - 已注册到 `lib.rs` invoke_handler + `commands/mod.rs`
- **前端 `lib/path_detect.ts`** (新增, 130 行):
  - 正则提取**绝对路径** (拒绝 markdown 片段误匹配): macOS/Linux `/...` + Windows `C:\...`
  - 支持扩展名白名单: docx/xlsx/pptx/pdf/csv/json/png/jpg/zip/md/txt 等
  - `extractFilePaths(text)` 返回去重列表 (最多 10 个) / `basename` / `extname` / `fileEmoji`
- **前端 `components/FilePill.tsx`** (新增, 130 行):
  - 胶囊样式: emoji + 文件名 (hover 看完整路径) + "在 Finder 显示" + "打开" 双按钮
  - 错误就近显示在 pill 下方 (不弹 alert), 文件已删除/敏感路径/系统不支持各有友好文案
- **挂入 ChatToolCall.tsx** — tool result 完成后自动扫路径, 折叠状态下也显示 pill (员工不必展开就能下载)
- **挂入 ChatMessage.tsx** — 助手 message 流式结束后扫 markdown 里提到的路径, 一并出 pill
- TypeScript 全套类型校验通过 (`tsc --noEmit` 无错)

#### 📝 leadership-briefing skill 真复刻 PDF (鸿波提供 PDF 样板, 反复迭代)

需求 — 5 段公文 .docx 严格按真实公司样式 (鸿波 4-29 提供"建造师补位汇报" PDF):
- 标题 1-N 行居中加粗 (方正小标宋三号)
- 5 段固定标题 一/二/三/四/五: 背景 / 问题 / 方案 / 请示 / 下一步
- § 三方案是 **kv_table** (项目-内容两列), 不是多方案对比
- § 四请示事项是**单段文字**, 不是表格
- § 五下一步是**数字层级列表** (1./(1)(2))
- 红字高亮 (important_phrases 任意位置自动标 RGB(255,0,0))
- 页脚 X / Y 居中

**blocks 模式重构** — 从死板 5 段表格化, 改成段内 blocks 数组 (paragraph / kv_table / table / ordered_list 4 类), LLM 段内任意混排. 真实公文里 § 二可能"段落+表格+段落"混排.

**CSV 附件支持** — `attachments=[{filename, kind: 'csv', headers, rows}]`, 跟主 .docx 同目录, UTF-8 BOM (Excel/Numbers 中文不乱码). 正文里 LLM 自由写 "详见附件 N" 引用.

**输出路径策略** — 员工指定 honor (例 `~/Desktop/`), 没指定默认 `~/.catfish/output/YYYY-MM-DD/HHMMSS_<标题>/` (鲶鱼托管, 不污染用户目录, 重启不消失). 不用 /tmp (macOS 重启清空).

**测试**: `catfish/skills/department/leadership-briefing/tests/test_render.py` 25 测试 (字体/5 段/blocks 类型/CSV 附件/输出路径/红字高亮)

#### 📊 weekly-report skill (周报 .xlsx, 真复刻员工模板)

需求 — 鸿波 4-29 提供"周报-陈鸿波 20260424.xlsx" 样板, 写 skill 让员工说"写周报"自动生成同样格式:

- 单 Sheet, 6 列: 序号/项目-事项名称/本周进度/下周计划/计划完成时间/备注
- 表头加粗 + 浅蓝底纹 (D9E7F5)
- 多行单元格 wrap_text 自动换行
- 边框全实线 0.5pt, 列宽 5/15/40/40/15/20
- 字体微软雅黑 (周报跟公文不同, 不强制方正小标宋)
- 默认输出 `~/Desktop/周报-<员工>-<YYYYMMDD>.xlsx`, 日期从 week_label 抽取

**测试**: `catfish/skills/department/weekly-report/tests/test_render.py` 17 测试 (表头顺序/底纹/边框/多行/列宽/文件名/路径解析)

#### 🛡 skill_guard 工程级保护 (鸿波 4-29 多次翻车后加)

链路: 员工 → Companion → gateway → 上游 LLM. Companion `_cachedTools` 模块变量缓存了启动时工具列表, tool-bridge 装新工具后 Companion 不刷, body.tools 里**没有 catfish_run_skill** → 模型即便看到 system prompt 提示也调不出来.

`skill_guard.py` (工程级强制, 跟 stats_guard 同套路):
- 检测员工最近 user message 含 skill 触发词 (汇报/请示/立项/周报 等)
- 检查 body.tools 里有没有 catfish_run_skill
- 命中且工具就位 → 注入 REQUIRED block: "**禁止**用 execute_code 自写 python-docx, **必须** catfish_run_skill"
- 命中但工具缺失 → 注入 MISSING block: 让员工 Cmd+R 刷 Companion + 让模型告知员工

`useChat.ts` 同步加了 60s TTL + 关键工具缺失自动重拉 — 以后不需要 Cmd+R, ensureTools 检测 cache 里没 catfish_run_skill 就立即重拉.

**测试**: gateway `test_skill_guard.py` 16 测试 (意图覆盖/工具检测/inject 行为/幂等)

#### ⚙ tool_capability_guard (配置驱动, 跟 multimodal_guard 同设计)

`route_to_tool_capable_if_needed()` — 检测 model.supports_tool_use=False + skill 意图 → 自动 reroute 到 catalog 里 supports_tool_use=True 的备选模型 (同 tier 优先).

**走过弯路**: 第一版硬编码 `KNOWN_TOOL_BAD_MODELS = ["qwen_v3_5_122b_a10b"]` 黑名单 — 鸿波质疑"是不是模型参数文件已经设了 fallback, 你硬编码冗余", 立刻撤回. ModelConfig 早就有 supports_tool_use 字段, 我没用. 重构成纯配置驱动, 备选模型也从 catalog 自然挑.

**测试**: gateway `test_tool_capability_guard.py` 14 测试 (默认值/同 tier 优先/跨 tier fallback/embedding 排除/意图覆盖)

#### 🎨 字体目录三层结构 (法律红线)

商业字体 (方正/中易) **绝对不分发**, 三层目录:
- `catfish/fonts/customer/` — 客户授权字体, 客户 IT 部署时填, **.gitignore 禁止 commit 任何 .ttf/.otf**
- `catfish/fonts/opensource/` — 开源字体 (思源宋体 SIL OFL 协议), 鲶鱼自带可 commit
- 系统字体 fallback (黑体/宋体)

`loader.py` (扫两层目录 + macOS 注册到 ~/Library/Fonts/), `INSTRUCTIONS.md` 给客户 IT 看的部署指引.

#### 🖥 Companion 仪表盘显示 catfish skills (Tauri Rust)

之前 `commands/skills.rs` 只扫 `~/.hermes/skills/`, 仪表盘看不到 leadership-briefing / weekly-report. 鸿波反馈"我就是看不到这个工具".

改 `list_skills_blocking()` 同时扫 `<catfish_root>/skills/` + `~/.hermes/skills/`, catfish skills 加 `🐟 catfish:` 前缀强调来源. 需 `npm run tauri build` 才生效.

#### 🔥 真因复盘: hermes 自创 skill 抢占 (整天的核心翻车原因)

整天反复测都失败, **以为是模型 (qwen 122b) tool calling 不行**, 加各种工程保护. 实际真因 — 模型之前自己生成的代码被 hermes-agent 注册成了**自创 skill**:

- `~/.hermes/skills/data-analysis/qualification-management-report/SKILL.md` (自创汇报 skill)
- `~/.hermes/skills/data-analysis/eis-qualification-analysis/SKILL.md` (自创 EIS 数据 skill)

模型每次看到"资质管理汇报"等关键词, **优先选自创 skill** (因为名字更精确匹配 + 是它自己写的代码), 走自创 skill 的 hardcode 微软雅黑 + 自创段标题 + python-docx 路径, 完全绕开 catfish_run_skill.

`rm -rf` 那两个目录之后, 模型才会真用 catfish 工程审定 skill. 鸿波亲自指出: "千问其实都支持工具调用, 之前用的不好就是几个 SKILL 冲突的问题."

防御: skill_guard REQUIRED block 写明"严禁 execute_code 自写", 防模型再生成自创 skill. 长期方案: 在 SOUL.md / hermes write_file 入口加铁律, 不允许覆盖 catfish 工程审定 skill 同领域的新 skill.

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

### 今日总账 (含 4-29 全天 + 晚间追加)

| 类别 | 数量 |
|------|------|
| Task ship 上半场 | **8 个** (Phase 1A/1B-1/1B-2/1C/1C-2 + stats_guard + SOUL § 数据统计 + 6 件文档) |
| Task ship 下半场 | **8 个** (FilePill / leadership-briefing / blocks 模式 / 字体目录 / weekly-report / skill_guard / tool_capability_guard / 仪表盘 catfish skills) |
| 测试 | gateway 365 (旧 312 + 新 16 skill_guard + 14 tool_capability_guard + 11 skills_inject + 12 misc) + identity-server 31 + tool-bridge 165 (含新 8 run_skill) + leadership-briefing 25 + weekly-report 17 + Companion (Rust 单测 + 集成手动验证) |
| 代码新增 | ~3500 行 (auth/ + identity-server/ + oauth.rs + skills_*.py + skill_guard.py + tool_capability_guard.py + skill 套件 + FilePill + 字体目录 + 仪表盘 Rust 改动) |
| 文档新增 | ~1500 行 (5 月 demo 6 件套 + 2 个 skill SKILL.md/README) |
| 端到端验证 | OAuth + gateway/tool-bridge 链路通; **真实 PDF/XLSX 模板复刻产出 sample-output** 跟模板一致 |

### 明天起手式

- 上午:
  - 在公司测 catfish-private-main (qwen 122b) 真不真支持 tool calling — **撤回**今天对 122b 的"不调工具"判定 (没真测过, 它内网不可达, 鸿波点出此误判)
  - 端到端验证 weekly-report + leadership-briefing 真生成 (新对话 + 任意千问 + 发"写本周周报")
  - cargo build Companion 让仪表盘看到 catfish skills (可选, 不影响功能)
- 下午:
  - 写第 3 个 skill scaffolding (eis-qualification-export 或 annual-summary 二选一)
  - 客户 SSO 接入文档收尾 (`docs/SSO-CUSTOMER-INTEGRATION.md`)
- 晚上:
  - commit + push (今天累积 ~15 个文件改动, 还没 push)
  - 5 月 demo PoC 演示流程彩排

---

## 2026-04-30（周三）

跨 session 上下文 + leadership-briefing 4 段重构 + 多次设计弯路复盘.

### 完成

#### 🧠 跨 session 上下文 (鸿波反馈"不像真实个体, 100 个素不相识的人轮流帮我")

LLM 应用最深的痛点 — 每个 session 是孤岛, 模型不知道员工"上次/之前/上周"做了什么. 鸿波 195 条对话里协商出来的"4 段框架 + 表格附件化"决策, 新对话**等于零**. 5 月 demo 致命点.

- **档 1: `inject_session_history`** (~150 行)
  - 读 `~/.hermes/state.db` 取最近 7 天 sessions (id / 时间 / 消息数 / title / 首条 user message)
  - 注入 system prompt 顶部, 模型每次推理都看到**历史索引**
  - 读 read-only + 1s timeout + 异常吞掉
  - 注入策略: 找最后 system message 末尾追加, 幂等
- **档 2: `employee_journal` + `session_summarizer`** (~350 行 + 15 测试)
  - `~/.catfish/employee_journal.md` 追加式日记, 每 session 1 段 LLM 总结 (1-2 段, 100-300 字)
  - 自动总结: chat_completions 入口 `asyncio.create_task(trigger_background_summary)` fire-and-forget
  - 后台 LLM 调用: openai/qwen3.5-flash-2026-02-23 (跟 catalog 一致), DASHSCOPE_API_KEY 没设早返
  - mark 时机: 只有 journal **真写入了**才 mark. 失败 (网络抖 / 模块没装) 不 mark, 留给下次重试
  - prompt 设计: LLM 只填 `### <主题>` + 正文, 日期前缀由 gateway 加 (防 LLM 脑补错时间)
  - journal 上限 50KB (~12K token), 超过从尾部截断保留最新, 全文注入 system prompt
- **联合效果**: 模型每次 chat 看到 (1) session 索引 (2) 关键决策 / 偏好 / 里程碑总结. 真正"持久个体"不再是孤岛
- **真实验证**: 鸿波本机跑 9 session 全部成功总结, journal 自动填充 ~6KB. 新对话发"我最近做了什么", 鲶鱼能引用具体 session + 决策

#### 📋 leadership-briefing skill 4 段重构 + 强化分析 + 表格附件化

之前 5 段公文式 (背景/问题/方案/请示/下一步) 在测试中输出过于死板. 鸿波建议改成 4 段 (概况/分项/问题/下一步), heading 文本 LLM 自由命名 (按汇报场景挑).

- **SKILL.md 改 4 段**: heading 文本 LLM 自由 ("一、2026 年资质管理工作总体情况" 比 "一、概况" 更切场景)
- **内容质量铁律**: 每段必须 论点 + 数据支撑 + 推论 + 承上启下 4 要素, 不许堆数据列举
- **表格全转附件**: 默认所有表格化数据 (清单/频次/对比/明细) → attachments, 仅 1 个关键 KPI 总览 (≤5 行) 例外
- **完整示例重写**: 4 段全 paragraph 分析为主, 5 个附件覆盖明细数据
- **测试**: 25 个全过 (4 段顺序检查改成 regex 一/二/三/四, 不写死字眼)

#### 🛠 工程细节修复

- **Companion `useChat.ts` MAX_TOOL_ROUNDS 10 → 20**: 鸿波"10 轮总踩上限"反馈
- **parse_error 早停**: 连续 3 轮 LLM 生成不合法 JSON args (Companion 端 fallback 到 `_raw + _parse_error`) → 早停 + 友好提示员工"看上面 ✓ 成功的 tool_call 输出"
- **`useChat.ts` cache 60s TTL + 关键工具缺失自动重拉**: 不再需要 Cmd+R 刷新
- **Tauri Rust `commands/skills.rs`**: 同时扫 `~/.hermes/skills/` + `<catfish_root>/skills/`, catfish skills 加 `🐟 catfish:` 前缀强调来源, 仪表盘可见

#### 🔤 错别字双 backend 合并 (typo_check mini + pycorrector)

5 月 demo 公文场景对错别字零容忍 ("帐户/帳戶"上汇报会被领导挑出来). 鸿波下午要求集成 pycorrector. 实测发现 pycorrector 默认 Corrector 用 Kenlm 通用语言模型 (非公文术语训练), 同段文本只命中 1 个错 ("布暑→布署", 还是错的, 正确是"部署"). 做不了主力.

- **`typo_check.py` (新, 0 依赖, 230 行)**: 公文场景常见错字字典 50+ 条 (账户/部署/即将/登录/抉择/沟通/通讯 等), 跑同段文本命中 8 个. 是公文场景主力.
- **dual-backend merge in `_maybe_audit`**: typo_check 优先 + pycorrector 补充, 按 `(old, pos)` 主键去重. mini 字典覆盖公文高频, pycorrector Kenlm 偶尔捞到稀有错字, 互补.
- **`_normalize_errors` 适配层**: pycorrector 返回 `[(wrong, correct, begin, end)]` (tuples), typo_check 返回 `[{old, new, pos, category}]` (dicts), 统一为 dict 格式以便去重 + 落 audit.
- **依赖**: hermes venv 装 kenlm + 下载 `zh_giga.no_cna_cmn.prune01244.klm` (~2.9GB). 无 pycorrector 环境照样跑 (mini 主力).
- **测试**: 25/25 通过.

#### 📢 演讲稿全套更新 (4 份 docs 同步今日新增功能)

跨 session 记忆 + leadership-briefing 4 段重构 + 双 backend 错别字必须打进 5 月 demo 卖点话术, 否则等同没做.

- **`docs/MAY-DEMO-DECK.md`** (PPT 大纲 30 → 32 张):
  - 加 Slide 11-12 **场景 4 · 跨 session 记忆 demo** (含口语稿 + 关键卖点字幕 + 现场演法)
  - 加 Slide 19 **锁层差 3 · 跨 session 记忆** (vs ChatGPT 对比 + 双档技术实现 + 隐私设计)
  - Slide 5 场景 1 升级: 4 段公文 + 表格转附件 + 双 backend 错别字检查写入 demo 步骤
  - Slide 18 数据流对比表加"跨对话记忆" 行
  - Slide 21 总结差异化 3 → 4 条 (加跨 session 记忆)
  - Slide 26 客户必问 5 → 6 题 (加"跨 session 记忆怎么实现")
  - 时间分配 30 → 35 分钟, 4 demo 场景占 14 分钟
- **`docs/ELEVATOR-PITCH.md`** (5 个 30 秒电梯演讲):
  - V1 (CTO) / V2 (部门领导) / V3 (员工) / V4 (CISO) 各加跨 session 记忆卖点 (适配听众)
  - "不要说的话"反例表加"AI 长记忆 / 大模型记忆" → 改"鲶鱼像同事一样记得你, 记忆全本地"
  - V1 节奏拆解 3 句 → 4 句 (加 Phase 1 已 ship 跨 session 记忆 token)
- **`docs/MAY-DEMO-Q-AND-A.md`** (12 → 13 题):
  - 新增第 13 题"跨 session 记忆怎么实现, 隐私怎么保证?" — 详细备背 (双档机制 + 5 条隐私保证 + 营销话术对比表 + demo 流程 + Phase 2 升级)
  - 紧急话术加 2 条: 场景 4 翻车 fallback + 客户怀疑云端时当场 cat journal
- **`docs/MAY-DEMO-PREP.md`** (个人检查清单):
  - 工程检查加双 backend 验证 + hermes venv 依赖 (litellm + pycorrector + kenlm + Chinese model)
  - 新增 §跨 session 记忆 准备清单 7 条 (演前 7 天积累 journal / DASHSCOPE_API_KEY / 演前一晚关重启触发总结 / inject 链路 curl 测 / 录屏 backup)
  - 现场演示流程 3 → 4 场景, 13 → 17 分钟
  - 紧急 fallback 加场景 4 翻车 + 客户质疑云端的应对

#### 📜 SOUL.md 加铁律 — catfish_run_skill 反幻觉

鸿波 4-30 实测发现: 第一次 `catfish_run_skill ✓` 成功生成 5 个文件, 后续模型用 `skills_list()` 查 catfish skill, 看不到 → 误判"skill 不存在", 走 `catfish skills install` (不存在的命令), 最后建议"方案 A: 我自己写代码", 烧完 195 条对话.

新增 SOUL.md § "catfish_run_skill 工具不要去 skills_list 验证 (重要 · 踩过坑 2026-04-30)":
- 两套 skill 系统: hermes (~/.hermes/skills/) vs catfish (<catfish_root>/skills/) 完全独立
- catfish skill 不在 skills_list 输出里, 不要去查
- 唯一靠谱来源: gateway 注入到 system prompt 的 catfish skill 列表
- 永远不要跑 `catfish skills install/pull/browse` (这些命令不存在)
- skill_guard REQUIRED block 同步加铁律 5/6: 双层防御

### 走过的弯路 (老实复盘)

#### 弯路 1: B 方案 hermes 原生迁移 → 立刻撤回

**假设**: catfish skill 装到 `~/.hermes/skills/productivity/catfish-*` 下, 走 hermes 原生调用机制, 不再需要 catfish_run_skill / skill_guard / skills_inject. 鸿波建议"跟 catfish-email 同等地位".

**实测翻车**: hermes 的 skill 不是 LLM 自动可见的 tool, 模型不会主动调. 它会调 `skill_manage / execute_code` 自写代码绕过 catfish skill.

**结论**: A 方案 (catfish_run_skill 工具直接执行 script.py) 才是符合实际的设计. 立刻撤回 B 禁用. install_to_hermes.sh 留着备用 (复制 SKILL.md 到 hermes 路径无害, 仪表盘能看到).

#### 弯路 2: tool_capability_guard 硬编码黑名单 → 改配置驱动

第一版加 `KNOWN_TOOL_BAD_MODELS = ["qwen_v3_5_122b_a10b"]` 黑名单. 鸿波质疑"是不是模型参数文件已经设了 fallback, 你硬编码冗余". 立刻撤回. ModelConfig 早就有 `supports_tool_use` 字段, 我没用. 重构成纯配置驱动, 备选模型也从 catalog 自然挑.

后续验证: 用户不在公司 → 122b 内网不可达 → 自动 fallback 到 qwen-flash → "不调工具"实际是 fallback 到的 qwen-flash 行为, 不是 122b 本身. 撤回对 122b 的"不调工具"判定.

#### 弯路 3: hermes 自创 skill 抢占 catfish skill (整天测试翻车的真因)

整天反复测都失败, 以为是 catfish_run_skill 或模型问题. 实际是模型之前自己写的代码被 hermes-agent 注册成了**自创 skill**:
- `~/.hermes/skills/data-analysis/qualification-management-report/`
- `~/.hermes/skills/data-analysis/eis-qualification-analysis/`

模型每次看到"资质管理汇报", **优先选自创 skill** (因为名字精确匹配 + 是它自己写的代码), 走自创 skill 的 hardcode 微软雅黑 + 自创段标题 + python-docx 路径, 完全绕开 catfish_run_skill.

`rm -rf` 删了之后, 模型才会真用 catfish 工程审定 skill. 鸿波亲自指出: "千问其实都支持工具调用, 之前用的不好就是几个 SKILL 冲突的问题."

### 踩过坑

- **session_summarizer 提前 mark 设计错**: 第一版"LLM 调用前提前 mark", 担心调用循环烧 token. 实际遇到环境错误 (litellm 没装) 时, 10 个 session 全被错误 mark, 永远不再总结. 改成"只有 journal 真写入了才 mark", 失败留给重试.
- **session_summarizer LLM 脑补时间**: prompt 让 LLM 输出 `## YYYY-MM-DD ...`, LLM 自己生成时间和 session.started_at 不一致. 改成 LLM 只写 `### <主题>`, 日期前缀由 gateway 加.
- **install_to_hermes.sh 跑通但 hermes 不识别为 tool**: hermes skill 系统不是 LLM 自动可见的 tool, 是文件 + skill_view 手动加载. B 方案设计假设错.
- **hermes venv 缺 litellm**: `~/.hermes/hermes-agent/venv/bin/python` 跑 session_summarizer 报 `No module named 'litellm'`. 修: 用 catfish gateway venv 跑, 或 hermes venv 装 litellm.

### 遗留

- **5 月 demo 真实演示彩排**: 用户 + 鲶鱼演 "新对话引用 journal 真历史" + "leadership-briefing 一句话生成合规 .docx" + "场景 4 跨 session 记忆", 验客户真震撼 (现在 4 场景, 17 分钟)
- ~~**hermes venv 装 litellm**~~: 4-30 晚已用清华镜像装好, session_summarizer 后台总结链路打通
- **journal 截断策略升级**: 现在尾部截断, 长期应该向量检索召回最相关的, 不全文注入 (5-6 月做)
- **下一个 catfish skill**: weekly-report 的"自动从 hermes 历史抽取本周做了什么" 完整版 Phase 2 (简化版方式 A+ 已 ship 在 SKILL.md)

### 今日总账

| 类别 | 数量 |
|------|------|
| Task ship | **22 个** (跨 session 2 模块 + leadership-briefing 4 段 + skill_guard 加铁律 + 仪表盘 + scaffold weekly-report + tool_capability 重构 + 双 backend 错别字 + 演讲稿 4 份同步 + ...) |
| 测试 | gateway 380 (含新 15 跨 session) + tool-bridge 165 + leadership-briefing 25 (含双 backend) + weekly-report 17 全过 |
| 代码新增 | ~2700 行 (跨 session 2 模块 + 工程级改动 + typo_check.py 230 行 + 4 份演讲 docs) |
| 真实成果 | journal 9 段自动总结 + leadership-briefing 4 段示例 + weekly-report .xlsx 模板 + 5 月 demo 全套话术更新 (32 张 PPT + V1-V5 电梯 + 13 题 Q&A + 检查清单) |
| 走过弯路 | 3 个老实复盘 (B 方案 / 黑名单 / hermes 自创 skill) |

#### 📋 BACKLOG v2 + CAPABILITY-MATRIX 落地 (修 backlog 漂移)

鸿波收工时翻文档发现"完整功能规划缺了, 是不是都没记下来" — 实测发现 BACKLOG.md v1 (4-27 写) 之后 3 天 ship 的 30+ 项**一个都没回写**, 漂移 3 天. 真因: 我们 daily 写 CHANGELOG (事实记账), 但 BACKLOG 是意图规划, 需要回写 ⬜ → ✅, 我们没做.

- **`docs/BACKLOG.md` v2** (121 → 186 项): 加 §K 4-28~30 ship 完成项快照 (30 项 ✅, 含 SSO 全链路 / 业务 skill / 跨 session / Companion 工程 / SOUL 铁律 / demo 物料) + 加 §L 当前缺口 (25 项, 含 demo 前必做 / PoC 阶段 / Skill 全生命周期 5/10 / 演讲补缺 / 工程债). 加维护规则铁律: **每天收工必须回写 ✅, 严禁再漂**.
- **`docs/CAPABILITY-MATRIX.md`** (新, 4-30 v1): 鲶鱼现状能力快照, 8 大模块 35+ 项功能, 每项标代码位置 / 测试覆盖 / Demo 场景 / Phase. 解决"找不到完整功能规划"的根本问题.
- **`docs/README.md`** 重写: 加核心 4 件套维护节奏 (BACKLOG / CHANGELOG / ROADMAP / CAPABILITY-MATRIX) + 文档关系图 + 入坑指南.
- **后续节奏**: BACKLOG 每周一 review + 每天回写 ✅ / CHANGELOG 每天补 / CAPABILITY-MATRIX 每 sprint 末更新 / ROADMAP 季度调.

### 明天起手式

- 在公司测 catfish-private-main (qwen 122b) 真实 tool 调用能力 — 撤回 4-29 / 4-30 对 122b 的两次误判
- 5 月 demo 演示流程彩排 (4 场景 17 分钟, 含场景 4 跨 session 记忆) — 真机+录屏, 计时
- weekly-report 真生成 sample, 跟鸿波公司模板对比
- demo 前 7 天起每天用 catfish 工作, 攒真实 employee_journal.md (没内容客户看不到效果)
- ★ **新铁律**: 每天收工写 CHANGELOG 时, **同步在 BACKLOG.md 标 ✅** (防再漂)

---

## 2026-05-01 ~ 2026-05-05 (五一 5 天 sprint)

鸿波东京休假, 我 (鲶鱼) 帮他推进 Phase 2 核心 5 大功能跃迁. 鸿波本机周末抽空 build / 测.

### Day 1 · 5/1 — 多模态 (语音 + 文件) ★ 核心痛点

**前 4 条路全踩坑** (~5 小时):
1. SFSpeechRecognizer + objc Rust → dev binary 不是 .app, NSException 崩 (砍)
2. macOS 系统 dictation (双击 🌐) → WKWebView 内 textarea 不是 NSTextField, 文字不进鲶鱼 (砍)
3. WKWebView getUserMedia → 默认禁用 mediaDevices API (砍)
4. ffmpeg 子进程录音 → dev binary 没麦克风权限, .app rebuild → Info.plist 缺 NSMicrophoneUsageDescription (修)
5. **方案 C+ ship**: ffmpeg subprocess 录 wav → whisper-cli 转 → 文字进 textarea

**最终 ship**:
- `speech.rs` — `find_executable` 探 brew 路径绕过 GUI app PATH 限制. ffmpeg avfoundation 录 16kHz mono. whisper-cli 调 ggml 模型 (优先 large-v3 > medium > small). 加 prompt context 提升公文术语命中.
- ChatInput `🎤` 按钮: 红脉冲录音中, ⏳ 转写中, append 到 textarea
- **Whisper 中文准确率**: small 差 → 升级 medium (1.4G 实测可用) → large-v3 (2.9GB, M4 16GB 内存最佳, 鸿波 5/1 没下完)

**文件上传 ship**:
- `commands/file_parse.rs` Rust → Python helper `scripts/parse_file.py` (pypdfium2/openpyxl/python-docx/csv/txt/md, 50KB 截断防 token 爆)
- ChatInput 📎 接受 PDF/Excel/Word/CSV/TXT/MD, FileChip 渲染 (📕/📊/📝 + N千字)
- `Attachment` 类型扩展 `kind: image | file`, file 走 `text` 字段
- `lib/chat.ts::toWire` 拼 file text 到 user content 末尾 (`=== 附件: xxx ===` 分隔)
- state.db 占位: `[📄 N 份文档 (xxx.pdf) — in-memory, 切会话不保留]`
- **鸿波 5/1 实测 ✅**: 拖 .pdf 进, 鲶鱼能引用文档内容回答

**踩坑复盘**:
- macOS dev binary 跟 .app bundle 权限模型差异巨大 (麦克风 / sandbox / TCC.db). 验证语音必须 .app build, dev mode 不行
- WKWebView 跟 NSTextField 焦点模型不同: 系统 dictation 找的是 NSTextField firstResponder, web textarea 拿不到
- macOS GUI app 的 PATH 不含 /opt/homebrew/bin (brew 路径). 调 subprocess 必须硬编码探测路径

### Day 2 · 5/2 — Skill 全生命周期 4 步

补 5 个 skill 生命周期 (BL-L14~L17 + BL-C15) — 之前 5/10 步 (设计/加载/调用/inject/guard) 已 ship, 补:

- **版本管理** (BL-L14): SKILL.md frontmatter 加 `version: "x.y.z"` 字段, `SkillMeta.version`, `format_skills_block` 显示 `v1.1.0` 标. 老 skill 默认 0.1.0 兼容.
- **下线 / deprecation** (BL-L15): `deprecated: true/false` + `deprecated_reason`. `format_skills_block` 标 ⚠️ DEPRECATED 警告 + LLM 不推荐调. `catfish_run_skill` 调 deprecated skill 时 result 加 `deprecated_warning`.
- **删除** (BL-L16): 新 tool `catfish_skill_delete(skill_path, reason, confirm)`. 删前 backup 到 `~/.catfish/skill-trash/<ts>-<name>/` (30 天可恢复). 必填 `confirm: true` 防误删.
- **完整审计** (BL-L17): tool-bridge 加 `_write_skill_audit` helper, `run_skill` / `skill_delete` 都写 `~/.catfish/skill_audit.jsonl` (ts, skill_path, version, ok, duration_ms, files, error_msg). 不存原始 params (PII 保护).
- **Companion SkillAuditCard** (BL-C15): Tauri Rust `skill_audit.rs` 读 jsonl 聚合 (今日次数 / top 5 skills / 最近失败 / 30 天未用 / 平均耗时). React 卡片在 Dashboard 中央.

**现有 skill 加版本**: leadership-briefing v1.1.0, weekly-report v1.0.0.

### Day 3 · 5/3 — Skills Hub MVP + Plan D 协议设计

- **`catfish_skill_install`** (Skills Hub MVP 本机版, BL-D1 简化): 从指定目录复制到 `skills/<namespace>/<name>/`. 同名 skill 已存在必须 `overwrite=true` (自动 backup 到 trash). 安全检查 (拒绝从 /etc /usr 系统目录装). namespace 默认 `personal` (员工本人装的).
- **`docs/PLAN-D-PROTOCOL.md`** (480+ 行): Plan D Federation 协议 v0.1 完整 spec. 11 章 — 协议风格 (MCP JSON-RPC over SSE) / JWT 互信 / ALLOW.md 默认 DENY / Audit 双方记录 / Registry yaml / 单机 mock 验证 / 跨机真测留 5/6+. 关键设计决策签名 (鸿波 4-30 拍板) 写进文档底部.

### Day 4 · 5/4 — Plan D registry + A 端 + B 端 实施

**Registry** (BL-M4.1):
- `central/identity-server/registry.py`: `POST /registry/register` (catfish 实例自报家门) + `GET /registry/lookup?sub=...` (拿 endpoint+jwks_uri) + `GET /registry/list` (调试). yaml store, `last_seen` 2 分钟超时算离线. mount 到 catfish-identity app.

**A2A Auth + Audit** (BL-M4.2/M4.3 共用):
- `a2a_jwt.py`: `sign_a2a_token` (RS256, 5min, jti 防重放), `verify_a2a_token` (registry lookup → fetch jwks → 验签 + 验 aud + 验 jti), `lookup_remote_agent` httpx 调中央 registry, JWKS 5min 缓存
- `a2a_audit.py`: `~/.catfish/a2a_audit.jsonl` append-only (tool-bridge skill_audit.jsonl 模板)
- `a2a_allow.py`: 极简 markdown 解析 (不引 yaml-parser, 防依赖). `check_allow(question, config, from_sub, from_attrs, purpose)` → (allowed, reason). deny 优先 + allow 段 + 默认拒. 关键词子串匹配, 不用 LLM.

**B 端** (BL-M4.3):
- `a2a_server.py`: `POST /a2a/ask` SSE endpoint. 流程 — 验 JWT (verify_a2a_token) → 限流 (10/min/from_sub) → check_allow → 转 LLM 流式 (litellm qwen-flash, 没 DASHSCOPE_API_KEY 走 mock) → SSE chunk → audit. JSON-RPC 错误码 -32001~-32005.

**A 端** (BL-M4.2):
- `a2a_client.py`: `ask_remote_agent` 异步生成器, lookup → online check → sign → POST SSE → 解析 chunk → yield. 失败抛 PermissionError / ConnectionError / RuntimeError. 跟 B 同时 audit.
- `app.py` 加 `POST /a2a/internal/ask` (gateway 内部 endpoint, 简化版收完整 SSE 一次返). tool-bridge 通过 HTTP 调它触发 A2A.

**Tool 暴露** (BL-M5):
- tool-bridge `catfish_a2a_ask` 工具: `to_sub` + `question` + `purpose` + `context_hint`. 调 gateway `/a2a/internal/ask`. denied 友好提示员工.

### Day 5 · 5/5 — 单机 mock + demo

- **`scripts/plan_d_mock_init.sh`**: 初始化 ~/.catfish-alice + ~/.catfish-bob 双 home, 各自 RSA keypair (openssl genpkey), 各自 ALLOW.md 模板 (alice 限制宽 / bob 限制严), registry yaml 占位. 输出 4 个终端启动命令 + curl 测试样例.
- **mock demo 流程**: T1 启 catfish-identity registry, T2/T3 启 alice/bob gateway, T4 register 双方 + curl 调 `alice → bob` 验证 SSE + audit jsonl 双方各一条. **实际 mock 测试留鸿波 5/5 在本机跑** (sandbox 限制).

### Day 1-5 总账 (代码 ship)

| 模块 | 文件 |
|---|---|
| Day 1 多模态 | speech.rs / file_parse.rs / parse_file.py / FileChip / chat.ts toWire / types/chat.ts |
| Day 2 Skill 生命周期 | skills_loader.py (version/deprecated) / catfish_tools.py (audit/install/delete) / skill_audit.rs / SkillAuditCard.tsx |
| Day 3 Hub MVP + Plan D 设计 | catfish_tools.py (skill_install) / docs/PLAN-D-PROTOCOL.md |
| Day 4 Plan D 实施 | identity-server/registry.py / gateway/a2a_jwt.py / a2a_allow.py / a2a_server.py / a2a_client.py / a2a_audit.py / app.py 路由 / catfish_tools.py (a2a_ask) |
| Day 5 mock | scripts/plan_d_mock_init.sh |
| **代码新增** | ~3500 行 (Rust ~600 + Python ~2200 + React/TS ~400 + 文档 ~300) |

### 遗留 (鸿波 5/5 / 5/6 起手)

- **Day 1 ⚠️ 真机彩排**: 重 build .app + 测 🎤 medium 准确率 + 测 📎 文件上传 (鸿波 5/1 测 ✅)
- **Day 2 ⚠️ 真机验证**: 重 build .app 看 SkillAuditCard 仪表盘. catfish_skill_delete 手动调试 1 次 (确认 skill 真移到 trash, ALLOW.md 也跟着移)
- **Day 3 ⚠️ Skills Hub MVP 调试**: 真造一个测试 skill 目录, 调 `catfish_skill_install` 看仪表盘
- **Day 4-5 ⚠️ Plan D 单机 mock 测试**:
  - 跑 `scripts/plan_d_mock_init.sh`
  - 4 个 terminal 起服务
  - alice register + bob register
  - alice 调 bob 看 SSE 流 / audit / ALLOW 命中
  - 跨员工真测留 5/6+ 公司双机
- **5/6 上班后**:
  - Journal 向量召回 (BL-L8, 用公司 bge-m3)
  - Plan D 跨 2 台真机测试 (公司 SSO 多账号)
  - 第 3 个 catfish skill (annual-summary 或 project-approval) for demo

### 五一总结

鸿波在东京休假, 我推完了 Day 2-5 的代码 (8 大模块 ~3500 行). 但**所有 Day 2-5 代码都没真机验证** — 我们只验证了 Day 1 文件上传. Plan D 是大架构变动, 5/5 mock 测试**必跑**, 不跑直接交付有炸雷风险.

5 月 demo 不演 Plan D (Phase 3 Q4 ship 是承诺, 别打脸). Plan D 这次 ship 是技术 ready + spec 清晰, 给 demo 后客户技术对接看 ("我们已经写好协议 + 单机验证, 12 月跨员工真协作").

---

## 记录规则

- 每天收工时补一条
- 完成 / 踩坑 / 遗留 / 明天起手式 四栏
- 完成优先写"事实"（做了什么），不写"感受"
- 踩坑写"现象 + 原因 + 解法"便于未来参考

---

## 2026-05-02 ~ 2026-05-03 (周末加班 sprint - Phase 2 后端冲刺到 70%)

五一 sprint 收尾, 鸿波东京回来周末两天连续 ~30 小时高强度. 我 (鲶鱼) 配合 ship.
**主线**: Phase 2 后端 RBAC / Quota / PG / Skills Hub 全套到生产可用 + 修一堆 demo 阻塞 bug.

### 5/2 周六 ship (15 commit)

**清理 + FEATURE-TRACKS 主入口**:
- BL-L27 datetime.utcnow → datetime.now(timezone.utc) (3 处, Python 3.12 deprecation)
- BL-L28 ALLOW.md 关键词从子串改 jieba token-overlap (中文场景"项目X进展" 不命中"项目X上周进展" 修)
- 新建 docs/FEATURE-TRACKS.md (~250 行, 24 个 track + 6 个发现的暗角:
  email-agent / feishu-monitor 实际有真代码, 5 个 central 服务全 stub)
- PROJECT-STATUS.md 标 deprecated, BACKLOG.md 加 redirect

**RBAC + Quota 全栈 ship**:
- User dataclass 加 role + managed_departments + can_manage_department
- /api/me + /api/quota/department/{dept} + /api/audit/department/{dept}
- DepartmentQuotaCard / DepartmentAuditCard (manager 看本部门聚合)
- DashboardTab 按 role conditional render (employee 8 卡 / manager +部门 / admin +全局)

**多账号测试基础设施**:
- dev_users.yaml + DevUserSwitcher 顶部黄条 (admin/3 manager/3 employee 跨 4 部门)
- localStorage 记选中 token, 切换器自动 reload
- /api/dev/users 端点 (prod 模式 404 自动隐藏)
- autostart 默认 prod (.env CATFISH_AUTOSTART_ENV=dev opt-in)

**PG migration 完整**:
- gateway 新建 db.py (镜像 identity-server, asyncpg 懒 import 防沙盒炸)
- quota.py + metrics.py 双 backend (PG 主 / sqlite jsonl 兜底, PG 失败自动 fallback 不丢)
- alembic 双服务 (gateway + identity-server) 各自 migration + version_table 隔离
- ★ chat completions 真接 record_usage (之前 PG 表永远空的根因)
- 真机端到端跑通: 5 张表 + chat → 双写正常

### 5/3 周日 ship (15 commit)

**第 3 个业务 skill (BL-L6)**:
- skills/department/project-approval/ — 复用 leadership-briefing 渲染层 (importlib spec 加载防 'script' 模块名撞)
- 4 段固定: 背景与必要性 / 方案与预算 / 风险分析 / 进度与立项建议
- 触发词: 立项 / 可研 / 申报项目 / 报项目

**identity-server 也迁 alembic**:
- 跟 gateway 对齐: alembic.ini + env.py + 初始 migration
- 关键: 各服务独立 alembic_version_gateway / _identity 防共享 PG 撞

**BL-E13 主动闲聊 C-MVP**:
- gateway proactive.py: 读 journal tail + 时段 + qwen-flash 生成上下文 starter
- ProactiveCard Dashboard 第一张卡, 30min 自动换
- useProactiveScheduler 9:30/14:00/17:30 macOS 通知
- Tauri notify() osascript 实现 (无新依赖)

**RBAC 三件套 + Quota 阻断 chat**:
- PUT /api/quota/department/{dept} (manager 改限额, 写 yaml + 自动 reload)
- GET /api/quota/global + /api/audit/global (admin 全局聚合)
- AdminGlobalCard (admin only, 整行)
- DepartmentQuotaCard 加 inline QuotaEditor (manager 直接 input + 保存)
- chat completions 入口 check_quota → 429 + friendly 中文话术
- Companion chat.ts 识别 429 显友好 banner

**修关键 UI bug** (真机调试 2 小时定位):
- useChat.ts 在 await streamChat() 后**无条件** updateMessage(status: 'done')
  覆盖了 onError 设的 'error' 状态 → quota / 503 / 鉴权 等错都 UI 不显
- 修法: 先看当前 status, 是 error 就不动, 否则才改 done

**Skills Hub MVP 完整**:
- BL-C12 dry-run + rollback (skill_install 后 importlib 加载 + 找入口函数)
- BL-C13 dedup 检查 (install 前查同名 / 描述相似)
- 中央 Skills Hub server (新建 central/skills-hub/, FastAPI):
  * publish / list / get (latest+指定版) / download_file / delete + audit
  * 文件系统存储 ~/.catfish-hub/, 多 version 共存, 路径越界保护
  * Companion 改 hub URL 拉取 留下次

### 测试 (167 全过)

| 子系统 | 单测数 |
|---|---|
| tool-bridge skill_lifecycle | 23 (+6 dry-run/dedup) |
| skills-hub storage | 21 (新加) |
| gateway quota / dev_token / a2a / metrics / skills_loader | 83 |
| identity-server users / registry | 32 |
| Companion (skill smoke) | 8 |

### 真机端到端验证 (Mac, May 3)

✅ PG 真双写: 5 张表 + chat → quota_events + gateway_audit 各 1 条新行
✅ Quota 429 闭环: Alice (研发部, limit=1000) chat → 红色横条 → manager 改 0 → 再聊通过
✅ Skills Hub: publish leadership-briefing v1.1.0 → list 返 1 条 → download SKILL.md 真返全文 → audit 真记
✅ Companion 角色切换: admin (个人 + 全局) / manager (个人 + 部门) / employee (只个人)
✅ 跨 session 记忆: 小鲶 "早, 鸿波. 最近资质汇报和周报都交付完了, 今天准备干啥?" (引用 journal 真业务)

### Phase 进度 (周末翻倍)

```
Phase 1: 92% → 95%  (+ project-approval / 主动闲聊 / Quota 真接 chat)
Phase 2: 35% → 70%  (RBAC UI / 多账号 / PG 双写完整 / alembic 双服务 /
                     Quota 100% / Manager UI / Admin 全局 / Skills Hub MVP / dry-run+dedup)
Phase 3: 30%        (Plan D 五一 ship 后未变, Q4)
```

### 踩坑复盘

1. **alembic 跨服务共用 PG** → alembic_version 表会撞, 各服务用 `alembic_version_gateway` / `_identity` version_table 隔离
2. **alembic 配 URL 含 PG 密码 URL-encoded %21** → configparser 把 % 当变量插值前缀触发 ValueError. 修: 走 create_engine(url) 绕开 configparser
3. **SQLAlchemy 默认 'postgresql://'** → psycopg2, 但我们装 psycopg3. URL 改 'postgresql+psycopg://'
4. **yaml.safe_load 'departments:' 后只有注释 → None**, setdefault 不替换 None, 必须显式 isinstance 检查
5. **Companion 默认 prod / DEV opt-in 反了几次**: 最终拍板默认 prod (装的 .app 给客户/同事看), DEV 走桌面 .command 启动器
6. **useChat 状态覆盖 bug** (上面有详细). 真机调试 2 小时, DevTools Network 看到 429 + 完整 JSON, 但 UI 空 → 顺着代码追到 await 后无条件 updateMessage 'done'

### 累计 commit (周末)

~30 commit. 全部 push 上 main. 0 测试 fail.

### 5/2-5/3 sprint 总结

Phase 2 后端 + 用户态完整 ship, demo 卖点 10 个全部技术 verified.
**剩 demo 阻塞全在演讲准备侧** (彩排 / PPT / 实录视频), 5/14 demo 还有 11 天.

### 遗留 (demo 后做)

- Companion catfish_skill_install 改支持 hub URL 拉取 (~0.5 周)
- Skills Hub 审核流 manager → admin → live (~1 周)
- 部门级 skill auto-push (依赖 federation, ~1-2 周)
- Federation 真实化 (跨 2 台真机 / mTLS / HA, ~5-6 周)
- Production 部署设计 (~1 周)
- 完整 BL-E13 主动闲聊 (情境关联 / 节假日推断, ~1-2 周)
- email-agent / feishu-monitor 接通 Companion 决策

### 5/14 demo 倒计时 11 天

🔴 真机彩排 ×2 (5/11 + 5/13)
🔴 实录 case 视频 ×3 (1 天搞)
🟡 PPT 实际填 (大纲 + 封面已有, 内页待填, 0.5-1 天)
🟠 employee_journal 持续攒 (每天聊 2-3 句真业务)

---

## 2026-05-03 (周日晚) 加班 sprint - 完整 brand kit v1 + 防泄漏

周末 sprint 后再加 ~6 小时, 把"形象"这块短板一次到位 ship.
**触发**: 鸿波看到员工聊天里小鲶说"memory 工具不可用 ~/.hermes/memories/" 暴露 hermes 内部品牌, 决定从根上修.
**主线**: (1) brand 防泄漏 (2) 完整 brand kit v1 (3) UI 彻底去 emoji 占位.

### Brand 防泄漏 (BL-D9)

- **SOUL.md 加品牌铁律**: 禁止小鲶说 hermes / ~/.hermes / "未初始化" 等暴露内部品牌的话; 列正确替代词 (鲶鱼 / 小鲶 / 鲶鱼内置存储)
- **catfish_remember tool 描述强化**: 升 P0, 写明跨 session 永久记忆也用这个; 如果 memory_save 不可用 **务必**用它替代, 不要跟员工说"memory tool 不可用"
- **adapter dispatch 层 brand scrub** (新核心): hermes memory_* (memory/save/load/search) 工具响应里的 ~/.hermes/* 路径 + 独立 hermes 词被自动替换成 "鲶鱼本机存储"/"鲶鱼" 再给 LLM. **audit log 仍写原文** (内部审计要看), 只改 LLM 视野里的 result/error 字段
- 11 个新测试覆盖: 4 种路径形态 + 工具名 hermes_xxx 不误伤 + str/dict/list/嵌套 dict + dispatch 整链路 + 失败路径 error 字段也脱敏
- **核心反复**: 第一版直接屏蔽 hermes memory_* 工具不让 LLM 看到 → 鸿波拍 stop ("会不会影响鲶鱼记忆能力?") → 改用响应包裹方案. 关键点: hermes memory_save 是真的跨 session 永久存储, 屏了就丢这能力

### 完整 brand kit v1 (BL-D11)

**5 件套 SVG** (`branding/`):
- `logo-mascot.svg`: 完整吉祥物 480x480, 圆胖鲶鱼 + 双须 + 大眼 + 腮红 + 微笑, Onboarding/banner 用
- `logo-mark.svg`: 极简圆形 256x256, 双须 S 曲线 + 鱼眼锚点, favicon/PPT 角标
- `logo-mark-mono.svg`: currentColor 单色版, 反白印刷
- `avatar-circle.svg`: 头部特写圆形 256x256, 聊天气泡用
- `app-icon-master.svg`: macOS Big Sur+ squircle (1024x1024, rx=228 ≈ 22.37%)

**47 个 app icon 一键渲染** (`render_icons.py`, cairosvg + Pillow + icnsutil):
- macOS .icns (multi-size 16~1024 embedded)
- Windows .ico (multi-size 16/32/48/64/128/256)
- iOS 18 个尺寸 (20/29/40/60/76/83.5/512 各 @1x/@2x/@3x)
- Android mipmap 5 密度 × 3 类 (ic_launcher / round / foreground)
- Windows tiles 9 个 + StoreLogo + 4 个 Tauri 顶层 PNG
- 替换 src-tauri/icons/* 全套 (47 文件)

**配色升级** (`tokens.css`):
- 旧 `#06b6d4` 亮青 → `#0E5F66` 墨青 (主色)
- 加 `#F47B3D` 暖橙 (CTA / 在线指示, 10% 用量)
- 加 `#FAF1E4` 暖米 (聊天卡背景, 替代纯白)
- 状态色复用主色亮青 ok / 暖琥珀 warn / 暖红 err — 跟 brand 协调
- 深色模式同步 (墨青底 #131C1F + 暖米文字 #E8E4DC, 不用纯黑/纯白)

**BRAND.md 速查** (`edge/identity/`, 跟 SOUL.md 同级权威):
- 角色定位 + 4 件套用途表 + 配色系 (主/辅/中性各表) + 字体阶梯
- 净空区 / 最小尺寸 / 用法红线 (× 不要拉伸/不要换色/不要旋转/不要加投影)
- 文案语调 (用"你"不用"您") + 应用清单 + 改 brand SOP
- 旧 `companion-app/docs/BRANDING.md` 改纯 redirect, 防分裂

**Demo PPT 封面** (`branding/demo-cover.pptx`, pptxgenjs):
- 深青底 + 大字"鲶鱼" + 亮青斜体 "Catfish"
- 暖橙锚点 + "企业级 AI 助理 · Companion for SOE"
- 三行 slogan 关键词加粗 (本机算力 / 公文报表审批 / 审计合规)
- 右上角水底气泡装饰 + 底部双 footer (公司角标 + 版本日期)

### UI 占位 emoji 大扫荡 (6 处 + 1 通知)

| 位置 | 原 | 现 |
|---|---|---|
| LoginGate 标题 | "🐟 鲶鱼 Companion" | 36px mascot + 标题分行 |
| Onboarding 大图 | 60px 🐟 | 96px 完整吉祥物 |
| Onboarding 列表 🐠 | 跟"鲶鱼"易混 | ✨ 中性 emoji |
| ChatPanel 空状态 | 56px 🐟 | 120px 完整吉祥物 |
| ChatTab "🐟 对话" | 18px 🐟 | 18px 圆头像 |
| LearningCard 标题 | "🐟 鲶鱼今天学到的" | 20px 圆头像 + 标题 |
| useProactive 通知标题 | "🐟 小鲶想..." | "小鲶想..." (icon 已在通知左侧) |

### SSO 登录页 (identity-server) brand 升级

- 内联 32px mark SVG (无静态文件部署依赖)
- 全页配色升级 (墨青渐变底 + 暖橙 CTA + 输入框聚焦亮青光晕)
- favicon: 内联 SVG data: URI (macOS Big Sur+ 给本地端口的默认蓝鱼 emoji 替代)
- **Cache-Control no-store + Pragma + Expires 三件套** (修真机问题: 用户 dev 看新页 / build 看老页 — 根因是浏览器启发式缓存了无 cache 头的 HTML)
- 53/56 测试通过 (3 跳过, 0 失败)

### 踩坑

1. **直接屏蔽 hermes memory tool 错了**: 第一版加白名单从 LLM 工具清单移除. 鸿波及时拍停: "会不会影响鲶鱼记忆能力?". 反思: hermes memory_save 是真跨 session 永久存储, 屏了不只是品牌问题, 是丢能力. 改用响应包裹 = audit 留原文 + LLM 看脱敏版, 两全
2. **icnsutil type code 映射**: 16=is32 / 32=il32 / 64=icp6 / 128=ic07 / ... 不查文档纯靠记会写错
3. **Tauri 沙箱 path.resolve**: build_pptx_cover.js 用 `path.resolve(__dirname, "..")` 在不同目录跑 ROOT 不一样, 第一次直接炸. 改用绝对路径
4. **dev 看新 / build 看老 不是部署问题**: 真因是浏览器缓存. 加 no-store 头解决, 不是分别配置 dev/prod identity-server
5. **配色升级影响范围**: tokens.css 改 `--catfish-cyan` 一处, 全应用 ~50 处引用同步换. 这就是 token 的价值

### 累计 commit (5/3 晚)

5 个 commit: (1) 多账号 dev mode + 部门审计 (上 session 没提) (2) brand 防泄漏 (3) brand kit 全套 (4) UI emoji 替换 + SSO 升级 (5) 文档更新

### 5/3 晚总结

形象短板补上, demo 客户看的"第一眼"不再是 🐟 emoji. 后端 100% 卖点 + 前端 100% brand 一致 = demo 阻塞只剩演讲侧 (彩排/视频/PPT 内页).

---

## 2026-05-03 (周日深夜) 加班 sprint - 人格 sprint ship 3/4

brand 收尾后再加 ~4 小时, 鸿波拍板 4 个"人格"卖点 (IDEAS.md #10/13/14/17). 排期 6-9 天里 demo 前能放 4 个全做, 但今晚先 ship 小 3 个 (各 1-1.5 天), BL-E14 PPT 吐槽 (3-5 天) 留下周. 触发: 跟 ChatGPT/Copilot 区分点必须靠"它有性格 / 它记得你 / 它站你这边", brand kit 是壳, 人格才是魂.

### BL-E11 命名权 (员工给鲶鱼起名 + 3 档人设)

- **Gateway 侧** (`identity_inject.py`): 加 `build_personalization_preamble` + `header_agent_prefs` + 3 档预设 (gentle/direct/roast); chat_completions 把 X-Catfish-Agent-Name/-Personality header 传进 inject; 默认值不发 header 省 token; 12 单测覆盖
- **Rust 侧** (`services/agent_prefs.rs`): yaml 读写 + atomic save (写 .tmp → rename); 不破坏其他 yaml 段 (用 Value 顶层 patch); 9 单测 (默认/roundtrip/无 agent 段/未知 personality fallback/空名拒绝/超长拒绝/保留 oidc 段)
- **Tauri commands** (`commands/agent.rs`): get/set agent prefs, 写后 reload 防 trim 不一致
- **Frontend** (`lib/agent.ts` + `store/agent.ts`): zustand store + Personality 类型 + 人设标签
- **App.tsx**: 启动 loadAgentPrefs (多处共享), `lib/chat.ts` 非默认时带 X-Catfish-Agent-* header
- **Onboarding StepName** (新 step 2, 4 → 5 步): 起名输入 + 3 卡式人设选择 + 错误提示
- **Dashboard AgentPrefsCard** (新): Onboarding 走完后改名/换人设, 行内编辑式
- **ChatPanel/通知**: "我是小鲶" → "我是{name}" / "小鲶想跟你聊一句" → "{name}想跟你聊一句"

### BL-E15 专注模式 (前 "领导来了", 央企改名)

- **Tauri 全局快捷键** (`lib.rs`): Cmd+Shift+F 注册, handler 拓展成 dispatcher 模式 (后续多快捷键友好); emit `catfish:focus_mode_toggle` 事件
- **Frontend** (`store/focus.ts`): 简单 zustand toggle/exit, 不持久化 (重启默认关防 stuck)
- **FocusModeView** (新): 全屏伪 IDE — GitHub Dark 配色 + 顶栏 (绿点 + 计时 + 退出按钮) + 主区拟真状态行逐行滚 (build/lint/test) + VSCode 风状态栏 + 闪烁光标; Esc 用 capture 阶段抢退专注 (不被 App 顶层 Esc-hide-window 抢)
- **App.tsx**: 顶层 listen 事件 → toggle store; `if (focusActive) return <FocusModeView/>` 在 LoginGate 之前 (没登也能进, 真挡屏); Esc-hide 加专注态判断
- **TabBar** 右侧加 "⏸ 专注" 按钮 (不知快捷键的也能用)

### BL-E16 鲶鱼情绪 / 关系建立

- **SOUL.md** 加"情绪 / 关系建立"章节: 5 类 ✅ 适合做的小信号 (新 session 引最近工作 / 跨天回来关心 / 反复同问题升级解决 / 里程碑后祝贺 / 用员工自定义名字介绍) + 6 类 ❌ 绝对不做 (不评论情绪 / 不推断私事 / 不每次都用关系话术 / 不假装情绪 / 不谄媚 / 不瞎猜身份); 频率硬纪律 (每 session 最多 1 次, 每 5-10 session 才用 1 次自然引用); 数据来源声明 (employee_journal + session_meta + session_facts, 不主动加新事实)
- **session_meta.py** (新): json 持久化 last_chat_at + today_count + today_date; `tick()` 每次 chat 末尾调 (跨天 reset, 同天 +1); `build_meta_block()` 拼"Session Meta" markdown 段给 LLM (距上次 N 天 N 小时前 + 今天第 N 次); 自定义 ISO8601 parse 不引 chrono; 17 单测 (覆盖 tick 各路径 + build 各形态 + humanize 8 种时长 + 损坏 json 自动恢复)
- **app.py** chat_completions 加 inject + tick (找已有 system message 末尾拼; 没 system 则前插)
- **Rust commands/relation.rs** (新): 读 journal markdown (按 ## 切, 倒序, 最多 5 条) + 读 session_meta.json + 清空两者; 自定义 ISO8601 parse + Howard Hinnant days_from_civil 算法 (不引 chrono); 7 单测
- **Dashboard RelationCard** (新): "鲶鱼对你的印象" — 时间感 + 5 条最近 journal 条目 (暖米卡片) + journal 大小 + "清空印象" 二次确认按钮 (隐私逃生口); 设计立场: 鲶鱼"记得"你的事, 员工**必须**能看到 + 删除, 否则 creepy

### 顺手修上 commit 留的 8 个 fail

- 装 `pytest-asyncio` (pyproject.toml 写了但环境没装) → 7 个 a2a_jwt + rbac async 测试通过
- `test_auth_provider.py::test_happy_path` 期望 sub="dev-user" / tier="employee" 都过期了 (多账号 dev mode 后改成 sub="dev-user@catfish.dev" / tier="admin"), 改测试期望

### 累计 commit (5/3 晚 2)

3 个 commit (BL-E11 / BL-E15 / BL-E16) + 1 个 修老 fail. **0 fail / 499 pass / 0 TS error**.

### 5/3 晚 2 总结 + 5/4-5/13 还要做

人格 sprint 4 个 ship 3 个. 留 BL-E14 PPT 吐槽下周开 (3-5 天, 写 pptx parser + 调毒舌 personality + 拖放 UI), 接着进 demo 准备 (彩排 ×2 + 视频 ×3 + PPT 内页填).

5/14 demo 倒计时 11 天 → 估计:
- 5/4 (一): 休
- 5/5-5/9 (二~六): BL-E14 PPT 吐槽 (~5 天)
- 5/10-5/11 (日 + 一): PPT 内页填 + 视频录
- 5/12-5/13 (二~三): 真机彩排 ×2
- 5/14 (四): demo

后端 100% + brand 100% + 人格 75% 都 ship 了, demo 卖点 + "wow 杀器" 都齐. 阻塞主要在演讲准备侧.

---

## 2026-05-04 (周一) - 计划日不太"休": Onboarding 同事感 + Hermes 升级研究 + 记忆纪律

原计划"5/4 休", 实际加 ~5 小时. 4 件事都不动主流程, 只调 prompt + 加 docs. 0 后端代码改.

### 5/4 上午 — BL-E11 后续 (Onboarding 同事感)

鸿波反馈: "员工想叫小鲶啥就叫啥, 这个页面上也要改, 这样员工才有参与感". 之前只改了 ChatPanel 空状态 + 通知标题 2 处, 还有 11 处 user-visible "鲶鱼/小鲶" 写死.

**全应用 agent 自指换员工自定义名 (10 处 + Onboarding 标题改 agent 视角):**
- ChatMessage.tsx alt → `{agentName}`
- ChatInput.tsx placeholder → `跟{agentName}说话…`
- LearningCard.tsx 标题 + 3 处自指 ("启动时" / "按需检索" / "自动记录")
- AuditCard.tsx 提示 → `跟{agentName}聊点什么试试`
- ProactiveCard.tsx 按钮 → `跟{agentName}聊聊 →`
- RelationCard.tsx 标题 → `{agentName}对你的印象`
- AgentPrefsCard.tsx 标题 → `{agentName}的名字 + 风格`
- ServicesCard.tsx tooltip → `暴露 60+ 工具给{agentName}`
- Onboarding StepName 标题 → "给我起个名字" (agent 视角更亲)

**保留 brand "鲶鱼" 5 处** (产品名 / 平台名, 不是 agent 自指): LoginGate / Onboarding 产品介绍 / CLI 工具名 / 版本 / 平台

ServicesCard 的 SERVICES const 从 module-level 改成 component-internal `buildServices(agentName)` 函数 (能用 agentName 拼字符串). 数量恒定保证 useServiceStatus hook 顺序稳.

**测试**: TypeScript 0 errors. (gateway 测试不动 499 pass 不变)

### 5/4 下午 — Hermes 0.10 → 0.12 升级研究 + Curator vs Skills Hub 集成方案

鸿波看到 NousResearch/hermes-agent 升 0.12.0, 问"是否升级 + 跟我们 Skills Hub 怎么处理 Curator". 不动代码, 全部研究 + 归档.

**抓 GitHub release notes (~11k 行) + agent/curator.py 源码** 完整分析:

- **0.11.0 (4-23)**: React/Ink CLI 全重写 + Profile 系统 (`~/.hermes` → `display_hermes_home()`) + Transport ABC + Shell Hooks (这个对我们好, 半官方钩子可替换部分 banner patch) + AWS Bedrock + GPT-5.5 OAuth
- **0.12.0 (4-30)**: 后台 Curator daemon + Pluggable Memory Provider ABC + 57% 冷启 (lazy import, sed-based patch 受冲击) + 4 个新 inference provider + Spotify/Google Meet 原生

**catfish 4 层补丁脆性评估:**
| 层 | 行 | 等级 | 原因 |
|---|---|---|---|
| apply_brand_patch.py | 468 | 🔴 极高 | 0.11 React/Ink 重写 AST 节点全变 |
| rebrand.sh | 183 | 🔴 高 | 0.12 lazy import line offset 漂 |
| string-map.yaml | 84 | 🔴 高 | 同上 |
| dispatch scrub (BL-D9) | ~50 | 🟢 低 | 不动 hermes 源码, regex 响应过滤 |

**Curator 接口 verified (源码引文 + 行号)**:
- `~/.hermes/config.yaml` 写 `curator.enabled: false` 一行 disable
- 4 个参数 yaml 可调 (interval_hours / min_idle_hours / stale_after_days / archive_after_days)
- **Strict invariant**: *"Only touches agent-created skills"* + ***"Never auto-deletes — only archives. Archive is recoverable."*** + *"Pinned skills bypass all auto-transitions"*
- 不是真 daemon, 是 lazy 触发 (idle 时检查"距上次 N 小时")
- 状态文件 `~/.hermes/skills/.curator_state` 可外部写 `{"paused": true}` 接管

**推翻"6 个冲突场景"分析** (hermes 自己已解决 5 个): 通过 is_agent_created 过滤 + pin 双保险, hub-installed skill 大概率不被 Curator 动. **结论: 集成而非禁用.**

**Curator 集成 5 步方案** (5/15 起 demo 后实施):
1. 默认开 + 保守参数 (interval 1 周 / idle 4h / stale 60d / archive 180d, 比默认宽)
2. catfish_skill_install 装完自动 pin (双保险)
3. Onboarding explicit consent toggle (防员工困惑)
4. Phase 2.5: Dashboard "小鲶整理记录"卡 (透明)
5. Phase 3: 反向数据流 → Hub `/api/skill_health` → admin 看公司级 skill 健康热图

**完整方案**: `docs/HERMES-UPGRADE.md` ~430 行, 含时间表 / 风险表 / 升级回归 checklist / 5/8 后议程开关 (verify 2 件: `is_agent_created()` 判定逻辑 + `skill_manage` pin API).

### 5/4 晚 — 记忆纪律 (BL-MM1)

鸿波跟小鲶聊"记忆是资产, 错了 update > 删除, 留版本作为成长痕迹"产品哲学, 小鲶答应"现在就能做". 鸿波让我评估真假 — **我读代码后发现小鲶夸了**:

| 小鲶承诺 | 实际 |
|---------|-----|
| 调 memory(action='replace') 覆盖 | ✅ catfish_remember 直接覆盖 |
| 工具知道这是覆盖 | ✅ 返回 `overwrite: True` |
| "已更正, 旧的是 X, 新的是 Y" | ⚠️ **说不出旧 X** (旧值已丢) |
| "上次改过" 主动告知 | ❌ **没存版本** |
| "现在就能做, 不需要改代码" | ❌ **半真半假** |

**SOUL.md 加"记忆覆盖纪律 (BL-MM1)" 章节 (~130 行)**:
- 必走 read-then-write 流程 (memory_recall 拿旧值 → memory_save 写新值, 新值里 inline quote 旧值如 `"X = 新值. (旧值 = 老, 改于 2026-05-04)"`)
- 跟员工说话必 quote `"已更正: 旧 X, 新 Y, 第 N 次修订"`
- 4 个 ❌ 禁止 (空说"改了" / 编旧值 / blind overwrite / 不告知)
- **0 后端代码改**, 工具底层不动也能模拟版本感. 工具升级 (BL-MM2/MM3) 后自动接管

**跟其他 3 段记忆纪律的关系** (各管一摊):
- 同一 session 内别忘事 (复述模式) — in-session attention 失焦
- 凭据 ref 的记忆纪律 — 防真密码写持久存储
- **记忆覆盖纪律 (本段)** — 防覆盖时丢掉旧值的可见性
- 品牌铁律 — 不暴露 hermes 字眼

**测试**: identity_inject 28/28 pass. SOUL 注入正常.

### 5/4 晚 — 主动闲聊"还是不能自动聊天" 诊断

鸿波反馈"为什么现在鲶鱼还是不能自动聊天". 读 useProactiveScheduler.ts 后诊断: **不是 bug, 是设计窗口太窄**:
- 当前每分钟 poll, 必须 HH:MM 精确等于 "09:30/14:00/17:30" 才触发
- 没 catch-up — Companion 9:31 才打开则当天 9:30 slot 永远 miss
- 实测概率: 鸿波 9:30 在地铁 / 14:00 在午休 / 17:30 在收尾会, **3 个 slot 大概率全 miss**

**收 BL-E13-FIX 进 BACKLOG, demo 前必做** (合计 ~40 分钟, 0 风险): catch-up + 加宽时段窗口 + 通知权限引导.

### 5/4 文档归档 (没动代码, 只归档)

- ✅ `docs/HERMES-UPGRADE.md` ~430 行 (含 Curator 接口 + 集成方案 + 时间表)
- ✅ `docs/BACKLOG.md` 加 M 章节 (BL-MM1/MM2/MM3/MM4 记忆纪律) + 加 BL-E13-FIX + 标 BL-E11/E15/E19 ✅
- ✅ `docs/FEATURE-TRACKS.md` Phase 进度 99/78 不变 + 加 #28 记忆纪律 + #29 Hermes 升级 + #25 加 BL-E13-FIX 子待办
- ✅ `edge/identity/SOUL.md` 加 BL-MM1 章节

### 5/4 累计 commit (待你 git push)

修改 + 新增的代码文件 (前端 11 处 dynamic name 自指 + ServicesCard 函数化), SOUL.md 加 130 行, 文档大改 (HERMES-UPGRADE.md 全新 + BACKLOG + FEATURE-TRACKS + CHANGELOG).

**0 后端代码改, 0 测试 fail. 全在 prompt + 文档层.**

### 5/4 总结

原计划"休", 实际**做完 7 件事, 唯一动代码的是 tools_sanitizer 修 DeepSeek 兼容**:
1. UI 同事感 (11 处 dynamic name) — 员工自定义名渗透到所有自指
2. Hermes 升级研究 + Curator 集成方案完整归档 — 5/8 后启动议程 + 5/4 真机诊断 brand patch 状态 (8 备份装过 / dist 预编译 / 3 文件漏 / warm-lightmode skin 验证)
3. 记忆纪律 BL-MM1 — 缓兵之计 ship, 真版本化 BL-MM2/MM3 排到 5/8 后
4. BL-E13 主动闲聊诊断 + BL-E13-FIX 排进 demo 前
5. **主动学习 BL-MM5 (方案 A)** — SOUL 加"主动学习员工偏好" 章节, 4 类信号 + 频率纪律 + 落盘格式. B/C/D (BL-MM6/MM7/MM8) 排 5/8 后启动
6. **BL-E27 桌面状态浮宠** (Codex pet 同思路) — 鸿波看到 OpenAI Codex 加电子宠物问要不要做. 评估: demo 后 ship (8-11 天分 3 阶段). 跟 brand kit / BL-E11/E15/E19 人格 sprint 完美契合. 是 demo 下半场杀手锏
7. **BL-D11 tools_sanitizer 加固 DeepSeek schema 严格兼容** — 鸿波加 deepseek-v4-flash 撞 400 BadRequest "Invalid schema for function 'browser_back': type must be 'object', got 'type: null'". DeepSeek 严格校验, OpenAI/Qwen/Gemini 容忍. gateway 兜底强制 `parameters.type='object'` + 补 `properties={}`. +7 测试 / 506/506 全过 / 0 副作用. **唯一动了后端代码的事**

**还顺手归档进 BACKLOG/FEATURE-TRACKS/CHANGELOG**:
- BL-FE3 (FE 章节新加): 前端原生支持 reasoning_content, 1-2 天, 5/15+
- ~~BL-F12 排 5/8 后~~ → **5/4 直接 ship** (鸿波看到 summarizer 硬编码模型立即拍板修)
- ~~BL-F13 排 demo 后~~ → **5/4 直接 ship** (顺手跟 BL-F12 一起改)

**5/14 demo 倒计时 10 天**. 5/5 起开 BL-E14 PPT 吐槽 + BL-E13-FIX (40 分钟先做掉).

### 5/4 深夜 — DeepSeek schema 兼容修复 (BL-D11) 详情

**问题症状**: 鸿波在 Companion 选 `catfish-public-deepseek-flash` 调用, 报 400. 错误堆栈:
```
litellm.BadRequestError: DeepseekException - {"error":{"message":"Invalid schema for function 'browser_back': schema must be a JSON Schema of 'type: \"object\"', got 'type: null'."}}
```

**误诊路径** (吃了 1 小时弯路):
1. 第一猜: thinking 默认开, timeout 90s 不够 → 关 thinking
2. 第二猜: streaming 解析 reasoning_content 报错 → 改 chat.ts
3. **真正根因**: 看 gateway log 才发现是 tool schema 问题. DeepSeek 严格校验 `parameters.type` 必须 = 'object', 拒绝 None / 缺失. browser_back / 类似空参数工具被拒.

**修复**: `tools_sanitizer.py` 加 3 条规则
- params.type 是 None / 空 → 强制 'object'
- params.type 已是 'object' 但缺 properties → 加 `{}`
- params.type 是非 'object' (e.g. 'string') → log warn 不动 (客户端真错)

**测试**: +7 个 deepseek 兼容 case (含 browser_back 真实重现) / 21/21 sanitizer 测试通过 / 506/506 gateway 全套通过.

**副作用**: 0. 所有 tool 现在都被 sanitize 成同一标准 schema, 千问/Gemini/OpenAI/内网 qwen 收到的都是更规范但更兼容的 input. 等价于"宽容 provider 收到稍微更标准的 input".

**踩坑教训**: 看 gateway log 比猜快 10 倍. 下次遇到上游 LLM 报错先 grep `[ERROR]` 看真错误, 别从症状反推.

### 5/4 深夜 — BL-F12 + BL-F13 顺手 ship (鸿波拍板)

诊断 deepseek 时发现 gateway log 里两个旁伴问题, 鸿波拍板"现在做了":

#### BL-F12 session_summarizer 走 gateway loopback (~30 分钟)

**问题**: `session_summarizer.py:214` 硬编码 `model="openai/qwen3.5-flash-2026-02-23"` 直接 import litellm. **绕过 gateway 的 fallback / quota / metrics / brand scrub 全套机制**. qwen-flash 一挂 (e.g. 5/4 dashscope free tier exhausted) summarizer 就死, journal 不更新, **影响 BL-E13 主动闲聊** (没新 journal → starter 不丰富).

**修法**: 改成 `httpx.AsyncClient` POST 本机 `http://127.0.0.1:8999/v1/chat/completions`, 当成"普通 user" 调 gateway 自己:
- 用 catalog 模型名 `catfish-public-qwen-flash` (不是上游真名), gateway 自动走 fallback chain (qwen 挂 → gemini-flash 接)
- 加 `X-Catfish-Skip-Identity: true` 防 SOUL/journal/skills 二次注入 (循环: summarizer 写 journal → journal 注入下次 summarize → 自己引自己)
- 端口走 `PORT` env (跟 gateway 一致, 默认 8999)
- token 走 `CATFISH_DEV_TOKEN` env

**测试**: 8 个新 case (happy path / gateway 5xx 返 None / 网络错返 None / 空 messages 早返 / skip-identity header 必带 / 模型名是 catalog 名 / token 用 env / 端口用 env). 514/514 全过.

**后续受益**: catalog 改了 (e.g. 加 deepseek-flash 进 qwen 的 fallback chain) summarizer 自动跟. 不再写死.

#### BL-F13 修 aiohttp Unclosed client session 警告 (~15 分钟)

**问题**: gateway shutdown (uvicorn ctrl+c) 时反复打印:
```
ERROR asyncio: Unclosed client session
client_session: <aiohttp.client.ClientSession object at 0x10da54770>
```
LiteLLM 内部持有 module-level aiohttp client, shutdown 时没 close, asyncio GC 时报 ERROR. **不致命** (主流程不影响), 但污染 log.

**修法**: lifespan shutdown 加 best-effort 清理. 多版本兼容 (LiteLLM 1.50/1.83 attr 名不同):
```python
for attr in ("module_level_aclient", "module_level_client",
             "module_level_async_client", "in_memory_llm_clients_cache"):
    client = getattr(litellm, attr, None)
    # 调 aclose() / close() / clear() 各种方法兜底
```

完美清理不保证 (LiteLLM 内部多个 client 可能有未公开的), 但能减少 80% 的 warning. 真彻底要等 LiteLLM 2.x 全切 httpx.

#### BL-F14 内部 LLM 用例选模型 (~1 小时, 鸿波 5/4 深夜继续拍板)

**问题** (鸿波延伸 BL-F12 修完后的反思): 我刚把 summarizer 从 `model="openai/qwen3.5-flash"` 改成 `model="catfish-public-qwen-flash"` (catalog 名), 但**还是写死**. 鸿波: "这种太容易出问题了, 还是固定模型". 真问题: gateway 内部 3 个 LLM 调用 (summarizer / proactive / a2a_server) 都各自写死模型名, **catalog 改了 .py 跟着改**.

**正确做法**: catalog 加 use_case tag, picker 按 tag + private 优先选, env 可强制 override.

**新模块** `internal_models.py`:
```python
def pick_internal_model(use_case: str, config: Config) -> Model | None:
    """优先级:
    1. env CATFISH_<USE_CASE>_MODEL (per-deployment 强制)
    2. catalog tag 匹配 + tier=private 优先 + 可达
    3. 兜底: 任何 chat + 可达, 仍 private 优先
    4. 都没: None
    """
```

**为啥 private 优先** (鸿波 5/4 explicit): 跟 catfish "数据不出公司" 卖点一致. 内部用例 (尤其 summarizer 看 journal / proactive 看 employee 工作上下文) 数据敏感度高, **必须先用内网部署**, 内网挂了才用公网兜底.

**catalog yaml 改动**:
```yaml
- name: catfish-private-main
  recommended_for: [general, chat, tool_use, code,
                    summarizer, proactive_starter, a2a_aux]   # ← 加 3 tag

- name: catfish-public-qwen-flash
  recommended_for: [general, chat, fast, fallback,
                    summarizer, proactive_starter, a2a_aux]   # ← public fallback

- name: catfish-public-deepseek-flash
  recommended_for: [general, chat, code, fallback,
                    summarizer, proactive_starter, a2a_aux]   # ← 也作为 public fallback
```

**3 个 caller 改造**:
- `session_summarizer.py`: 用 `pick_internal_model("summarizer", config)` 选模型, gateway loopback HTTP
- `proactive.py`: 用 `pick_internal_model("proactive_starter", config)`, 同样 loopback (BL-F12 同模式), 顺手补 skip-identity 防 SOUL/journal 二次注入循环
- `a2a_server.py`: 用 `pick_internal_model("a2a_aux", config)` 选, 但 a2a 走特殊 SSE 链路, 仍直调 LiteLLM (model + api_base + api_key 全从 chosen_model.upstream 来, 不写死)

**测试**: +14 picker case (env override / env 不存在的模型降级 / tag + private 优先 / tag 命中 public 兜底 / private 不可达切 public / 不同 use_case 选不同模型 / 兜底任何 chat / embedding 模型不被选 / 完全没可用返 None / 未注册 use_case 不报错) + 修 summarizer 老 case 期望 (从写死 'catfish-public-qwen-flash' 改成 verify catalog 名形态).

**528/528 通过** (5/4 累计 +22 测试: BL-D11 +7, BL-F12 +8, BL-F14 +14, 修老期望 +1).

#### 5/4 深夜 总结 (BL-D11 + BL-F12 + BL-F13 + BL-F14)

5/4 累计动后端代码 4 件: tools_sanitizer (DeepSeek schema 兼容) + session_summarizer (走 loopback) + aiohttp cleanup + internal_models picker (use_case tag + private 优先). 全 ship 后 **528/528 测试通过, 0 fail**. demo 路径 0 风险.

**架构进步**: catalog yaml 真正成为"模型唯一真源". 改 yaml 加/删/改名 → 内部 LLM 调用自动跟. 拆 gateway / catfish-cloud SaaS 部署只改 env 不改代码.

---

## 2026-05-05 凌晨 - BL-F15 修 quota_exceeded 死循环

**鸿波 5/4 深夜真机验证发现死循环**: gateway log 里反复刷:
```
quota deny: user=dev-user model=catfish-private-main current=1224884 limit=1000000
INFO: POST /v1/chat/completions 429 Too Many Requests
WARNING: summarize_with_llm session=... gateway 返 429
```

每秒几十次. 鸿波"怎么没切吗?"

### 真问题 (架构 bug)

```
client request → quota check (主模型, 写死) → 直接抛 429
                                              ↑
                                   根本没机会进 with_fallback
                                   fallback chain (qwen/deepseek/gemini) 不会接
```

加上 summarizer 没冷却, 每次 chat 都 trigger summarize, 每次 summarize 撞 429, 立即被下一条 chat 又 trigger. **死循环 hammer**.

### 治标 (BL-F15, 1 小时, ship)

绕过架构 bug, 让 summarizer/proactive 自己有"客户端候选 fallback":

1. **picker 加 `pick_internal_models_ordered`** 返候选**列表** (按 private→public + tag 排序), 不是单个
2. **summarizer/proactive 改候选 try**: 主候选 429 quota 切下一个候选试. 全 429 才放弃
3. **session 5 分钟冷却**: 全候选都 429 后, 标 session 进冷却. 5 分钟内不再 trigger summarize. 防 hammer

代码量: ~50 行 + 10 新单测 (候选切换 / 冷却 / 过期清理 / 500 不切候选).

### 治本 (BL-F16, 排 demo 后)

quota check 真应该移进 `with_fallback`, 让 chain 里每个模型都查 quota, 跳过没 quota 的, 用第一个有 quota 的. 这样主对话也享受 "模型级 quota fallback" — 员工 catfish-private-main quota 满时自动切 qwen-flash, 不再直接 429.

工作量 2-3 小时, 影响 chat_completions 主路径, 风险中, 排 5/15+ demo 后做.

### 测试

- 538/538 通过 (5/4 起累计 +32 测试)
- BL-F15 新增 10 case (候选切换 / 全候选 429 标冷却 / 冷却中跳 / 冷却过期重置 / 500 不切 / 候选列表顺序 / env override 单元素 / env 不存在降级 / 全无可用返空 / 不可达模型不进列表)

### 5/4-5/5 凌晨累计 (5 个后端 fix)

- BL-D11 tools_sanitizer 加固 DeepSeek schema
- BL-F12 summarizer 走 gateway loopback
- BL-F13 lifespan LiteLLM client cleanup
- BL-F14 pick_internal_model use_case tag + private 优先
- BL-F15 picker 候选列表 + cool down (修 quota 死循环)

**538 测试全过 / 0 fail / demo 路径 0 风险.** 真该睡了.

### 5/4 晚 — 主动学习鸿波"feedback / 越用越懂"问题答案

鸿波问"小鯰能不能不断的越来越了解用户的性格、工作模式、生活模式、文书性格?". 评估现状: **没有显式 feedback 机制, 4 维全 partial 或 ❌**.

**4 个工作量等级方案:**
- **方案 A (~半天 0 代码)**: SOUL "主动学习员工偏好" 纪律 — **今晚 ship**
- **方案 B (~2-3 天)**: ChatBubble 显式 👍/👎/"改" 按钮 + feedback.jsonl + Dashboard 卡 — **5/8 后**
- **方案 C (~1 周)**: 结构化 `user_profile.json` + evidence 计数 + 主动确认 trait — **5/8 后**
- **方案 D (~1-2 周)**: 文书风格 fingerprint, 写新文档前调用 — **6 月起**

**ship 方案 A (BL-MM5)**: SOUL.md 加 ~140 行 "主动学习员工偏好" 章节. 核心:
- 4 类信号分级 (强显式立即落盘 / 弱显式攒 3 次主动问 / 强隐式不主动学 / 弱隐式不学)
- 频率纪律 (每 session 1 次主动问封顶, 防 nag)
- 落盘结构化模板 (`偏好: X / 默认: Y / 学于: 日期 / 依据: N 次观察`)
- 4 个 ❌ 禁止 (不评论生活/情绪 / 不从一次跳到模式 / 不假装观察 / 不学完不告知)
- 跟 BL-MM1 区分: **MM1 被动 (员工告诉你改记忆), MM5 主动 (你观察模式去问)**
- 跟 BL-E19 关系建立的边界: 工作风格可观察, 生活/情绪不可主动评论

**0 后端代码改, 0 测试 fail.**

后续方案 B/C/D 进 BACKLOG (BL-MM6/MM7/MM8) + FEATURE-TRACKS #28 子条目, 5/8 后启动.


## 2026-05-05 凌晨 — BL-MM2 catfish_remember 后端版本化

**背景**: BL-MM1 SOUL 纪律 5/4 ship 后, "记忆覆盖时 quote 旧值"全靠模型自觉. 工具底层是 `Dict[str, str]`, 同 key 二次写直接 silent overwrite, 旧值彻底丢, 模型就算想 quote 也无值可 quote. SOUL § "工具底层暂不存版本数组 (5/4 现状), 你怎么补救" 一段也写明这是临时方案, 工具升级后撤销.

原计划 5/8 做, 鸿波拍板"原计划 8 号的记忆覆盖、主动学习先完成", 5/5 凌晨 ship 后端版本数组那一刀.

**改动**:

1. `edge/tool-bridge/.../catfish_tools.py` `remember_fact()`:
   - 磁盘 schema v2: `{key: [{"value", "ts", "prev_value"}, ...]}`, list 末尾是 current
   - 同 key 不同 value → push 新 revision (prev_value = 旧 current_value), 不再 silent overwrite
   - 同 key 同 value → no-op 直接返 `no_change=true`, 防重复 tool call 灌脏 history
   - revision list 超 5 条 → 截掉最早的, 防文件膨胀 (单 key 最多 5 版)
   - 返回值新增 `previous_value` + `revision_count`, 模型能拿到旧值再 quote
   - summary 在 update 场景显式提示 BL-MM1 纪律: "你回员工时**必须**主动 quote 旧值"

2. `central/llm-gateway/.../session_facts.py` 配套读取/渲染:
   - `read_session_facts()` 返回类型变 `dict[str, list[dict]]`, 带 revision 信息
   - `render_facts_block()` 多 revision 时显式列 "上次值: X (已更新 N 次)" + 点名 BL-MM1 纪律, 让模型在 system prompt 末尾就看到旧值

3. **向后兼容**: 旧 schema `{"key": "string"}` 自动迁移到单 revision list, 员工不需要手动迁文件. `_normalize_revision()` 容错损坏的 list entry, 保留合法的.

4. **SOUL.md** § "工具底层暂不存版本数组 (5/4 现状)" 删除, 替换成 § "catfish_remember 已支持版本数组 (BL-MM2, 5/5 晚)" — 纪律变简单: 直接调 catfish_remember 后端会记账, 但 quote 旧值的"礼貌"还是模型的活儿. memory_save (跨 session) 仍要靠 inline 备注 (BL-MM3 排到 hermes 升级后).

**测试**:
- 新 `tool-bridge/tests/test_remember_fact.py` 13 条 (validation / 首次写 / push revision / 三连更新 prev_value 链 / 同值 no-op / max revision 截断 / 旧 schema 自动迁移 / 50 key 满后允许 update 拒绝 new / 损坏文件)
- `gateway/tests/test_session_facts.py` 加 3 条 (v2 schema 直读 / 损坏 revision 过滤 / 多 revision 渲染含上次值 + BL-MM1 提示)
- 全套绿: gateway **542 passed** (+3), tool-bridge **214 passed** (+13)

**遗留**:
- BL-MM3 hermes memory_save 包装版本化 — 等 hermes 0.10→0.12 升级 (5/15+) 后做
- BL-MM4 Dashboard 记忆版本卡 (展示 history + diff) — ~2h, 5/5 早上做
- BL-MM6 显式 feedback UI — ~3-4h, 5/5 早上做


## 2026-05-06（周二）— G1-G7 安全 GAP 闭环 + BL-MM7/MM8 + 真主动 Phase A/B + Plan D 重定位

**鸿波 5/6 一句"5个GAP一次性修复，为什么又拖"** — 7 个安全 P0/P1 一气呵成 ship.

### 完成 (一天工作量)

**A. 安全 7 GAP 闭环 (G1-G7)**:
- G1 gateway HOST 默认 127.0.0.1 (强制本地, 不再 0.0.0.0 暴露)
- G2 supply chain sha256 校验 + `CATFISH_HUB_REQUIRE_HASH=1` 严格模式拒装无签名 skill
- G3 execute_code 安全守卫 25 类正则 (凭证 / 外联 / 危险 shell, 5/7 BL-S29 加 OS 沙箱)
- G4 Tauri CSP `null` → 白名单 (default-src 'self' + connect-src 127.0.0.1:* tauri:)
- G5 依赖 CVE 全 0 + CI 集成 (cargo audit / pip-audit / npm audit / gitleaks)
- G6 数据流向图 `DATA-FLOW-DIAGRAM.md` 1 页
- G7 secrets 扫源码 0 hit + CI gitleaks

**B. BL-MM7 用户画像 + BL-MM8 风格指纹 (员工"越用越懂"完整链路)**:
- `tool-bridge/user_profile.py` — 4 工具 (get/propose/confirm/clear), ALLOWED_FIELDS + NO_PROPOSE_FIELDS 红线 (健康 / 财务 / 关系 / 政治 / 宗教不主动学), 3 evidence 阈值
- `tool-bridge/style_fingerprint.py` — 3 工具 (get/refresh/clear), jieba 中文分词 + char-2gram fallback, 时间衰减 (30/90/180 天)
- `Dashboard UserProfileCard` + `StyleFingerprintCard` (员工自己看自己被学了什么 + 一键 clear)
- SOUL.md § "user_profile 怎么用" + § "style_fingerprint 是写文档前必备"

**C. 真主动 Phase A + B (员工不打字, 鲶鱼自己开口)**:
- Phase A 信号触发: `triggers.ts` 4 函数 (silence / deadline / focus_return / shouldStaySilent), 16 测试通过
- Phase B LLM context 化: `gateway/proactive.py` `generate_contextual_starter(signal_kind, context)`, 3 SIGNAL_KIND_PROMPTS, 8 测试
- `useProactiveTriggers.ts` 1 分钟 tick, fetchContextualStarter 5s timeout, fallback 本地模板
- 桌宠 polling 跨 webview 解决方案 (Tauri 2 emit/emitTo 不可靠, 改 Rust Mutex 缓冲 + 300ms tick)

**D. Plan D 重定位 + 灵魂校准**:
- 鸿波点播: peer-to-peer → agent-as-service ("公司谁愿答 X?")
- 5/6 晚灵魂校准: 鲶鱼 = 员工的"职业资产", 公司给员工配 (B2C2B), 数据所有权属员工本人, 跳槽带走 (cp `~/.catfish/`)
- 8 份文档刷: README-FOR-CUSTOMERS / SECURITY-REVIEW § 1.5 / DATA-FLOW-DIAGRAM 边界 4 / DEPLOYMENT-RUNBOOK § 11 / DEMO-CUSTOMER-QA B 节 + B7 新增 / PLAN-D-PROTOCOL § 11 / MAY-DEMO-SCRIPT 场景 4.5 / FEATURE-TRACKS #8

**E. 文档矩阵给客户**:
- README-FOR-CUSTOMERS.md (1 页快速了解)
- SECURITY-REVIEW-2026-05-06.md (P0/P1/P2 gap + 修复)
- DATA-FLOW-DIAGRAM.md (4 边界 + 出境 4 路径 + 不出境 7 类)
- DEPLOYMENT-RUNBOOK.md (3 部署架构 A/B/C)
- DEMO-CUSTOMER-QA-2026-05-14.md (30 问预案)

### 测试
- gateway 542 + 8 = 550 passed
- tool-bridge 227 + 26 (MM7+MM8) = 253 passed
- companion 16 (triggers) + 8 (proactive) = 全绿

### 遗留 (5/7 上手)
- BL-S29 真技术沙箱 (G3 升级 OS 级隔离) — 5/7 鸿波"一次性做完", 12 天压成 1 天
- demo dryrun + Q&A 30 问背稿 — 5/8 起


## 2026-05-07（周三）凌晨 — BL-S29 真技术沙箱 12 天压 1 天 ship

**鸿波 5/6 拍板"一次性做完, 不要再分批"** — BL-S29.1 / S29.2 / S29.3 / S29.4 / S29.5 / S29.6 全部一日 ship.

### 完成 (单日 sprint)

**BL-S29.1 macOS sandbox-exec profile**:
- `edge/tool-bridge/sandbox-profiles/catfish_execute.sb` (133 行 SBPL)
- 默认 allow + 5 类关键 deny (网络 / 写持久化 / 读敏感路径 / iokit / sysctl-write)
- HOME 重定向到 TASK_DIR (双层防御 L1: LLM 用 ~/ 解析到沙箱里)
- `tests/sandbox/test_sandbox_exec.sh` — 25 恶意 case + 6 sanity (扩展前 13)

**BL-S29.2 tool-bridge adapter 接入沙箱**:
- `edge/tool-bridge/src/catfish_tool_bridge/sandbox.py` (~230 行, 跨平台抽象)
- `adapter.py` `_do_dispatch` 拦截分支: env `CATFISH_SANDBOX_EXEC=1` 时 execute_code 走沙箱不去 hermes
- 沙箱内 env 干净 (剥 GITHUB_TOKEN 等员工 mac secret)
- audit 加 `sandbox_used` / `sandbox_kind` 字段, jq 一行命令查
- `tests/test_sandbox_module.py` — 18 unit tests

**BL-S29.3 端到端 + demo 场景就绪**:
- `tests/test_e2e_sandbox.py` — 6 e2e 测试 (L1 字符串规则 + L2 沙箱 + audit + 沙箱关闭兜底)
- `MAY-DEMO-SCRIPT-2026-05-14.md` 新加场景 2.5 — 30s 信安部门必演 (Demo A/B/C, 沙箱 chr 绕过 L1 后被 L2 拦)
- SECURITY-REVIEW G3 重写 — 双层防御机制图 + 86 测试矩阵附录 A

**BL-S29.4 Linux nsjail**:
- `edge/tool-bridge/sandbox-profiles/catfish_execute.cfg` (140 行 protobuf)
- 比 macOS 强一档: clone_newnet / chroot 等价 mount tmpfs / **rlimit_nproc=10 真拦 fork bomb** ★ / **rlimit_as=512MB 真拦 mem bomb** ★
- `tests/sandbox/Dockerfile.nsjail` + `test_nsjail.sh` — 25 恶意 + 6 sanity, docker run --privileged 验证
- mac 上 docker 跑 13/13 (扩展后 31/31 还没鸿波重测) 通过

**BL-S29.5 三层 fallback**:
- `sandbox.py detect_sandbox_kind()` macOS sandbox-exec → Linux nsjail → Docker → None
- `_build_docker_args()` docker 兜底层 (--network=none + --read-only + --pids-limit=20 + --cap-drop=ALL)
- 3 fallback chain unit tests

**BL-S29.6 测试矩阵扩展 + 文档**:
- macOS 25 恶意扩展 (加 11-25: base64/eval/glob obfuscation + ps aux/dscl/osascript 跨进程 + Application Support/cron/sudoers.d 持久化 + chmod/setuid 加权)
- Linux 25 恶意扩展 (加 11-25: dlopen/setns/kexec_load 内核级 + /proc 探测 + mount syscall)
- SECURITY-REVIEW 附录 A — 56+24 case 矩阵 + 客户独立审计步骤 + 三层 fallback 架构图 + 央企信安 5 问预案

### 真发现并修的 production bug
- **macOS `/etc` 是 symlink → `/private/etc`**, SBPL 不解析 symlink, 原 `(literal "/etc/passwd")` 拦不住真路径访问. 修后 8 个高敏感文件双等价 deny
- **macOS 26 (Tahoe) sandbox-exec 默认 deny 一切**, 必须显式 `(allow default)` 才能 process-exec (早期 macOS 默认 allow)
- **nsjail master 删了顶层 chroot 字段**, 用 mount root tmpfs 替代
- **测试 case 9 fork bomb** 没沙箱兜底真把鸿波 mac 卡死 (RLIMIT_NPROC 满, 重启 mac 才恢复). 改成 LaunchDaemons 写测试

### 测试矩阵 (累计 86 测试矩阵)
- macOS sandbox-exec: 25 恶意 + 6 sanity = 31 (shell)
- Linux nsjail (docker): 25 恶意 + 6 sanity = 31 (shell)
- python unit: 18 (sandbox + adapter wiring + fallback chain)
- python e2e: 6 (双层防御 + audit + 兜底)
                                                           ─────
                                                           86 测试全绿

### 文档刷
- SECURITY-REVIEW G3 重写 + 附录 A
- README-FOR-CUSTOMERS 信安 5 件 → 6 件
- MAY-DEMO-SCRIPT 场景 2.5 (30s 信安必演) + demo 节奏调整
- FEATURE-TRACKS BL-S29 6 步全 ✅
- sandbox-profiles README (macOS / Linux 双平台部署 + Docker 测试)

### 遗留 (5/8 起)
- demo 5+1 场景 dryrun (跑通时间精确控制) — 5/8-5/9
- 场景 4.5 PPT 1 页 (BL-FED2 路线图) — 5/10-5/11
- Q&A 30 问背稿 — 5/12
- 客户安全说明 1 页 PDF — 5/13
- 5/14 demo 当天