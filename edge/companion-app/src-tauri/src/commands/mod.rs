//! 暴露给前端的 invoke 命令集合。
//!
//! 设计原则：
//!   - 三个核心服务一人一个文件（gateway / chrome / local_search / tool_bridge），
//!     避免 service.rs 越长越大
//!   - 公共能力（health / logs / sessions / system / identity / skills）
//!     按职责而非按服务拆
//!   - 每个 #[tauri::command] 必须返回 Result<T, String>，
//!     错误统一序列化成字符串给前端

pub mod agent;
pub mod attachments;  // BL-FILE-SESSION-INDEX-V1 Phase 1 (5/30): 附件 metadata 持久化到 ~/.catfish/attachments.db, 跨会话恢复 + 列表
pub mod audit;
pub mod auth;
pub mod briefing_context;  // BL-BRIEFING-DECISION (5/21 Phase 5): distilled_facts + recent sessions
pub mod calendar;  // BL-CALENDAR-INTEGRATION (5/20): macOS Calendar.app via osascript JXA
pub mod chrome;
pub mod curator;
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
pub mod advisor_cache;   // BL-ADVISOR-CACHE (5/22 Phase 7): 缓存 advisor 结果
pub mod advisor_config;  // BL-ADVISOR-CONFIG (5/22 Phase 7): 读 yaml advisor section
pub mod advisor_task_state;  // BL-ADVISOR-TASK-STATE (5/22): done/snoozed/ignored 状态
pub mod style_fingerprint_dirs;  // BL-STYLE-FP-DIR-PICKER (5/22 鸿波): scan_dirs UI 管理
pub mod decisions;  // BL-ADVISOR-DECISIONS (5/21 Phase 7): ~/.catfish/decisions.jsonl 决策留痕
pub mod drafts;     // BL-ADVISOR-DRAFTS (5/21 Phase 7): ~/.catfish/outputs/<date>/ 草稿存储
pub mod pet;
pub mod proactive;  // BL-PROACTIVE-DECOUPLE (5/26): journal_tail + last_model 给 /api/proactive/* header 透传
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
pub mod wiki_save;  // BL-CATFISH-WIKI-MODE P1.2.2 (6/4): chat 真 💾 button → wiki/queries/ 写盘
