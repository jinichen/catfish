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
pub mod pet;
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
pub mod tool_bridge;
// BL-VOICE2 (5/10): Piper local TTS, 跟 speech.rs (whisper STT) 对称
pub mod tts;
pub mod types;
