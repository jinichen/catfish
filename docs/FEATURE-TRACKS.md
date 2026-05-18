# 鲶鱼 · Feature Tracks (主入口)

> **快照**: 2026-05-12 (周二深夜) · **维护人**: 鸿波 · **更新**: 每周日晚 + 重大 ship 时
> **角色**: 这是**唯一**的"我们在做啥 / 还差啥 / 在哪个 phase"主入口.
> 其他 doc 角色见底部 § 文档地图.

---

## 🚦 Phase 进度 (一行看清)

```
Phase 1 · 单员工 AI 副手           [██████████] 100%  · 5/12 BL-MM9-FREEZE-v2 教学→凝固→复用闭环跑通 (eis-login 2.3s 复用 + eis-checkin SOP 第二次验证)
Phase 2 · 团队版 (SSO/RBAC/Win)    [██████████] 99%   · 5/10 凌晨 4 个中央 service PG 统一 + 5/12 GitHub Actions CI 落地 (1257 测试零失败)
Phase 3 · ★ Federation             [█████████░] 95%   · Plan D v0.1 ✅ + 5/6 重定位 + 5/12 BL-FED2.1-2.6 全 ship ✅ (6 个 sub 一日内连发 + demo 真跑通, 剩 5% 是实时 Companion 弹窗 P1)
                                                          + Hermes 0.13 对齐建议清单落档 docs/HERMES-013-ALIGN.md (5/18 升级 sprint 拿来对)
Phase 4 · 集团级 mesh               [░░░░░░░░░░]  0%   · 2027 Q2+

★★★ 5/12 真闭环跑通 (BL-MM9-FREEZE-v2): catfish 卖点 "员工教一次 → 凝固成 skill → 下次秒开"
  从 PPT 概念变成可演示资产. trace_recorder + skill_freeze 两个新模块, 显式 teach session
  边界 (start/end 物理隔离), 凝固出可执行 script.py + SKILL.md, 自动同步 hermes.
  **eis-login 复用 2.3 秒秒过**. v2.1 chrome 状态预检 + v2.2 嵌套调 skill (eis-checkin 6 句
  替代 11 句). SOUL.md 加 ★★★ 教学边界铁律 + skill 失败不许降级铁律. 教学 SOP 文档
  (3 个完整例子) + 60 单元测试 + GitHub Actions CI workflow + Companion UX1/UX2
  (streaming 中不再"锁死"). 1257 测试零失败.

★ 5/10 凌晨架构决策 (BL-ARCH1): 5/15 起做 catfish-web 中央门户. Companion 瘦身.

★ 5/11 深夜战略重置 (BL-Q3-WEBSKILL): demo 主轴 "员工教 catfish 一次, catfish 凝固成 skill,
  全公司秒开". 5/12 真把这条路打通: 上午撞 12 次坑 → 中午鸿波拍板"彻底解决, 不要小打小闹"
  → 下午彻底重做 → 13:30 跑通真闭环. 跟传统 RPA (UiPath/用友) 差异: IT 写脚本 3 周 →
  员工教学 10 分钟自动生成 script.py + SKILL.md.
```

> 📈 5/2-5/3 周末 sprint 大幅推进 (Phase 2 后端 + Skills Hub + brand kit + 关系建立):
>   - Phase 1: 92% → 99% (+ project-approval skill / 主动闲聊 / Quota 真接 chat / brand kit v1 /
>     命名权 / 专注模式 / 情绪关系建立)
>   - Phase 2: 35% → 78% (+ Dashboard 角色化 / 多账号 / PG 完整双写 / alembic 双服务 /
>     Quota 100% / Manager PUT UI / Admin 全局聚合 / 中央 Skills Hub MVP / dry-run + dedup /
>     SSO 登录页 brand 升级 + 防泄漏)
>   - 5/3 晚 1 (brand): 完整 brand kit (mascot / mark / app icon / 47 个 PNG / BRAND.md /
>     tokens 升级 / 6 处占位 🐟 全替换 / 防 hermes 泄漏 dispatch scrub + SOUL 铁律)
>   - 5/3 晚 2 (人格 sprint, 4 个里 ship 3 个): **BL-E11** 命名权 (员工自定义鲶鱼名 + 3 档人设) +
>     **BL-E15** 专注模式 (Cmd+Shift+F 全屏伪 IDE) + **BL-E19** 关系建立 (SOUL 情绪铁律 + session_meta
>     时间感 + Dashboard "鲶鱼对你的印象" 透明可删) — 留 BL-E14 PPT 吐槽下周做.
>     ⚠ 注: 5/3 commit 把 E19 误叫 E16, 实际 BACKLOG BL-E16 是"社交健康检查". 后续以 E19 为准
>   - 5/4 晚 1 (Onboarding 同事感): BL-E11 后续 — 11 处 UI 自指 ("鲶鱼" / "小鲶") 全换员工自定义 dynamic name
>   - 5/4 晚 2 (Hermes 升级研究 + Curator 分析): 不动代码, 写 `docs/HERMES-UPGRADE.md` ~430 行 — 含
>     0.10→0.12 完整 changelog 摘要 + 4 层补丁脆性评估 + Curator 接口 verified + 集成方案 5 步 +
>     5/8 后议程时间表
>   - 5/4 晚 3 (记忆纪律): **BL-MM1** SOUL 加"覆盖前 read-then-write + quote 旧值"铁律 (~130 行).
>     0 代码, 修小鲶之前对话里"夸了'现在就能做'"问题
>   - 5/4 深夜 4 (DeepSeek tool schema 兼容): **BL-D11** tools_sanitizer 加固 — DeepSeek V4 严格校验
>     `parameters.type` 必须 'object', 拒绝 None / 缺失. browser_back 等空参数工具被拒.
>     gateway sanitizer 兜底强制 type='object' + 缺 properties → {}. **+7 测试 / 506/506 全过 / 0 副作用**.
>     demo 后 BL-FE3 加前端 reasoning_content 原生支持, 配合 deepseek thinking 重开
>
> 📈 **5/8 (周五一日 33+ commit)** — 视觉 demo 14 BL-FIX + Windows 客户端 8 BL-WIN + skill 自进化闭环 MM13/14/15 + 收尾 7 BL-FIX (UI 整体优化):
>   - **上半天 (BL-FIX2-15, 14 个 BL)**: 5/14 demo 卖点 "员工说'登录 EIS', catfish 自动识别验证码" — 早 8 点撞 BadRequest 400 (空 reason Go gRPC 风格), 一路追到 12 处 bug. 16:30 真跑通 (Qwen3.5 122B 主力识码 "S2CB" 准确). 关键修法:
>     - **FIX2** `multimodal_tool_unwrap` (gateway): tool 含图重组到 user multipart message — Qwen Go gRPC adapter 不接受 role=tool 含 multimodal, 改写 tool 留路径元数据 + 紧接插 user multipart [{type:text}, {type:image_url}]. **基础设施级修法**, 任何含图的 tool 都走这条.
>     - **FIX4 + FIX5**: dedupe hermes builtin browser_* 12 条 + 同步 scrub 历史 tool_calls (LLM 训练分布最熟 hermes browser_vision, 但 catfish 没配 vision provider → 撞 400). 一刀切去重, 历史里残留 tool_call 也清.
>     - **FIX6 + FIX8**: tool_retry_hint / self_critique role 'system → user' (Qwen 中段 system 撞 400, 末尾 user 等价于"员工又说一句").
>     - **FIX7** `catfish_browser_screenshot` (鸿波诊断 "之前都用 Playwright 截图给模型就能识别"): 加 Playwright `page.screenshot()` 工具, 同 connect_over_cdp 链路, 替代 hermes 一刀切丢的 browser_screenshot.
>     - **FIX9** snapshot max_elements 200→500 cap 1000 + truncated hint_for_llm (鸿波诊断 "max_elements=50 不够吧"): LLM 偷懒减小, CAS 登录 nav link 多, 登录 button 挤出. 默认大 + truncated 引导加大不减小. tool description 写明.
>     - **FIX10 + FIX11**: Playwright 硬 timeout wrapper. tool-bridge 单线程被卡死时 Companion 停止按钮也失效. ThreadPoolExecutor + future.result(timeout=) + finally shutdown(wait=False) leak 卡死线程. FIX10 写错被自己 with 块 shutdown(wait=True) 等死, FIX11 修 — **加真硬 timeout test 防 regression**.
>     - **FIX13** prompt_security regex 误报修 (鸿波 "我今天没输入明文为啥提示?"): 中文 `密码[是为:\s]+\S+` 把 \s 塞进 separator 集合, 跟 docstring 设计 (`密码[是为:][\s]*\S+`) 不符. "用户名、密码、 验证码" 这种正常陈述句撞误报. 改 separator 必须 是/为/: 之一.
>     - **FIX12** weekly-report 不丢桌面 (鸿波 "生成的文件不要放在桌面上, 乱的很"): 默认 ~/Desktop/ → ~/.catfish/output/<日期>/<时间>_周报-<员工>/, 跟其他 skill 归档对齐.
>     - **FIX14 + FIX15** (Companion UI): audit tip 阈值≥3+计数+dismiss / ProactiveCard / TasksCard 高度对齐.
>     - 测试: gateway 661 → 677 (+16) / tool-bridge 341 → 364 (+23) / weekly-report 17, 全过.
>   - **下半天 (BL-WIN1-9.2, 8 个 BL)** — 鸿波 Tokyo 9pm "现在去把 Windows 客户端完成". Windows 客户端从 0 到 demo-ready:
>     - **WIN1** mac → Windows cross-build (`.cargo/config.toml` mingw-w64 + scripts/build-windows.sh): 出 30MB `catfish-companion-app.exe`, 0 error 0 warning (经 WIN1.1/1.2/1.3 一路修 cfg(unix) gate / dead-code).
>     - **WIN8** tool-bridge IPC TCP localhost 替代 unix socket: Python server.py `os.name=='nt'` 时走 `asyncio.start_server('127.0.0.1', port=0)` + 端口写文件; Rust call_rpc cfg 分流 UnixStream/TcpStream. 跟 named pipe 比 TCP 简单, loopback 安全等价.
>     - **WIN3** Chrome 路径自动检测扩 12 候选 (per-user `%LOCALAPPDATA%\Google` + 双架构 Program Files + Edge Win11 fallback) + `CATFISH_CHROME_BIN` env 强制覆盖.
>     - **WIN2** secret_resolver `wincred://` 真实现: 优先 Python keyring → fallback PowerShell Get-Secret → friendly error 含 cmdkey/Set-Secret 教程. pyproject.toml 加 `keyring>=24; sys_platform == 'win32'`.
>     - **WIN9 / DEPLOY1** 网关地址走 yaml 配置 (鸿波 "网关装中央服务器 mac/Win 怎么设地址?"): mac .app + Win .exe 双击启动**都不读 shell env**, 走 `~/.catfish/companion.yaml`. 3 端联动 — Rust endpoints.rs 加 yaml 解析 + 新 Tauri command `get_runtime_endpoints` + 前端 `bootstrapEndpoints()` 启动写回 config.gatewayUrl. 客户改 yaml 重启即生效, 不需要重新打包.
>     - **WIN9.1** README-deploy 补 Windows 具体步骤 (鸿波 "我没看出来"): 部署形态 A (纯聊天 只 .exe + yaml) vs B (完整 .exe+Python+Chrome) + Windows 路径例 + 文件管理器步骤 + console.info 验证.
>     - **WIN9.2** yaml 默认 audience 'test' → 'catfish-companion' (生产部署会 401 aud mismatch 隐性 bug, 顺手修).
>     - Windows 真机能跑: chat / 仪表盘 / SSO / 浏览器自动化 / 截图 / skill / wincred / yaml 配置. 还差: outlook (WIN4) / Windows Service daemon (WIN5) / MSI 签名 (WIN6) / sandbox (WIN7) — 真上线分批做.
>   - **深夜 23:00-2:00 (BL-MM13/14/15 + 收尾 BL-FIX16-22, 10 个 BL)** — 鸿波"现在就开始做 13、14、15 一起完成不留尾巴". skill 自进化对称半边补齐:
>     - **MM13** `catfish_propose_skill_revision` 工具 (~150 行 + 14 单测): SemVer 校验 + red-line 字段冻 + 24h ≤3/session 限频. 写 `~/.catfish/skill_revisions.jsonl` 等员工 accept, **不直接改 skill 文件**. 跟 MM9 propose_new_skill 对称.
>     - **MM14** Dashboard `SkillRevisionCard` (~455 行) 三段 UI: pending / effectiveness_due / recent_resolved. 30s polling. accept 时 fetch 当前 quality_score 存 baseline. 4 Tauri commands.
>     - **MM15** 14 天有效性跟踪: accept 后比当前 quality_score vs baseline. ≥+10 improved (✅ 真改善) / ≤-10 regressed (❌ 反而坏了, 建议回退) / 中间 neutral. **闭环成立**: 鲶鱼自己看到回退建议会主动反向 propose, 不是只发声.
>     - **FIX16** `catfish_browser_find_by_text` (鸿波 "还是一样找不到登录按钮"): CAS 登录 button 是非标准 `<a class="btn" onclick>`, snapshot a11y 不报. JS evaluate 扫 button/a/[onclick]/[role=button]/[class*=btn] 按可见文字匹配, LLM 直接 find_by_text("登录") → selector → click.
>     - **FIX17** `catfish_browser_screenshot` 智能压缩 (鸿波 "截图必须要压缩否则一定卡死"): 4MB PNG 经 IPC → context → LLM 一连串撑爆. 三档: element 不压, viewport max 1280px JPEG q80, full_page max 1600px JPEG q75. 超 800KB 降级 q60. +9 单测.
>     - **FIX18** ServicesCard tooltip 出界改 inline 副标题 (HTML native `title=` 冒出去糊到 QuotaCard).
>     - **FIX19** Dashboard 内容控制框内 (鸿波 "不要左右移动"): `.app-main` overflow-x: hidden + grid 改 `minmax(0, 1fr)` 强制最小 0, 长 URL 不再撑爆.
>     - **FIX20** 仪表盘 UI 整体优化 (鸿波 "内容太多"): 响应式 grid `repeat(auto-fit, minmax(280px, 1fr))` (宽 3 列 / 中 2 列 / 窄 1 列) + section 标题 uppercase 轻量 + maxWidth 1600 居中.
>     - **FIX21** SkillRevisionCard 空状态全宽 banner (鸿波 "半截很难看"): `gridColumn: '1 / -1'`.
>     - **FIX22** CuratorCard 整卡全宽 (鸿波 "脚本整理也是同样的问题"): 跟 FIX21 同款修法.
>   - 测试 1097+ passing (gateway 677 + tool-bridge 403 + weekly-report 17 + cross-build clean).
>   - 鸿波诊断功劳: 23 个补丁里 6 个 (FIX7/9/12/13/16/17) + 4 个视觉 (FIX18/19/21/22) 是鸿波直接指根因 / 提需求, 比我顺症状追快. 教训: tool 描述 + 默认参数也是 LLM 行为的一部分; 一刀切去重前先看 LLM 还有没有等价能力 tool; UI 视觉对称 (奇数卡半格旁边空白) 同款修法批量处理.

