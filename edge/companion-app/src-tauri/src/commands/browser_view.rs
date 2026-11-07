//! 对话右侧「浏览器」页 (11/07 鸿波「浏览器两步一起做」): 看小鲶正在操作的网页, 需要时自己接手点 / 输入。
//!
//! 画面和输入由前端直接走 Chrome 调试协议 (src/lib/cdp.ts) —— 鲶鱼 Chrome 启动时就带了
//! `--remote-allow-origins=*` (services/chrome_runtime.rs), Companion 页面能直接连 ws://127.0.0.1:<端口>。
//! 这里只告诉前端浏览器级的 WebSocket 地址; 前端拿不到 /json/version (Chrome 不给跨源头), 所以从 Rust 取。
//! 只接受本机地址 (cdp_url 里校验过), 不会连到别的机器上去。

use crate::services::endpoints;

/// 鲶鱼 Chrome 的浏览器级调试地址; 没开着就报「没开」, 不在这里自动拉起 (拉起走现有的 chrome_launch)。
#[tauri::command]
pub async fn browser_view_endpoint() -> Result<String, String> {
    let ep = endpoints::endpoints();
    crate::services::chrome_runtime::cdp_url(&ep.chrome_base())
        .await
        .map_err(|_| "小鲶的浏览器还没打开".to_string())
}
