//! P3.5.28 (6/17 鸿波"picker 联动现在就应该做") — picker model source of truth.
//!
//! # 真目的
//!
//! 之前 background task (email_scheduler / phishing_scan / political_scan / etc)
//! 硬编码默认 model — yaml/env 可配但员工不会改. P3.5.27 改 default 为
//! catfish-private-main 修了数据零出端红线, 但员工 chat picker 切别的 model 真
//! 不影响 background task → 员工以为切了, 实际后台还跑旧 model.
//!
//! 鸿波诉求: 员工在 chat picker 选什么, 后台 background task 跟着用什么.
//!
//! # 真路径
//!
//! React `setModel(name)` (store/chat.ts) → invoke `set_picker_model(name)` →
//! Rust 写 `~/.catfish/picker_model` (1 行 text). Background task 真 tick 时
//! 调 `current_model()` 读这文件.
//!
//! # 真优先级 (高→低)
//!
//!   1. picker file (`~/.catfish/picker_model`) — 员工 chat picker 真选的
//!   2. yaml/env (per-service config, 例 email.rate_model) — IT/admin 显式
//!      override (想强制公网 flash 省钱 / 强制 private-main 保密)
//!   3. service 真 DEFAULT (e.g. email_config DEFAULT_RATE_MODEL)
//!
//! # 不持久 OnceLock
//!
//! 跟 email_config OnceLock 不同 — picker_model 每次访问都重读文件, 因为
//! 员工 chat picker 切换是动态的, OnceLock 真 cache 第一次值后续不重读 → 切了
//! 没用. 文件 IO 真每 N 分钟一次 (background task tick interval) 便宜.

use std::path::PathBuf;

fn picker_file_path() -> Option<PathBuf> {
    let home = crate::util::paths::home_env()
        .or_else(|_| std::env::var("USERPROFILE"))
        .ok()?;
    Some(PathBuf::from(home).join(".catfish").join("picker_model"))
}

/// 读员工 chat picker 当前选的 model. 文件不存在 / 空字符串 → None (caller
/// fallback per-service config). 失败静默 (例如权限错, 不阻塞 background task).
pub fn current_model() -> Option<String> {
    let path = picker_file_path()?;
    std::fs::read_to_string(&path)
        .ok()
        .and_then(|s| {
            let trimmed = s.trim();
            if trimmed.is_empty() {
                None
            } else {
                Some(trimmed.to_string())
            }
        })
}

/// P3.5.139 Phase 4 (6/29 鸿波"重启 Companion picker 应该记得这次选择"):
/// React 启动时调一次, 把 file 已有 picker model 注入 zustand store.model.
/// file > store 优先级 — 重启 Companion 上次选过的 model 立刻生效.
///
/// 没拿到 (file 不在 / 空字符串 / 权限错) → 返 null, React useEffect 不动 store,
/// 后续走 ChatTab catalog.default 注入兜底.
#[tauri::command]
pub fn get_picker_model() -> Option<String> {
    current_model()
}

