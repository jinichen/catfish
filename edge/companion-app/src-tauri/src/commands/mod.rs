//! 暴露给前端的 invoke 命令集合。
//!
//! 设计原则：
//!   - 三个核心服务一人一个文件（gateway / chrome / local_search / tool_bridge），
//!     避免 service.rs 越长越大
//!   - 公共能力（health / logs / sessions / system / identity / skills）
//!     按职责而非按服务拆
//!   - 每个 #[tauri::command] 必须返回 Result<T, String>，
//!     错误统一序列化成字符串给前端

pub mod advisor_relevance;  // P3.5.4.2 (6/16 鸿波): BGE-M3 advisor 注入相关性筛选 + sqlite cache
pub mod advisory;  // 6/7 BL-MANIFESTO-ADVISORY-PHASE1: advisory local state (~/.catfish/advisory_state.db)
pub mod agent;
pub mod self_serve;  // 6/8 BL-EMPLOYEE-SELF-SERVE: A1 重置 / A2 导出 / A3 导入 (BL)
pub mod transparent_log;  // 6/8 BL-EMPLOYEE-SELF-SERVE A4: 数据外发日志 (员工自审)
pub mod attachments;  // BL-FILE-SESSION-INDEX-V1 Phase 1 (5/30): 附件 metadata 持久化到 ~/.catfish/attachments.db, 跨会话恢复 + 列表
pub mod audit;
pub mod audit_chain;  // P3.3.51 (6/12): jsonl 防篡改哈希链 (decisions / political_scan 用)
pub mod audit_export;  // P3.3.54 (6/12): 审计员看的多 sheet xlsx 导出
pub mod auth;
pub mod briefing_context;  // BL-BRIEFING-DECISION (5/21 Phase 5): distilled_facts + recent sessions
pub mod calendar;  // BL-CALENDAR-INTEGRATION (5/20): macOS Calendar.app via osascript JXA
pub mod chrome;
pub mod curator;
pub mod dream;  // P3.5.1 (6/15 鸿波): Dream Engine — 员工主动触发 long-term 蒸馏, picker model
pub mod picker_state;  // P3.5.2 (6/16 鸿波): chat picker 持久化 ~/.catfish/picker_state.json — plugin sync_turn 跟随
pub mod task_uid_cache;  // P3.5.91 (6/23 鸿波): 早安 task → canonical taskUid 客户端 cache — 治 LLM 给新 uid 时 session 关联失联
pub mod email;  // BL-COMPANION-EMAIL-DIGEST (5/18): 邮件简报卡的后端 shell-out
pub mod endpoints;
pub mod feedback;
pub mod file;
pub mod file_parse;
pub mod gateway;
pub mod health;
pub mod identity;
pub mod journal;  // BL-JOURNAL-TODO-EXTRACT (5/20): ~/.catfish/employee_journal.md TODO 抽取
pub mod learning;
pub mod local_search;
pub mod logs;
pub mod memory_history;
pub mod hermes_memory;  // BL-DASHBOARD-HERMES-MEMORY-CARD (5/16)
pub mod identity_bundle;  // BL-IDENTITY-INJECT-DECOUPLE (5/26): SOUL/USER/memories prefetch 给 /v1/chat/completions body
pub mod hermes_plugin;    // P3.5.56 (6/21 鸿波): Companion boot 自动装 catfish-xcatfish-user plugin (baked + ensure config.yaml enabled)
pub mod tool_perf;        // P3.5.59 (6/22 鸿波): tool-bridge audit jsonl 聚合 per-tool perf (count / success_rate / p50/p95/p99)
pub mod advisor_cache;   // BL-ADVISOR-CACHE (5/22 Phase 7): 缓存 advisor 结果
pub mod advisor_config;  // BL-ADVISOR-CONFIG (5/22 Phase 7): 读 yaml advisor section
pub mod advisor_task_state;  // BL-ADVISOR-TASK-STATE (5/22): done/snoozed/ignored 状态
pub mod style_fingerprint_dirs;  // BL-STYLE-FP-DIR-PICKER (5/22 鸿波): scan_dirs UI 管理
pub mod decisions;  // BL-ADVISOR-DECISIONS (5/21 Phase 7): ~/.catfish/decisions.jsonl 决策留痕
pub mod drafts;     // BL-ADVISOR-DRAFTS (5/21 Phase 7): ~/.catfish/outputs/<date>/ 草稿存储
pub mod task_chat;  // P3.3.7 Phase 2 (6/10): ~/.catfish/task_chat/<key>.jsonl task-scoped chat 持久化
pub mod task_chat_migration;  // P3.3.19 C Phase 4 (6/11): jsonl → ~/.hermes/state.db 一次性 migration
pub mod weather;    // P3.3.8 (6/10): 早安天气 (wttr.in + IP 定位 + 多城市 + 6h cache)
pub mod pet;
pub mod proactive;
pub mod recmode;    // P3.5.45 (6/20 鸿波): 录屏 Tauri command 直调 tool-bridge sock, 砍 gateway HTTP path  // BL-PROACTIVE-DECOUPLE (5/26): journal_tail + last_model 给 /api/proactive/* header 透传
pub mod profile;    // BL-ADVISOR-PROFILE (5/21 Phase 7): 员工职级 + 画像自动识别
pub mod recordings; // BL-RECMODE-DASHBOARD-UI (#75, 5/25): 我的录屏 inventory / show in Finder / delete
pub mod relation;
pub mod session_goal;  // BL-BRIEFING-GOAL-INPUT (5/20): /goal UI 路径
pub mod session_write;
pub mod sessions;
pub mod skill_audit;
pub mod skill_feedback;
pub mod skill_revision;
pub mod skills;
pub mod speech;
pub mod system;
pub mod tasks_history;  // BL-LONG-RUNNING-V1 (5/30): 读 ~/.catfish/tasks.jsonl 历史任务
pub mod tool_bridge;
// BL-VOICE2 (5/10): Piper local TTS, 跟 speech.rs (whisper STT) 对称
pub mod tts;
pub mod types;
pub mod wechat_binding;  // BL-WECHAT-CATFISH-BIND v1 (5/26): WeChat ↔ catfish 员工绑定状态
pub mod wiki_read;  // BL-CATFISH-WIKI-MODE P3.3.2 (6/4): wiki read API (list_files + read_file)
pub mod wiki_save;  // BL-CATFISH-WIKI-MODE P1.2.2 (6/4): chat 真 💾 button → wiki/queries/ 写盘
pub mod wiki_write; // BL-CATFISH-WIKI-MODE P3.3.7 (6/4): wiki write API (create_entity_or_concept + update_file)
pub mod server_config; // P28 (6/5 鸿波): Companion Dashboard 改 gateway URL/token, 不 vim yaml
pub mod wiki_embed; // P38 (6/5 鸿波): 本机 ONNX BGE-M3 wiki 语义搜索, 100% 离线
pub mod mcp_oauth; // P3.4.1 (6/13 鸿波): mcp OAuth token 本机存 ~/.catfish/mcp/oauth-tokens/ (砍 secret-broker)
