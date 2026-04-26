//! Catfish Companion App —— Tauri 后端入口。
//!
//! 模块划分：
//!   - `commands/`  —— 暴露给前端的 #[tauri::command] 函数（按服务/职责拆分）
//!   - `services/`  —— 内部进程管理（前端不可直接访问）
//!   - `tray/`      —— menubar 托盘菜单

mod commands;
mod services;
mod tray;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    env_logger::init();

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            #[cfg(desktop)]
            tray::install(app.handle())?;

            // 调试需要时手动打开 DevTools:右键页面 → Inspect Element,
            // 或者在 lib.rs 里临时加 window.open_devtools() 重新编译。
            // 之前为了 debug 自动开过,但日常用不需要。

            // 后台静默拉起 gateway + tool-bridge —— 不让员工手动按"启动"。
            // tool-bridge 没起来 = 聊天工具列表为空 = Gemini 退化到 native tool_code。
            // 见 services/autostart.rs 详细说明。
            services::autostart::schedule_autostart();

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            // gateway
            commands::gateway::gateway_start,
            commands::gateway::gateway_stop,
            commands::gateway::gateway_status,
            commands::gateway::gateway_get_dev_token,
            // chrome
            commands::chrome::chrome_launch,
            commands::chrome::chrome_kill,
            commands::chrome::chrome_status,
            // local_search
            commands::local_search::local_search_start,
            commands::local_search::local_search_stop,
            commands::local_search::local_search_status,
            // tool_bridge
            commands::tool_bridge::tool_bridge_start,
            commands::tool_bridge::tool_bridge_stop,
            commands::tool_bridge::tool_bridge_status,
            commands::tool_bridge::tool_bridge_list_tools,
            commands::tool_bridge::tool_bridge_call_tool,
            // health
            commands::health::healthz,
            commands::health::catalog,
            // logs
            commands::logs::tail,
            commands::logs::stop_tail,
            // sessions (read)
            commands::sessions::sessions_list,
            commands::sessions::sessions_get,
            // sessions (write) —— Plan C Week 2 持久化
            commands::session_write::session_create,
            commands::session_write::session_message_append,
            commands::session_write::session_finalize,
            commands::session_write::session_update_title,
            commands::session_write::session_check,
            // identity
            commands::identity::identity_info,
            // skills + mcp
            commands::skills::list_skills,
            commands::skills::list_mcp_servers,
            // self-evolution: 鲶鱼今天学了什么
            commands::learning::learning_today_stats,
            // system
            commands::system::open_terminal,
            commands::system::notify,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
