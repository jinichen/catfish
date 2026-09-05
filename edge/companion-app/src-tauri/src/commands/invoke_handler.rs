//! Tauri invoke 命令注册表。
//!
//! 命令清单单独维护，避免把前端 API 映射和应用启动生命周期混在 `lib.rs`。

/// Build the handler for the current Tauri runtime.
///
/// The generic runtime keeps this usable by desktop and mobile entry points.
pub(crate) fn handler(
) -> impl Fn(tauri::ipc::Invoke<tauri::Wry>) -> bool + Send + Sync + 'static {
    tauri::generate_handler![
            // gateway (P39 5/22 解耦收尾: start/stop/get_dev_token 删, 只留 status 探活.
            // 生产员工机 gateway 由 launchctl/客户 IT 管, Companion 不 spawn.)
            crate::commands::gateway::gateway_status,
            // P3.5.125 (6/26 鸿波 catch "catfish 对 hermes/chrome hang 无监控"):
            // hermes hang detection + auto restart (kill -9 触发 launchd 拉)
            crate::commands::hermes::hermes_status,
            crate::commands::hermes::hermes_kill,
            // Hermes 0.18 原生 Codex app-server runtime：检测 / 登录 / 一键切换.
            crate::commands::codex_backend::codex_backend_status,
            crate::commands::codex_backend::codex_backend_set_enabled,
            crate::commands::codex_backend::codex_backend_select_model,
            crate::commands::codex_backend::codex_backend_open_login,
            // BL-CSP-PROXY (7/18 鸿波): Rust reqwest HTTP 代理, 让前端 fetch 走 Rust,
            // CSP connect-src 保持严格 (无外网白名单). 达华 POC 员工输达华 IP 才能通 chat.
            crate::commands::http_proxy::http_proxy,
            crate::commands::http_proxy::http_proxy_abortable,
            crate::commands::http_proxy::http_proxy_stream,
            crate::commands::http_proxy::http_proxy_abort,
            // chrome
            crate::commands::chrome::chrome_launch,
            crate::commands::chrome::chrome_kill,
            crate::commands::chrome::chrome_status,
            // local_search
            crate::commands::local_search::local_search_start,
            crate::commands::local_search::local_search_stop,
            crate::commands::local_search::local_search_status,
            // BL-SEARCH-NO-BOOTSTRAP (7/27): 前台跑一次索引 (整库 / 单目录).
            // watcher 只吃变化事件, 存量文件得靠这个进索引.
            crate::commands::local_search::local_search_index,
            // BL-SEARCH-STALE-SCOPE (7/27): 删目录后清掉它在索引里的数据.
            crate::commands::local_search::local_search_clean,
            // P3.5.126 (6/26 鸿波 catch "local_search 目录设置 UI 找不到"):
            // 索引目录 UI 管理 (search-scope.yaml include 段读写)
            crate::commands::local_search_scope::local_search_scope_get,
            crate::commands::local_search_scope::local_search_scope_add,
            crate::commands::local_search_scope::local_search_scope_remove,
            // P3.5.127 (6/26 鸿波 catch "怎么知道文件是不是有被索引?"):
            // 索引状态查询 — 直接读 ~/.catfish/search.db, 复用 Python stats_summary 逻辑
            crate::commands::local_search_stats::local_search_stats,
            // tool_bridge
            crate::commands::tool_bridge::tool_bridge_start,
            crate::commands::tool_bridge::tool_bridge_stop,
            // P3.5.196 (7/7 鸿波): 手动强制重启 tool-bridge (pkill + fresh spawn),
            // 无需重启 Companion. 用于 code 变更后 pick up 新逻辑, 或 hermes 升级
            // 后 monkey-patch 签名对齐修复.
            crate::commands::tool_bridge::tool_bridge_restart,
            crate::commands::tool_bridge::tool_bridge_status,
            crate::commands::tool_bridge::tool_bridge_list_tools,
            crate::commands::tool_bridge::tool_bridge_call_tool,
            crate::commands::tool_bridge::tool_bridge_chat_approval,
            // health
            crate::commands::health::healthz,
            crate::commands::health::catalog,
            // logs
            crate::commands::logs::tail,
            crate::commands::logs::stop_tail,
            // sessions (read)
            crate::commands::sessions::sessions_list,
            crate::commands::sessions::sessions_count,
            crate::commands::sessions::sessions_get,
            // sessions (delete) —— BL-SESSION-MGMT C (5/15)
            crate::commands::sessions::session_soft_delete,
            crate::commands::sessions::session_restore,
            crate::commands::sessions::sessions_bulk_delete_short,
            // sessions (write) —— Plan C Week 2 持久化
            crate::commands::session_write::session_create,
            crate::commands::session_write::session_message_append,
            crate::commands::session_write::session_finalize,
            crate::commands::session_write::session_update_title,
            crate::commands::session_write::session_check,
            // P3.3.19 (6/11) C 路线 Phase 1: task ↔ session 关联 sidecar
            crate::commands::session_write::session_set_task_uid,
            crate::commands::session_write::session_get_task_uid,
            crate::commands::session_write::session_get_by_task_uid,
            crate::commands::session_write::list_sessions_by_task_uid,
            // identity
            crate::commands::identity::identity_info,
            // skills + mcp
            // 6/2 BL-SKILLS-CARD-SPLIT (鸿波): 拆 2 命令 — list_my_skills (扫 ~/.catfish/skills/,
            // 员工真生成) + list_installed_skills (扫 catfish 仓库 + ~/.hermes/skills/, 内置/装的).
            // 7/17 BL-DEADCODE-SWEEP: 老 list_skills 兜底命令死链已删.
            crate::commands::skills::list_my_skills,
            crate::commands::skills::list_installed_skills,
            crate::commands::skills::list_mcp_servers,
            // E7 phase 2 (6/6): skill 安装/卸载, MCP 接入/移除 + undo 5s
            crate::commands::skills::install_skill_from_url,
            crate::commands::skills::install_skill_from_zip,  // P3.3.23 (6/11)
            crate::commands::skills::uninstall_skill,
            crate::commands::skills::restore_skill,
            crate::commands::skills::add_mcp_server,
            crate::commands::skills::remove_mcp_server,
            // 6/7 BL-MANIFESTO-ADVISORY-PHASE1: advisory local state (本机 SQLite)
            crate::commands::advisory::advisory_list_local_states,
            crate::commands::advisory::advisory_get_local_state,
            crate::commands::advisory::advisory_mark_shown,
            crate::commands::advisory::advisory_ack,
            crate::commands::advisory::advisory_snooze,
            crate::commands::advisory::advisory_dismiss,
            // 6/8 BL-EMPLOYEE-SELF-SERVE A1+A2: 重置 / 导出 (manifesto 公理 1 员工主权)
            crate::commands::self_serve::self_serve_preview_reset,
            crate::commands::self_serve::self_serve_execute_reset,
            crate::commands::self_serve::self_serve_restore_reset,
            crate::commands::self_serve::self_serve_export_data,
            // 6/8 BL-EMPLOYEE-SELF-SERVE A4: 数据外发日志 (员工自审 catfish 中央交换)
            crate::commands::transparent_log::transparent_log_record,
            crate::commands::transparent_log::transparent_log_query,
            crate::commands::transparent_log::transparent_log_export_csv,
            crate::commands::transparent_log::transparent_log_gc,
            // self-evolution: 鲶鱼今天学了什么
            crate::commands::learning::learning_today_stats,
            // BL-MM9-accept (5/9): skill proposal accept/reject 按钮
            crate::commands::learning::skill_proposal_accept,
            crate::commands::learning::skill_proposal_reject,
            crate::commands::audit::audit_summary,
            // P3.5.59 (6/22 鸿波): tool-bridge audit jsonl 聚合 per-tool perf
            crate::commands::tool_perf::tool_perf_summary,
            // P3.3.51 (6/12 鸿波): audit hash chain — decisions / political_scan jsonl 防篡改
            crate::commands::audit_chain::audit_chain_append,
            crate::commands::audit_chain::audit_chain_verify,
            crate::commands::audit_chain::audit_chain_status,
            // P3.3.54 (6/12 鸿波): 审计员看的 xlsx 多 sheet 导出 (~/.catfish/exports/)
            crate::commands::audit_export::audit_export_xlsx,
            // P3.3.55 (6/12 鸿波): 审计视图 Tab 表格化呈现 jsonl raw read
            crate::commands::audit_export::audit_decisions_raw_read,
            crate::commands::audit_export::audit_hermes_jsonl_read,
            // BL-RECMODE-DASHBOARD-UI (#75, 5/25): 我的录屏 inventory + Finder + delete
            crate::commands::recordings::recordings_list,
            crate::commands::recordings::recordings_show_in_finder,
            crate::commands::recordings::recordings_delete,
            // SSO Phase 1C: OAuth flow + Keychain
            crate::commands::auth::auth_whoami,
            crate::commands::auth::auth_login,
            crate::commands::auth::auth_logout,
            crate::commands::auth::auth_get_access_token,
            // system
            crate::commands::system::notify,
            // BL-REMINDER (5/13): macOS Reminders.app 集成
            crate::commands::system::create_reminder,
            crate::commands::system::list_reminder_lists,
            // BL-CALENDAR (5/14 0:30): macOS Calendar.app 集成 — 时间锚定事件
            crate::commands::system::create_calendar_event,
            crate::commands::system::list_calendars,
            // BL-CATFISH-HERMES-VERSION-SYNC-B (6/1): AboutModal 显 hermes 版本
            crate::commands::system::get_hermes_version,
            // file (Phase 2 优雅下载: skill 生成的 .docx/.xlsx/.pptx 在 Finder 显示)
            crate::commands::file::reveal_in_finder,
            crate::commands::file::open_file,
            // file_parse (五一 sprint Day 1: 文件上传解析 PDF/Excel/Word/CSV/TXT/MD)
            crate::commands::file_parse::parse_file,
            crate::commands::file_parse::parse_file_from_b64,
            // BL-L26 (5/7): 大文件 (≥50KB) BM25 段落检索
            crate::commands::file_parse::attachment_bm25_search,
            // BL-FILE-SESSION-INDEX-V1 Phase 1 (5/30): 附件 metadata 持久化 ~/.catfish/attachments.db
            crate::commands::attachments::attachment_record,
            crate::commands::attachments::attachment_list_by_session,
            crate::commands::attachments::attachment_list_by_user,
            crate::commands::attachments::attachment_search_local,
            crate::commands::attachments::attachment_delete,
            crate::commands::attachments::attachment_delete_by_user,
            // P3.5.8 Phase 2 (6/16): image 落盘 + 从 keptPath 读 base64.
            //   - attachment_save_image: 上传时调一次, 写到 ~/.catfish/uploads/, 返 keptPath
            //   - attachment_load_base64: resume 时调一次, 读回 base64 填 attachments
            crate::commands::attachments::attachment_save_image,
            crate::commands::attachments::attachment_load_base64,
            // BL-LONG-RUNNING-V1 (5/30): 读 ~/.catfish/tasks.jsonl 历史任务
            crate::commands::tasks_history::tasks_history_read,
            // skill_audit (五一 sprint Day 2: skill 调用审计 + 30 天未用统计)
            crate::commands::skill_audit::skill_audit_summary,
            // speech (五一 sprint Day 1 方案 C+: ffmpeg 录 + Whisper.cpp 转, 全本地)
            crate::commands::speech::speech_start_recording,
            crate::commands::speech::speech_stop_and_transcribe,
            crate::commands::speech::speech_cancel_recording,
            // BL-VOICE3 (5/10): 拖音频文件转文字 (mp3/m4a/wav/...) → ffmpeg + whisper
            crate::commands::speech::transcribe_audio_from_b64,
            // BL-VOICE2 (5/10): Piper local TTS — 跟 STT 对称, 100% 本地数据不出公司
            crate::commands::tts::tts_synthesize,
            crate::commands::tts::tts_status,
            // BL-E11 命名权 (五一 sprint 5/3 晚): 员工自定义鲶鱼名 + 人设
            crate::commands::agent::get_agent_prefs,
            crate::commands::agent::set_agent_prefs,
            // BL-WIN9 / DEPLOY1 (5/8): 暴露 yaml 配置的 endpoints 给前端动态读
            crate::commands::endpoints::get_runtime_endpoints,
            // BL-CR Curator 集成 (5/7): hermes 0.12 自动整理脚本配置 + 状态展示
            crate::commands::curator::get_curator_config,
            crate::commands::curator::set_curator_config,
            crate::commands::curator::ensure_curator_default,
            crate::commands::curator::get_curator_state,
            // BL-CALENDAR-INTEGRATION (5/20): macOS Calendar.app 今日 events
            crate::commands::calendar::calendar_today_fetch,
            // BL-CALENDAR-WEEK (5/20): 跨日 7 天 events (今天 + 明天 + 后 5 天)
            crate::commands::calendar::calendar_week_fetch,
            // BL-BRIEFING-DECISION (5/21 Phase 5): 综合判断上下文包 (distilled_facts + 24h sessions)
            crate::commands::briefing_context::briefing_context_fetch,
            // BL-JOURNAL-TODO-EXTRACT step2 (5/20): 读 journal 最近 5KB 给 LLM 抽自然语言 TODO
            crate::commands::journal::journal_read_recent,
            // BL-PROACTIVE-DECOUPLE (5/26): journal_tail + last_model 一次拿, 给 /api/proactive/* header 透传
            crate::commands::proactive::proactive_context,
            // P3.5.45 (6/20 鸿波): 录屏 RPC 直调 tool-bridge sock, 砍 gateway HTTP path
            crate::commands::recmode::recmode_rpc,
            // BL-WECHAT-CATFISH-BIND v1 + v2 (5/26): WeChat openid ↔ catfish 员工 email 绑定
            // v1 read-only 状态; v2 写命令给 Dashboard UI 一键审批/改绑/解绑/拒绝.
            crate::commands::wechat_binding::wechat_binding_status,
            crate::commands::wechat_binding::wechat_binding_pending_list,
            crate::commands::wechat_binding::wechat_binding_approve,
            crate::commands::wechat_binding::wechat_binding_set_email,
            crate::commands::wechat_binding::wechat_binding_revoke,
            crate::commands::wechat_binding::wechat_binding_reject,
            crate::commands::wechat_archive::wechat_archive_status,
            crate::commands::wechat_archive::wechat_archive_pick_export,
            crate::commands::wechat_archive::wechat_archive_clear_source,
            crate::commands::wechat_archive::wechat_archive_enable,
            crate::commands::wechat_archive::wechat_archive_disable,
            // BL-IDENTITY-INJECT-DECOUPLE (5/26): SOUL/USER/memories 6 字段, 给 /v1/chat/completions body 透传
            crate::commands::identity_bundle::identity_bundle,
            // BL-BRIEFING-GOAL-INPUT (5/20): /goal UI 路径 — BriefingCard 输入框
            // 5/26 DEPRECATED: hermes 0.14 原生 /goal 替代. 3 个 command 改 stub 返 error
            // 防回归. 保留 invoke_handler 注册防遗漏 JS caller 编译失败.
            crate::commands::session_goal::session_goal_read,
            crate::commands::session_goal::session_goal_write,
            crate::commands::session_goal::session_goal_clear,
            // P3.5.1 (6/15 鸿波): Dream Engine — 员工主动触发 long-term 蒸馏, 用 picker model
            crate::commands::dream::dream_distill_run,
            crate::commands::dream::dream_distill_status,
            // P3.5.2 (6/16 鸿波): chat picker 持久化 → plugin sync_turn 跟随 picker (绕过 hermes API 没透传 picker 限制)
            crate::commands::picker_state::picker_state_save,
            crate::commands::picker_state::picker_state_get,
            // P3.5.91 (6/23 鸿波): 早安 task → canonical taskUid client cache —
            // 治 LLM advisor refresh 给同 title 新 uid 导致 session 关联失联问题
            crate::commands::task_uid_cache::task_uid_cache_get,
            crate::commands::task_uid_cache::task_uid_cache_put,
            crate::commands::task_uid_cache::task_uid_cache_dump,
            // P3.5.4 (6/16 鸿波): BGE-M3 advisor 注入相关性筛选 — 砍 prompt + 砍 LLM 输出, advisor 不再 truncated
            crate::commands::advisor_relevance::advisor_rank_relevance,
            crate::commands::advisor_relevance::advisor_relevance_cache_stats,
            // BL-COMPANION-EMAIL-DIGEST (5/18): 邮件简报 shell-out
            crate::commands::email::email_digest_fetch,
            crate::commands::email::email_accounts_fetch,
            // BL-COMPANION-EMAIL-TAB (5/18): 邮件 tab 用的扩展能力 (全列表 + 读全文 + 起草)
            crate::commands::email::email_list_fetch,
            crate::commands::email::email_read_message,
            crate::commands::email::email_create_draft,
            // P3.5.204.c (7/9 鸿波): 客户端还没同步的邮件, 员工可点刷新触发 IMAP/POP 同步
            crate::commands::email::email_check_new,
            crate::commands::email::email_mail_dir_status,  // 8/8: 缺完全磁盘访问权限时提示 (见该 fn 注释)
            // BL-EMAIL-MARK-READ (5/18): 单独标已读/未读 (右键 / 批量场景)
            crate::commands::email::email_mark_read,
            // BL-EMAIL-DELETE (5/18): 删邮件 (移到 Trash, 软删)
            crate::commands::email::email_delete_message,
            // BL-EMAIL-COMPOSE-SEND (5/18): 把 Drafts 草稿真发出去 (人工 confirm 红线)
            crate::commands::email::email_send_message,
            // P3.3.58 (6/12 鸿波): 批量查邮件钓鱼扫描结果
            crate::commands::email::email_phishing_get,
            // P3.3.53 (6/13): 政治敏感扫描 — 给前端 detail pane 调
            crate::commands::email::email_political_scan_now,
            crate::commands::email::email_political_get,
            // P3.5.103 (6/24 鸿波 catch "附件不能点"): 导出附件到本地 tmp, 配合 open_file 系统打开
            crate::commands::email::email_export_attachment,
            // 回复拟稿：附件只在本地解析预览，不上传原始文件
            crate::commands::email::email_attachment_preview,
            // P3.5.105 (6/25 鸿波 catch "定时任务跑没跑结果如何都看不到"): cron 监控 + 操作
            crate::commands::cron::cron_jobs_list,
            crate::commands::cron::cron_job_outputs,
            crate::commands::cron::cron_job_output_read,
            crate::commands::cron::cron_job_pause,
            crate::commands::cron::cron_job_resume,
            crate::commands::cron::cron_job_delete,
            // BL-COMPANION-EMAIL-TAB-STEP2 (5/18): 评级 badge 取数
            crate::services::email_scheduler::email_urgency_map,
            // BL-COMPANION-PREFS-TOGGLES (5/20): 暴露 email config 给前端 AgentPrefsCard 展示
            crate::services::email_config::email_config_get,
            // P3.5.28 (6/17 鸿波"picker 联动现在就应该做"): chat picker 选的 model 写文件,
            // background task (email_scheduler / phishing_scan) 跟着用员工选的 model.
            crate::services::picker_config::set_picker_model,
            // P3.5.139 Phase 4 (6/29 鸿波"重启 Companion picker 应该记得这次选择"):
            // 启动时读 ~/.catfish/picker_model 注入 zustand store.model.
            crate::services::picker_config::get_picker_model,
            // P3.5.139 (6/29 鸿波"都要去除硬编码"): 前端 caller (visionSwitch /
            // DetailPane / Chat fallback) 拉 /v1/roles 拿全 mapping, 5min cache.
            // P3.3.65 (6/13): 钓鱼规则可配置 — 仪表盘显当前 effective 配置
            crate::services::phishing_config::phishing_config_get,
            // P3.3.53 (6/13): 政治敏感规则可配置 — 仪表盘显当前 effective 配置
            crate::services::political_config::political_config_get,
            // P3.4.1 (6/13): mcp OAuth token 本机存 (砍 secret-broker 中央存储)
            crate::commands::mcp_oauth::mcp_oauth_token_save,
            crate::commands::mcp_oauth::mcp_oauth_token_delete,
            // BL-EMAIL-URGENCY-BADGE (5/18): 前端主动 batch 评级 (历史邮件也能评)
            crate::services::email_scheduler::email_classify_now,
            // P3.3.58 段 2B (6/12 鸿波): 前端 trigger 钓鱼扫描
            crate::services::email_scheduler::email_phishing_scan_now,
            // BL-COMPANION-HERMES-API-CONFIG (5/19 Phase 2-2A): 暴露 hermes_api 配置给 React
            crate::services::hermes_api_config::hermes_api_config_get,
            // BL-COMPANION-CHAT-SWITCH-TO-HERMES (5/19 Phase 2-2B): chat.ts 走 hermes 时拿 auth header
            crate::services::hermes_api_config::hermes_api_auth_header,
            // BL-WECHAT-QR-HERMES-STANDALONE (7/18): hermes 独占 endpoint 强用 (无视 enabled)
            crate::services::hermes_api_config::hermes_api_auth_header_forced,
            crate::services::hermes_api_config::hermes_api_url_forced,
            // BL-E16 关系建立 (五一 sprint 5/3 晚): "鲶鱼对你的印象" 透明 + 清空
            crate::commands::relation::relation_summary,
            crate::commands::relation::relation_forget,
            crate::commands::relation::journal_read_raw,
            // BL-ADVISOR-PROFILE (5/21 Phase 7 第 1 步): 员工职级 + 画像自动识别
            crate::commands::profile::profile_get,
            crate::commands::profile::profile_save,
            crate::commands::profile::profile_mark_wrong,
            crate::commands::profile::profile_hints_read,
            crate::commands::profile::profile_needs_recompute,
            crate::commands::profile::profile_next_recompute_at,
            // Hermes Profile 专家 Bot：管理、场景绑定与请求路由
            crate::commands::expert_bots::expert_bots_status,
            crate::commands::expert_bots::expert_bots_list,
            crate::commands::expert_bots::expert_bots_set_enabled,
            crate::commands::expert_bots::expert_bot_create,
            crate::commands::expert_bots::expert_bot_update,
            crate::commands::expert_bots::expert_bot_register_existing,
            crate::commands::expert_bots::expert_bot_unregister,
            crate::commands::expert_bots::expert_bot_delete,
            crate::commands::expert_bots::expert_bot_bind,
            crate::commands::expert_bots::expert_bot_soul_get,
            crate::commands::expert_bots::expert_bot_route,
            // BL-ADVISOR-DRAFTS (5/21 Phase 7 第 2 步): ~/.catfish/outputs/<date>/ 草稿存储
            crate::commands::drafts::draft_save,
            crate::commands::drafts::draft_read,
            // BL-X (5/26): chat timeout toast 自显本地 outputs (替代砍掉的 gateway recent_outputs.list_recent)
            crate::commands::drafts::recent_outputs_list,
            // P3.3.62 (6/13): draft_parse_md 给 FilePill 解析草稿头
            // (8/9: TodayDraftsCard 已砍, draft_delete_md 留着 —— 它的
            //  path-traversal 红线测试覆盖着仍在用的 guard_outputs_path)
            crate::commands::drafts::draft_parse_md,
            crate::commands::drafts::draft_delete_md,
            // BL-ADVISOR-DECISIONS (5/21 Phase 7 第 2 步): ~/.catfish/decisions.jsonl 决策留痕
            crate::commands::decisions::decision_record,
            // P3.3.7 Phase 2 (6/10): task-scoped chat 持久化
            crate::commands::task_chat::task_chat_get,
            crate::commands::task_chat::task_chat_append,
            crate::commands::task_chat::task_chat_clear,
            // P3.3.12 (6/10): jsonl 大小 (给 advisor task chat summary cache 用)
            crate::commands::task_chat::task_chat_size,
            // P3.3.8 (6/10): 早安天气
            crate::commands::weather::weather_get,
            crate::commands::weather::weather_config_get,
            crate::commands::weather::weather_config_set,
            crate::commands::decisions::decision_list_recent,
            crate::commands::decisions::decision_search,
            // P3.3.52 (6/12 鸿波): 员工"标记完成 / 推迟 / 不做" 时同步留痕到 decisions.jsonl
            crate::commands::decisions::decision_record_status_change,
            // BL-ADVISOR-CACHE + CONFIG (5/22 Phase 7 cold start v3): 缓存 + yaml 时段配置
            crate::commands::advisor_cache::advisor_cache_get,
            crate::commands::advisor_cache::advisor_cache_save,
            crate::commands::advisor_cache::advisor_cache_clear,
            crate::commands::advisor_config::advisor_config_get,
            // BL-ADVISOR-TASK-STATE (5/22 鸿波): 任务状态 done/snoozed/ignored
            crate::commands::advisor_task_state::advisor_task_state_get,
            crate::commands::advisor_task_state::advisor_task_state_set,
            crate::commands::advisor_task_state::advisor_task_state_clear,
            crate::commands::advisor_task_state::advisor_task_state_prune_old,
            // BL-STYLE-FP-USE-INDEX (7/27): 删了 style_fingerprint_scan_dirs_*.
            // 文书风格改查 local_search 索引, 目录范围走上面的 local_search_scope_*.
            // BL-MM4 v1 (5/5 晚): "鲶鱼记的硬事实" 版本卡 (跟 BL-MM2 配套)
            crate::commands::memory_history::memory_history_summary,
            crate::commands::memory_history::memory_history_clear_key,
            crate::commands::memory_history::memory_history_forget_all,
            // BL-DASHBOARD-HERMES-MEMORY-CARD (5/16): 读 hermes 0.13 真活 memory 文件
            crate::commands::hermes_memory::hermes_memory_read,
            // P3.3.49 (6/12 鸿波 "删除无效"): Rust 直写, 绕过 memory_tool silent fail
            crate::commands::hermes_memory::hermes_memory_remove,
            // BL-MM6 (5/5 晚): 显式 feedback 👍/👎/改 + ~/.catfish/feedback.jsonl
            crate::commands::feedback::feedback_record,
            crate::commands::feedback::feedback_summary,
            crate::commands::feedback::feedback_clear,
            // BL-E27 spike (5/5 凌晨): 桌宠副窗 toggle + 点击唤主窗 + 4 屏角切换
            crate::commands::pet::pet_show,
            crate::commands::pet::pet_hide,
            crate::commands::pet::pet_is_visible,
            crate::commands::pet::pet_clicked,
            crate::commands::pet::pet_move_corner,
            crate::commands::pet::pet_set_bubble_visible,
            crate::commands::pet::pet_start_drag,
            crate::commands::pet::pet_emit_bubble,
            crate::commands::pet::pet_emit_status,
            crate::commands::pet::pet_pop_bubble,
            crate::commands::pet::pet_pop_status,
            crate::commands::pet::pet_log,
            // BL-E27.4 (5/8): 桌宠状态颜色 indicator + 单击重置
            crate::commands::pet::pet_status_summary,
            crate::commands::pet::pet_status_clear,
            // BL-MM11 (5/8): skill 级 👍/👎/改 评分
            crate::commands::skill_feedback::skill_feedback_record,
            crate::commands::skill_feedback::skill_feedback_summary,
            crate::commands::skill_feedback::skill_feedback_clear,
            // BL-MM14 / MM15 (5/8): skill revision proposals + 改进有效性跟踪
            crate::commands::skill_revision::skill_revision_summary,
            crate::commands::skill_revision::skill_revision_accept,
            crate::commands::skill_revision::skill_revision_reject,
            crate::commands::skill_revision::skill_revision_check_effectiveness,
            // BL-CATFISH-WIKI-MODE P1.2.2 (6/4): chat 真 💾 button → wiki/queries/ 写盘
            crate::commands::wiki_save::wiki_save_chat_message,
            // BL-CATFISH-WIKI-MODE P3.3.2 (6/4): wiki read API
            crate::commands::wiki_files::wiki_list_files,
            crate::commands::wiki_read::wiki_read_file,
            // P37 (6/5): wiki 全文搜索 (BM25 score)
            crate::commands::wiki_search::wiki_search_text,
            crate::commands::wiki_search::wiki_search_hybrid,
            // P3.3.18 Phase 4 (6/10): 已装部门 wiki 扫描 (~/.catfish/wiki-shared/)
            crate::commands::wiki_read::list_installed_wiki_shared,
            // P38 (6/5): wiki 语义搜索 (本机 BGE-M3 ONNX)
            crate::commands::wiki_embed::wiki_search_semantic,
            // BL-CATFISH-WIKI-MODE P3.3.7 (6/4): wiki write API
            crate::commands::wiki_write::wiki_create_entity_or_concept,
            crate::commands::wiki_write::wiki_update_file,
            // P3.3.4 (6/9): wiki 软删 (mv 到 .trash)
            crate::commands::wiki_write::wiki_delete_file,
            // P3.3.18 Phase 4 P2 (6/10): 卸载本机部门 wiki 副本 (软删 → wiki-shared/.trash/)
            crate::commands::wiki_write::wiki_uninstall_shared,
            // P3.3.18 Phase 4 P2 (6/10): 敏感词文件 onboarding (catfish_wiki_publish 扫用)
            crate::commands::wiki_write::wiki_sensitive_terms_ensure,
            // P3.3.19 C Phase 4 (6/11): task_chat jsonl → state.db 一次性 migration
            crate::commands::task_chat_migration::task_chat_migrate_to_state_db,
            // P16 (6/5): 对话上传文件 auto ingest → wiki/raw/sources/
            crate::commands::wiki_write::wiki_ingest_source,
            // P28 (6/5): Companion Dashboard 改 gateway URL/token
            crate::commands::server_config::read_server_config,
            crate::commands::server_config::write_server_config,
            // 7/15 BL-CATFISH-MAC-OFFLINE-INSTALL: 员工 Dashboard 手工重装 hermes (若首启 auto install 挂)
            crate::commands::hermes_install::reinstall_hermes_agent,
            crate::commands::hermes_install::hermes_bootstrap_status,
            crate::commands::teaching_credentials::teaching_credential_save,
            crate::commands::teaching_credentials::teaching_credential_list,
            crate::commands::teaching_credentials::teaching_credential_delete,
            crate::commands::teaching_credentials::teaching_credential_add_site,
    ]
}