> 📈 **5/10 (周日凌晨 23:00-04:30 五个半小时)** — OAuth 全链路真打通 + 4 个中央 service PG 统一收尾 + Skills Hub 集成闭环 + 架构反思定调 (18 件事, ~3000 行新增):
>   - **18 件事一夜**: BL-FIX27~38 (12 个 OAuth/quota/认证修) + BL-FIX35.1 (漏改 inline 补) + BL-D2 (Skills Hub 4 件并发: gateway 反代 + Companion Card + publish 工具 + OIDC 鉴权) + BL-D2 Phase 2 (skills-hub 元数据迁 PG) + BL-D3 fix5 (mcp-registry / skills-hub _load_dotenv 隐性 bug) + BL-FIX36 (SOUL BL-MM5 段 memory_save → catfish_user_profile_propose + 历史 660 条偏好句子 journal 迁移脚本 v2 winner-only confirm) + 架构决策 BL-ARCH1.
>   - **真根因 BL-FIX32 ⭐**: macOS Keychain 在 unsigned dev binary (cargo run target/debug, 没 codesign) 下 keyring crate set_password **报 success 但实际 silent no-op**. log 一直打 "OAuth login OK", 但 security find-generic-password 永远 NoEntry. 前 5 次绕路 (FIX26/28/29/30/31) 全是被这条绿灯 log 引导的下游误判. 改文件存储 ~/.catfish/oauth/ 0600 绕开. **教训: "save 看似成功但 read 永远空" 直接怀疑存储后端本身, 不要先假设链路下游**.
>   - **OAuth 端到端真闭环**: Companion 点登录 → 浏览器 SSO (127.0.0.1:8998) → identity-server 颁 id_token (sub=chenhongbo@ffcs.cn, aud=catfish-companion) → ~/.catfish/oauth/ 文件存 → me.getToken/chat.getToken priority 0 → gateway OIDC 验签通过 → litellm streaming + stream_options.include_usage=True → PG quota_events / gateway_audit 真员工身份落盘. 5/2 起 8 天所有 chat 挂 dev-user@catfish.dev 的虚构 user 全清.
>   - **4 个中央 service PG 统一**: identity (users / registry_agents) + gateway (quota_events / gateway_audit) + mcp-registry (mcp_subscriptions / mcp_audit) + **skills-hub (skills_versions / skills_audit, 5/10 凌晨新加)** 共享同一 catfish PG, 4 个 alembic_version_* 版本表共存. **设计**: 元数据 PG (索引 / cross-service join), 文件内容继续 FS, Phase 3 切 S3/MinIO 改 _content_url 一行.
>   - **Skills Hub 集成闭环 (BL-D2)**: 5/2 后端 887 行 ship 但 10 个集成口子全断, 鸿波 5/10 "做完了吗?" 触发审计. 4 件并发: gateway/skills_hub_proxy.py 反代 + skills-hub require_user 信 X-Catfish-User-* + Companion SkillsHubCard.tsx + tool-bridge catfish_skill_publish (含 6 类凭据正则扫描拒上传). 验证 publish 真员工 chenhongbo 落 PG.
>   - **画像 6 项真显示 (BL-FIX36)**: 真因 SOUL.md BL-MM5 段 (5/4 写, 教用 memory_save) + BL-MM7 段 (5/6 加, 教用 catfish_user_profile_propose) **两套并存 8 天**, LLM 偏向先看到的 BL-MM5 → 偏好全写 hermes memory (其实也没写, 实际只在 employee_journal "员工偏好 X" 句子里), user_profile.json 一字未写. A 改 SOUL 4 处示例 + 9 字段映射表; B 写 migrate_journal_to_profile.py 30 条规则 + winner-only confirm + 收紧"长" 规则, 660 句迁出 6 个 confirm trait (急 / 直接 / 结果导向 / 短 / 对照表 / 先看摘要), 完美贴合鸿波风格.
>   - **架构反思 BL-ARCH1 / 2 (鸿波 4:00 challenge)**: "中央复杂, 都塞客户端不合适?" → "方案 B 都中央化是不是跟初衷背离?" → "中央功能 WEB 化, 助手客户端化". 业界标杆 (VSCode + GitHub / Cursor + cursor.sh / 1Password + 1password.com / Slack + admin.slack.com) 都是 **客户端 = "我"的体验, web = "组织/管理"的体验**. 决策: 5/15 起做 catfish-web 中央门户 (Skills Hub 全广场 / MCP 市场 / Manager 视图 / Admin 后台), Companion 瘦身留 10 个左右"我的"卡 (Identity / Quota / 画像 / 我的 skill / 桌宠 / 快捷键 / 语音). 客户端跟初衷对齐 (本机 / 离线 / 数据不出端), web 是中央门户.
>   - **鸿波诊断 7 条** (5/10 凌晨):
>     1. "为什么放在 sqlite, 这个奇怪" → 推动查 PG 路径, 发现 quota_events 全 dev-user
>     2. "你应该把所有的测试账号删除, 也没用测试通道" → 砍 FIX28 治标方案, 强制走真 OIDC, 暴露 keychain silent fail (FIX32 真根因)
>     3. "你要不猜谜语了, 要仔细的分析" → 强制让我停下基于 log 推理, 让 npm run tauri dev tee 到日志看 OAuth 真实步骤, 才看清 "OAuth login OK" 跟 keychain NoEntry 的矛盾
>     4. "central/skills-hub 做完了吗?" → 触发 10 个集成口子审计 + BL-D2 4 件并发 ship
>     5. "数据库是不是也要切到 PG?" → BL-D2 Phase 2 PG 统一收尾
>     6. "数据库连接没有写到 .env?" → BL-D3 fix5 隐性 bug (mcp-registry 5/9 起一直跑 sqlite 没人发现)
>     7. "中央很复杂, 都塞客户端不合适?" + "方案 B 跟初衷背离?" + "中央 WEB 化, 助手客户端化" → BL-ARCH1/2 架构演进路径定调
>   - 测试 / 验证: PG 4 个 service 9 张表全跑通 / curl publish hello-pg 200 / dashboard 画像 6 项真显示 / Companion release dmg 已 bundle / 5/14 demo 主线全就位.

