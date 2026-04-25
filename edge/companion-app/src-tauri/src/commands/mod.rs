//! 暴露给前端的 invoke 命令集合。
//!
//! 设计原则：
//!   - 三个核心服务一人一个文件（gateway / chrome / local_search / tool_bridge），
//!     避免 service.rs 越长越大
//!   - 公共能力（health / logs / sessions / system / identity / skills）
//!     按职责而非按服务拆
//!   - 每个 #[tauri::command] 必须返回 Result<T, String>，
//!     错误统一序列化成字符串给前端

pub mod chrome;
pub mod gateway;
pub mod health;
pub mod identity;
pub mod learning;
pub mod local_search;
pub mod logs;
pub mod session_write;
pub mod sessions;
pub mod skills;
pub mod system;
pub mod tool_bridge;
pub mod types;
