//! 内部服务管理 —— 不暴露给前端。
//!
//! 凡是涉及子进程生命周期、跨平台路径解析这类"基础设施"代码都进这里，
//! commands/ 调它，前端不能直接 invoke。

pub mod agent_prefs;
pub mod autostart;
pub mod catfish_paths;
pub mod curator_config;
pub mod memory_provider_config;
pub mod curator_state;
pub mod email_config;     // BL-COMPANION-EMAIL-YAML-CONFIG (5/18)
pub mod email_scheduler;  // BL-COMPANION-EMAIL-DIGEST-STEP2 (5/18)
pub mod embedding;        // P3.5.4.1 (6/16 鸿波): 公共 BGE-M3 ONNX, 给 advisor + wiki 共享同一 model session
pub mod embedding_config; // P3.5.15 (6/16 鸿波): embedding provider yaml 配置 (本机/远程切换 + 参数可调)
pub mod endpoints;
pub mod phishing_scan;    // P3.3.58 (6/12 鸿波): 钓鱼邮件 deterministic 规则集 + audit chain 留档
pub mod phishing_config;  // P3.3.65 (6/13 鸿波): 钓鱼规则可配置 yaml + 政企域名白名单
pub mod political_scan;   // P3.3.53 (6/13 鸿波): 政治敏感规则引擎 + audit chain 留档 (默认关)
pub mod political_config; // P3.3.53 (6/13 鸿波): 政治敏感规则配置 — yaml 段, 集团信安/党办下发
pub mod hermes_api_config; // BL-COMPANION-HERMES-API-CONFIG (5/19, Phase 2-2A)
pub mod hermes_jwt_sync;   // BL-HERMES-JWT-SYNC (7/19 Task #15): JWT auto-sync hermes 3 处
pub mod oauth;
pub mod distill_scheduler;
pub mod pet_hover;
pub mod picker_config;    // P3.5.28 (6/17 鸿波): chat picker 选的 model 桥给 background task
pub mod role_config;      // P3.5.29 Phase 4 (6/17 鸿波): GET /v1/roles fetch + 5min cache 给 background task
pub mod pet_status;
pub mod process;
pub mod tool_bridge_rpc;  // P3.5.45 (6/20 鸿波): tool-bridge sock NDJSON RPC 公共 helper, recmode + 后续 callers 复用
pub mod watchdog;
