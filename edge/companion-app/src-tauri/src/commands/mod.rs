//! 暴露给前端的 invoke 命令集合。
//!
//! 设计原则：
//!   - 三个核心服务一人一个文件（gateway / chrome / local_search / tool_bridge），
//!     避免 service.rs 越长越大
//!   - 公共能力（health / logs / sessions / system / identity / skills）
//!     按职责而非按服务拆
//!   - 每个 #[tauri::command] 必须返回 Result<T, String>，
//!     错误统一序列化成字符串给前端

pub mod advisor_cache; // BL-ADVISOR-CACHE (5/22 Phase 7): 缓存 advisor 结果
pub mod advisor_config; // BL-ADVISOR-CONFIG (5/22 Phase 7): 读 yaml advisor section
pub mod advisor_relevance; // P3.5.4.2 (6/16 鸿波): BGE-M3 advisor 注入相关性筛选 + sqlite cache
pub mod advisor_closed;
pub mod advisor_task_state;
pub mod advisory; // 6/7 BL-MANIFESTO-ADVISORY-PHASE1: advisory local state (~/.catfish/advisory_state.db)
pub mod agent;
pub mod connect_link; // 10/14: catfish://connect 接入链接 (门户地址 + 接入码一点即填)
pub mod server_pairing; // 10/14: 门户地址 + 接入码自动导入中央 CA
pub mod upload_stage; // 10/14: 上传分块写临时文件 (上限统一 200MB)
pub mod attachments; // BL-FILE-SESSION-INDEX-V1 Phase 1 (5/30): 附件 metadata 持久化到 ~/.catfish/attachments.db, 跨会话恢复 + 列表
pub mod audit;
pub mod audit_chain; // P3.3.51 (6/12): jsonl 防篡改哈希链 (decisions / political_scan 用)
pub mod audit_export; // P3.3.54 (6/12): 审计员看的多 sheet xlsx 导出
pub mod auth;
pub mod briefing_context; // BL-BRIEFING-DECISION (5/21 Phase 5): distilled_facts + recent sessions
pub mod calendar; // BL-CALENDAR-INTEGRATION (5/20): macOS Calendar.app via osascript JXA
pub mod chrome;
pub mod meeting_minutes_cmd; // 10/1: 说话人改名 + 纪要 (tool-bridge meeting/summarize)
pub mod meeting_asr_cmd; // 10/1: 会议组件包安装 + 转写, 见 services::meeting_asr
pub mod embed_model_cmd; // 10/6: 向量模型组件包状态 + 安装, 见 services::embed_model
pub mod meeting;       // 10/1: 会议录音 (建会议 / 录 / 停 / 列表), 见 services::meeting_*
pub mod meeting_import; // 10/11: 导入录音笔 / 手机录音 → 一场会议, 后面复用转写
pub mod notify_prefs; // 10/12: 「提醒同步到微信」开关 + 测试推送 (tool-bridge wechat_push.py)
pub mod experts_local; // 10/18: 本机专家 —— 定义编译在 tool-bridge, 这里落 hermes profile + 工作台「找谁」
pub mod workflows; // 10/17: 工作流 skill —— 技能页编辑 / 试跑 / 运行记录 + 全局对外步骤确认
pub mod forms; // 10/17: 一起填表 —— 「协同」页发起 / 填写 / 进度 (tool-bridge forms.py)
pub mod sales; // 10/12: 销售跟进 —— 早安「客户跟进」雷达 + 销售模板 (tool-bridge customers.py)
pub mod components;    // 10/1: 可选大组件 (会议纪要组件包等) 下载 / 校验, 见 services::components
pub mod codex_gateway; // hermes 网关健康检查 + 重启 (8/15 从 codex_backend 切出)
pub mod codex_helper;  // 内嵌 Python helper + JSON 解析 (8/15 从 codex_backend 切出)
pub mod codex_probe;   // 路径解析 + codex 二进制探测 (8/15 从 codex_backend 切出)
pub mod codex_shim;    // hermes codex shim 装卸 (8/15 从 codex_backend 切出)
pub mod codex_backend; // Hermes 原生 codex_app_server runtime 的 Companion UI 接入
pub mod cron; // P3.5.105 (6/25 鸿波 catch "定时任务跑没跑结果如何都看不到"): cron 监控 + 操作 (读 ~/.hermes/cron + 写 P26 endpoint)
pub mod curator;
pub mod dream; // P3.5.1 (6/15 鸿波): Dream Engine — 员工主动触发 long-term 蒸馏, picker model
pub mod email_list_cache; // 10/31: 邮件列表落盘缓存, 重启后先显示上次的列表
pub mod email; // BL-COMPANION-EMAIL-DIGEST (5/18): 邮件简报卡的后端 shell-out
pub mod email_last_draft; // 9/29: 最近存进草稿箱的那封 → 对话结束后跳到草稿箱
pub mod endpoints;
pub mod expert_bots; // Hermes Profile 专家 Bot 管理器（默认关闭）
mod expert_bots_config;
mod expert_bots_gateway;
mod expert_bots_profiles;
mod expert_bots_types;
pub mod feedback;
pub mod file;
pub mod file_parse;
mod file_parse_env; // 9/28: 解析用的 Python / 脚本在哪 (Windows 找不到 Python)
pub mod gateway;
pub mod health;
pub mod hermes; // P3.5.125 (6/26 鸿波 catch): hermes hang 监控 + 自动重启
pub mod hermes_install_artifacts; // 离线安装包定位 (8/15 从 hermes_install 切出)
pub mod hermes_install_base;      // 常量 + 进度上报 + pinned 版本 (8/15 切出, 是其余几层的地基)
pub mod hermes_install_health;    // 版本解析 + 健康检查 (8/15 切出)
pub mod hermes_install_recover;   // 中断事务恢复 + 装机锁 (8/15 切出)
pub mod hermes_install_state;     // 落盘状态 + 原子写 (8/15 切出)
pub mod hermes_install_steps;     // Unix 装机步骤 + Windows 共用的附加依赖安装
pub mod hermes_gateway_launcher; // 10/19 Windows: hermes 升级后重写开机自启脚本 (老脚本写死 3.11)
pub mod hermes_install_gateway;   // 10/14: 换 hermes 目录前停网关, 装完再起
pub mod hermes_install_native;    // 10/14 hermes v0.22: Companion 自己装 (Python 3.14 + wheelhouse), mac / Windows 共用, 不再调上游安装器
pub(crate) mod hermes_python_dlls; // 10/21 Windows: 安全软件拦了 vcruntime DLL 时用 System32 那份继续, 都没有就说清楚
pub mod hermes_install; // 7/15 BL-CATFISH-MAC-OFFLINE-INSTALL: mac dmg 首启装 hermes-agent 本体 (offline install.sh + 4 artifacts)
#[cfg(any(target_os = "windows", test))]
pub mod hermes_install_windows; // Windows 首启后台安装，避免 MSI CustomAction 弹黑窗
pub mod hermes_memory; // BL-DASHBOARD-HERMES-MEMORY-CARD (5/16)
pub mod hermes_plugin_env; // 8/9: 从 hermes_plugin 抽出 —— ~/.hermes/.env 的 API_SERVER_KEY 维护
pub mod hermes_plugin_baked; // 8/21: BAKED_* 常量表, 从 hermes_plugin 抽出 (800 行红线)
pub(crate) mod hermes_runtime_deps; // 10/21: hermes 实际在用的 Python 环境 (含它另建的) 缺鲶鱼的包就离线补
pub(crate) mod hermes_dep_overrides; // 10/25: 不升 hermes 单独把有漏洞的依赖换新 (hermes-dep-overrides.txt)
pub mod hermes_profile_plugins; // 10/30: 专家机器人 profile 里的插件副本跟着同步
pub mod hermes_policy_plugin; // 10/21: 红线策略插件 catfish-policy 随 Companion 装 + 启用
pub mod hermes_plugin; // P3.5.56 (6/21 鸿波): Companion boot 自动装 catfish-xcatfish-user plugin (baked + ensure config.yaml enabled)
pub mod hermes_store_guard; // 10/29: hermes 登记的 Python 没建好依赖环境 → 挪开登记, 网关回到 venv
pub mod hermes_gateway_health; // 10/27: hermes 网关没在跑就 `hermes gateway start` 拉起来 (重启后 / 打开 Companion 时)
pub mod http_proxy; // BL-CSP-PROXY (7/18 鸿波): Rust reqwest HTTP 代理, CSP connect-src 保持严格
pub mod identity;
pub mod identity_bundle; // BL-IDENTITY-INJECT-DECOUPLE (5/26): SOUL/USER/memories prefetch 给 /v1/chat/completions body
pub mod journal; // BL-JOURNAL-TODO-EXTRACT (5/20): ~/.catfish/employee_journal.md TODO 抽取
pub mod learning;
pub mod local_search;
pub mod local_search_scope; // P3.5.126 (6/26 鸿波 catch): local_search 索引目录 UI 管理
pub mod local_search_stats; // P3.5.127 (6/26 鸿波 catch): local_search 索引状态查询 (文件数/类型/大小/时间)
pub mod logs;
pub mod memory_history;
pub mod picker_state; // P3.5.2 (6/16 鸿波): chat picker 持久化 ~/.catfish/picker_state.json — plugin sync_turn 跟随
pub mod self_serve; // 6/8 BL-EMPLOYEE-SELF-SERVE: A1 重置 / A2 导出 / A3 导入 (BL)
pub mod task_uid_cache; // P3.5.91 (6/23 鸿波): 早安 task → canonical taskUid 客户端 cache — 治 LLM 给新 uid 时 session 关联失联
pub mod tool_perf; // P3.5.59 (6/22 鸿波): tool-bridge audit jsonl 聚合 per-tool perf (count / success_rate / p50/p95/p99)
pub mod transparent_log_store; // 10/25: 外发记录存内容 + 指纹链 + 保留期
pub(crate) mod transparent_log_redact; // 10/25: 打码规则变严后旧记录统一脱敏 + 重算指纹 (留存原指纹给中央锚点核对)
pub mod transparent_log; // 6/8 BL-EMPLOYEE-SELF-SERVE A4: 数据外发日志 (员工自审) // BL-ADVISOR-TASK-STATE (5/22): done/snoozed/ignored 状态
                         // BL-STYLE-FP-USE-INDEX (7/27): style_fingerprint_dirs 整个删了.
                         // style_fingerprint 现在直接查 local_search 索引 (~/.catfish/search.db), 目录范围
                         // 唯一由 search-scope.yaml 决定 → 用 local_search_scope 那套命令, 不再有第二份
                         // companion.yaml style_fingerprint.scan_dirs 配置.
