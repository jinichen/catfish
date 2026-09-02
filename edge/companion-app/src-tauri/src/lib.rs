//! Catfish Companion App —— Tauri 后端入口。
//!
//! 模块划分：
//!   - `commands/`  —— 暴露给前端的 #[tauri::command] 函数（按服务/职责拆分）
//!   - `services/`  —— 内部进程管理（前端不可直接访问）
//!   - `tray/`      —— menubar 托盘菜单

#[cfg(target_os = "macos")]
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
        //           Windows = %LOCALAPPDATA%/com.catfish.companion/logs/Catfish Companion.log
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

            // 9/2: Hermes 已有完整的重复失败/无进展检测器，但硬停止默认关闭。
            // Companion 场景里一次浏览器任务曾累计 65 个工具结果才撞中央 409。
            // 这里只开启上游开关，阈值继承当前 Hermes 默认，避免维护第二套数字。
            match services::tool_loop_config::ensure() {
                Ok(true) => log::info!(
                    "[tool-loop] 已启用 Hermes 每轮工具循环硬停止；下次 Hermes 重启生效"
                ),
                Ok(false) => log::debug!(
                    "[tool-loop] 已有 tool_loop_guardrails 配置或 Hermes 尚未初始化，不动"
                ),
                Err(e) => log::warn!("[tool-loop] 配置失败 (不阻塞启动): {e}"),
            }

            // 8/19: 教学凭据的取值通道。tool-bridge 教学时要填密码 → 走
            // ~/.catfish/companion-secrets.sock 问我们, 我们读钥匙串给它。
            //
            // 为什么不让 tool-bridge 自己读: macOS 钥匙串按二进制授权, 条目只信任
            // Companion; tool-bridge exec /usr/bin/security 会弹一个后台进程看不见
            // 的确认框然后超时。把 security 加进信任名单 ≈ 对所有程序开放, 所以改成
            // 谁有权限谁去读。详见 commands/teaching_credentials/socket.rs 文件头。
            //
            // 只有 macOS 需要 —— Windows 凭据管理器同用户下本来就都读得到。
            // 起不来不阻塞启动 (spawn 内部自己 log), 后果只是教学时取不到密码,
            // 而 tool-bridge 那边会明说"连不上 Companion"。
            #[cfg(target_os = "macos")]
            commands::teaching_credentials::socket::spawn();

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
            // 8/9: plugin 文件**内容真变了**才 launchctl kickstart 重启 hermes。
            // hermes 只在启动时加载 plugin, 老行为 (从不重启) 导致装了新包之后
            // 新端点 404 且完全静默。内容没变不动 —— 不打断在跑的 turn。
            //
            // Escape hatch: CATFISH_HERMES_PLUGIN_NO_BOOTSTRAP=1 跳全部 (调试用).
            commands::hermes_plugin::bootstrap_hermes_plugin();

            // 首次启动后台准备 Hermes。MSI 只负责落盘，Windows 也走同一条 GUI
            // bootstrap，避免安装事务中运行 PowerShell 导致黑窗和长时间卡住。
            // Escape: CATFISH_HERMES_INSTALL_NO_BOOTSTRAP=1 (dev 已装本地 hermes).
            #[cfg(any(
                all(any(target_os = "macos", target_os = "linux"), not(debug_assertions)),
                all(target_os = "windows", not(debug_assertions))
            ))]
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
            #[cfg(any(
                all(any(target_os = "macos", target_os = "linux"), debug_assertions),
                all(target_os = "windows", debug_assertions)
            ))]
            log::info!("调试构建跳过 packaged Hermes bootstrap，使用本机已安装的 Hermes");

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
            #[cfg(any(
                target_os = "macos",
                target_os = "linux",
                target_os = "windows"
            ))]
            if !cfg!(debug_assertions)
                && std::env::var("CATFISH_HERMES_INSTALL_NO_BOOTSTRAP").is_err()
            {
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

            #[cfg(not(any(
                target_os = "macos",
                target_os = "linux",
                target_os = "windows"
            )))]
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
        .invoke_handler(commands::invoke_handler::handler())
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
