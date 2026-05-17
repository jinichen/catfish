//! macOS 应用菜单 (顶部 menu bar) 自定义.
//!
//! 5/18 BL-COMPANION-ABOUT-HIJACK: 默认 macOS app menu 的 "About" 走 PredefinedMenuItem::about
//! 弹原生 panel (只显 productName + version + 几行 Info.plist 字段). 我们想让员工看到
//! 跟仪表盘顶部"鲶鱼 v0.14.0" chip 同一个完整 React 模态 (介绍 + 版本 + 客户).
//!
//! 做法:
//!   1. 自己 build 一份 app menu, "关于鲶鱼" item 用 MenuItemBuilder (不是 Predefined::about),
//!      id 设为 "about", 点击只 emit 事件不走原生 panel.
//!   2. 其余菜单项 (Services / Hide / Hide Others / Show All / Quit) 还用 Predefined,
//!      避开重造系统能力.
//!   3. 加 Edit / Window submenu, 让员工还能 Cut/Copy/Paste + 最小化 — 默认 menu 会被
//!      我们 set_menu 整个替换掉, 不补就丢功能.
//!   4. on_menu_event 接 "about" id, emit "show-about" 事件给前端.

use tauri::{
    menu::{MenuBuilder, MenuItemBuilder, SubmenuBuilder},
    App, AppHandle, Emitter, Manager,
};

/// 装应用菜单. 在 lib.rs setup() 里调一次.
pub fn install(app: &App) -> tauri::Result<()> {
    // ── 应用菜单 (左起第二项, 紧邻 Apple 菜单) ─────────────────────
    let about_item = MenuItemBuilder::new("关于鲶鱼")
        .id("about")
        .build(app)?;

    let app_submenu = SubmenuBuilder::new(app, "鲶鱼 Companion")
        .item(&about_item)
        .separator()
        .services()
        .separator()
        .hide()
        .hide_others()
        .show_all()
        .separator()
        .quit()
        .build()?;

    // ── 编辑菜单 (Cut/Copy/Paste/Undo/Redo/Select All) ────────────
    let edit_submenu = SubmenuBuilder::new(app, "编辑")
        .undo()
        .redo()
        .separator()
        .cut()
        .copy()
        .paste()
        .select_all()
        .build()?;

    // ── 窗口菜单 (最小化 / Zoom / 全屏切换) ────────────────────────
    let window_submenu = SubmenuBuilder::new(app, "窗口")
        .minimize()
        .maximize()
        .separator()
        .close_window()
        .build()?;

    let menu = MenuBuilder::new(app)
        .item(&app_submenu)
        .item(&edit_submenu)
        .item(&window_submenu)
        .build()?;

    app.set_menu(menu)?;

    // ── 菜单事件 ───────────────────────────────────────────────
    app.on_menu_event(handle_menu_event);

    Ok(())
}

fn handle_menu_event(app: &AppHandle, event: tauri::menu::MenuEvent) {
    if event.id() == "about" {
        // 主窗口可能最小化 / 隐藏到 dock — 先 show+focus, 不然模态弹了员工看不到.
        if let Some(win) = app.get_webview_window("main") {
            let _ = win.show();
            let _ = win.unminimize();
            let _ = win.set_focus();
        }
        // 前端 App.tsx 监听 "show-about" 事件 → 渲染 AboutModal.
        // payload 空; 模态自己 getVersion() 取版本号.
        if let Err(e) = app.emit("show-about", ()) {
            log::warn!("emit show-about 失败: {e}");
        }
    }
}
