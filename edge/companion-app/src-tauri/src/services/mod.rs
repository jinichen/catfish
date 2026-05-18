//! 内部服务管理 —— 不暴露给前端。
//!
//! 凡是涉及子进程生命周期、跨平台路径解析这类"基础设施"代码都进这里，
//! commands/ 调它，前端不能直接 invoke。

pub mod agent_prefs;
pub mod autostart;
pub mod catfish_paths;
pub mod curator_config;
pub mod curator_state;
pub mod email_config;     // BL-COMPANION-EMAIL-YAML-CONFIG (5/18)
pub mod email_scheduler;  // BL-COMPANION-EMAIL-DIGEST-STEP2 (5/18)
pub mod endpoints;
pub mod hermes_api_config; // BL-COMPANION-HERMES-API-CONFIG (5/19, Phase 2-2A)
pub mod oauth;
pub mod pet_hover;
pub mod pet_status;
pub mod process;
pub mod watchdog;
