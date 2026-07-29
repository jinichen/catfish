//! Companion 后端通用小工具. 挑那些跨 command/service 都要用的.

pub mod http_client;
pub mod paths;

// P3.5.80 (7/28): 全进程唯一的 env 锁, 只在 test build 里编译.
// 5 个模块原来各建一把 ENV_LOCK, 跨模块并发改 HOME 对撞 —— 详见该文件说明.
#[cfg(test)]
pub mod test_env;