> 📈 **5/12 (周二一整天)** — BL-MM9-FREEZE-v2 真闭环跑通 + BL-COMPANION-UX1/UX2 + CI + 60 单元测试 + 修 22 个预存 fail. 一天清出一周量的活儿:
>   - **BL-MM9-FREEZE 教学→凝固→复用真闭环**: 5/11 写过的 eis-login SKILL.md 其实是我代笔手工凝固. 5/12 鸿波拍板"鲶鱼能不能跑一遍就凝固成 skill, 这才是 BL-MM9 真承诺". 早上叠 fix 撞 12 次坑 (procedural skill 路径 / cold start / chrome 状态 / 死循环 retry) → 鸿波 11:30 拍板"不要小打小闹, 要彻底解决" → 中午一次性 commit BL-MM9-FREEZE v1 (删 BL-FIX47 / 加 CATFISH_LEAN_INJECT 总开关 / 新建 trace_recorder + skill_freeze + 3 个新 tool) → 12:21 测试又撞坑 (LLM 没主动调 catfish_teach_start, 7 步白教) → v2 加 SOUL.md ★★★ 教学边界铁律 (强触发关键词自动 teach_start) + skill 失败铁律 (不许 LLM 降级手工) → 13:14:34 鸿波第二次教学 7 步 ok=7/7 干净凝固 → 13:30 新会话一句话"上 EIS 看待办" → catfish_run_skill 2.3 秒报告 6 条待办. **BL-MM9 卖点正式从 PPT 变成可演示资产**.
>   - **trace_recorder.py + skill_freeze.py 两个新模块** (~900 行): 显式 teach session 边界 (start_session / end_session), 物理隔离教学 vs 复用/探索. 模板化 script.py 生成 (走 dispatch_native 复用 catfish_browser_* 内部基建, 不自己开 Playwright). captcha 数据流依赖识别 (`fill('#captcha', 'cT92')` 改成 `text=captcha_text` 变量). secret_ref 透传, 明文密码拒凝固 (#pwd selector + 8-32 字符 + 含数字字母启发拒). 嵌套 _DepthGuard 防 script.py 内部 dispatch 又被录.
>   - **v2.1 chrome 状态预检** (5/12 鸿波 13:38 撞坑修): script.py 第一步 goto 插入 actual_url 校验. chrome 已登录 redirect 到非预期 URL → 报清楚错误 + 引导员工重启 chrome, 不再继续 fill 撞 30s timeout.
>   - **v2.2 嵌套调 skill** (rapid skill 扩展): trace_recorder RECORDED_TOOLS 加 catfish_run_skill, freeze 引擎模板化 `_call("catfish_run_skill", ...)`. eis-checkin 教学 11 句 → 6 句, 第一句 `catfish_run_skill('department/eis-login')` 替代 7 步登录. eis-login 升级后所有依赖它的 skill 自动升级.
>   - **CATFISH_LEAN_INJECT 总开关**: 教学场景关掉 10 个干扰 inject (session_facts / stats_guard / skill_guard / session_history / employee_journal / feedback / tool_retry_hint / self_critique / duplicate_tool_call_guard / BL-FIX23 L8 retry). 保留 5 个核心 (identity / skills_catalog / session_goal / session_meta / prompt_security). 默认 LEAN=0 不破老行为.
>   - **3 个新 native tool**: `catfish_teach_start` / `catfish_teach_end` / `catfish_freeze_skill` (+ `_inspect` / `_rotate` 辅助). SOUL.md 加 ★★★ 教学边界铁律段 (强触发关键词清单 / 自检 3 秒 / 正反例 5/12 12:21 撞坑写进 ❌ 案例).
>   - **真凝固出来的 2 个 skill 落 git**: eis-login (13:26:39, steps 1-7 ok=7/7) + eis-checkin (16:11:17, 8 步 ok=8/8, SOP v1 第二次验证).
>   - **教学 SOP v1.1 文档** (`docs/TEACHING-SOP.md`, 305 行): 5 步 SOP + 3 个完整教学剧本 (eis-checkin v2.2 嵌套写法 / eis-checkout 改 1 行 / 高级模板 复用 eis-login 教别的 EIS 操作) + 常见坑 9 项对照表 (每个今天撞的坑对应 SOP 哪步漏了 + 怎么避).
>   - **BL-COMPANION-UX1 streaming 中 "⏹ 停下接着发"**: 鸿波 14:00 抱怨"对话内容区可以滚动, 其他都不能操作". 修法 ChatInput 三态按钮 + useChat.cancelAndSend (abort → sleep 200ms → send 新). Enter 键同款.
>   - **BL-COMPANION-UX2 左侧列表 streaming 中可切换**: 点别的会话 → 自动 cancel 当前 stream + 等 200ms cleanup + loadSession. "+ 新对话"按钮同理. 列表 tooltip + 底部小字提示.
>   - **GitHub Actions CI workflow** (`.github/workflows/ci.yml`): 3 个 job (tool-bridge 458 tests / gateway 799 tests / companion tsc+vite build) + ci-pass gate. PR / push main 自动跑, 并发取消老 run.
>   - **单元测试 60 个新加**: trace_recorder (21) / skill_freeze (23, 含 v2.1/v2.2 增强) / lean_inject (17). 修 22 个预存 fail (沙箱无 psycopg / yaml.default 段 / sysadmin 角色 / internal_dev_token / _has_completion_claim '已经'). **tool-bridge 458 passed + gateway 799 passed = 1257 测试零失败**.
>   - **鸿波诊断功劳 (5/12 五次关键)**: (1) 上午 "你这样改代码的方式, 其他的也不能达到目标？" — 打破我叠补丁惯性; (2) "不要管 5/14 的事, 没用的代码要删掉" — 拍板真活儿; (3) "彻底解决" — v1 翻车后拍板 v2 显式 session 边界; (4) 12:21 撞坑 "我们能不能跑一遍就凝固成 skill" — 把 v2 推到 SOUL.md 铁律层; (5) "下次新 skill 是不是都要规范教法不然撞坑?" — 拍板写 SOP 文档.
>   - **教训**: (1) **打补丁 vs 治本 — 5/11 撞坑 13 次 + 5/12 上午 12 次, 都是 LLM agent 路径加补丁. 鸿波两天连续看出"工具/产品定位错了 不是 bug 数量问题"**; (2) **trace 必须有边界 — append-only 单文件 + 时间窗口取的设计在 LLM 探索 / 复用 / 多对话场景下必败. start/end session 物理隔离才是 MVP 真最小**; (3) **铁律比软纪律稳 — SOUL.md 教学边界铁律比 tool description 提示有效得多, 12:21 失败 / 13:14 成功就是这条**; (4) **测试网兜底 — 早上 12 次撞坑很多是"改一行没人替你看着". 60 个单元测试 + CI = 改代码不再裸奔**; (5) **文档 + 例子要并行 — SOP 单纯文字员工记不住, 配 3 个可复制粘贴例子才能用**.

> 📈 **5/11 (周一深夜)** — 一晚 ship 13 commit: BL-Q3-ARCHIVE 双层 + BL-FIX23 L6/L7/L8 + BL-FIX42/44/45/46 + BL-Q3-WEBSKILL (recognize_captcha + browser_locate + eis-login skill 骨架) + demo 主轴重定位:
>   - **BL-Q3-ARCHIVE 完整 ship** (设计文档 18 段 + 1300 行实施 + 31 单测): tool message 超 4KB → 写 PG (lossless) + 头 500B + 尾 500B + 异步 haiku 摘要, LLM 调 `catfish_read_tool_archive(ref, grep / line_range)` 召回. 替代 BL-FIX41 硬切 (5/11 早 ship 的临时方案). 实测 199 messages / 63 tool_msgs / 258KB → 112KB, 省 ~36K tokens lossless. 跟 mcp-registry / skills-hub / facts_db 同 PG+jsonl 双写模板. Alembic 新表 + 后续 fix2 加 origin_model 列让 summary_worker 用 chat 同款模型 (私有部署 token 不要钱, 不绕 catalog tag).
>   - **BL-FIX23 L6/L7/L8 三轮迭代修 plan-only retry**: L6 死循环紧急修 (Jaccard + 上限 + last_is_tool 一刀切); L7 拆 last_is_tool 二分 (未来意图 vs 完成态); L8 反向判定 — 不问"是 plan-only?" 改问"是真任务完成?" (鸿波"已识别验证码 'XXXX'" 中性陈述句没踩 keyword list 暴露 L7 漏洞). 死循环保险全保留 (retries < 1, Jaccard ≥ 0.55). 决策矩阵 4 case + 29 单测全过.
>   - **BL-FIX42 历史截图折叠**: 多张截图累积 prompt 5-10MB → 122b 推理 60-108s → Companion macOS/Tauri fetch idle 默认超时 abort. 修法保留最近 1 张图, 老图 image_url part 替成 `[历史截图已折叠]`. 实测 4 张 → 1 张 prompt 减 75%.
>   - **BL-FIX44 浏览器自动化彻底修** (鸿波"不是够不够的问题, 是要彻底解决问题"): 真根因诊断 — 工具链断点 (screenshot 给视觉但没 selector, click 要 selector 但不给视觉, LLM 中间硬桥). 两步组合: (1) `catfish_browser_click` 加 `coordinates=[x,y]` 走 `page.mouse.click` 完全绕开 selector 歧义; (2) `catfish_browser_find_by_text` 返排序候选 + 元数据 (selector / role / match_type / is_clickable / bounds / center / score), top_recommendation 给最佳猜测, 加 `role='button'` 避开 placeholder 撞文字. 排序权重透明 (JS 内): role +50 / clickable +30 / innerText +20 / placeholder +3. EIS 真实场景 Python 重现验证 (登录按钮 67 vs 密码框 20 不传 role, 117 vs 0 传 role=button).
>   - **BL-Q3-WEBSKILL 视觉双子**: `catfish_recognize_captcha` (~250 行) 走 vision OCR 不让 LLM 自己 OCR + `catfish_browser_locate` (~330 行) 自然语言找元素位置 (返 `{x, y, w, h, center, confidence, reasoning}`). 配合 BL-FIX44 coords click, 形成完整视觉驱动浏览器自动化链路 (recognize_captcha → OCR / browser_locate → 找位置 / click(coordinates) → 点). PNG header 直接解尺寸无 PIL 依赖, strict JSON 输出, 校验坐标在图内防幻觉. 10 单测全过.
>   - **eis-login skill 骨架** (`docs/samples/eis-login-skill/SKILL.md` ~300 行): frontmatter + 8 步流程 + 失败处理矩阵 + session-renewal 段 (BL-FIX45 C). 草版, 5/12 鸿波内网测后填真实 selector → publish hub. **5/14 demo 主轴示范案例**.
>   - **BL-FIX45 错误自动恢复 UX** (3 类一起): A. chat.ts 检测 401 → 调 tauri auth_login 弹浏览器 OAuth → 拿新 token → silent 重发 (retries < 1); B. 检测 500/502/503/504 → fetchCatalog 找下一个可达 chat 模型 → silent 切 + 友好提示 (retries < 2); C. eis-login SKILL.md 加 session-renewal 段 — skill 跑到一半检测页面跳回 /login → 自动跑 step 2-7 子集重登 → 回原步骤继续 (renewal < 2).
>   - **BL-FIX46 请示停顿铁律**: 鸿波点透 — LLM 答完后说"要不要继续看待办?" 立刻自己 `screenshot()` 没等回答, 越救越乱. 跟 L7/L8 反向问题 (该 act 没 act vs 不该 act 却 act). SOUL.md 加段, 跟 L1 "做完才说" + FIX24 "做完不再问" 三条 turn 控制铁律互补. 句末"要不要 X?" / "需要我..." / "?" 指向员工决策 → 必须 stop, 不能 emit tool_call act on 自己建议.
>   - **demo 主轴重定位** (鸿波 5/11 深夜战略反思): 从 "AI 多智能 (LLM agent 跑通流程)" 调整为 "员工教 catfish 一次, catfish 凝固成 skill". 跟传统 RPA 差异: IT 3 周 → 员工 10 分钟. 跟 BL-Q3-FACT 同源, 形成 Q3 完整产品线 BL-Q3-WEBSKILL. ROI: 100 流程 × 1000 员工 = 600 万 RPA 部署成本节省/年 + 员工日常时间 8.3 万小时/年.
>   - **鸿波诊断功劳 (5/11 三次关键)**: (1) "彻底解决问题" — 拍板 BL-FIX44 走真根因路线; (2) "本来想做的就是教导一次生成 skill" — 拍板 Q3-WEBSKILL 产品线 + demo 主轴; (3) "过度思考" — 点透 FIX46 turn 控制反方向问题.
>   - **教训**: (1) 打补丁 vs 治本 — L5-L8 累积 4 层都改进 LLM agent 路径, 鸿波 5/11 反思后才看清 LLM agent 不该是用户日常路径; (2) Keyword 列表局限 — L5/L7 plan-only keyword 抓不全, L8 反向判定 task_complete 更稳; (3) 视觉双子设计 — OCR 跟空间定位用同模型不同 prompt 拆两个工具线; (4) Turn 控制双向 — 软纪律 + 工程兜底两路都要; (5) 工程师惯性 → 鸿波视角差 — 工程师看 bug 加补丁, 鸿波看是不是工具/产品定位错了.

> 📈 **5/9 (周六)** — BL-FIX23 五层 + BL-FIX24 治"半截就停 / 死循环" turn 控制 5 道护栏全摆齐 (6 commit + 33 单测):
>   - **三轮诊断踩坑**: 第一轮 L1 SOUL "做完才说" 改纪律无效 (RLHF > system prompt); 第二轮 L2 reasoning_content 兜底也无效 (chunk_stats 显示 reasoning=0 全程); 第三轮看 chunk_stats 日志 finish_reason=length 才定位真根因 #1 (Qwen vLLM max_tokens 默认太小, streaming 路径 BL-A1.1 auto-continue 没接), 鸿波拍板"方案 C" ship L5 plan-only retry (真根因 #2); 鸿波最后一击诊断 "**任务完成度评估缺位**" 点透真根因 #3 (重复 tool_call 检测), ship BL-FIX24.
>   - **L1** (4ccea2b) SOUL ★ "做完才说铁律": 反馈 = 立刻 emit tool_call 不发 plan-only message. 软纪律.
>   - **L2** (6a0b459) chat.ts SSE parser 加 delta.reasoning_content 处理 (Qwen/DS thinking 模式不丢内容) + gateway streaming chunk_stats 采样日志 (debug 工具).
>   - **L4** (780670a) gateway _build_litellm_params 强制 max_tokens=4096 默认 (client 没传时). 修 Qwen vLLM 默认 ~600 token 长 docx 被截 finish_reason=length. **真根因 #1**. 5 单测.
>   - **L5** (04ed80f, 修 follow-up 37a5ae2) gateway 强制 plan-only retry: 4 条 AND 触发 (finish_reason=stop + 0 tool_call + plan-only content + user 反馈) → deepcopy body + 加 assistant 已输出 + 加 user 硬 hint, litellm.acompletion 起新一轮 (同 used_model 不再 fallback), 新 stream chunks 接到原 SSE. 上限 2 次. **真根因 #2**, 鸿波拍板"方案 C, 不要考虑别的". 13 单测.
>   - **FIX24** (4 文件 staged 待 mac 端 commit): duplicate_tool_call_guard.py 扫近 12 条 messages, 抓 productive tool_calls, 算 arguments sha256 (normalized JSON, key 顺序无关), 同 (tool, hash) ≥ 2 次 → 注入 user hint "你重复 N 次, 不要再调, 等新指令". 跟 self_critique 互补 — 治"做了又做". SOUL 加 ★ "做完不再问铁律" (跟"做完才说"配套, 一进一出). 15 单测.
>   - **"切 DS" 反思**: 三轮诊断中我反复挂"切 deepseek 兜底" — 鸿波质疑 "为什么老是想切 DS? DS 就能解决吗? 很奇怪的逻辑". 我承认惯性思维 N=1 样本不严谨, DS 引入新问题 (公网保密性破坏 / ttft 187 秒比 private 慢 3 倍 / 公网拥塞). **5/14 demo 主模型继续 catfish-private-main**, DS 只在 fallback 链被动接住. 真招是 BL-FIX24 治本.
>   - **完整 turn 控制护栏** (5 道): L1 软纪律 → L2 不丢内容 → L4 length 兜底 → L5 stop+无 tool_call 兜底 → FIX24 重复 tool_call 兜底 → SOUL 后置纪律 "做完不再问".
>   - 测试 1130+ passing (gateway 710+ +33 单测 / companion tsc 0 error).
>   - 鸿波诊断功劳 (5/9 三条最关键的都是): (1) 拍板方案 C 砍掉切 DS / /compress 选项, 让 ship 真招; (2) 一句"任务完成度评估缺位" 点透 BL-FIX24 真根因; (3) 打脸切 DS 让我反思惯性思维.
>   - 教训: (1) **铁证之前别下结论** — 三轮诊断都是先猜后做, debug 工具 (chunk_stats / finish_reason 日志) 应该提前加; (2) **"切模型"不是修 bug** — 模型层差异是体验, 真招在 turn 控制 / inject hint / 工程兜底; (3) **互补检测才完整** — self_critique (该做没做) + duplicate_guard (做了又做) + plan-only retry (嘴说不做) 三管齐下; (4) **软纪律 + 工程兜底双管** — SOUL 软纪律 ~30% 听话率, gateway 工程兜底 ~95%, 单靠软纪律治不了 RLHF 习惯.

> 📈 5/5-5/6 sprint 桌宠 ship + 7 gap 安全闭环 + 主动闲聊 8 bug 修 + 文档套件 (~40 commit):
>   - **5/5 BL-E27 桌宠 spike + ship**: 计划 5/22 起做的 BL-E27 提前两周 ship 到 BL-E27.2 阶段. 透明 NSPanel + macOSPrivateApi + 4 状态联 LLM + Cmd+Shift+P toggle + Option+Shift+1/2/3/4 4 屏角 + LogicalPosition 修 Retina 物理像素出屏幕 + SVG v1 (鱼竿) → v2 (圆胖浮游). 鸿波拍板"主动信息出口" — 桌宠取代 macOS 通知做 chat starter.
>   - **5/6 BL-E27.2 真透明穿透 + Rust polling 通信**: 三轮 emit 协议层 dead end 后弃用 (app.emit_to / pet.emit / emitTo 都通不到 pet listener), 改 Rust 全局 Mutex polling buffer (300ms tick) — 100% 可靠不依赖 Tauri 跨 webview event. cursor_position 80ms 轮询切 setIgnoreCursorEvents (透明角穿透 + 桌宠区接事件), pet_emit_bubble / pet_emit_status 两条命令.
>   - **5/6 主动闲聊 8 bug 全修**: ① 精确分钟匹配丢消息 (员工 9:31 启动当天 9:30 整丢) → 改"过点补发" ② StrictMode 双 mount 重复 fire (LLM 多调一次 token) → markFired 提前到 tick ③ Tauri 跨 webview emit 通不到 pet listener → polling fallback ④ 鸿波抱怨"prefill 输入框还要按发送" → startProactiveChat 改成直接 push assistant message 到 chat (无需员工再发, 持久化到 session) ⑤ ProactiveCard 加 "测一下 ▶" + visual feedback ⑥ formatFileAttachment 在 PDF 分支用 meta.structured_path 替代 raw text hint ⑦ markdown.tsx 修伪表格不渲染 (rehype-raw + GFM 重组 + cell 内 br) ⑧ parse_file.py PDF anchor 模式识别 (社保/工资 等 ≥5 个身份证号自动结构化, 防 5.5 万字爆 context).
>   - **5/6 7 个 P0/P1 安全 gap 全修 + CI 集成**: G1 gateway/skills-hub 0.0.0.0 → 127.0.0.1 (员工电脑不再暴露局域网) · G2 Skills Hub URL fetch sha256 校验 + `CATFISH_HUB_REQUIRE_HASH=1` 严格模式 + server 端发布时自动算 sha256 (端到端闭环) · G3 execute_code 安全守卫拦 25 类危险 (凭证/外联/危险 shell) + audit 留痕 · G4 Tauri CSP null → 显式 8 条策略 · G5 cargo audit (1067 advisory **0 vulnerability** / 19 informational warning 不在调用路径) + pip-audit (4 组件 **0 CVE**) + npm audit (**0 CVE**) + CI workflow · G6 数据出境流向图 (`DATA-FLOW-DIAGRAM.md`, 4 出境路径 + 3 配置档位 + 7 不出境数据) · G7 gitleaks 8 类正则源码 **0 hit** + CI 集成. 详见 `SECURITY-REVIEW-2026-05-06.md`.
>   - **5/6 文档套件 5/14 demo 用**: SECURITY-REVIEW + DATA-FLOW-DIAGRAM + audit-summary + secrets-scan + cargo-audit-actual + 5 场景 demo 脚本 (`MAY-DEMO-SCRIPT-2026-05-14.md`) + 客户 Q&A 30+ 题库 + 现场应急表 + RUNBOOK-DEMO-VERIFICATION (你机器跑 9 项 5-10min) + README-FOR-CUSTOMERS + QUICKSTART-EMPLOYEE + DEPLOYMENT-RUNBOOK (12 节客户 IT 部署).
>   - **5/6 BL-MM2 / MM4 / MM6 ship**: RelationCard / MemoryHistoryCard 实时刷新 (30s setInterval) + 全量条目 + AuditCard 文案改员工口语 + 显式 feedback UI 👍/👎/改.
>   - **5/6 业务 skill 端到端验证**: weekly-report 17 + leadership-briefing 25 + project-approval 1 = **43 测试全过** + leadership-briefing 真 render docx 端到端跑通.
>   - **5/6 版本号去硬编码**: IdentityCard `getVersion()` Tauri runtime API + branding/catfish shell 读 package.json + 30s 自动刷新.
>   - **5/6 parse_file 找错 Python venv 修**: catfish gateway venv 优先 + `_has_parse_deps()` 检测 pypdfium2/openpyxl/docx 全装才用. PDF 上传不再报缺依赖.

---

## 🚨 当前最大风险 / 缺位 (Top 5, 5/8 更新)

| # | 风险 / 缺位 | 影响 | 跟踪 track |
|---|---|---|---|
| 1 | **5/13 真机彩排 ×2 没做** (距 5/14 demo ~6 天) | 现场翻车 | #18 销售物料 |
| 2 | **5 场景实录视频没做** | 现场全挂兜底缺位 | #18 销售物料 |
| 3 | **5/14 demo 机子 USER.md / journal seed 没准备** | 场景 1/4 演不出"鲶鱼记得我" | #18 + 现场准备 |
| 4 | **employee_journal 真业务内容不够** | 跨 session 记忆演不出, 5/6 桌宠主动闲聊已缓解一半 | #25 + 持续用 |
| ~~5 Win 客户端 0%~~ | ~~~~ 5/8 突击 8 BL ship 到 demo-ready (cross-build .exe 0 error / IPC TCP / Chrome 路径 / wincred / yaml 配置). 真机验证 + outlook + MSI 签名留 BL-WIN4-7. | #2 ★ |
| 5 (新) | **Windows 真机端到端验证** (cross-build .exe 没在 Windows 实跑过) | 现场客户演 Windows 抓瞎 | #2 (5/12 之前) |
| 6 | **Production 完整部署 50% → 75%** (5/8 加 yaml 配置 + 中央/单机部署形态文档) | 5/14 demo 后客户问"装一份给我们"基本能交付 | #16 ★ |
| 7 | **团队 1 人** (Phase 2 一定带不动) | Q3 KPI 跳票 | #20 |

> 📊 **5/8 后**: Windows 客户端从"未启动"跨到"代码 ship + 文档齐 + cross-build 0 error", Phase 2 占比 88% → 95%. demo 链 (验证码自动识别) 真验证. 阻塞**只剩演讲准备侧**: 彩排 / 视频 / demo 机子 seed / Windows 真机验证. 都是工程量小的事, 5/9-13 能闭环.

---

## 📋 全部 Tracks (按 phase + 重要性排)

### Phase 1 · 已 ship 主线

#### #1 Companion macOS [Phase 1, 99%]  ★ 5/6 桌宠 ship + 7 安全 gap 闭环
> Tauri 桌面客户端, 鲶鱼员工每天打开的入口.
- ✅ 三 tab (对话/控制台/仪表盘) + 浮窗 Cmd+Shift+Space 召唤 (5/5)
- ✅ 多模态 (Whisper.cpp 语音 + PDF/Excel/Word/CSV 文件解析 + PDF anchor 模式结构化抽取)
- ✅ 仪表盘 5 卡 (身份/服务/Catalog/Skills/审计/Quota 接通)
- ✅ **Onboarding 引导 4 步** (5/3 BL-F3 MVP): welcome / 鉴权 / 选模型 / 试聊, localStorage 记 onboarded
- ✅ **桌宠副窗 BL-E27.2** (5/5-5/6): 透明 NSPanel + 4 状态 (idle/thinking/running/done) + Cmd+Shift+P toggle + Option+Shift+1/2/3/4 4 屏角 + 真透明穿透 (cursor_position 80ms 轮询切 setIgnoreCursorEvents) + 真拖拽 + Rust polling 通信 (跨 webview 100% 可靠)
- ✅ **Tauri CSP 加固** (5/6 G4): default-src 'self' / connect-src 限本机 + tauri ipc / object-src 'none'
- ⬜ 数据迁移工具 (换电脑搬 memory/skill/state) · 0.5 周 (BL-F4)
- ⬜ 备份方案 (auto backup) · 0.5 周 (BL-F5)
- ⬜ 完整 Onboarding (动画 / 多语言 / 真试聊 / 跟 SSO flow 集成) · 0.5 周
- ✅ **.app prod build + 装应用目录** (鸿波 5/6 已 build, 双击启动正常)

#### #3 tool-bridge (本机 IPC) [Phase 1, 90%]
> 把鲶鱼 native tools 暴露给 hermes / Companion 调.
- ✅ 12 个 catfish_* 工具 (run_skill / a2a_ask / skill_install/delete / today_summary / browser × 4 / screenshot / remember 等)
- ✅ tool_bridge_call_tool Tauri command
- ⬜ 端到端集成测试 (现 165 单测) · 1 周 (BL-G6)

#### #4 中央 gateway [Phase 1, 95%]
> LLM 路由 + auth + audit + quota 入口.
- ✅ OpenAI 兼容 /v1/chat/completions + /embeddings + /catalog
- ✅ litellm + fallback 链 + tool_capability_guard + multimodal_guard
- ✅ inject (skills_catalog / session_history / employee_journal / SOUL identity / stats_guard)
- ✅ /api/quota/me (5/3 接 Dashboard) + RBAC 矩阵 (5/2)
- ✅ A2A 端 (sign + verify + ALLOW.md 拦截) (5/4)
- ⬜ Production 部署 (现 dev mode) · 1 周 (BL-F7) — 见 #16
- ⬜ 多 user 并发性能基准 · 0.5 周 (BL-F10)

#### #5 SSO + identity-server [Phase 1, 95%]
> OIDC issuer + 飞书 adapter + dev_token fallback + Keychain.
- ✅ 自建 OIDC server (~600 行) + JWKS + token endpoint
- ✅ 飞书 OIDC adapter
- ✅ Companion JWT 客户端 (Keychain + 自动刷)
- ✅ dev_token + warning banner
- ✅ users 双 backend (PG / yaml fallback) (5/4)
- ⬜ 钉钉 / 企微 adapter (各 2 周) (BL-D15/D16)
- ⬜ 客户 IT 自助接入文档收尾 (BL-L21)

#### #8 Plan D Federation [Phase 3, 50% — 5/6 重定位 agent-as-service + 5/6 晚灵魂校准]
> ⚠️ **方向调整 (5/6)**: 不是"peer-to-peer 临时问答" (Plan D v0.1), 是 **agent 代员工本人提供专业互助**.
> ⚠️ **灵魂校准 (5/6 晚, 鸿波)**: agent-as-service ≠ "公司从员工脑里抽知识沉淀". 真定位是 **员工自愿出来同事互助, 数据所有权始终属员工本人, 跨雇主可携带 (员工跳槽 agent 跟人走)**.
>
> 真卖点 (3 边对比):
> - vs ChatGPT 企业版: 那个知识属 OpenAI, 鲶鱼属员工本人
> - vs 公司知识库 (Confluence/wiki): 那个是公司资产 (员工跳槽不丢), 鲶鱼是员工资产 (跳槽跟人走)
> - vs 啥都没有: 同事一天问 30 次资质打断老李 → agent 接 80% 简单咨询, 老李专注力回来
>
> 跟"对抗不良雇主"灵魂一致: 员工知识 / 工作画像 / 技能库 = 员工的"职业资产", 公司给员工配 (B2C2B), 跳槽带走.

**已 ship (协议 + 单 agent 画像层, 5/2-5/6)**:
- ✅ 协议 spec v0.1 (`PLAN-D-PROTOCOL.md` 580 行, 5/6 加 § 11 agent-as-service 愿景, 5/6 晚校准灵魂)
- ✅ Per-agent registry + JWKS + RS256 JWT 签验 (`gateway/a2a_jwt.py`)
- ✅ A 端 (sign + send) + B 端 (verify + ALLOW.md 拦截) + audit jsonl (`gateway/a2a_server.py`)
- ✅ 单机 mock 端到端 (Alice ↔ Bob, 2 gateway)
- ✅ ALLOW.md token-overlap 匹配 (5/2 BL-L28, jieba 中文分词)
- ✅ **agent 懂托管员工** (BL-MM7 user_profile / MM8 fingerprint / catfish_remember / journal, **全在员工 mac**)
- ✅ **3 部署架构选项** (PROTOCOL § 9.5: A 员工本机 ★ 默认 / B 部门 / C 公司)
- ✅ **跨雇主可携带设计** (DATA-FLOW 边界 4: cp `~/.catfish/` 到新 mac, agent 跟员工走)

**BL-FED2 (5/15 起 6 周, agent-as-service 真 ship — 灵魂校准版)**:
- ✅ **BL-FED2.1** **员工自愿**专长声明 schema (员工 mac 本机 `~/.catfish/expertise.yaml`, 上行只发 tag 列表给 identity-server, **不传画像内容**. 员工随时可删 / 改 / 下线) · 1-2 天 · 5/15-5/16 → **5/12 鸿波拍板"专长从 journal 自动抽"提前完成**: `expertise.py` (~530 行 + 25 单测) 接 LLM 抽 tag, `tool_extract / list / confirm` 3 个 native tool, `export_for_registry()` 隐私阀门 (只返 confirmed tag 串), 接 catfish-private-main 走 gateway loopback. 全测 483 passed.
- ✅ **BL-FED2.2** 同事黄页 (`identity-server` 加 `/registry/search?expertise=资质` endpoint, 只返**已自愿登记**的 agent. 员工删除专长 → 黄页立即失踪) · 1 周 · 5/19-5/23 → **5/12 提前完成**: identity-server `/registry/by-expertise?tag=X[&online_only=true]` endpoint, 大小写不敏感匹配, 在线员工排序优先, 不返 jwks_uri/public_pem (隐私). gateway self_register 读 `~/.catfish/expertise.yaml` confirmed tag 上报. 加 alembic migration `20260512_002_registry_expertise` (JSONB + GIN 索引). identity-server 65 / gateway 817 测试零失败.
- ✅ **BL-FED2.3** 路由层 (`gateway/expertise_router`, 新工具 `catfish_expert_consult`. 调用前给被咨询员工**显式提示** [谁在问 / 问什么], 员工可拒) · 1-2 周 · 5/26-6/6 → **5/12 提前完成**: `expert_consult.py` (~260 行 + 31 单测) 走 by-expertise 自动路由 + 复用 a2a_ask 链路 (含 ALLOW.md 拦截). 路由策略: 排除自己 / 优先在线 / preferred_sub 强转 / 全离线友好降级. 实时 Companion 弹窗确认作为 P1 留 BL-FED2.4 后做 (当前由 ALLOW.md 软策略守护). tool-bridge 514 测试零失败.
- ✅ **BL-FED2.4** 答案质量反馈 (复用 BL-MM6 feedback UI: 小赵评老李 agent 答 → 反馈**只进老李 mac 本机**的 user_profile, 反馈数据**不离开老李 mac**) · 1 周 · 6/9-6/13 → **5/12 提前完成 (但用更轻的形式)**: 不再走 feedback UI 而是直接走 **a2a_journal_hook** — 被问者答完自动在自己 journal 写 [a2a-help] 条目, 下次 extract 加权. 隐私同向 (只进被问者 mac, 不外泄). gateway 加 a2a_journal_hook.py (~80 行 + 11 测试), expertise.py extract prompt 升级识别 [a2a-help] 加权.
- ✅ **BL-FED2.5** 跨员工 demo (单机 mock 升级到 3 agent + 公司目录, 含**员工离职带走 agent** 演示 `cp ~/.catfish/`) · 3 天 · 6/16-6/18 → **5/12 提前完成**: `scripts/fed_demo.sh` 一键起 4 进程 (identity + 3 gateway), 7 步验全链路含隐私边界 assertion + 反馈环 assertion. Sandbox 友好版 `test_fed25_e2e_chain.py` 3 测试覆盖代码层 (pending/rejected tag 不可路由的隐私防御).

**Phase 4 演进**:
- ⬜ 跨 2 台真机测试 · 1-2 周 (BL-E18)
- ⬜ mTLS / 自签 CA · 1 周
- ⬜ Federation registry HA · 1 周
- ⬜ 隐私边界严格化 (ALLOW 协商 / 答案脱敏 / 反馈不漏 raw) · 2-3 周
- ⬜ **跨雇主 agent 迁移工具** (`catfish migrate --from-mac --to-mac`, 自动 sync `~/.catfish/`, 处理 keychain 引用) · 1-2 周
- ⬜ 跨组织 federation (集团 A ↔ 集团 B 通过 OIDC 互认) · 1-3 月

**5/14 demo 加场景 4.5** (60s + PPT 1 页讲 BL-FED2 路线): 单机 mock 演 agent 自愿互助, 客户问"今天能演吗" 答 "今天演 peer-to-peer (8/8 ship), agent-as-service 6 月 ship, 含员工跳槽带走 agent demo". 详见 `MAY-DEMO-SCRIPT-2026-05-14.md` 场景 4.5.

---

### Phase 2 · 五一 sprint 完整 ship Phase 2 后端

#### #6 RBAC [Phase 2, 75%]  ★ 5/2 大幅推进
> 三角色 (admin/manager/employee) + 部门隔离 + Dashboard 角色化.
- ✅ Permission Enum (8 维度) + ROLE_PERMISSIONS 矩阵
- ✅ require_permission FastAPI 依赖 (401/403)
- ✅ users.role + managed_departments + effective_role()
- ✅ User dataclass 加 is_admin / is_manager / can_manage_department
- ✅ Dashboard 按角色 conditional render (employee/manager/admin 看不同卡)
- ✅ DepartmentQuotaCard + DepartmentAuditCard (manager 看本部门聚合 + top 员工)
- ✅ /api/quota/department/{dept} + /api/audit/department/{dept} (RBAC 检查)
- ✅ /api/me 端点 + useMe hook
- ✅ dev_users.yaml 多账号 + DevUserSwitcher 黄条 (7 测试账号切换)
- ✅ 21 单测过 (rbac 16 + dev_token 5)
- ⬜ 实际接到所有路由 (现只 quota / audit / me) · 0.5 周
- ⬜ Manager 改本部门 quota PUT 端点 (现 read-only) · 1 周
- ⬜ Admin 全局聚合卡 · 0.5 周

#### #7 Quota [Phase 2, 100%]  ★ 5/2 完整 ship
> 三维滑动窗口 (用户/模型/部门) + 实时 chat 接通 + manager UI.
- ✅ sliding window sqlite + check_quota (4 维度)
- ✅ /api/quota/me 端点 + Dashboard QuotaCard 实时
- ✅ /api/quota/department/{dept} GET 部门聚合 + top 10 员工
- ✅ /api/quota/department/{dept} PUT manager 改限额 (写 quotas.yaml)
- ✅ /api/quota/global admin 全员 top departments
- ✅ quotas.yaml.example + path 修正
- ✅ 双 backend (PG / sqlite, 自动 fallback 不丢数据)
- ✅ chat completions 主流程接 record_usage (PG 双写)
- ✅ **chat 入口接 check_quota 阻断 (5/2 完整 ship): 超额返 429 + friendly 话术**
- ✅ **Companion 识别 429 显友好 banner ("你今日 X 用满了, 切到 Y")**
- ✅ **DepartmentQuotaCard 加 inline QuotaEditor (manager 直接 input + 保存)**
- ✅ 28 单测过 (含 5 部门聚合 + 3 update_department_quota + 2 全局聚合)

#### #9 PG 中央数据库 [Phase 2, 95%]  ★ 5/2 完整 ship
> users + registry + quota_events + gateway_audit 全走 PG, fallback 完整.
- ✅ identity-server db.py asyncpg 池 + 双 backend (PG / yaml)
- ✅ users.reload_from_pg + seed_pg_from_yaml
- ✅ registry 双 backend (load/save_async)
- ✅ gateway db.py 镜像 (asyncpg 懒 import)
- ✅ quota.py PG 双写 (psycopg sync, 自动 fallback sqlite 不丢)
- ✅ metrics.py PG 双写 (gateway_audit 表, fallback jsonl)
- ✅ database.yaml.example + .gitignore 真 yaml
- ✅ alembic 双服务 (gateway + identity-server) 各自 migration
- ✅ 各服务独立 alembic_version_gateway / _identity 防共享 PG 撞
- ✅ 3 PG integration 测 + 双 backend sqlite/jsonl 测全过
- ✅ 真机 Mac PG 端到端跑通 (5 张表 + chat → quota_events + gateway_audit 双写)
- ⬜ 多 gateway 实例共享 PG 测试 (BL-D17 完整) · 0.5 周
- ⬜ ALEMBIC CI 集成 (.github/workflows/) · 0.5 周

#### #10 Skill 系统 (load/run/inject/lifecycle) [Phase 1+2, 80%]
> 设计 / 加载 / 调用 / inject / guard / 版本 / 下线 / 删除 / 审计 全套.
- ✅ catfish_run_skill (importlib 反射) + skill_guard REQUIRED block
- ✅ inject_skills_catalog (注入到 system prompt)
- ✅ 版本 (SemVer) + deprecated 字段 + 审计 jsonl
- ✅ skill_install / skill_delete (30 天回收站) + Skills Hub MVP 本机版
- ✅ 17 lifecycle 测过
- ⬜ skill 创建后立即 dry-run 验证, 失败回滚 · 0.5 天 (BL-C12)
- ⬜ skill 创建前重复检查 · 0.3 天 (BL-C13)

#### #11 Skills Hub (中央托管) [Phase 2, 90%]  ★ 5/6 sha256 + hub URL pull 闭环
> 组织级技能市场, 让员工分享 skill, 部门集体学习.
- ✅ 本机版 MVP (skill_install 工具) (5/3)
- ✅ **中央 hub server FastAPI MVP** (5/2): publish / list / get / download / delete + audit
- ✅ **dry-run 验证** (5/2 BL-C12): skill_install 后 import 检查 + 失败 rollback
- ✅ **dedup 检查** (5/2 BL-C13): install 前查同名 / 描述相似的 skill, force_install 跳
- ✅ 文件系统存储 (~/.catfish-hub/), 多 version 共存, audit jsonl
- ✅ **Companion `catfish_skill_install` 接 hub URL 拉取** (5/5): 客户端 `_install_from_hub("ns/name@version")` urllib 拉远端 + 复用本地 dedup/dry-run
- ✅ **sha256 签名校验闭环** (5/6 G2): server `get_skill()` 返 `files_sha256: {file: hex}` + 客户端拉文件后比对, 不匹配 → rmtree + 拒装 (中间人攻击防御) + `CATFISH_HUB_REQUIRE_HASH=1` 严格模式拒装无签名 skill
- ✅ host 默认 127.0.0.1 (5/6 G1) + ⚠️ HOST=0.0.0.0 警告
- ✅ 23 storage 测过 (含 sha256 2 case) + 28 lifecycle 测过 (含 hub mode 5 case) = 51 全过
- ⬜ 审核流 (manager publish → admin approve → live) · 1 周 (现 MVP 直发)
- ⬜ 部门级 skill auto-推 · 1-2 周 (依赖 #8 federation 协议)
- ⬜ PG 存储替代文件 (Phase 2.5) · 1 周

#### #2 ★ Companion Windows [Phase 2, 80% · 5/8 突击 8 BL 跨到 demo-ready]
> 大客户都用 Win, 没 Win 客户端 = Q3 大客户阻塞.
> **5/8 鸿波 Tokyo 9pm 拍板做** — 一晚 8 BL ship 到 demo-ready 形态.
> 凭据安全模型已对齐 (Rust `keyring` crate / Python `keyring`+wincred 跨平台).
- ✅ **BL-WIN1** mac → Win cross-build (mingw-w64 / `cargo build --target x86_64-pc-windows-gnu`) — 出 30MB `catfish-companion-app.exe`, 0 error 0 warning. `scripts/build-windows.sh` 一键脚本.
- ✅ **BL-WIN1.1/1.2/1.3** cfg(unix)/cfg(macos) gate 修 5 个编译错+warning (UnixStream / RecordingState / dead-code imports).
- ✅ **BL-WIN8** tool-bridge IPC TCP localhost 替代 Unix socket — Python server.py `os.name=='nt'` 时走 `asyncio.start_server('127.0.0.1', port=0)`, 端口写文件; Rust call_rpc cfg 分流 UnixStream/TcpStream.
- ✅ **BL-WIN3** Chrome 路径检测扩 12 候选 (per-user `%LOCALAPPDATA%\Google` + 双架构 Program Files + Edge Win11 fallback) + `CATFISH_CHROME_BIN` env 强制覆盖.
- ✅ **BL-WIN2** secret_resolver `wincred://` 真实现 — Python `keyring` 包 (跨平台 wincred backend) + PowerShell Get-Secret fallback + 完整 cmdkey/Set-Secret 教程. `keyring>=24; sys_platform == 'win32'` 平台条件依赖.
- ✅ **BL-WIN9 / DEPLOY1** 网关地址走 yaml 配置 — mac .app + Win .exe 双击启动**都不读 shell env**, 走 `~/.catfish/companion.yaml`. 3 端联动: Rust endpoints.rs 加 yaml 解析 + Tauri command `get_runtime_endpoints` + 前端 `bootstrapEndpoints()` startup 写回. 客户改 yaml 重启即生效.
- ✅ **BL-WIN9.1** README-deploy 补 Windows 具体步骤 (部署形态 A 纯聊天 vs B 完整 / `%USERPROFILE%\.catfish\companion.yaml` 路径 / 文件管理器+记事本+完全退出+重启验证).
- ✅ **BL-WIN9.2** yaml 默认 audience 'test' → 'catfish-companion' (修隐性 401 aud mismatch).
- ⬜ **BL-WIN4** outlook_win.py (pywin32 COM) · 1-2 天 — demo 用不到, 客户上线用
- ⬜ **BL-WIN5** daemon_windows.py (Windows Service / startup folder) · 0.5 天
- ⬜ **BL-WIN6** MSI / NSIS installer + Authenticode 签名 · 0.5 天 (要 Windows 真机 + 证书)
- ⬜ **BL-WIN7** sandbox AppContainer (代替 sandbox-exec) · 2-3 天 — 安全侧, 不阻塞 demo
- ⬜ **BL-WIN10** Windows 真机端到端验证 (Parallels / VMware / 同事机) — 5/12 之前必做
- **估时已 ship**: 4 小时 (5/8 晚 7-11pm) · **剩**: WIN4-7 真上线分批 + WIN10 真机验证
- **触发**: 5/8 鸿波拍板"现在做" → 一晚 ship 完
- **能力清单 (Win 真机能跑)**: chat / 仪表盘 / SSO 登录 / 浏览器自动化 catfish_browser_* / Playwright 截图 / skill (docx/pptx/xlsx/pdf) / wincred 密码 / yaml 配置网关地址

---

### Phase 1 · 完全没在主跟踪上的 ★

#### #12 ★ email-agent (邮件 agent) [Phase 1, **95%**]  ★ 5/17-18 大跃进, AI 邮件管家闭环
> ★ 5/17-5/18 24+ ship: macOS adapter 完整 ship + Companion 邮件简报卡 4 step 闭环
> (step1 卡 + step2 后台 scheduler + step3 LLM 评级 + step4 桌宠主动闲聊).
> 走客户端集成 (Apple Mail + Foxmail + Outlook), **不碰密码**, 不走 IMAP, 红线.

**5/17-5/18 ship 清单**:
- ✅ Apple Mail (Mail.app) adapter 完整: AppleScript via osascript + EMLX fallback (没权限时只读) +
  body_html 抽取 + bcc 起草 + cross-locale inbox 名 (`INBOX`/`Inbox`/`收件箱`/`受信箱`) + ISO 日期
  + character id 31 控制字符 (BL-EMAIL-APPLEMAIL-IMPL/FULL/STALE-REFS/AS-CTRLCHAR/INBOX-NAMES/DATE-ISO)
- ✅ Foxmail Mac adapter (已有) + Apple Mail 同时跑, CLI `--client` 没传 → 跨客户端合并查
  (BL-EMAIL-MULTI-CLIENT, 修立项前提"公司邮箱走 Foxmail" 被 factory 短路漏掉)
- ✅ CLI `_cmd_list` 跨账号合并 (BL-EMAIL-MULTI-ACCOUNT, 修"只查默认账号 0 未读" bug)
- ✅ **Companion 邮件简报卡 + 后台 scheduler + LLM 评级 + 桌宠主动闲聊** 4 step 闭环
  (BL-COMPANION-EMAIL-DIGEST step1-4, 详见 CHANGELOG 5/18)
- ✅ `~/.catfish/companion.yaml` 的 `email:` 段配置 (poll_secs / rate_enabled / rate_model)
- ✅ SKILL.md 加红线段防 LLM 反设计建议 ("不要建议 IMAP / Gmail API / himalaya")
- ✅ DESIGN.md 完整 (P1-1 三大支柱之二)
- ⬜ Win Outlook adapter · 1-2 周 (BL-C4)
- ⬜ Win Foxmail adapter · 1 周 (BL-C5, .box parser 已有)
- ⬜ Companion 邮件 tab GUI (独立 tab, 不只 dashboard 卡) · 1 周 (BL-D13)
- ⬜ 邮件简报 step5: 卡片显急/中/低 badge + 评级"已读"自动消除通知 · 1-2 天
- ⬜ 起草工具 (`catfish_email_draft`) 接到 tool-bridge · 0.5 周
- **决策已定**: 演这个! macOS 端 (Apple Mail + Foxmail Mac) 已闭环可演, 实盘鸿波 3 账号 5 未读跨客户端正常显示

#### #13 ★ feishu / 微信 / 钉钉 / 企微 — Hermes Unified Inbox [Phase 1, 重定位 5/7]
> ⚠️ **5/7 鸿波关键发现**: hermes v0.12.0 内置 19 个 messaging platform, 含 **DingTalk / Feishu/Lark / WeCom (企微) / Weixin / QQ Bot / Yuanbao** 中国 IM 全栈. 之前 catfish 自己写的 `edge/feishu-monitor/` (CDP 模式) 是重复造轮子, 应切到 hermes gateway.
>
> **架构**: Companion + hermes CLI + hermes gateway (19 platform) 全部共享 `~/.hermes/state.db` SQLite. 一份 chat 历史, 多入口访问.

**hermes gateway 内置支持** (5/7 验证 hermes v0.12.0):
- ✅ 飞书 / Lark
- ✅ 企业微信 (WeCom + WeCom Callback Self-Built)
- ✅ 微信 (Weixin / WeChat)
- ✅ 钉钉 (DingTalk)
- ✅ QQ Bot / Yuanbao (元宝)
- ✅ Telegram / Discord / Slack / WhatsApp / Signal / Email / SMS / iMessage / IRC / Mattermost / Matrix / Microsoft Teams

**catfish/edge/feishu-monitor/ (CDP 模式) — 🪦 deprecated 5/7 决定砍, 5/22 后 git rm**:
- ❌ hermes gateway feishu (WebSocket) 全覆盖, CDP 单向 + 需 Chrome 完全劣势
- ❌ 维护两套代码增加复杂度, 客户故事变乱
- ❌ 鸿波 5/7 拍板 "原来这个飞书没意义了不是吗" — 砍
- 5/22 后 git rm edge/feishu-monitor/ + 删 src/ tests/ scripts/
- (短期 5/14 demo 前不删代码, 怕动了别的, 但文档不再提)

**5/14 demo 决策 (5/7 鸿波拍板)**:
- 演 hermes gateway setup wizard (展示 19 platform 列表, 客户看到飞书/微信/钉钉/企微全有)
- 现场配 Telegram 演真双向 (5 分钟, 鸿波手机演)
- 跟客户讲: "你们生产用就选飞书, 一样流程, 我们底层是 hermes 不重写"

**5/14 后路线**:
- ⬜ BL-D14 hermes feishu/wecom/weixin gateway 真接通 (5/8 IT 凭证 → 5/13 dryrun) · 1 周
- ⬜ BL-D15 / BL-D16 (钉钉) — 不再单独做, 直接用 hermes 内置
- 🪦 catfish/edge/feishu-monitor (CDP) — 5/22 后 git rm, 5/7 鸿波拍板砍
- ⬜ 跟 hermes brand patch 冲突 fix (5/7 update 时撞了, 5/8+ 修, BL-D14.5)

#### #14 ★ 业务 skill 库 [Phase 1, 50%]  ★ 5/6 端到端验证 43 测试全过
> 客户 demo / PoC 看的"摸得着的能力", 现 3 个 (5/2 拍板 project-approval).
- ✅ leadership-briefing (4 段公文 + 表格附件 + 双 backend 错别字) — **25 测试 + 真 render docx 端到端跑通** (5/6)
- ✅ weekly-report (.xlsx 周报) — **17 测试**
- ✅ project-approval (项目立项, 复用 leadership-briefing 渲染层 + 4 段语义改) ★ 5/2 — **1 测试**
- ⬜ qualification-export (4 月停在 demo) · 0.5 周
- ⬜ meeting-minutes (会议纪要, 配合 #4 视频) · 1 周
- ⬜ annual-summary (年度总结) · 1 周
- ⬜ procurement (采购单) · 1 周
- **demo 阶段**: 3 个够初步多样性, **43 测试全过**, 5 月后扩到 5+ 个

#### #15 多模态 [Phase 1, 65%]  ★ 5/6 PDF anchor 模式结构化抽取
> 语音 / 文件 / 视频 / 音频.
- ✅ 截图 + vision (4 月已通)
- ✅ 语音输入 (Whisper.cpp + ffmpeg avfoundation, 5/1)
- ✅ 文件上传 (PDF/Excel/Word/CSV/TXT/MD, 5/1)
- ✅ **PDF anchor 模式结构化抽取** (5/6 BL-D17): parse_file.py 检测 ≥5 个身份证号/长 ID 锚点, 围切片提取 sub-records (社保险种 / 金额条目两 SUB_PATTERN), 跨页同人合并, 噪音 ID 过滤. 输出完整 JSON 到 /tmp/, LLM 直接 pandas.read_json 不啃 5.5 万字爆 context. 11 测试全过. **真用社保 PDF 320 人验证 0 错**.
- ✅ **BL-I3.1 视频抽音轨转写** (5/8 ship, 复用 BL-I4 链路): ChatInput 加 .mp4/.mov/.m4v/.mkv/.webm, parse_file.py `parse_video_preview` 用 ffmpeg `-vn -ar 16000` 抽音轨 → whisper-cli 转写. 用例: 会议录像 → 提要点 / 待办. ≥50KB 转写自动走 BL-L26 BM25
- ⬜ BL-I3.2 视频帧抽取 + vision 描述 (推后, vision 调用费 + "全本地"故事冲突)
- ✅ **BL-I4 音频文件转写** (5/8 ship): ChatInput 加 .mp3/.wav/.m4a/.flac/.aac/.ogg, parse_file.py `parse_audio_preview` 复用 BL 语音输入 (5/1 ship) 的 whisper.cpp + ggml-small.bin + ffmpeg 链路. duration_sec / language / 转写文本 + BM25 sidecar
- ✅ **大文件 (≥50KB) BM25 检索** · 5/7 ship (BL-L26) — 1 天压完: parse_file.py 写 sidecar `<keptPath>.parsed.txt` (PDF/Word/Text 全文), Tauri command `attachment_bm25_search` 调 attachment_bm25.py 取 top-K 段落 (TF + 长度归一化, 中文 2-char window 解决 trigram FTS5 不能命中央企 2 字词的痛). useChat.send 自动 enrich, formatFileAttachment 用 BM25 段落代替 5K preview. 24 单测 PASS

#### #17 浮窗 / 全局 UX [Phase 1, 80%]  ★ 5/2 加系统通知
> 让员工随时召唤鲶鱼 (类 Spotlight).
- ✅ Cmd+Shift+Space 全局快捷键 (5/5)
- ✅ Esc 隐藏 + dock 单击恢复 (5/5)
- ✅ macOS 原生标题栏 (品牌不重复)
- ✅ macOS 通知 (osascript, BL-E13 用) ★ 5/2
- ✅ Companion 默认 prod, DEV 模式 opt-in (CATFISH_AUTOSTART_ENV=dev) ★ 5/2
- ⬜ 浮窗版 (无标题栏 / 居中悬浮) 单独 UX · 1 周
- ⬜ 选中即翻译 Cmd+Shift+T · 1-2 天 (BL-E5)
- ✅ 专注模式 (前 "领导来了") · 5/3 ship (BL-E15, 见 #27)
- ⬜ menubar 状态指示 · 0.5 周

#### #25 ★ 主动闲聊 (BL-E13) [Phase 1, 75%]  ★ 5/6 8 bug 全修 + 改 assistant 直发
> 让小鲶按时段主动找员工聊, 不让 employee_journal 饿死 (demo 跨 session 记忆有内容).
- ✅ gateway proactive.py: 读 journal tail + 时段 + qwen-flash 生成 starter
- ✅ /api/proactive/starter 端点
- ✅ Companion ProactiveCard (Dashboard 第一卡, 30min 自动换)
- ✅ useProactiveScheduler hook: 9:30 / 14:00 / 17:30 (localStorage 防重)
- ✅ Tauri notify() 用 osascript display notification (无新依赖)
- ✅ **过点补发** (5/6): 员工 9:31 启动也能补发当天 9:30 (老逻辑只精确分钟匹配会丢)
- ✅ **StrictMode 双 mount race 修** (5/6): markFired 提前到 tick 决定 fire 时立即调
- ✅ **桌宠头顶气泡 + macOS 通知 fallback** (5/6): 桌宠 visible → emit pet bubble (Rust polling 100% 可靠), 桌宠 hidden → osascript 通知
- ✅ **改成"桌宠真主动说话"** (5/6 鸿波点播): startProactiveChat 不再 prefill 输入框让员工按发送, 改成直接 push assistant message 到当前 chat + 异步 sessionMessageAppend 持久化. 员工看到桌宠真开口, 直接打字回复 = user msg
- ✅ **ProactiveCard "测一下 ▶" 按钮** + visual feedback (5/6): dev/test 不用等 9:30, 立刻触发整链路 + 浮层显示发送状态 + macOS 通知权限提示
- ⬜ 节假日 / 晚 10 点不打扰 (推断状态) · 1 周
- ⬜ snooze / 拒绝机制 (现 localStorage 开关) · 0.5 周
- ⬜ 配置 UI (时段 / 频率 / 模板) · 0.5 周
- ⬜ 跨 session 深度情境关联 (journal 结构化解析) · 1-2 周
- ⬜ Phase 2.5: 真主动 (AI 行为信号触发, 不死时间) · 1-2 周, demo 后

#### #26 ★ 品牌视觉身份 (brand kit v1) [Phase 1, 90%]  ★ 5/3 晚 ship 完整套件
> 之前 logo / 配色 / 头像全是 🐟 emoji 占位. 5 月 demo 客户看到必扣分. 今晚一次到位 ship.
- ✅ **5 件套 SVG** (`branding/`): logo-mascot (完整吉祥物 480x480) + logo-mark (极简圆形 256x256) +
  logo-mark-mono (单色反白) + avatar-circle (聊天头像) + app-icon-master (macOS squircle)
- ✅ **47 个 app icon 全套** (`render_icons.py` 一键): macOS .icns + Windows .ico (multi-size embedded) +
  iOS 18 个尺寸 + Android 5 密度 mipmap × 3 类 + Windows tiles 9 个 + favicon
- ✅ **BRAND.md 速查** (`edge/identity/`, 跟 SOUL.md 同级): 角色定位 + 4 件套用途表 + 配色 +
  字体 + 净空区 + 用法红线 + 文案语调 + 应用清单 + 改 brand SOP
- ✅ **配色升级** (`tokens.css`): 旧 #06b6d4 亮青 → #0E5F66 墨青 + 暖橙 #F47B3D + 暖米 #FAF1E4
  (深色模式同步) - 6 个新 CSS 变量
- ✅ **6 处占位 🐟 → 正式 mascot** (Companion 前端): LoginGate / OnboardingWizard /
  ChatPanel / ChatTab / LearningCard / useProactiveScheduler 通知
- ✅ **SSO 登录页 brand 升级** (identity-server): 内联 mark SVG (无静态文件依赖) + 全页色升级 +
  暖橙 CTA + favicon (data: URI) + Cache-Control no-store 防浏览器缓存老 HTML
- ✅ **Demo PPT 封面** (`branding/demo-cover.pptx`): 深青底 + 大字"鲶鱼 Catfish" +
  暖橙锚点 slogan + 三行卖点 (本机算力 / 公文报表 / 审计合规)
- ✅ **品牌铁律** (SOUL.md 加章节): 禁止小鲶向员工说 "hermes / ~/.hermes / 未初始化", 必说"鲶鱼内置存储"
- ✅ **dispatch 层 brand scrub** (adapter.py): hermes memory_* 工具响应里的 ~/.hermes 路径 +
  hermes 字眼自动过滤再给 LLM (audit log 留原文); 11 个测试覆盖
- ⬜ 桌面壁纸 / 名片 / 邮件签名模板 (P1, demo 后) · 0.5 周
- ⬜ H5 介绍页 (P2) · 1 周
- 见 `edge/identity/BRAND.md` (完整速查) + `branding/` (源文件)

#### #27 ★ 人格 sprint (BL-E11 + E15 + E16) [Phase 1, 75%]  ★ 5/3 晚 ship 3/4
> "鲶鱼不是 IT 工具, 是有性格 / 站员工这边 / 记得你的同事" — 跟 ChatGPT/Copilot 区分点.
> 4 个 demo 杀器 ship 3 个 (留 BL-E14 PPT 吐槽下周做).
- ✅ **BL-E11 命名权** (1 天): 员工 Onboarding 第 2 步给鲶鱼起名 + 3 档人设 (温柔/直爽/毒舌);
  yaml 持久化 + Tauri commands + zustand store + Dashboard AgentPrefsCard 改名;
  X-Catfish-Agent-Name/Personality header 让 gateway 拼 personalization preamble 在 SOUL 前面;
  9 Rust 单测 + 12 Python 单测
- ✅ **BL-E15 专注模式** (1 天, 央企语境从"领导来了"改名): Cmd+Shift+F 全屏伪 IDE;
  状态行/计时/底栏/光标都拟真 (GitHub Dark 配色);
  TabBar 加"⏸ 专注"按钮 (不知快捷键的员工也能用); Esc/快捷键/按钮 3 种退出
- ✅ **BL-E16 鲶鱼情绪 / 关系建立** (1.5 天):
  SOUL.md 加"情绪 / 关系建立"铁律 (5 类 ✅ 适合做 + 6 类 ❌ 不做 + 频率: 每 5-10 session 1 次);
  session_meta.py 持久化 last_chat_at + today_count, 给 LLM 时间感 ("3 天没找我"/"今天第 5 次");
  Dashboard "鲶鱼对你的印象" 卡 (透明 + 一键清空, 防 creepy);
  17 Python 单测 + 7 Rust 单测
- ⬜ **BL-E14 鲶鱼吐槽 PPT** (3-5 天, 留下周): pptx parser + critical personality + 拖放 UI
- ✅ **BL-E27 桌面状态浮宠** (原计划 5/22 起, 5/5-5/6 提前两周 ship 到 BL-E27.2):
  - ✅ BL-E27.1 MVP 4 状态浮窗 + 单击唤起主窗 (5/5)
  - ✅ BL-E27.2 真透明穿透 + 真拖拽 (5/6): cursor_position 80ms 轮询切 setIgnoreCursorEvents 解决 NSPanel 透明区拦点击; mousedown > 5px 触发 startDragging 真拖; 头顶气泡 + 4 状态走 Rust polling buffer (跨 webview 100% 可靠)
  - ✅ Cmd+Shift+P toggle + Option+Shift+1/2/3/4 4 屏角 (Retina 物理像素 → logical 修)
  - ✅ SVG 设计 v2 圆胖浮游 (v1 鱼竿弃)
  - ⬜ BL-E27.3 联动 BL-E13/E15 + 全屏自动隐藏 (2-3 天, 6 月初)
- 见 `docs/IDEAS.md` § 10/13/14/17 + `docs/BACKLOG.md` BL-E11/E14/E15/E19/E27 (E16 是另外的"社交健康检查")

#### #28 ★ 记忆纪律 / 记忆是资产 / 越用越懂 (BL-MM1~MM8) [Phase 1.5, 25%]  ★ 5/4 晚拍板 + ship MM1+MM5
> 鸿波 5/4 共识: **记忆是资产, 错了 update > 删除, 留版本作为成长痕迹**.
> 5/6 update: BL-MM2/MM4/MM6 ship, M.1 + M.2 完成度 25% → 60%.

**M.1 记忆覆盖 (改不删, 留版本):**
- ✅ **BL-MM1 记忆覆盖纪律 (SOUL 章节)** (5/4): 0 后端改动. 走 read-then-write + 把旧值 inline 塞进新值的备注里, 模拟版本感. 4 个 ❌ 禁止 (空说"改了"/编旧值/blind overwrite/不告知)
- ✅ **BL-MM2 / BL-MM4 RelationCard + MemoryHistoryCard ship** (5/6): Dashboard 双卡 — RelationCard 显"印象/影响"(鲶鱼帮员工总结的对话主题, 像日记), MemoryHistoryCard 显"硬事实"(员工明确告诉的事实, 像便签贴), 两卡都 30s setInterval 实时刷新 + 全量条目, AuditCard 文案改员工口语 ("这个提示很奇怪让人看不懂啥意思" 修了)
- ✅ **BL-MM3 hermes memory_save 包一层版本化** (5/7, 跟 BL-D14.5 hermes 0.12 升级提前一并 ship): `adapter.py:_memory_save_versioned` read-modify-write wrapper, 28 单测全 PASS, SOUL § 561 更新跨 session 也版本化了, `CATFISH_DISABLE_MM3=1` 可回退原行为

**M.2 主动学习 / feedback / 越用越懂员工:**
- ✅ **BL-MM5 主动学习员工偏好 (SOUL 章节)** (5/4 晚, **方案 A**): 0 后端代码
- ✅ **BL-MM6 显式 feedback UI** (5/6, **方案 B** ship): ChatBubble 加 👍/👎/"改" 按钮
- ✅ **BL-MM7 结构化用户画像** (5/6 鸿波"直接开始"拍板当天 ship, **方案 C**): `~/.catfish/user_profile.json` (writing_style / work_pattern.peak_hours/task_pref/review_pref / personality_traits + evidence_count). 3 次 evidence 才 propose, 红线字段严禁 LLM propose, 员工可 lock. user_profile.py 317 行 + UserProfileCard.tsx + 19 单测 PASS
- ✅ **BL-MM8 文书风格 fingerprint** (5/6 跟 MM7 同日 ship, **方案 D**): 员工历史文档抽: 句长 / 段落数 / 词频 (jieba 分词 + char-level n-gram 兜底) / 标点偏好 / 列表-散文比例 / 3-5 样本句. 存 `~/.catfish/style_fingerprint.json`, 写新文档时 skill 调用. style_fingerprint.py 454 行 + StyleFingerprintCard.tsx + 20 单测 PASS
- ✅ **BL-MM9 agent 自动抽 skill** (5/8 凌晨 ship, 鸿波"一次性别再分批"): catfish_propose_skill 工具 + 20 单测 PASS + SOUL § BL-MM9 纪律 (3 次门槛 / 红线 / 限流 24h / session ≤5). 跟 hermes "creates skills from experience" 对标但加**员工 confirm 门槛** + ~/.catfish/skill_proposals.jsonl 透明 audit trail
- ✅ **BL-MM10 memory 自精炼 loop MVP** (5/8 凌晨 ship, MVP 规则版): memory_distill.py + 24 单测 PASS. 抽 work_pattern.peak_hours (时间戳分布) + writing_style.bullet_pref (列表 vs 散文比例) + 红线过滤 + 24h 限流. **6/15 PoC 1 个月时**接入 LLM 真抽 (现在留 hook), 走 BL-MM7 confirm 流程
- ✅ **BL-MM11 skill 级 👍/👎/改 评分** (5/8 ship, 鸿波"一起做"): skill_feedback.rs + SkillFeedbackButtons.tsx 复用 BL-MM6 UI 模式. 一条 assistant 消息含 catfish_run_skill 时, 对每个 skill 独立加按钮. 写 ~/.catfish/skill_quality.jsonl, 给 BL-MM12 公式用
- ✅ **BL-MM12 综合质量分数 0-100** (5/8 ship): skill_audit.rs `compute_quality_scores` 公式 = 50×success_rate + 30×log-normalized_freq + 20×explicit_feedback_ratio. 6 单测 PASS. SkillAuditCard 加 quality_scores 区, 优/良/中/差 4 档颜色, 鼠标悬停看公式明细

- 见 `edge/identity/SOUL.md § 记忆覆盖纪律 + § 主动学习员工偏好` + `docs/BACKLOG.md § M`

#### #29 ★ Hermes 升级 + Curator 集成 [Phase 1.5, 30% 研究完成]  ★ 5/4 晚研究, 5/8 后启动议程
> NousResearch hermes-agent 升 0.12.0. 我们 0.10.0. 完整研究 + 集成方案归档, demo 前不动.
- ✅ **0.10→0.12 changelog 完整摘要** (5/4): 0.11 React/Ink CLI 重写 + Profile 系统 + Transport ABC. 0.12 后台 Curator + 57% 冷启 + 多 provider
- ✅ **catfish 4 层补丁脆性评估**: apply_brand_patch.py 468 行 AST 🔴 高 / rebrand.sh 183 行 sed 🔴 高 / string-map.yaml 84 行 🔴 高 / dispatch scrub (BL-D9) 🟢 低
- ✅ **Curator 接口 verified** (源码 quote): `curator.enabled: false` 一行 disable, 4 个参数 yaml 可调, Strict invariant *only touches agent-created skills*, **never auto-deletes — only archives**, pin 可豁免, `.curator_state` 可外部写
- ✅ **Curator vs Skills Hub 集成方案 5 步**: 不禁用, 默认开 + 保守参数 + 装时 pin 双保险 + Onboarding 知情同意 + Phase 2.5 反向数据流 → admin 看公司级 skill 健康热图
- ⬜ **5/8 后 verify 2 件**: `tools/skill_usage.is_agent_created()` 判定逻辑 + `skill_manage` pin API
- ⬜ **5/15-5/22 升级实施** (走 HERMES-UPGRADE.md § 5 阶段 B): brand patch 重构 字符串 → 三层运行时拦截 (wrapper subprocess + Shell Hook + 兜底 source patch). 估计 468 行能裁到 50-100 行
- ⬜ **Q3 上游 issue (不预投 PR)**: 5/7 修订 — git hooks 方案 ship 后我们这端已够稳, 上游 PR 降级到 nice-to-have. Q3 投个 issue 探 NousResearch 意愿 (5 分钟), 有 buy-in 再投 PR; 没回应就维持 git hooks 路线 (每次升级 30 分钟补规则). 详见 `HERMES-UPGRADE.md § 6`
- 完整方案见 `docs/HERMES-UPGRADE.md` (430 行, 含时间表 / 风险表 / 升级回归 checklist)

---

### 缺位 area · 完全没动

#### #16 ★ Production 部署 + 运维 [Phase 1.5, 50% MVP]  ★ 5/3 docker-compose ship
> 现都跑鸿波本机 dev mode, 给客户也跑不起.
- ✅ **docker-compose 全栈** (5/3 BL-F7 MVP): postgres + identity + gateway + skills-hub + nginx 反代
- ✅ **Dockerfile × 2** (gateway / identity, builder + runtime 两阶段, 非 root, healthcheck, alembic 启动建表)
- ✅ **nginx.conf.example** (HTTPS 强制 / SSE 不缓冲 / /api / /sso / /hub 路由 / cert 配置)
- ✅ **.env.production.example** (PG 密码 / OIDC issuer / API key / hub token)
- ✅ **docs/PRODUCTION-DEPLOYMENT.md** (~250 行, 5 步 15 分钟装好 + 升级 + 监控 + 备份 + 5 个常见问题)
- ⬜ k8s manifests (Helm chart, 大客户 ≥ 200 员工用) · 1 周
- ⬜ catfish-cloud 多租户运营手册 (SaaS 版) · 1 周 (BL-F8)
- ⬜ 监控告警 (gateway 错误率 / hermes 崩溃) · 1 周 (BL-F9)
- ⬜ 性能基准 (多 user 并发) · 0.5 周 (BL-F10)
- ⬜ 安全测试 (SSO/RBAC/审计渗透) · 1 周 (BL-F11)
- ⬜ central/distribution (托管安装/PyPI/签名) · ★ 只 README stub
- ⬜ central/telemetry (匿名遥测) · ★ 只 README stub
- **风险**: 5 月 demo 后客户说"装一份给我们", 没产品形态可交付

#### #19 中央服务 stub (5 个未实现) [Phase 2, 0%]
> README 写了定位 + P0/P1, 全无代码.
- ⬜ central/distribution · 安装包托管 + 签名 · 1-2 周
- 🟢 central/mcp-registry · 企业 MCP 连接器仓库 · **5/9 一夜 ship Phase 1+2 完整闭环**. 鸿波"可以完成了" → "B" → "现在就开始做" → "为什么又留尾巴" → "剩下的一点做完" 五次推动. 服务 :8996 + 4 manifest (jira/gitlab/filesystem/time) + 部门权限过滤 + gateway 反代 + Companion 订阅按钮真接通 (mock OAuth 闭环) + secret-broker 集成 + PG 主存储 (跟 gateway/identity 同套) + sqlite fallback + alembic migration + 45 单测. Phase 2.1 真 OAuth token exchange 框架就位 (env=real 切). Phase 3 (pod-per-user + Agent tool 注入) 留 5/26+ demo 后做. 飞书/钉钉/Confluence 排 6 月.
- 🟢 central/secret-broker · 凭据集中存储 :8995 · **5/9 ship Phase 2 dev MVP**. FastAPI + keyring 后端 (mac Keychain / Win wincred / Linux libsecret) + 内存兜底 + 5 endpoint (set/get/exists/delete/health). 给 mcp-registry Phase 2 OAuth token 存储用. 16 单测. prod KMS / Vault 升级留 5/22+.
- ⬜ central/telemetry · 匿名遥测聚合 · 1 周
- ⬜ central/skills-hub · 见 #11 (跟它合并)
- **优先级**: secret-broker 是 SOE 客户合规硬要求, 应该先做

#### #18 销售物料 + demo 准备 [Phase 1, 90%]  ★ 5/6 7 文档套件全 ship
> 5 月中旬 demo 倒计时, 距 5/14 ~8 天.
- ✅ 演讲稿 4 份 (DECK 32 张 / ELEVATOR V1-V5 / Q&A 13 题 / PREP 检查清单)
- ✅ POC-PLAN.md (1-2-3 周 PoC 计划模板)
- ✅ POSITIONING / COMPARE-1PAGER / COMPETITIVE-DIFFERENTIATION
- ✅ **MAY-DEMO-SCENARIO-5-SELF-EVOLUTION.md** (5/5, 60s 弹性加场)
- ✅ **MAY-DEMO-SCRIPT-2026-05-14.md** (5/6, 完整 5 场景 17-22 min, 每场含目标/时长/鸿波说什么/鲶鱼期望响应/客户视角痛点/失败 fallback/关键代码位置)
- ✅ **DEMO-CUSTOMER-QA-2026-05-14.md** (5/6, 30+ 题 6 类: 安全/出境/部署/性能/商业/技术 + 兜底句)
- ✅ **DEMO-EMERGENCY-PLAN-2026-05-14.md** (5/6, 现场挂 → 30s 切稳态预案 + checklist + 用语模板)
- ✅ **RUNBOOK-DEMO-VERIFICATION-5-14.md** (5/6, 鸿波本机 5-10 分钟跑完 9 项)
- ✅ **README-FOR-CUSTOMERS.md / QUICKSTART-EMPLOYEE.md / DEPLOYMENT-RUNBOOK.md** (5/6, 客户决策人/员工/IT 各一份)
- 🔴 **真机彩排 ×2** (demo 前 3 天 5/11 + 前 1 天 5/13) · 各 1h
- 🔴 **5 场景实录视频** (现场全挂兜底) · 1 天 (5/12 前)
- 🔴 **demo 机子 USER.md / journal seed** (老李 / 戴明利 / KA017 / 60天未下单 等场景 1/4 用到的预存事实) · 0.5 天 (5/10 前)
- 🟠 **PPT 实际制作** (按 DECK 大纲填 Keynote) · 1 天
- 🟠 **客户安全说明 1 页 PDF** (从 SECURITY-REVIEW 抽精华给信安部门) · 0.5 天 (5/12 前)
- 🟠 部门汇报模板 + EIS 截图 · 公司带回 (BL-L3/L4)
- 🟠 报价单 (50/200/1000+ 三档) · 鸿波决策 (BL-B2)
- 🟠 销售路径 (自销/渠道) · 鸿波决策 (BL-B6)

#### #20 团队建设 [跨 phase, 1人]
> Phase 2/3 一定带不动 1 人, 但扩到 3-5 人才能 ship.
- 当前: 鸿波 1 人 + 鲶鱼 dogfood
- ⬜ 1 后端工程师 (Phase 2 RBAC/Skills Hub/Win) · 5 月底前启动
- ⬜ 1 销售 / BD (5 月 demo 后必须考虑)
- ⬜ 1 客户成功 / 驻场 FAE (PoC 启动后)
- ⬜ 法务 / 合规 (软著 + 商标 + 法律 review)

#### #21 法律合规 [Phase 1, 0%]
> 软著 + 商标 + 公司注册 + 隐私政策, 拖会出大问题.
- ⬜ 公司注册 / 工商登记 · Phase 1 内 (BL-H3)
- ⬜ 软著申报 · Phase 1 内 (BL-H1, CHANGELOG 已为这个写)
- ⬜ 商标"鲶鱼/Catfish" · Phase 1 内 (BL-H2) — 容易被抢注
- ⬜ 数据合规 律师 review · Phase 2 前 (BL-H4)
- ⬜ 算法备案 (catfish-cloud 国内运营) · cloud 上线前 (BL-H5)
- ⬜ 隐私政策 / 用户协议 · cloud 上线前 (BL-H6)
- ⬜ 第三方依赖 license 全审 · 开源前 (BL-H7)
- ⬜ fonts/opensource 二进制从 git history 移出 · 开源前必做 (BL-H9)

---

#### #30 ★ 安全 + 合规 [Phase 1.5, 85%]  ★ 5/6 7 gap 全闭环 + CI 集成
> 央企客户信安部审查的核心区, 5/14 demo 客户必问. 5/6 一天集中闭环.
- ✅ **威胁模型 + 现状盘点** (`SECURITY-REVIEW-2026-05-06.md`): T1 数据出境 / T2 任意代码 / T3 凭证 / T4 供应链 / T5 网络暴露 / T6 webview / T7 审计 7 类攻击面对照
- ✅ **G1 网络暴露**: gateway / skills-hub 默认 0.0.0.0 → 127.0.0.1 (员工电脑不暴露局域网) + HOST=0.0.0.0 显式警告
- ✅ **G2 供应链**: Skills Hub URL fetch sha256 校验 (客户端) + server 端发布时自动算 sha256 + `CATFISH_HUB_REQUIRE_HASH=1` 严格模式 + 篡改场景拒装 (端到端验证)
- ✅ **G3 任意代码**: execute_code dispatcher 守卫拦 25 类危险 (凭证~/.ssh ~/.aws/credentials Keychain /etc/shadow / 外联 curl wget requests urllib httpx socket / 危险 shell rm -rf / fork bomb / dd / mkfs / chmod 777 /), 命中 → 拒绝 + audit log 留痕
- ✅ **G4 webview**: Tauri CSP null → 显式 `default-src 'self'` 等 8 条策略
- ✅ **G5 依赖 CVE 全扫**: cargo audit (Cargo.lock 556 deps × RustSec 1067 advisory = **0 vulnerability** + 19 informational warning 不在调用路径) + pip-audit (4 组件 / 103 deps **0 CVE**) + npm audit (companion-app prod **0 CVE**) + `.github/workflows/security.yml` 4 job CI
- ✅ **G6 数据出境清晰**: `DATA-FLOW-DIAGRAM.md` 1 页 ASCII 架构图 + 4 条出境路径 (chat/SSO/skills hub/audit) + 7 类不出境数据 + 3 配置档位 (内网闭环/内网主+外网 fallback/全外网) + 6 客户预期问答
- ✅ **G7 secrets 硬编码**: gitleaks 8 类正则全仓 (AWS / GitHub PAT / OpenAI / Anthropic / Google / JWT / 私钥 PEM / 字面量密码) **源码 0 hit** + CI 自动拦
- ✅ **prompt_security 凭证检测** (5/6 之前已有, sec-review 整理到位): ≥40 种 secret 模式, 检测到的密码值不入 audit log
- ✅ **OIDC RS256 SSO + a2a JWT** (5/6 之前已有)
- ✅ **双层 audit log**: gateway 中央 metadata-only / tool-bridge 边缘含 args_preview, jsonl 标准 SIEM 接 (DEPLOYMENT-RUNBOOK § 6 fluent-bit 配置示例)
- ⬜ **第三方渗透测试** · 5/22 后, 找外部团队
- ⬜ **macOS Notarization 真公证** · 5/22 后, release 前
- ⬜ **SBOM 自动化 (CycloneDX)** · 5/12 前可出, 客户合规要
- ✅ **BL-A1+A2 真 Agent (单任务自完成 + 多任务并发)** · ★ 5/7 单日 ship (本来 6+5 天 sprint):
  - ✅ A1.1 gateway auto-continue on finish_reason=length (12 测试)
  - ✅ A1.2 tool 失败 retry hint 注入 (13 测试)
  - ✅ A1.3 self-critique 幻觉完成检测 (14 测试)
  - ✅ A1.4 DAG step plan 铁律 (SOUL.md, 软纪律, 真后端 DAG planner 5/22 后)
  - ✅ A2.1 chat 非阻塞 + task_manager + 3 工具 (catfish_run_task / status / result, 12 测试)
  - ✅ A2.2 任务状态查询 (跟 A2.1 一起 ship, catfish_task_status)
  - ✅ A2.3 任务完成通知 (桌宠 bubble queue + macOS osascript, 4 测试)
  - ⬜ A1.5 e2e 真 docx 30 页 (留 5/13 dryrun 真跑验)
  - ⬜ A1.6 文档收口 (✅ 场景 2.6/2.7 已加, README/SECURITY ✅, FEATURE-TRACKS ← 在加)
  - ⬜ A2.4 Dashboard TasksCard (P1, 5/22 后)
  - ⬜ A2.5 e2e 并发 demo (留 5/13 dryrun)
  - **5/14 demo 杀手场景**: 2.6 (单任务 Agent 自完成) + 2.7 (多任务并发)

- ✅ **BL-S29 真技术沙箱 nsjail / sandbox-exec** · ★ 5/7 单日全 ship (本来 12 天 sprint, 实际 1 天):
  - ✅ S29.1 macOS sandbox-exec .sb profile + 13 shell tests (10 恶意 + 3 sanity)
  - ✅ S29.2 tool-bridge adapter 接入沙箱 dispatch + 15 unit tests
  - ✅ S29.3 端到端 (双层 L1+L2) + 6 e2e tests + audit 字段 + 5/14 demo 场景 2.5 + SECURITY-REVIEW G3 重写
  - ✅ S29.4 nsjail Linux 容器 + 13 docker tests (★ fork bomb / mem bomb 真拦)
  - ✅ S29.5 三层 fallback (sandbox-exec / nsjail / docker) + sandbox.py detect_sandbox_kind() 跨平台
  - ✅ S29.6 测试矩阵扩到 macOS 25 + Linux 25 + python 24 = **86 测试全绿** + SECURITY-REVIEW 附录 A 给信安看
- ⬜ **客户安全说明 1 页 PDF** · 5/12 前 (信安部门交付物)
- ⬜ **红队演练 (LLM jailbreak / prompt injection)** · 6 月

---

### Phase 2/3 · 长期 backlog (不细列)

#### #22 三层统一搜索 [Phase 1.5, 0%]
> 公网 + 公司内 + 本地, 智能路由. (BL-E25, 1-2 周)
- ✅ 本地 FTS5 已 ship (edge/local-search)
- ⬜ 公司内 (Browser Agent + MCP)
- ⬜ 公网 (现走 Browser Agent)
- ⬜ 智能路由层

#### #23 长期人机关系 / 反直觉特性 [Phase 3+, 5%]  ★ 5/2 部分 (BL-E13 MVP)
> 鲶鱼晨报 / 周末不干活 / 主动闲聊 / 情绪 / 社交健康检查 / 学习清单等 (BL-E1~E20).
> ★ 主动闲聊 BL-E13 已 C-MVP ship, 见 #25.
> ~10-15 个 1-3 周的 idea, 都在 IDEAS.md 草稿里, 不在 demo 主线.

#### #24 web-ui [Phase 2 可选, 0%]
> 给不想装 Companion 的员工纯浏览器入口. README stub.

---

## 📁 文档地图 (老 doc 角色澄清)

| Doc | 角色 | 状态 |
|---|---|---|
| **`FEATURE-TRACKS.md`** (这份) | 主入口, 唯一 single source of truth | ★ 新 |
| `BACKLOG.md` | 工程颗粒度归档 (BL-XXX commit-level) | 仍维护, 不再加新 section, 入口移到这里 |
| `ROADMAP.md` | 客户视角 4 phase 一页 | 不动, 销售用 |
| `PROJECT-STATUS.md` | ⚠️ 4-30 stale, 替代为 FEATURE-TRACKS | 待标 deprecated |
| `CAPABILITY-MATRIX.md` | 现状能力快照 (跟 tracks 互补) | 不动 |
| `IDEAS.md` | 想法草稿源 | 进 backlog 前的池子 |
| `*-DESIGN.md` (RBAC/QUOTA/AUTH) | 技术 spec | 不动, tracks 引用 |
| `*-PROTOCOL.md` (PLAN-D) | 协议 spec | 不动 |
| `MAY-DEMO-*` (4 份) | demo 临时物料 | 5 月后归档 |
| `POC-PLAN.md` | PoC 模板 | 不动 |
| `POSITIONING / ELEVATOR / COMPARE / COMPETITIVE` | 销售对外口径 | 不动 |
| `STRATEGY / SSO-RATIFY / SSO-CUSTOMER / SKILL-LIFECYCLE` | 决策记录 | 历史归档 |
| `TOMORROW.md` | ⚠️ 4-26 stale | 待删 |
| `BRAND-VOICE.md` | 品牌叙述纪律 | 不动 |
| `CHANGELOG.md` | 实际 ship 日记 | 持续更 |

---

## 🔍 这次扫描的 6 个发现 (PROJECT-STATUS 漏的 / mismatch)

1. **★ email-agent 有真代码** (Foxmail mac adapter + box parser + 7 tests), PROJECT-STATUS 完全没提
2. **★ feishu-monitor 有真代码** (CDP client + monitor + handlers + relevance + tests), 没主线跟踪
3. **★ 5 个 central 服务全 stub** (distribution/mcp-registry/secret-broker/skills-hub/telemetry), README 写了 P0/P1 优先级但代码 0
4. **edge/communication-coach** 是 catfish-roleplay skill 不是产品, 名字误导
5. **edge/web-ui** README 自己写"P2 MVP 不做", 别老挂着空文件夹
6. **PROJECT-STATUS Phase 数 stale**: 五一推前 Phase 2 (20%→35%) + Phase 3 (10%→30%)

---

## 🎯 鸿波 review 重点 (你拍这些)

**已拍** (5/2):
- ✅ Win 客户端: 暂不动, 等 PoC 客户触发 (#2)
- ✅ 第 3 个 skill: project-approval (#14)
- ✅ identity-server alembic 迁: 跟 gateway 对齐 (#9)

**待拍**:
1. **email-agent / feishu-monitor demo 演不演** — 演 → 立即接通 Companion (1-2 周); 不演 → 改"P2 拖" 标记
2. **central 5 个 stub 删 / 留 / 做** — distribution/secret-broker 建议留(SOE 合规要), 其他 3 个考虑删 README 减负
3. **TOMORROW.md / PROJECT-STATUS.md 处理** — TOMORROW 4-26 stale 删了; PROJECT-STATUS 标 deprecated 还是合到 FEATURE-TRACKS?
4. **track 漏吗** — hermes-fork / hermes-customizations / hermes-plugins 是否建 track?
5. **跨 gateway 实例共享 PG 测试** (BL-D17 完整) — 0.5 天, demo 后做?

---

## 📜 这次 sprint ship 总结 (5/2 周末工作日记)

**~30 个 commit**, 127+ 单测 0 fail, 真机端到端跑通 5 大子系统 (PG / RBAC / Quota / Hub / 主动闲聊).

| # | commit 主题 | 改动 | 触发 |
|---|---|---|---|
| 1 | 五一 sprint 5/1-5/5 | 多模态 / Skill lifecycle / Plan D / RBAC / Quota / PG MVP / 浮窗 | 五一 sprint 计划 |
| 2 | datetime.utcnow 清理 | Python 3.12 deprecation 修复 | BL-L27 |
| 3 | ALLOW.md token-overlap | jieba 中文分词替代子串 | BL-L28 |
| 4 | FEATURE-TRACKS.md 主入口 | 24 个 track + 6 个发现 | 鸿波点播 |
| 5 | Dashboard 角色化 | manager 部门 quota/audit 卡片 | 鸿波点播 |
| 6 | dev_users.yaml 多账号 | DevUserSwitcher 顶部黄条 | 鸿波点播 (env hack 太丑) |
| 7 | autostart 默认 prod | DEV opt-in 走 CATFISH_AUTOSTART_ENV=dev | 鸿波点播 |
| 8 | PG migration 全套 | gateway db.py + quota PG + audit PG + alembic | 鸿波点播 |
| 9 | chat 接 record_usage | quota_events / gateway_audit 真 PG 双写 | bug 修 (PG 表空) |
| 10 | project-approval skill | 第 3 个业务 skill (BL-L6) | 鸿波拍板 |
| 11 | identity-server alembic | 双服务 version_table 隔离 | 鸿波拍板 |
| 12 | BL-E13 主动闲聊 C-MVP | LLM 起话题 + 通知 + Dashboard 卡 | 鸿波点播 (journal 攒) |
| 13 | RBAC 三件套 | manager PUT quota / admin 全局聚合 / DepartmentQuotaCard inline edit | 鸿波点播 |
| 14 | Quota 接 chat 阻断 | check_quota → 429 + Companion friendly UI | 鸿波点播 |
| 15 | useChat status 覆盖 bug 修 | streamChat 后无条件 done 覆盖 onError 的 error 状态 (UI 不显错的根因) | 真机调试 |
| 16 | BL-C12 dry-run + 回滚 | skill_install 后 importlib 验证 + 失败 rollback | 鸿波点播 |
| 17 | BL-C13 dedup 检查 | install 前查同名 / 描述相似 skill | 鸿波点播 |
| 18 | 中央 Skills Hub server MVP | central/skills-hub FastAPI: publish/list/get/download/delete + audit | 鸿波点播 |

**测试**: tool-bridge 23 + skills-hub 21 + gateway 83 + identity 32 + Companion 8 = **167 全过**.

**真机验证 (Mac)**:
- ✅ PG 真双写: 5 张表 (alembic_version_gateway/_identity / users / registry_agents / quota_events / gateway_audit)
- ✅ Quota 429 闭环: Alice → 撞 429 → friendly 横条 → manager 改 → 再聊通过
- ✅ Skills Hub publish/list/download/audit 全通
- ✅ Companion 角色切换 (admin/manager/employee 看不同卡)
- ✅ 跨 session 记忆 (employee_journal 真注入, 小鲶引用上次具体事项)

**Phase 2 后端 + 用户态 + Skills Hub 中央 MVP 完整 ship**, 剩:
- Win 跨平台 (拍板暂不动)
- Production 部署 (0%)
- Hub 审核流 / Companion hub URL 拉取 / 部门 auto-push (3 周, demo 后)
- email-agent / feishu-monitor 接通 Companion (待决策)

---

## 📅 下次开工建议 (5/6 update)

**🔴 5/14 demo 阻塞 (距今 ~8 天)** — 全是非工程的现场准备:
- 真机彩排 ×2 (5/11 前 3 天 + 5/13 前 1 天) · 用 DEMO-CUSTOMER-QA-2026-05-14 题库练
- 5 场景实录视频 (现场全挂兜底) · 1 天 (5/12 前)
- demo 机子 USER.md / journal seed (老李/戴明利/KA017 故事 fixture) · 0.5 天 (5/10 前)
- .app prod build + 临时签名 · 0.5 天 (5/9 前)
- PPT 实际填 (按 docs/MAY-DEMO-DECK.md 大纲) · 1 天
- 客户安全说明 1 页 PDF · 0.5 天 (5/12 前)
- RUNBOOK-DEMO-VERIFICATION 鸿波本机跑 9 项 · 5-10 min (任何 ❌ 立刻修)

**🟠 demo 后 (5/15+)**:
- 完整 BL-E13 主动闲聊 (节假日 / snooze / 配置 UI) · ~1-2 周
- ~~**BL-MM3 hermes memory_save 包版本化** (跟 5/15 hermes 0.10→0.12 升级捆绑)~~ ✅ 5/7 提前完成 (跟 BL-D14.5 一起)
- ~~**BL-MM7 结构化用户画像** · ~1 周~~ ✅ 5/6 提前 ship (跟 MM6/feedback 同日)
- ~~**BL-MM8 文书风格 fingerprint** · ~1-2 周~~ ✅ 5/6 提前 ship (跟 MM7 同日)
- ~~⭐ **BL-MM9 agent 自动抽 skill**~~ ✅ **5/8 凌晨提前 ship** (鸿波"一次性别再分批") — 20 单测 PASS, SOUL § BL-MM9 纪律 (3 次门槛 / 红线 / 限流)
- ~~⭐ **BL-MM10 memory 自精炼 loop**~~ ✅ **5/8 凌晨 MVP ship** — 24 单测 PASS. MVP 规则版 (peak_hours + bullet_pref). LLM 真抽 hook 留, 6/15 PoC 时接入
- BL-E27.3 桌宠联动 BL-E13/E15 + 全屏自动隐藏 · 2-3 天 (6 月初)
- ~~真技术沙箱 nsjail/sandbox-exec (G3 升级) · 5/22 后~~ ✅ 5/7 凌晨 ship (12 天压 1 天, BL-S29 全套, 86 测试矩阵)
- macOS Notarization 真公证 + 第三方渗透测试 · 5/22 后
- email-agent / feishu-monitor 接通决策
- Skills Hub 审核流 (#11) · 1 周
- 部门 auto-push (依赖 federation) · 1-2 周

**🔴 商业决策 (鸿波拍板)**:
- 5 月 demo 选 1-3 家具体客户名单 (5/5 前)
- PoC 报价单 (50/200/1000+ 三档)
- 销售路径 (自销 vs 渠道)
- 5 月底前启动扩招 (1 后端 + 1 销售)

---

## 📊 demo 卖点 verified 全清单 (12 个, 5/6 加 2)

| 卖点 | 演法 | 状态 |
|---|---|---|
| 1. **跨 session 记忆** | "上次戴明利那事" → 小鲶引用 journal 具体事 | ✅ 真验过 |
| 2. **多模态 + PDF 结构化** | 上传社保 PDF (320 人) → parse_file 自动识别 anchor → catfish_xlsx 转 Excel, 不啃原文件不出员工电脑 | ✅ 5/6 真用社保 PDF 验 |
| 3. **业务 skill (3 个)** | 汇报 / 周报 / 立项 .docx 真出文件, **43 测试** | ✅ |
| 4. **RBAC 三角色** | DEV 切 admin/manager/employee 看不同卡 | ✅ |
| 5. **Quota 闭环** | 撞 429 → friendly 提示 → manager 改 → 通过 | ✅ |
| 6. **中央 PG audit** | psql 直查 quota_events / gateway_audit | ✅ |
| 7. **Skills Hub + sha256** | publish → list → download + 篡改场景拒装 | ✅ 5/6 端到端验 |
| 8. **Plan D federation 协议** | alice ↔ bob 单机 mock (给 IT lead 看) | ✅ |
| 9. **主动闲聊 + 桌宠气泡** | "测一下 ▶" → 桌宠头顶冒气泡 + chat 直接出 assistant 消息 (员工不用按发送) | ✅ 5/6 真验 |
| 10. **浮窗** | Cmd+Shift+Space 全局召唤 | ✅ |
| 11. **桌宠 + 4 状态联 LLM** ★ 5/6 加 | Cmd+Shift+P / Option+Shift+1234 4 屏角 / 透明穿透不拦点击 / 真拖拽 | ✅ |
| 12. **安全合规 7 gap 闭环** ★ 5/6 加 | 0 高危 CVE / execute_code 守卫 25 类 / sha256 供应链 / 数据流向图 / OIDC SSO + 双层 audit | ✅ 信安部门 demo 必给 |

10 个卖点全部技术 ready, demo 主战场转移到**演讲 / 物料**.
