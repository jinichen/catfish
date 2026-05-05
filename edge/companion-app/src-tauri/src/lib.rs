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
    //   - 已显示未聚焦  → 抢焦
    //   - 已隐藏/最小化 → 解最小化 + 显示 + 居中 + 抢焦
    // 配套: dock 单击鲶鱼图标会发 RunEvent::Reopen, 在文件末尾的 .run() 闭包里接.
    // 全局快捷键, 任何 app 都能召唤鲶鱼.
    #[cfg(desktop)]
    let toggle_shortcut = tauri_plugin_global_shortcut::Shortcut::new(
        Some(tauri_plugin_global_shortcut::Modifiers::SUPER | tauri_plugin_global_shortcut::Modifiers::SHIFT),
        tauri_plugin_global_shortcut::Code::Space,
    );

    // BL-E15 专注模式快捷键 (五一 sprint 5/3 晚) — Cmd+Shift+F.
    // 触发后给前端发 "catfish:focus_mode_toggle" 事件, App.tsx 切伪 IDE 视图.
    // 跟召唤快捷键独立, 互不影响.
    #[cfg(desktop)]
    let focus_shortcut = tauri_plugin_global_shortcut::Shortcut::new(
        Some(tauri_plugin_global_shortcut::Modifiers::SUPER | tauri_plugin_global_shortcut::Modifiers::SHIFT),
        tauri_plugin_global_shortcut::Code::KeyF,
    );

    #[allow(unused_mut)]
    let mut builder = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init());

    #[cfg(desktop)]
    {
        builder = builder.plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(move |app, shortcut, event| {
                    use tauri::{Emitter, Manager};
                    use tauri_plugin_global_shortcut::ShortcutState;
                    // 只在 Pressed 时响应 (Released 也会触发, 不去重就抖)
                    if event.state() != ShortcutState::Pressed {
                        return;
                    }
                    // 召唤快捷键 Cmd+Shift+Space
                    if shortcut == &toggle_shortcut {
                        if let Some(window) = app.get_webview_window("main") {
                            let visible = window.is_visible().unwrap_or(false);
                            let focused = window.is_focused().unwrap_or(false);
                            if visible && focused {
                                // 已经在前台 → 收起 (再按一次召唤 toggle)
                                let _ = window.hide();
                            } else {
                                // 召唤: 解最小化 → 显示 → 居中 → 抢焦
                                // 注意: 不调 set_always_on_top, 否则 macOS 上窗口被提到
                                // NSFloatingWindowLevel, 最小化按钮失效, dock 单击也不响应.
                                let _ = window.unminimize();
                                let _ = window.show();
                                let _ = window.center();
                                let _ = window.set_focus();
                            }
                        }
                        return;
                    }
                    // BL-E15 专注模式 Cmd+Shift+F → 给前端发事件 (toggle, 不区分开/关).
                    // 顺手把窗口拉到前台 (没显示就显示), 切完员工立刻看到伪 IDE.
                    if shortcut == &focus_shortcut {
                        if let Some(window) = app.get_webview_window("main") {
                            let _ = window.unminimize();
                            let _ = window.show();
                            let _ = window.set_focus();
                            if let Err(e) = window.emit("catfish:focus_mode_toggle", ()) {
                                log::warn!("emit focus_mode_toggle 失败: {e}");
                            }
                        }
                        return;
                    }
                })
                .build(),
        );
    }

    builder
        .setup(move |app| {
            #[cfg(desktop)]
            tray::install(app.handle())?;

            // 注册全局快捷键 Cmd+Shift+Space (浮窗召唤) + Cmd+Shift+F (BL-E15 专注模式)
            #[cfg(desktop)]
            {
                use tauri_plugin_global_shortcut::GlobalShortcutExt;
                if let Err(e) = app.global_shortcut().register(toggle_shortcut) {
                    log::warn!("注册 Cmd+Shift+Space 失败 (已被其他 app 占用?): {e}");
                } else {
                    log::info!("已注册全局快捷键 Cmd+Shift+Space → 召唤鲶鱼浮窗");
                }
                if let Err(e) = app.global_shortcut().register(focus_shortcut) {
                    log::warn!("注册 Cmd+Shift+F 失败 (已被其他 app 占用?): {e}");
                } else {
                    log::info!("已注册全局快捷键 Cmd+Shift+F → 切专注模式");
                }
            }

            // 5/5 鸿波拍板: 取消自动 open_devtools.
            // 之前 dev mode 启动自动弹 DevTools, 鸿波每次都得手动关.
            // 真要调试: dev mode 下 Cmd+Option+I 手动开.
            // release mode 默认就关 (tauri 2 release feature 默认关 devtools).

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
            commands::sessions::sessions_count,
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
            // BL-E11 命名权 (五一 sprint 5/3 晚): 员工自定义鲶鱼名 + 人设
            commands::agent::get_agent_prefs,
            commands::agent::set_agent_prefs,
            // BL-E16 关系建立 (五一 sprint 5/3 晚): "鲶鱼对你的印象" 透明 + 清空
            commands::relation::relation_summary,
            commands::relation::relation_forget,
            // BL-MM4 v1 (5/5 晚): "鲶鱼记的硬事实" 版本卡 (跟 BL-MM2 配套)
            commands::memory_history::memory_history_summary,
            commands::memory_history::memory_history_clear_key,
            commands::memory_history::memory_history_forget_all,
            // BL-MM6 (5/5 晚): 显式 feedback 👍/👎/改 + ~/.catfish/feedback.jsonl
            commands::feedback::feedback_record,
            commands::feedback::feedback_summary,
            commands::feedback::feedback_clear,
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            // 五一 sprint 5/5: dock 单击 / 主菜单"激活"鲶鱼时, 把隐藏窗口拉回来.
            //
            // 背景: 浮窗 UX 下 Cmd+Shift+Space 会调 window.hide(), 之后用户
            // 点 dock 上的鲶鱼图标默认不会重开 (Tauri 不暴露默认 reopen 行为).
            // macOS NSApplicationDelegate applicationShouldHandleReopen 会派发
            // tauri::RunEvent::Reopen, 这里接住, has_visible_windows=false 时
            // 把主窗口拽出来 + 抢焦.
            //
            // 注: RunEvent::Reopen 只 macOS 有, Linux/Win 没这个变体, 故 cfg = macos.
            #[cfg(target_os = "macos")]
            if let tauri::RunEvent::Reopen { has_visible_windows, .. } = event {
                if !has_visible_windows {
                    use tauri::Manager;
                    if let Some(window) = app_handle.get_webview_window("main") {
                        let _ = window.unminimize();
                        let _ = window.show();
                        let _ = window.set_focus();
                    }
                }
            }
            #[cfg(not(target_os = "macos"))]
            {
                let _ = (app_handle, event);
            }
        });
}
