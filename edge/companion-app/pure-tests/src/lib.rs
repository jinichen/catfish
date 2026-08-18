//! 把 src-tauri 里的纯逻辑模块原地拉进来跑测试。
//!
//! 测试本身写在被包含的那个文件里 (`#[cfg(test)] mod tests`), 这里只提供一个
//! 编得动的宿主: serde / serde_json / url / log, 没有 tauri, 没有 keyring。
//!
//! 加新模块就在下面多一行 #[path]。

// allow 只加在这个宿主上, 不加到生产文件里 —— 那边的 dead_code 检查要留着
// (8/17 `service_name is never used` 就是靠它抓到的)。
#[allow(dead_code)]
#[path = "../../src-tauri/src/commands/teaching_credentials/index.rs"]
mod teaching_credentials_index;
