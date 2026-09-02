//! 内部服务管理 —— 不暴露给前端。
//!
//! 凡是涉及子进程生命周期、跨平台路径解析这类"基础设施"代码都进这里，
//! commands/ 调它，前端不能直接 invoke。

pub mod agent_prefs;
pub mod autostart_deps; // 运行时依赖自检 (8/15 从 autostart 切出)
pub mod autostart_mcp;  // hermes config 自愈 + 其测试 (8/15 切出)
pub mod autostart;
pub mod catfish_paths;
pub mod codex_gate;       // 8/8: Codex 后端总开关 (旁路能力, 默认关 — 见该文件头)
pub mod curator_config;
pub mod memory_provider_config;
pub mod tool_loop_config; // 9/2: Companion 默认启用 Hermes 每轮工具循环硬停止
pub mod curator_state;
pub mod email_config;     // BL-COMPANION-EMAIL-YAML-CONFIG (5/18)
pub mod email_classify_parse; // 分诊 LLM 输出解析 (8/21, 纯函数独立可测)
pub mod email_llm;    // 评级 LLM 调用 (8/15 从 email_scheduler 切出)
pub mod email_notify; // 前端事件 + 系统通知 (8/15 切出)
pub mod email_state;  // 评级缓存 / push 历史 / 落盘 (8/15 切出)
pub mod email_types;  // EmailItem + Urgency (8/15 切出, 是其余几层的地基)
pub mod email_scheduler;  // BL-COMPANION-EMAIL-DIGEST-STEP2 (5/18)
pub mod embed_cache_meta; // 8/14: 向量缓存的身份记账 (中央换模型 → 自动重建)
pub mod embedding;        // P3.5.4.1 (6/16 鸿波): 公共 BGE-M3 ONNX, 给 advisor + wiki 共享同一 model session
pub mod embedding_config; // P3.5.15 (6/16 鸿波): embedding provider yaml 配置 (本机/远程切换 + 参数可调)
// 两个 provider 各自一个文件 (8/14: embedding.rs 到 890 行, 越过 800 红线)
#[cfg(target_arch = "aarch64")] // ort 只在 aarch64 有 prebuilt, 见文件头
pub mod embedding_local;
pub mod embedding_remote;
pub mod endpoints;
pub mod phishing_llm; // LLM batch 复审 (8/15 从 phishing_scan 切出)
pub mod phishing_rules; // 5 个工具 + 5 个 scan_* 纯函数 (8/15 从 phishing_scan 切出)
pub mod phishing_scan;    // P3.3.58 (6/12 鸿波): 钓鱼邮件 deterministic 规则集 + audit chain 留档
pub mod phishing_config;  // P3.3.65 (6/13 鸿波): 钓鱼规则可配置 yaml + 政企域名白名单
pub mod political_scan;   // P3.3.53 (6/13 鸿波): 政治敏感规则引擎 + audit chain 留档 (默认关)
pub mod political_config; // P3.3.53 (6/13 鸿波): 政治敏感规则配置 — yaml 段, 集团信安/党办下发
pub mod hermes_api_config; // BL-COMPANION-HERMES-API-CONFIG (5/19, Phase 2-2A)
pub mod hermes_jwt_sync;   // BL-HERMES-JWT-SYNC (7/19 Task #15): JWT auto-sync hermes 3 处
pub mod oauth_config; // OIDC 配置来源 (8/15 从 oauth 切出)
pub mod oauth_store;  // ⚠ 凭证落盘 —— 改这里前先看文件头 (8/15 切出)
pub mod oauth_token;  // 换 token + id_token 解码 (8/15 切出)
pub mod oauth;
pub mod distill_scheduler;
pub mod pet_hover;
pub mod picker_config;    // P3.5.28 (6/17 鸿波): chat picker 选的 model 桥给 background task
pub mod pet_status;
pub mod process;
pub mod upstream_error_guard; // 8/8: 上游把错误当正文返 (HTTP 200 + 错误文本) 的识别 + 冷却
pub mod tool_bridge_rpc;  // P3.5.45 (6/20 鸿波): tool-bridge sock NDJSON RPC 公共 helper, recmode + 后续 callers 复用
pub mod watchdog;