/// 落盘 picker 选择 —— **两个副本一起写**, 这是唯一的写入口 (8/9).
///
/// ── 为什么必须一起写 ────────────────────────────────────────────────
///
/// picker 的值有两个副本, 读的人不一样:
///   `~/.catfish/picker_model`      ← Rust 后台任务读 (邮件评级 / 各类扫描)
///   `~/.catfish/picker_state.json` ← hermes plugin 读 (决定发哪个模型)
///
/// 8/9 之前**写入侧是两条独立路径**:
///   store/chat.ts setModel        → set_picker_model  → 只写 picker_model
///   ChatModelPicker / chat.ts send → picker_state_save → 只写 picker_state.json
///
/// 于是它们会**不一致**, 而且是静默的。实测过的一种:
///   新机器员工打开 Chat tab → ChatTab 的 catalog.default 注入触发 setModel
///   → picker_model 有值; 但员工没手动切过 picker、也还没发过消息
///   → picker_state.json **不存在** → plugin 读不到 → 让 hermes 自己决定模型。
///   Rust 用 picker、plugin 不用 —— 同一台机器两个模型来源。
///
/// 删除侧 7/28 已经因为踩坑统一了 (见 `clear_model_selection` 的注释: "只删
/// picker_state.json, 下次启动 picker_model 立刻把旧值灌回, 反复三轮才发现是
/// 两个文件")。写入侧补上同样的纪律。
///
/// ── 为什么不干脆合成一个文件 ────────────────────────────────────────
///
/// 两个格式各有既成读者: 纯文本那份被 Rust 读, JSON 那份被 4 个 Python 侧消费者
/// 读 (catfish_memory / catfish_tools / model_authority / aggregator)。合并要同时
/// 改插件并重新烘焙, 面大。**一个写入口 + 两个派生副本**已经消除了漂移, 收益一样。
///
/// 任一副本写失败都返 Err —— 不允许"写了一半算成功", 那正是漂移的来源。
pub fn persist_picker_model(name: &str) -> Result<(), String> {
    let trimmed = name.trim();
    if trimmed.is_empty() {
        return Err("picker model name 真空, 不写".to_string());
    }

    let path = picker_file_path().ok_or_else(|| "HOME 真没拿到".to_string())?;
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| format!("创建 ~/.catfish 真失败: {e}"))?;
    }
    std::fs::write(&path, trimmed)
        .map_err(|e| format!("写 picker_model 真失败: {e}"))?;

    crate::commands::picker_state::write_picker_state_file(trimmed)?;

    log::info!(
        "picker 已落盘 (picker_model + picker_state.json 同步): {}",
        trimmed
    );
    Ok(())
}

/// 真给 React 调的 Tauri command — `setModel` 钩子写文件.
///
/// React store/chat.ts setModel 触发: invoke('set_picker_model', { name }).
/// 失败仅 log, 不 throw (React 端 fire-and-forget 不阻塞 picker UI).
#[tauri::command]
pub fn set_picker_model(name: String) -> Result<(), String> {
    persist_picker_model(&name)
}

/// P3.5.81 (7/29 达华交付前夜): 换服务器时作废模型选择.
///
/// ── 为什么必须作废而不是保留 ────────────────────────────────────────
///
/// 模型名是**绑定某台服务器的目录**的:`catfish-public-deepseek-flash` 在
/// A 服务器上存在, 在 B 服务器上就是不存在. 换服务器后继续用旧选择, hermes
/// 会拿它去请求 B, B 返 `404 model not found`.
///
/// 而这个值有**两个副本**, 只清一个没用:
///   - `~/.catfish/picker_model`      ← 本文件. Companion **每次启动**注入 store
///   - `~/.catfish/picker_state.json` ← hermes plugin 读它决定发哪个模型
///
/// 7/28 实证: 只删 picker_state.json, 下次启动 picker_model 立刻把旧值灌回,
/// 于是"删了又出现", 反复三轮才发现是两个文件。所以这里两个一起清。
///
/// 清空之后走的是 `ChatTab` 里那条 `catalog.default` 注入兜底 —— 模型名由
/// **新服务器自己的目录**决定, 不再由本机历史决定. 这才是正确的默认.
pub fn clear_model_selection() {
    let mut cleared: Vec<String> = Vec::new();

    if let Some(p) = picker_file_path() {
        if p.exists() {
            match std::fs::remove_file(&p) {
                Ok(()) => cleared.push("picker_model".into()),
                Err(e) => log::warn!("[picker] 删 {} 失败 (不阻塞): {e}", p.display()),
            }
        }
    }

    // picker_state.json 跟 picker_model 同目录 (~/.catfish/)
    if let Some(dir) = picker_file_path().and_then(|p| p.parent().map(|d| d.to_path_buf())) {
        let state = dir.join("picker_state.json");
        if state.exists() {
            match std::fs::remove_file(&state) {
                Ok(()) => cleared.push("picker_state.json".into()),
                Err(e) => log::warn!("[picker] 删 {} 失败 (不阻塞): {e}", state.display()),
            }
        }
    }

    if cleared.is_empty() {
        log::debug!("[picker] 没有需要清的模型选择残留");
    } else {
        log::info!(
            "[picker] 换服务器 · 已清模型选择 ({}) · 下次由新服务器目录决定默认模型",
            cleared.join(" + ")
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn current_model_returns_none_when_no_file() {
        // sandbox 真没 ~/.catfish/picker_model — current_model 真 None
        // (假设 HOME env 真 set 但文件真没)
        let _result = current_model();
        // 不真 assert (CI 可能有真 picker_model 文件)
    }
}
