//! 装的共享内容 (技能 / 专家包 / 部门知识页) 后台定时检查 —— 有没有新版、被撤回了没 (11/14)。
//!
//! # 为什么要有它
//!
//! 原来只有员工自己打开「技能市场」「同事分享」那几张卡片才看得到更新; 作者 / 主管撤回的技能、
//! 专家包, 装过的人一点提示都没有, 还继续在用。查和本机标记都在 tool-bridge (shared_check.py),
//! 这里只管两件 tool-bridge 做不了的:
//!
//! - 技能停用 / 恢复要改 hermes 的 config.yaml (skills.disabled), 走 hermes_skills_set_enabled;
//! - 专家停用 / 恢复后要重新 apply 一次 (改 hermes 的 profile 注册), 走 expert_local_apply。
//!
//! 启动后 5 分钟查一次, 之后每 6 小时一次; 早安简报那一条也能手动「现在查」(shared_check_run)。
//! 中央不推 (公理 3), 都是员工这边拉。
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

use serde_json::{json, Value};
use tokio::sync::Mutex;
use tokio::time::{self, Instant, MissedTickBehavior};

use crate::services::hermes_skills_default::hermes_skills_set_enabled;
use crate::services::tool_bridge_rpc;

const ROUND_EVERY: Duration = Duration::from_secs(6 * 60 * 60);
/// 启动后先等 —— tool-bridge / hermes 都在起, 登录令牌也可能还在刷新。
const FIRST_ROUND_AFTER: Duration = Duration::from_secs(5 * 60);
/// 要问中央两次 (技能 + 专家包一次, 知识页列表一次), 列表里没有的知识页还要单独问; 给足余量。
const RPC_TIMEOUT: Duration = Duration::from_secs(120);

static STARTED: AtomicBool = AtomicBool::new(false);
/// 定时那轮和员工手动点的那轮不要叠在一起跑 (都会改 expert.yaml / hermes 配置)。
static RUNNING: Mutex<()> = Mutex::const_new(());

pub fn schedule_shared_check() {
    if STARTED.swap(true, Ordering::SeqCst) {
        return;
    }
    tauri::async_runtime::spawn(async move {
        let mut interval = time::interval_at(Instant::now() + FIRST_ROUND_AFTER, ROUND_EVERY);
        interval.set_missed_tick_behavior(MissedTickBehavior::Skip);
        loop {
            interval.tick().await;
            match run_round().await {
                Ok(v) => log::info!(
                    "shared_check: 一轮完成, 有更新 {} / 撤回 {} / 看不到了 {}",
                    len(&v, "updates"),
                    len(&v, "withdrawn"),
                    len(&v, "hidden")
                ),
                Err(e) => log::warn!("shared_check: 这一轮没跑成, 下一轮再试: {e}"),
            }
        }
    });
}

fn len(v: &Value, key: &str) -> usize {
    v.get(key).and_then(Value::as_array).map_or(0, Vec::len)
}

fn names(v: &Value, key: &str) -> Vec<String> {
    v.get(key)
        .and_then(Value::as_array)
        .map(|a| a.iter().filter_map(|x| x.as_str().map(str::to_string)).collect())
        .unwrap_or_default()
}

async fn run_round() -> Result<Value, String> {
    let _guard = RUNNING.lock().await;
    let mut v = tool_bridge_rpc::call_with_timeout("pim/shared_check", json!({}), RPC_TIMEOUT).await?;
    let mut applied_errors: Vec<String> = Vec::new();

    for (key, enabled) in [("disable_skill_names", false), ("enable_skill_names", true)] {
        let list = names(&v, key);
        if list.is_empty() {
            continue;
        }
        let what = if enabled { "恢复" } else { "停用" };
        match tokio::task::spawn_blocking(move || hermes_skills_set_enabled(list, enabled)).await {
            Ok(Ok(n)) => log::info!("shared_check: {what}了 {n} 个技能"),
            Ok(Err(e)) => applied_errors.push(format!("{what}技能: {e}")),
            Err(e) => applied_errors.push(format!("{what}技能: {e}")),
        }
    }
    for id in names(&v, "apply_experts") {
        if let Err(e) = crate::commands::experts_local::expert_local_apply(id.clone()).await {
            applied_errors.push(format!("专家 {id}: {e}"));
        }
    }
    if !applied_errors.is_empty() {
        log::warn!("shared_check: 本机停用 / 恢复有没做成的: {}", applied_errors.join("; "));
        if let Some(obj) = v.as_object_mut() {
            obj.insert("apply_errors".into(), json!(applied_errors));
        }
    }
    Ok(v)
}

/// 早安简报那一条的「现在查一下」, 以及员工在卡片上更新完之后刷新用。
#[tauri::command]
pub async fn shared_check_run() -> Result<Value, String> {
    run_round().await
}

/// 上一次查的结果 (tool-bridge 落在 ~/.catfish/shared_check.json), 不联网。
#[tauri::command]
pub async fn shared_check_last() -> Result<Value, String> {
    tool_bridge_rpc::call_with_timeout("pim/shared_check_last", json!({}), Duration::from_secs(10)).await
}