pub mod decisions; // BL-ADVISOR-DECISIONS (5/21 Phase 7): ~/.catfish/decisions.jsonl 决策留痕
pub mod drafts; // BL-ADVISOR-DRAFTS (5/21 Phase 7): ~/.catfish/outputs/<date>/ 草稿存储
pub mod proactive; // BL-PROACTIVE-DECOUPLE (5/26): journal_tail + last_model 给 /api/proactive/* header 透传
pub mod profile; // BL-ADVISOR-PROFILE (5/21 Phase 7): 员工职级 + 画像自动识别
pub mod relation;
pub mod room_link; // P50 (9/10): 横向协同 —— 本机 install_id + 内网地址
pub mod session_goal; // BL-BRIEFING-GOAL-INPUT (5/20): /goal UI 路径
pub mod session_write;
pub mod sessions;
pub mod skill_audit;
pub mod skill_feedback;
pub mod skill_revision;
pub mod skills; // 8/14 拆分: 只剩 tauri command 层, 实现在下面三个
pub mod skills_install; // 安装 / 卸载 / zip 导入 / 还原
pub mod skill_names;
pub mod skills_list; // 扫描 / 解析 / 列举
pub mod skills_mcp; // MCP servers 的读和写 (同一段 config.yaml)
pub mod speech;
pub mod system;
#[cfg(windows)]
mod system_local_pim; // 10/2: Windows 提醒 / 日历存在 Catfish 自己 (tool-bridge local_pim.py)
mod system_outlook; // Windows 早安页读经典版 Outlook 的会议 (有就并进来, 10/2 起只读)
pub mod task_chat; // P3.3.7 Phase 2 (6/10): ~/.catfish/task_chat/<key>.jsonl task-scoped chat 持久化
pub mod task_chat_migration; // P3.3.19 C Phase 4 (6/11): jsonl → ~/.hermes/state.db 一次性 migration
pub mod imap_credentials; // 9/18: IMAP 凭据 —— 密码进系统凭据库, 配置进不含密码的 JSON
pub mod teaching_credentials;
pub mod tasks_history; // BL-LONG-RUNNING-V1 (5/30): 读 ~/.catfish/tasks.jsonl 历史任务
pub mod tool_bridge;
pub mod weather; // P3.3.8 (6/10): 早安天气 (wttr.in + IP 定位 + 多城市 + 6h cache)
pub mod mcp_oauth;
pub mod server_config; // P28 (6/5 鸿波): Companion Dashboard 改 gateway URL/token, 不 vim yaml
pub mod types;
pub mod wechat_binding; // BL-WECHAT-CATFISH-BIND v1 (5/26): WeChat ↔ catfish 员工绑定状态
pub mod wechat_archive; // 8/28: 微信导出文件只读授权，分析沿用当前 Picker
pub mod wechat_exports; // 9/23: 聊天框拖入微信合并转发 ZIP → 导入库
pub mod wiki_embed; // P38 (6/5 鸿波): 本机 ONNX BGE-M3 wiki 语义搜索, 100% 离线
pub mod wiki_files;
pub(crate) mod wiki_graph; // 9/11: SQLite 关系镜像 + 一跳图检索扩展
pub mod wiki_ontology;
pub mod wiki_conflicts; // 9/17: frontmatter conflicts 解析 + 员工二选一 (semantica 第 2 条)
pub mod wiki_read; // BL-CATFISH-WIKI-MODE P3.3.2 (6/4): wiki read API (list_files + read_file)
pub(crate) mod wiki_status; // 9/24: 正文是否记着进度 (工作台「进度待核对」)
pub mod wiki_search;
mod wiki_search_calibrate;
pub mod wiki_save; // BL-CATFISH-WIKI-MODE P1.2.2 (6/4): chat 真 💾 button → wiki/queries/ 写盘
pub mod wiki_relation_vocab; // 10/23: 关系类型 = 内置词表 + 员工登记的自定义词
pub mod wiki_frontmatter; // 受控词表归一化 + authored_by 标记 (8/15 从 wiki_write 切出)
pub mod wiki_slug; // slug 校验/归一化/碰撞检测 (8/15 从 wiki_write 切出)
pub mod wiki_write; // BL-CATFISH-WIKI-MODE P3.3.7 (6/4): wiki write API (create_entity_or_concept + update_file) // P3.4.1 (6/13 鸿波): mcp OAuth token 本机存 ~/.catfish/mcp/oauth-tokens/ (砍 secret-broker)
pub mod wiki_actions; // 知识节点 action_id → 本机声明式 Action Registry → 现有 skill bridge
pub(crate) mod invoke_handler; // centralized Tauri invoke command registry
