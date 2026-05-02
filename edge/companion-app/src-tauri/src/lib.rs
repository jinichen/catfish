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

    // 五一 sprint 5/5: Cmd+Shift+Space 召唤主窗口浮窗.
    // 设计:
    //   - 已显示并聚焦  → 隐藏 (再按一次收起)
    //   - 已显示未聚焦  → 居中 + 置顶 + 抢焦
    //   - 已隐藏        → 显示 + 居中 + 置顶 + 抢焦
    // 全局快捷键, 任何 app 都能召唤鲶鱼.
    #[cfg(desktop)]
    let toggle_shortcut = tauri_plugin_global_shortcut::Shortcut::new(
        Some(tauri_plugin_global_shortcut::Modifiers::SUPER | tauri_plugin_global_shortcut::Modifiers::SHIFT),
        tauri_plugin_global_shortcut::Code::Space,
    );

    #[allow(unused_mut)]
    let mut builder = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init());

    #[cfg(desktop)]
    {
        builder = builder.plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(move |app, shortcut, event| {
                    use tauri::Manager;
                    use tauri_plugin_global_shortcut::ShortcutState;
                    if shortcut == &toggle_shortcut && event.state() == ShortcutState::Pressed {
                        if let Some(window) = app.get_webview_window("main") {
                            let visible = window.is_visible().unwrap_or(false);
                            let focused = window.is_focused().unwrap_or(false);
                            if visible && focused {
                                let _ = window.hide();
                            } else {
                                let _ = window.show();
                                let _ = window.center();
                                let _ = window.set_always_on_top(true);
                                let _ = window.unminimize();
                                let _ = window.set_focus();
                            }
                        }
                    }
                })
                .build(),
        );
    }

    builder
        .setup(move |app| {
            #[cfg(desktop)]
            tray::install(app.handle())?;

            // 注册全局快捷键 Cmd+Shift+Space (浮窗召唤)
            #[cfg(desktop)]
            {
                use tauri_plugin_global_shortcut::GlobalShortcutExt;
                if let Err(e) = app.global_shortcut().register(toggle_shortcut) {
                    log::warn!("注册 Cmd+Shift+Space 失败 (已被其他 app 占用?): {e}");
                } else {
                    log::info!("已注册全局快捷键 Cmd+Shift+Space → 召唤鲶鱼浮窗");
                }
            }

            // 五一 sprint Day 1: dev build 启动自动开 DevTools (debug_assertions 只在 cargo run / tauri dev 为 true).
            // release build (cargo build --release / tauri build) 不开, 不影响员工端.
            #[cfg(debug_assertions)]
            {
                use tauri::Manager;
                if let Some(window) = app.get_webview_window("main") {
                    window.open_devtools();
                }
            }

            // 后台静默拉起 gateway + tool-bridge —— 不让员工手动按"启动"。
            // tool-bridge 没起来 = 聊天工具列表为空 = Gemini 退化到 native tool_code。
            // 见 services/autostart.rs 详细说明。
            services::autostart::schedule_autostart();

            // Watchdog: 5s 一次检查 gateway / tool-bridge 死活, 死了 respawn.
            // skill_watcher / config_watcher 主动退进程后必须有人接锅, 否则
            // 员工卡死. 见 services/watchdog.rs.
            services::watchdog::schedule_watchdog();

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
            commands::audit::audit_summary,
            // SSO Phase 1C: OAuth flow + Keychain
            commands::auth::auth_whoami,
            commands::auth::auth_login,
            commands::auth::auth_logout,
            commands::auth::auth_get_access_token,
            // system
            commands::system::open_terminal,
            commands::system::notify,
            // file (Phase 2 优雅下载: skill 生成的 .docx/.xlsx/.pptx 在 Finder 显示)
            commands::file::reveal_in_finder,
            commands::file::open_file,
            // file_parse (五一 sprint Day 1: 文件上传解析 PDF/Excel/Word/CSV/TXT/MD)
            commands::file_parse::parse_file,
            commands::file_parse::parse_file_from_b64,
            // skill_audit (五一 sprint Day 2: skill 调用审计 + 30 天未用统计)
            commands::skill_audit::skill_audit_summary,
            // speech (五一 sprint Day 1 方案 C+: ffmpeg 录 + Whisper.cpp 转, 全本地)
            commands::speech::speech_start_recording,
            commands::speech::speech_stop_and_transcribe,
            commands::speech::speech_cancel_recording,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
