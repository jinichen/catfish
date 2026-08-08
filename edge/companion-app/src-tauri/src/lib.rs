//! Catfish Companion App —— Tauri 后端入口。
//!
//! 模块划分：
//!   - `commands/`  —— 暴露给前端的 #[tauri::command] 函数（按服务/职责拆分）
//!   - `services/`  —— 内部进程管理（前端不可直接访问）
//!   - `tray/`      —— menubar 托盘菜单

mod app_menu;
mod commands;
mod services;
mod tray;
mod util;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // 7/15 BL-COMPANION-LOG-FILE: tauri-plugin-log 替代 env_logger.
    // - env_logger 只写 stdout/stderr, GUI app (从 Applications launch) 拿不到, log::info! 全丢
    // - tauri-plugin-log 在 setup() 里注册, 写 ~/Library/Logs/com.catfish.companion/*.log
    //   同时保留 stderr 输出 (companion.err.log 里也能看到)
    // 具体 plugin 注册在下面 builder.plugin(...) 里, 这里不 env_logger::init().

    // 五一 sprint 5/5: Cmd+Shift+Space 召唤主窗口浮窗.
    // 设计:
    //   - 已显示并聚焦  → 隐藏 (再按一次收起)
    //   - 已显示未聚焦  → 抢焦
    //   - 已隐藏/最小化 → 解最小化 + 显示 + 居中 + 抢焦
    // 配套: dock 单击鲶鱼图标会发 RunEvent::Reopen, 在文件末尾的 .run() 闭包里接.
    // 全局快捷键, 任何 app 都能召唤鲶鱼.
    #[cfg(desktop)]
    let toggle_shortcut = tauri_plugin_global_shortcut::Shortcut::new(
        Some(
            tauri_plugin_global_shortcut::Modifiers::SUPER
                | tauri_plugin_global_shortcut::Modifiers::SHIFT,
        ),
        tauri_plugin_global_shortcut::Code::Space,
    );

    // BL-E15 专注模式快捷键 (五一 sprint 5/3 晚) — Cmd+Shift+F.
    // 触发后给前端发 "catfish:focus_mode_toggle" 事件, App.tsx 切伪 IDE 视图.
    // 跟召唤快捷键独立, 互不影响.
    #[cfg(desktop)]
    let focus_shortcut = tauri_plugin_global_shortcut::Shortcut::new(
        Some(
            tauri_plugin_global_shortcut::Modifiers::SUPER
                | tauri_plugin_global_shortcut::Modifiers::SHIFT,
        ),
        tauri_plugin_global_shortcut::Code::KeyF,
    );

    // BL-E27 桌宠快捷键 (五一 sprint 5/5 凌晨) — Cmd+Shift+P (Pet).
    // 切显示 / 隐藏桌宠副窗. 鸿波 spike 后反馈"是不是有快捷键关闭" → 加这条.
    // 行为: visible → hide; hidden → show.
    #[cfg(desktop)]
    let pet_shortcut = tauri_plugin_global_shortcut::Shortcut::new(
        Some(
            tauri_plugin_global_shortcut::Modifiers::SUPER
                | tauri_plugin_global_shortcut::Modifiers::SHIFT,
        ),
        tauri_plugin_global_shortcut::Code::KeyP,
    );
    // BL-E27 4 屏角快捷键 (5/5 凌晨拖拽 NSPanel 不工作的妥协):
    // Option+Shift+1 左上 / 2 右上 / 3 左下 / 4 右下.
    // ⚠️ 不用 Cmd+Shift+3/4/5 — 跟 macOS 截屏快捷键冲突.
    #[cfg(desktop)]
    let pet_corner_tl = tauri_plugin_global_shortcut::Shortcut::new(
        Some(
            tauri_plugin_global_shortcut::Modifiers::ALT
                | tauri_plugin_global_shortcut::Modifiers::SHIFT,
        ),
        tauri_plugin_global_shortcut::Code::Digit1,
    );
    #[cfg(desktop)]
    let pet_corner_tr = tauri_plugin_global_shortcut::Shortcut::new(
        Some(
            tauri_plugin_global_shortcut::Modifiers::ALT
                | tauri_plugin_global_shortcut::Modifiers::SHIFT,
        ),
        tauri_plugin_global_shortcut::Code::Digit2,
    );
    #[cfg(desktop)]
    let pet_corner_bl = tauri_plugin_global_shortcut::Shortcut::new(
        Some(
            tauri_plugin_global_shortcut::Modifiers::ALT
                | tauri_plugin_global_shortcut::Modifiers::SHIFT,
        ),
        tauri_plugin_global_shortcut::Code::Digit3,
    );
    #[cfg(desktop)]
    let pet_corner_br = tauri_plugin_global_shortcut::Shortcut::new(
        Some(
            tauri_plugin_global_shortcut::Modifiers::ALT
                | tauri_plugin_global_shortcut::Modifiers::SHIFT,
        ),
        tauri_plugin_global_shortcut::Code::Digit4,
    );

    #[allow(unused_mut)]
    let mut builder = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        // 7/15 BL-COMPANION-LOG-FILE: log::info!/warn!/error! → 文件 + stderr
        // 文件路径: macOS = ~/Library/Logs/com.catfish.companion/Catfish Companion.log
        //           Windows = %APPDATA%/com.catfish.companion/logs/Catfish Companion.log
        // rotate: 10MB 单文件, 保 5 份历史 (下游 debug 够用, 磁盘可控)
        .plugin(
            tauri_plugin_log::Builder::new()
                .level(log::LevelFilter::Info)
                .max_file_size(10 * 1024 * 1024u128) // 10MB per file
                .rotation_strategy(tauri_plugin_log::RotationStrategy::KeepAll)
                .targets([
                    // macOS: ~/Library/Logs/com.catfish.companion/<bundle-id>.log
                    // Windows: %LOCALAPPDATA%\com.catfish.companion\logs\
                    // 用 identifier 不是 productName, 跟 stdio 重定向 log (Catfish Companion/) 区分
                    tauri_plugin_log::Target::new(tauri_plugin_log::TargetKind::LogDir {
                        file_name: None,
                    }),
                    // stderr 保留 · GUI app 无 stdout, 但 launchd 会把 stderr 转到 companion.err.log
                    tauri_plugin_log::Target::new(tauri_plugin_log::TargetKind::Stderr),
                ])
                .build(),
        );

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
                    // BL-E27 桌宠 Cmd+Shift+P → toggle pet 副窗显示/隐藏
                    if shortcut == &pet_shortcut {
                        if let Some(pet) = app.get_webview_window("pet") {
                            let visible = pet.is_visible().unwrap_or(false);
                            if visible {
                                let _ = pet.hide();
                                log::info!("Cmd+Shift+P: 桌宠隐藏");
                            } else {
                                // 5/6 BL-E27.2: 默认 ignore=true (透明区穿透),
                                // hover tracker 80ms 后会在鲶鱼区切回 false
                                let _ = pet.set_ignore_cursor_events(true);
                                let _ = pet.show();
                                log::info!("Cmd+Shift+P: 桌宠显示");
                            }
                        }
                        return;
                    }
                    // BL-E27 4 屏角 Cmd+Shift+1/2/3/4 — 拖拽不工作的妥协.
                    // 复用 pet_move_corner 命令逻辑 (避免 Rust 重写).
                    let corner: Option<&str> = if shortcut == &pet_corner_tl {
                        Some("tl")
                    } else if shortcut == &pet_corner_tr {
                        Some("tr")
                    } else if shortcut == &pet_corner_bl {
                        Some("bl")
                    } else if shortcut == &pet_corner_br {
                        Some("br")
                    } else {
                        None
                    };
                    if let Some(corner) = corner {
                        if let Some(pet) = app.get_webview_window("pet") {
                            // 5/5 鸿波二报"3/4 出屏幕" 修: 用 logical 坐标. Retina 2x 屏
                            // monitor.size() 返物理像素 (2880x1800), 直接用 setPosition
                            // 桌宠会被定位到 logical (1440x900) 屏外.
                            if let Ok(Some(monitor)) = pet.current_monitor() {
                                let m_size = monitor.size();
                                let m_pos = monitor.position();
                                let scale = monitor.scale_factor();
                                let logical_w = (m_size.width as f64 / scale) as i32;
                                let logical_h = (m_size.height as f64 / scale) as i32;
                                let logical_pos_x = (m_pos.x as f64 / scale) as i32;
                                let logical_pos_y = (m_pos.y as f64 / scale) as i32;
                                const W: i32 = 200;  // 5/6 桌宠窗 120 → 200 留气泡空间
                                const H: i32 = 200;
                                const MARGIN: i32 = 16;
                                const TOP_RESERVED: i32 = 32;     // menu bar
                                const BOTTOM_RESERVED: i32 = 80;  // dock 估值
                                let (x, y) = match corner {
                                    "tl" => (logical_pos_x + MARGIN, logical_pos_y + TOP_RESERVED),
                                    "tr" => (
                                        logical_pos_x + logical_w - W - MARGIN,
                                        logical_pos_y + TOP_RESERVED,
                                    ),
                                    "bl" => (
                                        logical_pos_x + MARGIN,
                                        logical_pos_y + logical_h - H - BOTTOM_RESERVED,
                                    ),
                                    "br" => (
                                        logical_pos_x + logical_w - W - MARGIN,
                                        logical_pos_y + logical_h - H - BOTTOM_RESERVED,
                                    ),
                                    _ => unreachable!(),
                                };
                                let _ = pet.set_position(
                                    tauri::LogicalPosition::new(x as f64, y as f64),
                                );
                                // 5/6 BL-E27.2: 默认 ignore=true, hover tracker 80ms 修
                                let _ = pet.set_ignore_cursor_events(true);
                                let _ = pet.show();
                                log::info!(
                                    "Option+Shift+{} (corner {}): logical ({}, {}) on {}x{} scale {}",
                                    match corner { "tl" => 1, "tr" => 2, "bl" => 3, "br" => 4, _ => 0 },
                                    corner, x, y, logical_w, logical_h, scale,
                                );
                            }
                        }
                    }
                })
                .build(),
        );
    }

    builder
        .setup(move |app| {
            // Release QA can launch against an isolated HOME without touching
            // the user's real launch agents, global shortcuts or background
            // services. Hermes bootstrap still runs so first-launch timing and
            // recovery are exercised end to end.
            let qa_mode = std::env::var_os("CATFISH_QA_MODE").is_some();

            #[cfg(desktop)]
            tray::install(app.handle())?;

            // 5/18 BL-COMPANION-ABOUT-HIJACK: macOS app menu 自定义,
            // "关于鲶鱼" item 走我们自己的 React 模态而不是原生 panel.
            #[cfg(target_os = "macos")]
            {
                if let Err(e) = app_menu::install(app) {
                    log::warn!("app_menu::install 失败 (不阻塞启动, 默认菜单兜底): {e}");
                }
            }

            // 5/7 BL-CR: 启动时确保 ~/.hermes/config.yaml 有保守 curator 段
            // (hermes 0.12 默认 30/90/2h 太激进, 我们 patch 成 60/180/4h).
            // 已存在 curator 段 → 不动 (尊重员工 tune 过的值).
            match services::curator_config::ensure_default() {
                Ok(true) => log::info!("BL-CR: 写入鲶鱼保守 curator 默认配置 (60d stale / 180d archive / 4h idle)"),
                Ok(false) => log::debug!("BL-CR: ~/.hermes/config.yaml 已有 curator 段, 不动"),
                Err(e) => log::warn!("BL-CR: ensure_curator_default 失败 (不阻塞启动): {e}"),
            }

            // 8/6 鸿波「早安里说的项目进度, 工作台不知道」: catfish-memory 这个
            // memory provider **从来没被激活过** —— 打包只 cp -R 拷文件
            // (build-mac-resources.sh:185), 激活逻辑在 install-catfish-memory.sh:122,
            // 而装机流程一处都没调它。于是每台机器的 config.yaml 里都没有 memory 段,
            // 长期记忆整个功能从未开启, 而 lib/chat.ts 一直假定它在跑。
            //
            // 放启动路径而不是装机路径: 已装好的机器重启一次就自动修好, 不用重装。
            // 已有 memory 段则不动 —— 员工可能自己配了 mem0/honcho, 跟 curator 同原则。
            match services::memory_provider_config::ensure() {
                Ok(true) => log::info!(
                    "[memory-provider] 写入 memory.provider: catfish-memory —— \
                     长期记忆此前从未激活, hermes 下次重启后生效"
                ),
                Ok(false) => log::debug!("[memory-provider] config.yaml 已有 memory 段, 不动"),
                Err(e) => log::warn!("[memory-provider] 激活失败 (不阻塞启动): {e}"),
            }

            // 7/17 BL-SESSIONS-INDEX: 后台 build state.db 索引, 员工点侧栏"对话"不卡.
            // 鸿波 2761 sessions 时 catch: 无 index 时 sessions_list 子查询 O(N×M)
            // 首启就要 1-3 秒卡. 挪到 startup 后台线程建, 员工首次点侧栏时索引就绪.
            // 幂等 IF NOT EXISTS · 已存在秒过 · 首次 build 1-3 秒不阻塞 UI.
            std::thread::spawn(|| {
                if let Err(e) = commands::sessions::ensure_indexes_background() {
                    log::warn!("BL-SESSIONS-INDEX: 后台建索引失败 (list_blocking 会 lazy 建兜底): {e}");
                } else {
                    log::info!("BL-SESSIONS-INDEX: state.db 索引就绪 (sessions_list < 100ms)");
                }
            });

            // P3.5.55 (6/21 鸿波 2 次 catch):
            //   1st: "客户没 catfish 源 → SOUL 软链 dangling → 鲶鱼退化"
            //   2nd: "思路是错的, catfish 应该能修改 hermes soul.md 才对, 保证一致"
            //
            // catfish 是 SOUL **唯一 source of truth**. Companion 启动主动写
            // ~/.hermes/SOUL*.md, 强制跟 catfish 当前版本一致 (overwrite regular file).
            //
            // 唯一不 overwrite 的情况: ~/.hermes/SOUL.md 是**健康软链** (开发者
            // catfish git clone + install.sh 路径) — 那条线让 catfish/edge/identity/
            // SOUL.md 改即生效, 不能被覆盖. dangling 软链 / regular file / 不存在 → 写 baked.
            //
            // Escape hatch: CATFISH_SOUL_NO_BOOTSTRAP=1 跳全部 (调试用).
            commands::identity_bundle::bootstrap_soul_files();

            // P3.5.56 (6/21 鸿波 "有坑就要立刻填平"): Companion boot 自动装
            // catfish-xcatfish-user hermes plugin (跟 SOUL P3.5.55 同款机制, 治本"客户
            // 装 Companion 但没装 plugin → 19 个 P-patch 全失效 → 鲶鱼集成裸 hermes").
            //
            // 同步 9 个 plugin 文件 (~189KB include_str! baked) 到 ~/.hermes/plugins/
            // catfish-xcatfish-user/. 同款 4 状态分支: 健康软链不动, 别的全 overwrite.
            //
            // 顺带 ensure ~/.hermes/config.yaml plugins.enabled 含 catfish-xcatfish-user
            // (hermes plugin loader 白名单, 没在里面即使文件就位也不加载).
            //
            // **不重启 hermes daemon** — Companion 不管 hermes 进程 (launchctl 管),
            // 等 hermes 下次自然重启 / 员工手动 kickstart 自动生效.
            //
            // Escape hatch: CATFISH_HERMES_PLUGIN_NO_BOOTSTRAP=1 跳全部 (调试用).
            commands::hermes_plugin::bootstrap_hermes_plugin();

            // 7/15 BL-CATFISH-MAC-OFFLINE-INSTALL: macOS/Linux dmg 首启装 hermes-agent 本体.
            // Windows msi CustomAction (wix/catfish-postinstall.wxs) 已在 msi 装机时装 hermes,
            // 跳过. Escape: CATFISH_HERMES_INSTALL_NO_BOOTSTRAP=1 (dev 已装本地 hermes).
            #[cfg(any(target_os = "macos", target_os = "linux"))]
            {
                if std::env::var("CATFISH_HERMES_INSTALL_NO_BOOTSTRAP").is_err() {
                    use tauri::Manager;
                    match app.path().resource_dir() {
                        Ok(res_dir) => {
                            // 首启可能要解压/安装 1GB+ Python、Node、Chromium 与 Hermes。
                            // setup hook 必须立即返回，让主窗口先显示；后台通过
                            // `hermes-bootstrap-progress` 发结构化进度。
                            commands::hermes_install::spawn_hermes_bootstrap(
                                app.handle().clone(),
                                res_dir,
                            );
                        }
                        Err(e) => {
                            log::warn!("拿不到 resource_dir, 跳过 hermes install: {e}");
                        }
                    }
                }
            }

            if qa_mode {
                log::info!(
                    "CATFISH_QA_MODE: 跳过 JWT、全局快捷键、自动服务、迁移和桌宠调度"
                );
                return Ok(());
            }

            // BL-HERMES-JWT-STARTUP-SYNC (7/19 Task #15 鸿波): 启动时同步 hermes JWT 3 处.
            //
            // 为啥必要: 达华员工 · 装完 Companion · 关机 · 第二天开机 · Companion 起 ·
            // hermes daemon 也起 (launchd auto-start) · **但 hermes daemon 用的
            // ~/.hermes/.env OPENAI_API_KEY 是老 JWT** (可能过期). WeChat 立刻显英文.
            // 修 · 启动 30 秒后 · 走 ensure_fresh_access_token → 若 exp<5min 自动 refresh ·
            // 然后 sync 3 处. 员工完全无感.
            //
            // 等 30 秒是给 · hermes install 完 (首启 offline install ~10min · 不首启秒过) +
            // OAuth session 从 keyring load 完. 保守 · 免 race.
            #[cfg(any(target_os = "macos", target_os = "linux"))]
            {
                tauri::async_runtime::spawn(async {
                    tokio::time::sleep(std::time::Duration::from_secs(30)).await;
                    if let Some(jwt) = services::oauth::ensure_fresh_access_token().await {
                        if let Err(e) = services::hermes_jwt_sync::sync_all(&jwt) {
                            log::warn!("[startup-jwt-sync] hermes_jwt_sync 挂: {e:#}");
                        } else {
                            log::info!("[startup-jwt-sync] ✓ hermes config.yaml + auth.json 同步完 (启动 +30s)");
                        }
                    } else {
                        log::debug!("[startup-jwt-sync] 未 SSO 登 (access_token 空) · skip");
                    }

                    // BL-P26-SERVICE-TOKEN-STARTUP (7/19 Task #26): 启动时 · 拿 30 天
                    // service token 塞 ~/.hermes/.env OPENAI_API_KEY. 老 sync_all 用
                    // access_token 覆盖 env (TTL 1h) · Companion 关闭无 refresh 就
                    // 过期 · hermes 401. 现在 env 独立 · 30 天 service token · 稳.
                    let identity_url = match services::oauth::OidcConfig::load() {
                        Ok(cfg) => cfg.issuer,
                        Err(e) => {
                            log::debug!(
                                "[startup-service-token-sync] OidcConfig::load 挂 · skip: {e:#}"
                            );
                            return;
                        }
                    };
                    if let Err(e) = services::hermes_jwt_sync::sync_service_token_to_env(
                        &identity_url,
                    ).await {
                        log::warn!(
                            "[startup-service-token-sync] service token 塞 env 挂: {e:#}"
                        );
                    } else {
                        log::info!(
                            "[startup-service-token-sync] ✓ hermes/.env OPENAI_API_KEY = 30 天 service token"
                        );
                    }
                });

                // BL-HERMES-JWT-PERIODIC-SYNC (7/19 Task #15 鸿波): 每 25 min 定时同步.
                //
                // 为啥 25 min: id_token/access_token TTL 1h. 25 min < 1h · 保证在 access
                // 快过期前 · ensure_fresh_access_token 内部会 refresh (< 5 min 剩量触发) ·
                // 然后 sync config.yaml. Companion 关 · env 30 天 service token 兜底.
                tauri::async_runtime::spawn(async {
                    let period = std::time::Duration::from_secs(25 * 60);
                    loop {
                        tokio::time::sleep(period).await;
                        if let Some(jwt) = services::oauth::ensure_fresh_access_token().await {
                            if let Err(e) = services::hermes_jwt_sync::sync_all(&jwt) {
                                log::warn!("[periodic-jwt-sync] hermes_jwt_sync 挂: {e:#}");
                            } else {
                                log::debug!("[periodic-jwt-sync] ✓ hermes config.yaml 同步 (25min tick)");
                            }
                        }
                    }
                });

                // BL-P26-SERVICE-TOKEN-PERIODIC (7/19 Task #26): 每 25 天定时 refresh
                // service token · 提前 5 天覆盖 · 免 30 天 exp 撞. 达华员工连用 3 月
                // 也不撞 401.
                tauri::async_runtime::spawn(async {
                    let period = std::time::Duration::from_secs(25 * 24 * 60 * 60);
                    loop {
                        tokio::time::sleep(period).await;
                        let identity_url = match services::oauth::OidcConfig::load() {
                            Ok(cfg) => cfg.issuer,
                            Err(_) => continue,
                        };
                        if let Err(e) = services::hermes_jwt_sync::sync_service_token_to_env(
                            &identity_url,
                        ).await {
                            log::warn!(
                                "[periodic-service-token-sync] 挂 (env 里旧 token 还有效): {e:#}"
                            );
                        } else {
                            log::info!(
                                "[periodic-service-token-sync] ✓ hermes/.env service token 续 30 天"
                            );
                        }
                    }
                });
            }

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
                if let Err(e) = app.global_shortcut().register(pet_shortcut) {
                    log::warn!("注册 Cmd+Shift+P 失败 (已被其他 app 占用?): {e}");
                } else {
                    log::info!("已注册全局快捷键 Cmd+Shift+P → 切桌宠显示/隐藏");
                }
                // 4 屏角快捷键 (拖拽妥协方案), 用 Option+Shift+1/2/3/4 避开
                // macOS 截屏快捷键 (Cmd+Shift+3/4/5).
                for (sc, label) in [
                    (pet_corner_tl, "Option+Shift+1 → 桌宠左上"),
                    (pet_corner_tr, "Option+Shift+2 → 桌宠右上"),
                    (pet_corner_bl, "Option+Shift+3 → 桌宠左下"),
                    (pet_corner_br, "Option+Shift+4 → 桌宠右下"),
                ] {
                    if let Err(e) = app.global_shortcut().register(sc) {
                        log::warn!("注册 {label} 失败: {e}");
                    } else {
                        log::info!("已注册全局快捷键 {label}");
                    }
                }
            }

            // 5/5 鸿波拍板: 取消自动 open_devtools.
            // 之前 dev mode 启动自动弹 DevTools, 鸿波每次都得手动关.
            // 真要调试: dev mode 下 Cmd+Option+I 手动开.
            // release mode 默认就关 (tauri 2 release feature 默认关 devtools).

            // tool-bridge/watchdog/email 都依赖完整 Hermes venv。首启 bootstrap
            // 已改成后台后，若这里仍立即启动，watchdog 会在安装的几十秒内连续
            // 失败并进入 180 秒 backoff，造成“窗口快了但工具暂时不可用”。
            // 因此正常安装路径等待严格完成标记；开发者显式禁用 bootstrap 时仍
            // 沿用立即启动，兼容自管 Hermes 环境。
            let runtime_services_app = app.handle().clone();
            #[cfg(any(target_os = "macos", target_os = "linux"))]
            if std::env::var("CATFISH_HERMES_INSTALL_NO_BOOTSTRAP").is_err() {
                tauri::async_runtime::spawn(async move {
                    let mut waited_secs = 0u64;
                    loop {
                        if commands::hermes_install::hermes_agent_installed() {
                            log::info!(
                                "Hermes bootstrap 就绪（等待 {waited_secs}s），启动本地服务"
                            );
                            services::autostart::schedule_autostart();
                            services::watchdog::schedule_watchdog();
                            services::email_scheduler::schedule_email_scheduler(
                                runtime_services_app,
                            );
                            break;
                        }
                        if waited_secs == 0 {
                            log::info!("本地服务等待 Hermes bootstrap 完成");
                        } else if waited_secs % 30 == 0 {
                            log::info!("本地服务仍在等待 Hermes bootstrap（{waited_secs}s）");
                        }
                        tokio::time::sleep(std::time::Duration::from_secs(2)).await;
                        waited_secs += 2;
                    }
                });
            } else {
                services::autostart::schedule_autostart();
                services::watchdog::schedule_watchdog();
                services::email_scheduler::schedule_email_scheduler(runtime_services_app);
            }

            #[cfg(not(any(target_os = "macos", target_os = "linux")))]
            {
                services::autostart::schedule_autostart();
                services::watchdog::schedule_watchdog();
                services::email_scheduler::schedule_email_scheduler(runtime_services_app);
            }

            // P3.3.19 C Phase 4 (6/11): jsonl → state.db 一次性 migration. flag
            // ~/.catfish/migration_v1_done 存在跳过. fire-and-forget 后台跑,
            // 不阻塞 UI. 量级估 < 300ms (8 file / 40KB). log 完成报告给员工排错.
            // 用 std::thread (sync rusqlite) 而非 tokio::spawn —
            // setup hook 不在 tokio runtime context, tokio::spawn 当场 panic.
            std::thread::spawn(|| {
                match commands::task_chat_migration::run_migration() {
                    Ok(report) => {
                        if report.already_done {
                            log::info!("[migration v1] 已 done, 跳过 (~/.catfish/migration_v1_done 存在)");
                        } else {
                            log::info!(
                                "[migration v1] 完成: scanned={} sessions={} msgs={} skipped={} failed={} elapsed={}ms",
                                report.jsonl_files_scanned,
                                report.sessions_created,
                                report.messages_imported,
                                report.skipped_already_exists,
                                report.failed_files.len(),
                                report.elapsed_ms,
                            );
                            for f in &report.failed_files {
                                log::warn!("[migration v1] failed: {f}");
                            }
                        }
                    }
                    Err(e) => log::warn!("[migration v1] 跑挂 (不阻塞启动): {e}"),
                }
            });

            // P3.3.52 (6/12 鸿波): decisions.jsonl pre-chain 迁移. 一次性, 不阻塞启动.
            // 鸿波 6/12 拍板 A 方案: 历史数据不参与 hash chain, 整体 rename .pre-chain.bak.
            // 判定条件: ~/.catfish/decisions.jsonl 存在 + decisions.jsonl.chain.json 不存在.
            std::thread::spawn(|| {
                let Ok(home) = crate::util::paths::home_env() else {
                    log::warn!("[decisions migration] HOME 未设, 跳过");
                    return;
                };
                let dir = std::path::PathBuf::from(home).join(".catfish");
                let jsonl = dir.join("decisions.jsonl");
                let chain = dir.join("decisions.jsonl.chain.json");
                if !jsonl.exists() {
                    log::info!("[decisions migration] decisions.jsonl 不存在, 跳过");
                    return;
                }
                if chain.exists() {
                    log::info!("[decisions migration] chain.json 已存在, 跳过 (已迁移过或 fresh start)");
                    return;
                }
                let nanos = std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .map(|d| d.as_secs())
                    .unwrap_or(0);
                let bak = dir.join(format!("decisions.jsonl.pre-chain.bak.{nanos}"));
                match std::fs::rename(&jsonl, &bak) {
                    Ok(_) => log::info!(
                        "[decisions migration] {} → {} (P3.3.52 A 方案, 历史不参与 chain)",
                        jsonl.display(), bak.display(),
                    ),
                    Err(e) => log::warn!(
                        "[decisions migration] rename 失败 (不阻塞启动): {e}",
                    ),
                }
            });

            // 5/6 BL-E27.2: 桌宠 hover tracker — 80ms 一次轮询鼠标位置,
            // 切 set_ignore_cursor_events 让透明区真透 (附近点击穿到桌面),
            // 桌宠区接事件 (能点能拖). 见 services/pet_hover.rs.
            services::pet_hover::schedule_pet_hover_tracker(app.handle().clone());
            // 8/4: 定时蒸馏。以前那句"由 hermes plugin 自动每 24h 跑"是空的 ——
            // catfish-memory 根本没被 hermes 加载 (config.yaml plugins.enabled
            // 里没有它), 蒸馏只在员工点按钮时以 dream_cli.py 子进程形态跑过。
            // 详见 services/distill_scheduler.rs 顶部。
            services::distill_scheduler::schedule_distill(app.handle().clone());

            // BL-E27 spike (5/5 凌晨): macOS 透明窗 — 不依赖 unsafe NSWindow 调用.
            // 单纯 transparent:true 在某些 macOS 版本仍白底, macOSPrivateApi:true (config 顶层加)
            // 让 Tauri 用 NSPanel 替代 NSWindow, NSPanel 默认 backgroundColor=clear,
            // 配合 pet.html body { background: transparent } 真透明.
            //
            // 如果 macOSPrivateApi 还不够 (5/5 鸿波报"白底"), 5/22 BL-E27.1 真做时
            // 加 tauri-plugin-window-vibrancy crate 用 setBackgroundColor:clearColor.

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            // gateway (P39 5/22 解耦收尾: start/stop/get_dev_token 删, 只留 status 探活.
            // 生产员工机 gateway 由 launchctl/客户 IT 管, Companion 不 spawn.)
            commands::gateway::gateway_status,
            // P3.5.125 (6/26 鸿波 catch "catfish 对 hermes/chrome hang 无监控"):
            // hermes hang detection + auto restart (kill -9 触发 launchd 拉)
            commands::hermes::hermes_status,
            commands::hermes::hermes_kill,
            // Hermes 0.18 原生 Codex app-server runtime：检测 / 登录 / 一键切换.
            commands::codex_backend::codex_backend_status,
            commands::codex_backend::codex_backend_set_enabled,
            commands::codex_backend::codex_backend_select_model,
            commands::codex_backend::codex_backend_open_login,
            // BL-CSP-PROXY (7/18 鸿波): Rust reqwest HTTP 代理, 让前端 fetch 走 Rust,
            // CSP connect-src 保持严格 (无外网白名单). 达华 POC 员工输达华 IP 才能通 chat.
            commands::http_proxy::http_proxy,
            commands::http_proxy::http_proxy_stream,
            commands::http_proxy::http_proxy_abort,
            // chrome
            commands::chrome::chrome_launch,
            commands::chrome::chrome_kill,
            commands::chrome::chrome_status,
            // local_search
            commands::local_search::local_search_start,
            commands::local_search::local_search_stop,
            commands::local_search::local_search_status,
            // BL-SEARCH-NO-BOOTSTRAP (7/27): 前台跑一次索引 (整库 / 单目录).
            // watcher 只吃变化事件, 存量文件得靠这个进索引.
            commands::local_search::local_search_index,
            // BL-SEARCH-STALE-SCOPE (7/27): 删目录后清掉它在索引里的数据.
            commands::local_search::local_search_clean,
            // P3.5.126 (6/26 鸿波 catch "local_search 目录设置 UI 找不到"):
            // 索引目录 UI 管理 (search-scope.yaml include 段读写)
            commands::local_search_scope::local_search_scope_get,
            commands::local_search_scope::local_search_scope_add,
            commands::local_search_scope::local_search_scope_remove,
            // P3.5.127 (6/26 鸿波 catch "怎么知道文件是不是有被索引?"):
            // 索引状态查询 — 直接读 ~/.catfish/search.db, 复用 Python stats_summary 逻辑
            commands::local_search_stats::local_search_stats,
            // tool_bridge
            commands::tool_bridge::tool_bridge_start,
            commands::tool_bridge::tool_bridge_stop,
            // P3.5.196 (7/7 鸿波): 手动强制重启 tool-bridge (pkill + fresh spawn),
            // 无需重启 Companion. 用于 code 变更后 pick up 新逻辑, 或 hermes 升级
            // 后 monkey-patch 签名对齐修复.
            commands::tool_bridge::tool_bridge_restart,
            commands::tool_bridge::tool_bridge_status,
            commands::tool_bridge::tool_bridge_list_tools,
            commands::tool_bridge::tool_bridge_call_tool,
            commands::tool_bridge::tool_bridge_chat_approval,
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
            // sessions (delete) —— BL-SESSION-MGMT C (5/15)
            commands::sessions::session_soft_delete,
            commands::sessions::session_restore,
            commands::sessions::sessions_bulk_delete_short,
            // sessions (write) —— Plan C Week 2 持久化
            commands::session_write::session_create,
            commands::session_write::session_message_append,
            commands::session_write::session_finalize,
            commands::session_write::session_update_title,
            commands::session_write::session_check,
            // P3.3.19 (6/11) C 路线 Phase 1: task ↔ session 关联 sidecar
            commands::session_write::session_set_task_uid,
            commands::session_write::session_get_task_uid,
            commands::session_write::session_get_by_task_uid,
            commands::session_write::list_sessions_by_task_uid,
            // identity
            commands::identity::identity_info,
            // skills + mcp
            // 6/2 BL-SKILLS-CARD-SPLIT (鸿波): 拆 2 命令 — list_my_skills (扫 ~/.catfish/skills/,
            // 员工真生成) + list_installed_skills (扫 catfish 仓库 + ~/.hermes/skills/, 内置/装的).
            // 7/17 BL-DEADCODE-SWEEP: 老 list_skills 兜底命令死链已删.
            commands::skills::list_my_skills,
            commands::skills::list_installed_skills,
            commands::skills::list_mcp_servers,
            // E7 phase 2 (6/6): skill 安装/卸载, MCP 接入/移除 + undo 5s
            commands::skills::install_skill_from_url,
            commands::skills::install_skill_from_zip,  // P3.3.23 (6/11)
            commands::skills::uninstall_skill,
            commands::skills::restore_skill,
            commands::skills::add_mcp_server,
            commands::skills::remove_mcp_server,
            // 6/7 BL-MANIFESTO-ADVISORY-PHASE1: advisory local state (本机 SQLite)
            commands::advisory::advisory_list_local_states,
            commands::advisory::advisory_get_local_state,
            commands::advisory::advisory_mark_shown,
            commands::advisory::advisory_ack,
            commands::advisory::advisory_snooze,
            commands::advisory::advisory_dismiss,
            // 6/8 BL-EMPLOYEE-SELF-SERVE A1+A2: 重置 / 导出 (manifesto 公理 1 员工主权)
            commands::self_serve::self_serve_preview_reset,
            commands::self_serve::self_serve_execute_reset,
            commands::self_serve::self_serve_restore_reset,
            commands::self_serve::self_serve_export_data,
            // 6/8 BL-EMPLOYEE-SELF-SERVE A4: 数据外发日志 (员工自审 catfish 中央交换)
            commands::transparent_log::transparent_log_record,
            commands::transparent_log::transparent_log_query,
            commands::transparent_log::transparent_log_export_csv,
            commands::transparent_log::transparent_log_gc,
            // self-evolution: 鲶鱼今天学了什么
            commands::learning::learning_today_stats,
            // BL-MM9-accept (5/9): skill proposal accept/reject 按钮
            commands::learning::skill_proposal_accept,
            commands::learning::skill_proposal_reject,
            commands::audit::audit_summary,
            // P3.5.59 (6/22 鸿波): tool-bridge audit jsonl 聚合 per-tool perf
            commands::tool_perf::tool_perf_summary,
            // P3.3.51 (6/12 鸿波): audit hash chain — decisions / political_scan jsonl 防篡改
            commands::audit_chain::audit_chain_append,
            commands::audit_chain::audit_chain_verify,
            commands::audit_chain::audit_chain_status,
            // P3.3.54 (6/12 鸿波): 审计员看的 xlsx 多 sheet 导出 (~/.catfish/exports/)
            commands::audit_export::audit_export_xlsx,
            // P3.3.55 (6/12 鸿波): 审计视图 Tab 表格化呈现 jsonl raw read
            commands::audit_export::audit_decisions_raw_read,
            commands::audit_export::audit_hermes_jsonl_read,
            // BL-RECMODE-DASHBOARD-UI (#75, 5/25): 我的录屏 inventory + Finder + delete
            commands::recordings::recordings_list,
            commands::recordings::recordings_show_in_finder,
            commands::recordings::recordings_delete,
            // SSO Phase 1C: OAuth flow + Keychain
            commands::auth::auth_whoami,
            commands::auth::auth_login,
            commands::auth::auth_logout,
            commands::auth::auth_get_access_token,
            // system
            commands::system::notify,
            // BL-REMINDER (5/13): macOS Reminders.app 集成
            commands::system::create_reminder,
            commands::system::list_reminder_lists,
            // BL-CALENDAR (5/14 0:30): macOS Calendar.app 集成 — 时间锚定事件
            commands::system::create_calendar_event,
            commands::system::list_calendars,
            // BL-CATFISH-HERMES-VERSION-SYNC-B (6/1): AboutModal 显 hermes 版本
            commands::system::get_hermes_version,
            // file (Phase 2 优雅下载: skill 生成的 .docx/.xlsx/.pptx 在 Finder 显示)
            commands::file::reveal_in_finder,
            commands::file::open_file,
            // file_parse (五一 sprint Day 1: 文件上传解析 PDF/Excel/Word/CSV/TXT/MD)
            commands::file_parse::parse_file,
            commands::file_parse::parse_file_from_b64,
            // BL-L26 (5/7): 大文件 (≥50KB) BM25 段落检索
            commands::file_parse::attachment_bm25_search,
            // BL-FILE-SESSION-INDEX-V1 Phase 1 (5/30): 附件 metadata 持久化 ~/.catfish/attachments.db
            commands::attachments::attachment_record,
            commands::attachments::attachment_list_by_session,
            commands::attachments::attachment_list_by_user,
            commands::attachments::attachment_search_local,
            commands::attachments::attachment_delete,
            commands::attachments::attachment_delete_by_user,
            // P3.5.8 Phase 2 (6/16): image 落盘 + 从 keptPath 读 base64.
            //   - attachment_save_image: 上传时调一次, 写到 ~/.catfish/uploads/, 返 keptPath
            //   - attachment_load_base64: resume 时调一次, 读回 base64 填 attachments
            commands::attachments::attachment_save_image,
            commands::attachments::attachment_load_base64,
            // BL-LONG-RUNNING-V1 (5/30): 读 ~/.catfish/tasks.jsonl 历史任务
            commands::tasks_history::tasks_history_read,
            // skill_audit (五一 sprint Day 2: skill 调用审计 + 30 天未用统计)
            commands::skill_audit::skill_audit_summary,
            // speech (五一 sprint Day 1 方案 C+: ffmpeg 录 + Whisper.cpp 转, 全本地)
            commands::speech::speech_start_recording,
            commands::speech::speech_stop_and_transcribe,
            commands::speech::speech_cancel_recording,
            // BL-VOICE3 (5/10): 拖音频文件转文字 (mp3/m4a/wav/...) → ffmpeg + whisper
            commands::speech::transcribe_audio_from_b64,
            // BL-VOICE2 (5/10): Piper local TTS — 跟 STT 对称, 100% 本地数据不出公司
            commands::tts::tts_synthesize,
            commands::tts::tts_status,
            // BL-E11 命名权 (五一 sprint 5/3 晚): 员工自定义鲶鱼名 + 人设
            commands::agent::get_agent_prefs,
            commands::agent::set_agent_prefs,
            // BL-WIN9 / DEPLOY1 (5/8): 暴露 yaml 配置的 endpoints 给前端动态读
            commands::endpoints::get_runtime_endpoints,
            // BL-CR Curator 集成 (5/7): hermes 0.12 自动整理脚本配置 + 状态展示
            commands::curator::get_curator_config,
            commands::curator::set_curator_config,
            commands::curator::ensure_curator_default,
            commands::curator::get_curator_state,
            // BL-CALENDAR-INTEGRATION (5/20): macOS Calendar.app 今日 events
            commands::calendar::calendar_today_fetch,
            // BL-CALENDAR-WEEK (5/20): 跨日 7 天 events (今天 + 明天 + 后 5 天)
            commands::calendar::calendar_week_fetch,
            // BL-BRIEFING-DECISION (5/21 Phase 5): 综合判断上下文包 (distilled_facts + 24h sessions)
            commands::briefing_context::briefing_context_fetch,
            // BL-JOURNAL-TODO-EXTRACT (5/20): ~/.catfish/employee_journal.md 未完成 TODO
            commands::journal::journal_todos_fetch,
            // BL-JOURNAL-TODO-EXTRACT step2 (5/20): 读 journal 最近 5KB 给 LLM 抽自然语言 TODO
            commands::journal::journal_read_recent,
            // BL-JOURNAL-TODO-EDIT-CHAT Stage 1 (5/20): journal CRUD 给 LLM tool calling 改
            commands::journal::journal_mark_todo_done,
            commands::journal::journal_delete_todo,
            commands::journal::journal_add_todo,
            // P3.4.7c (6/15 鸿波): current_todos.md 每周日 reset (autostart 自动跑 + 员工手动触发)
            commands::journal::current_todos_weekly_reset,
            // BL-PROACTIVE-DECOUPLE (5/26): journal_tail + last_model 一次拿, 给 /api/proactive/* header 透传
            commands::proactive::proactive_context,
            // P3.5.45 (6/20 鸿波): 录屏 RPC 直调 tool-bridge sock, 砍 gateway HTTP path
            commands::recmode::recmode_rpc,
            // BL-WECHAT-CATFISH-BIND v1 + v2 (5/26): WeChat openid ↔ catfish 员工 email 绑定
            // v1 read-only 状态; v2 写命令给 Dashboard UI 一键审批/改绑/解绑/拒绝.
            commands::wechat_binding::wechat_binding_status,
            commands::wechat_binding::wechat_binding_pending_list,
            commands::wechat_binding::wechat_binding_approve,
            commands::wechat_binding::wechat_binding_set_email,
            commands::wechat_binding::wechat_binding_revoke,
            commands::wechat_binding::wechat_binding_reject,
            // BL-IDENTITY-INJECT-DECOUPLE (5/26): SOUL/USER/memories 6 字段, 给 /v1/chat/completions body 透传
            commands::identity_bundle::identity_bundle,
            // BL-BRIEFING-GOAL-INPUT (5/20): /goal UI 路径 — BriefingCard 输入框
            // 5/26 DEPRECATED: hermes 0.14 原生 /goal 替代. 3 个 command 改 stub 返 error
            // 防回归. 保留 invoke_handler 注册防遗漏 JS caller 编译失败.
            commands::session_goal::session_goal_read,
            commands::session_goal::session_goal_write,
            commands::session_goal::session_goal_clear,
            // P3.5.1 (6/15 鸿波): Dream Engine — 员工主动触发 long-term 蒸馏, 用 picker model
            commands::dream::dream_distill_run,
            commands::dream::dream_distill_status,
            // P3.5.2 (6/16 鸿波): chat picker 持久化 → plugin sync_turn 跟随 picker (绕过 hermes API 没透传 picker 限制)
            commands::picker_state::picker_state_save,
            commands::picker_state::picker_state_get,
            // P3.5.91 (6/23 鸿波): 早安 task → canonical taskUid client cache —
            // 治 LLM advisor refresh 给同 title 新 uid 导致 session 关联失联问题
            commands::task_uid_cache::task_uid_cache_get,
            commands::task_uid_cache::task_uid_cache_put,
            commands::task_uid_cache::task_uid_cache_dump,
            // P3.5.4 (6/16 鸿波): BGE-M3 advisor 注入相关性筛选 — 砍 prompt + 砍 LLM 输出, advisor 不再 truncated
            commands::advisor_relevance::advisor_rank_relevance,
            commands::advisor_relevance::advisor_relevance_cache_stats,
            // BL-COMPANION-EMAIL-DIGEST (5/18): 邮件简报 shell-out
            commands::email::email_digest_fetch,
            commands::email::email_accounts_fetch,
            // BL-COMPANION-EMAIL-TAB (5/18): 邮件 tab 用的扩展能力 (全列表 + 读全文 + 起草)
            commands::email::email_list_fetch,
            commands::email::email_read_message,
            commands::email::email_create_draft,
            // P3.5.204.c (7/9 鸿波): 客户端还没同步的邮件, 员工可点刷新触发 IMAP/POP 同步
            commands::email::email_check_new,
            commands::email::email_mail_dir_status,  // 8/8: 缺完全磁盘访问权限时提示 (见该 fn 注释)
            // BL-EMAIL-MARK-READ (5/18): 单独标已读/未读 (右键 / 批量场景)
            commands::email::email_mark_read,
            // BL-EMAIL-DELETE (5/18): 删邮件 (移到 Trash, 软删)
            commands::email::email_delete_message,
            // BL-EMAIL-COMPOSE-SEND (5/18): 把 Drafts 草稿真发出去 (人工 confirm 红线)
            commands::email::email_send_message,
            // P3.3.58 (6/12 鸿波): 批量查邮件钓鱼扫描结果
            commands::email::email_phishing_get,
            // P3.3.53 (6/13): 政治敏感扫描 — 给前端 detail pane 调
            commands::email::email_political_scan_now,
            commands::email::email_political_get,
            // P3.5.103 (6/24 鸿波 catch "附件不能点"): 导出附件到本地 tmp, 配合 open_file 系统打开
            commands::email::email_export_attachment,
            // P3.5.105 (6/25 鸿波 catch "定时任务跑没跑结果如何都看不到"): cron 监控 + 操作
            commands::cron::cron_jobs_list,
            commands::cron::cron_job_outputs,
            commands::cron::cron_job_output_read,
            commands::cron::cron_job_pause,
            commands::cron::cron_job_resume,
            commands::cron::cron_job_delete,
            // BL-COMPANION-EMAIL-TAB-STEP2 (5/18): 评级 badge 取数
            services::email_scheduler::email_urgency_map,
            // BL-COMPANION-PREFS-TOGGLES (5/20): 暴露 email config 给前端 AgentPrefsCard 展示
            services::email_config::email_config_get,
            // P3.5.28 (6/17 鸿波"picker 联动现在就应该做"): chat picker 选的 model 写文件,
            // background task (email_scheduler / phishing_scan) 跟着用员工选的 model.
            services::picker_config::set_picker_model,
            // P3.5.139 Phase 4 (6/29 鸿波"重启 Companion picker 应该记得这次选择"):
            // 启动时读 ~/.catfish/picker_model 注入 zustand store.model.
            services::picker_config::get_picker_model,
            // P3.5.139 (6/29 鸿波"都要去除硬编码"): 前端 caller (visionSwitch /
            // DetailPane / Chat fallback) 拉 /v1/roles 拿全 mapping, 5min cache.
            services::role_config::roles_get_all,
            // P3.3.65 (6/13): 钓鱼规则可配置 — 仪表盘显当前 effective 配置
            services::phishing_config::phishing_config_get,
            // P3.3.53 (6/13): 政治敏感规则可配置 — 仪表盘显当前 effective 配置
            services::political_config::political_config_get,
            // P3.4.1 (6/13): mcp OAuth token 本机存 (砍 secret-broker 中央存储)
            commands::mcp_oauth::mcp_oauth_token_save,
            commands::mcp_oauth::mcp_oauth_token_delete,
            // BL-EMAIL-URGENCY-BADGE (5/18): 前端主动 batch 评级 (历史邮件也能评)
            services::email_scheduler::email_classify_now,
            // P3.3.58 段 2B (6/12 鸿波): 前端 trigger 钓鱼扫描
            services::email_scheduler::email_phishing_scan_now,
            // BL-COMPANION-HERMES-API-CONFIG (5/19 Phase 2-2A): 暴露 hermes_api 配置给 React
            services::hermes_api_config::hermes_api_config_get,
            // BL-COMPANION-CHAT-SWITCH-TO-HERMES (5/19 Phase 2-2B): chat.ts 走 hermes 时拿 auth header
            services::hermes_api_config::hermes_api_auth_header,
            // BL-WECHAT-QR-HERMES-STANDALONE (7/18): hermes 独占 endpoint 强用 (无视 enabled)
            services::hermes_api_config::hermes_api_auth_header_forced,
            services::hermes_api_config::hermes_api_url_forced,
            // BL-E16 关系建立 (五一 sprint 5/3 晚): "鲶鱼对你的印象" 透明 + 清空
            commands::relation::relation_summary,
            commands::relation::relation_forget,
            commands::relation::journal_read_raw,
            // BL-ADVISOR-PROFILE (5/21 Phase 7 第 1 步): 员工职级 + 画像自动识别
            commands::profile::profile_get,
            commands::profile::profile_save,
            commands::profile::profile_mark_wrong,
            commands::profile::profile_hints_read,
            commands::profile::profile_needs_recompute,
            commands::profile::profile_next_recompute_at,
            // BL-ADVISOR-DRAFTS (5/21 Phase 7 第 2 步): ~/.catfish/outputs/<date>/ 草稿存储
            commands::drafts::draft_save,
            commands::drafts::draft_read,
            commands::drafts::draft_list_today,
            commands::drafts::draft_open_in_editor,
            // BL-X (5/26): chat timeout toast 自显本地 outputs (替代砍掉的 gateway recent_outputs.list_recent)
            commands::drafts::recent_outputs_list,
            // P3.3.62 (6/13): TodayDraftsCard 接 Mail.app Drafts 链路
            commands::drafts::draft_parse_md,
            commands::drafts::draft_delete_md,
            // BL-ADVISOR-DECISIONS (5/21 Phase 7 第 2 步): ~/.catfish/decisions.jsonl 决策留痕
            commands::decisions::decision_record,
            // P3.3.7 Phase 2 (6/10): task-scoped chat 持久化
            commands::task_chat::task_chat_get,
            commands::task_chat::task_chat_append,
            commands::task_chat::task_chat_clear,
            // P3.3.12 (6/10): jsonl 大小 (给 advisor task chat summary cache 用)
            commands::task_chat::task_chat_size,
            // P3.3.8 (6/10): 早安天气
            commands::weather::weather_get,
            commands::weather::weather_config_get,
            commands::weather::weather_config_set,
            commands::decisions::decision_list_recent,
            commands::decisions::decision_search,
            // P3.3.52 (6/12 鸿波): 员工"标记完成 / 推迟 / 不做" 时同步留痕到 decisions.jsonl
            commands::decisions::decision_record_status_change,
            // BL-ADVISOR-CACHE + CONFIG (5/22 Phase 7 cold start v3): 缓存 + yaml 时段配置
            commands::advisor_cache::advisor_cache_get,
            commands::advisor_cache::advisor_cache_save,
            commands::advisor_cache::advisor_cache_clear,
            commands::advisor_config::advisor_config_get,
            // BL-ADVISOR-TASK-STATE (5/22 鸿波): 任务状态 done/snoozed/ignored
            commands::advisor_task_state::advisor_task_state_get,
            commands::advisor_task_state::advisor_task_state_set,
            commands::advisor_task_state::advisor_task_state_clear,
            commands::advisor_task_state::advisor_task_state_prune_old,
            // BL-STYLE-FP-USE-INDEX (7/27): 删了 style_fingerprint_scan_dirs_*.
            // 文书风格改查 local_search 索引, 目录范围走上面的 local_search_scope_*.
            // BL-MM4 v1 (5/5 晚): "鲶鱼记的硬事实" 版本卡 (跟 BL-MM2 配套)
            commands::memory_history::memory_history_summary,
            commands::memory_history::memory_history_clear_key,
            commands::memory_history::memory_history_forget_all,
            // BL-DASHBOARD-HERMES-MEMORY-CARD (5/16): 读 hermes 0.13 真活 memory 文件
            commands::hermes_memory::hermes_memory_read,
            // P3.3.49 (6/12 鸿波 "删除无效"): Rust 直写, 绕过 memory_tool silent fail
            commands::hermes_memory::hermes_memory_remove,
            // BL-MM6 (5/5 晚): 显式 feedback 👍/👎/改 + ~/.catfish/feedback.jsonl
            commands::feedback::feedback_record,
            commands::feedback::feedback_summary,
            commands::feedback::feedback_clear,
            // BL-E27 spike (5/5 凌晨): 桌宠副窗 toggle + 点击唤主窗 + 4 屏角切换
            commands::pet::pet_show,
            commands::pet::pet_hide,
            commands::pet::pet_is_visible,
            commands::pet::pet_clicked,
            commands::pet::pet_move_corner,
            commands::pet::pet_set_bubble_visible,
            commands::pet::pet_start_drag,
            commands::pet::pet_emit_bubble,
            commands::pet::pet_emit_status,
            commands::pet::pet_pop_bubble,
            commands::pet::pet_pop_status,
            commands::pet::pet_log,
            // BL-E27.4 (5/8): 桌宠状态颜色 indicator + 单击重置
            commands::pet::pet_status_summary,
            commands::pet::pet_status_clear,
            // BL-MM11 (5/8): skill 级 👍/👎/改 评分
            commands::skill_feedback::skill_feedback_record,
            commands::skill_feedback::skill_feedback_summary,
            commands::skill_feedback::skill_feedback_clear,
            // BL-MM14 / MM15 (5/8): skill revision proposals + 改进有效性跟踪
            commands::skill_revision::skill_revision_summary,
            commands::skill_revision::skill_revision_accept,
            commands::skill_revision::skill_revision_reject,
            commands::skill_revision::skill_revision_check_effectiveness,
            // BL-CATFISH-WIKI-MODE P1.2.2 (6/4): chat 真 💾 button → wiki/queries/ 写盘
            commands::wiki_save::wiki_save_chat_message,
            // BL-CATFISH-WIKI-MODE P3.3.2 (6/4): wiki read API
            commands::wiki_read::wiki_list_files,
            commands::wiki_read::wiki_read_file,
            // P37 (6/5): wiki 全文搜索 (BM25 score)
            commands::wiki_read::wiki_search_text,
            // P3.3.18 Phase 4 (6/10): 已装部门 wiki 扫描 (~/.catfish/wiki-shared/)
            commands::wiki_read::list_installed_wiki_shared,
            // P38 (6/5): wiki 语义搜索 (本机 BGE-M3 ONNX)
            commands::wiki_embed::wiki_search_semantic,
            // BL-CATFISH-WIKI-MODE P3.3.7 (6/4): wiki write API
            commands::wiki_write::wiki_create_entity_or_concept,
            commands::wiki_write::wiki_update_file,
            // P3.3.4 (6/9): wiki 软删 (mv 到 .trash)
            commands::wiki_write::wiki_delete_file,
            // P3.3.18 Phase 4 P2 (6/10): 卸载本机部门 wiki 副本 (软删 → wiki-shared/.trash/)
            commands::wiki_write::wiki_uninstall_shared,
            // P3.3.18 Phase 4 P2 (6/10): 敏感词文件 onboarding (catfish_wiki_publish 扫用)
            commands::wiki_write::wiki_sensitive_terms_ensure,
            // P3.3.19 C Phase 4 (6/11): task_chat jsonl → state.db 一次性 migration
            commands::task_chat_migration::task_chat_migrate_to_state_db,
            // P16 (6/5): 对话上传文件 auto ingest → wiki/raw/sources/
            commands::wiki_write::wiki_ingest_source,
            // P28 (6/5): Companion Dashboard 改 gateway URL/token
            commands::server_config::read_server_config,
            commands::server_config::write_server_config,
            // 7/15 BL-CATFISH-MAC-OFFLINE-INSTALL: 员工 Dashboard 手工重装 hermes (若首启 auto install 挂)
            commands::hermes_install::reinstall_hermes_agent,
            commands::hermes_install::hermes_bootstrap_status,
            commands::teaching_credentials::teaching_credential_save,
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
