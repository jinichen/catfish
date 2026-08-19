//! 把 src-tauri 里的纯逻辑模块原地拉进来跑测试。
//!
//! 测试本身写在被包含的那个文件里 (`#[cfg(test)] mod tests`), 这里只提供一个
//! 编得动的宿主: serde / serde_json / url / log, 没有 tauri, 没有 keyring。
//!
//! 加新模块就在下面多一行 #[path]。

// allow 只加在这个宿主上, 不加到生产文件里 —— 那边的 dead_code 检查要留着
// (8/17 `service_name is never used` 就是靠它抓到的)。
//
// ⚠ 模块层级要跟真实的一样。socket_proto.rs 里写的是 `super::index::SERVICE_PREFIX`
//   —— 那两个文件在真 crate 里是 `teaching_credentials` 下的兄弟, 这里也得摆成
//   兄弟, 否则 `super::` 指到别处去。平铺成两个顶层 mod 是编不过的。
//   `#[path = "."]` 是必需的: 内联 mod 默认把自己的名字当成一层目录, 于是子模块
//   的相对路径从 `src/teaching_credentials/` 起算 —— 那个目录不存在, cargo 直接
//   报 "couldn't read"。钉回 `src/` 就对了。
#[allow(dead_code)]
#[path = "."]
mod teaching_credentials {
    #[path = "../../src-tauri/src/commands/teaching_credentials/index.rs"]
    pub mod index;

    #[path = "../../src-tauri/src/commands/teaching_credentials/socket_proto.rs"]
    pub mod socket_proto;
}
