//! BL-WIN-HOME (7/17 达华): 统一"家目录"读取, 修 Windows 员工 $HOME 未设的锅.
//!
//! 背景:
//!   老代码全项目 60+ 处直接调 `std::env::var("HOME")`, mac/Linux 都有 HOME
//!   这个 env, 没问题. 但 Windows 员工装完 Companion 打开 · SSO 回来存
//!   token 时挂 "登录失败: $HOME 未设置" — 因为 Windows **没有** HOME env,
//!   Windows 用 `%USERPROFILE%` (或 `%HOMEDRIVE%%HOMEPATH%` 拼接).
//!
//!   全项目里有 6 处 service (email_config/agent_prefs/curator_config/
//!   hermes_api_config/curator_state/server_config/endpoints)
//!   手写了 `.or_else(|_| std::env::var("USERPROFILE"))` fallback, 但另外
//!   60+ 处 command 全部漏了. 达华 Windows 员工任何一处触发都会挂或跳过.
//!
//! 设计原则:
//!   - **返回类型与 `std::env::var` 完全一致** — 返 `Result<String, VarError>`,
//!     这样调用方 `.ok()?` / `.map_err()` / `.context()` / `.unwrap_or_default()`
//!     / `if let Ok(...)` / `match Ok/Err` 老写法**零改动**编译通过.
//!   - 全项目 sed 替换 `std::env::var("HOME")` → `crate::util::paths::home_env()`,
//!     一次改, 60+ 处一次生效.
//!   - 不引入 `dirs` / `home` / `directories` 之类 crate, 保持最少依赖.
//!
//! 军规:
//!   - #58 不带 src 源码进 delivery tar (本 helper 是 src, 只在员工装的 app
//!     里编译进 binary, 达华 IT 拿到的 msi/dmg 里没源, 合规)
//!   - 修完必须 cargo check 全项目 (release + x86_64-apple-darwin target 各一遍)
//!     再打 msi/dmg. Windows 员工端口验证: SSO 走完能看到 chat 页面.

use std::env::VarError;

/// 返回员工家目录 (`String`), 优先 `HOME` (mac/Linux) → fallback `USERPROFILE` (Windows).
///
/// 返回类型与 `std::env::var("HOME")` **完全一致** — `Result<String, VarError>` —
/// 所以老代码 `std::env::var("HOME").map_err(...)?` 直接 sed 替换成
/// `crate::util::paths::home_env().map_err(...)?` 即可, 后半段一律不动.
///
/// # Windows 说明
/// Windows 上 `HOME` 通常不存在, `USERPROFILE` 一般是 `C:\Users\<name>`.
/// 若两个 env 都缺 (罕见, 只见于 service 账户或裸容器), 返最后一次 `Err(VarError::NotPresent)`.
///
/// # 例子
/// P3.5.80 (7/28): ignore → text. ignore 的 doctest 会注册成被忽略的测试,
/// `cargo test -- --ignored` 会真的去编译它 (这段示例故意含 `...` 省略号,
/// 编译必炸). text 完全不当代码处理, 任何 flag 下都不会被碰.
/// ```text
/// // 老代码:
/// let home = std::env::var("HOME").map_err(|e| format!("HOME 未设: {e}"))?;
/// // 新代码 (逐字 sed 就够):
/// let home = crate::util::paths::home_env().map_err(|e| format!("HOME 未设: {e}"))?;
///
/// // 老代码:
/// if let Ok(home) = std::env::var("HOME") { ... }
/// // 新代码 (Ok 分支照旧, 因为 Result 类型没换):
/// if let Ok(home) = crate::util::paths::home_env() { ... }
/// ```
pub fn home_env() -> Result<String, VarError> {
    std::env::var("HOME").or_else(|_| std::env::var("USERPROFILE"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn home_env_returns_something_on_normal_dev_env() {
        // 开发机上 HOME 一定有, mac/Linux CI 也有.
        // Windows CI (CircleCI Windows Server 2022 executor) 有 USERPROFILE.
        // 只 assert 拿到非空 string, 不 assert 具体路径.
        let h = home_env().expect("dev env 应该有 HOME 或 USERPROFILE");
        assert!(!h.is_empty(), "home path 不应为空");
    }
}
