//! 定时蒸馏 —— 每 24h 把 employee_journal.md 重蒸成 distilled_facts.md。
//!
//! # 为什么在 Companion 这边 (8/4 鸿波「为什么超 24 小时蒸馏没自动启动」)
//!
//! 卡片上一直写着"平时由 hermes plugin 自动每 24h 跑"。查下来那句话是空的：
//!
//!   1. catfish-memory 的 Python 侧确实有蒸馏逻辑, 但只在会话结束路径上被调
//!   2. 更根本的是 —— **它压根没被 hermes 加载**。实测
//!      `~/.hermes/config.yaml` 的 `plugins.enabled` 里只有 catfish-xcatfish-user,
//!      没有 catfish-memory; Companion 全部 Rust 代码里也没有一处把它当 plugin
//!      注册, 每一处都是把它当**源码目录**用来 spawn dream_cli.py。
//!      旁证: ~/.catfish/.catfish_memory_buffer.jsonl 最后修改停在 7/18。
//!
//! 所以 sync_turn / on_session_end 那些钩子一次都没被调过, "会话结束时检查 24h"
//! 这条路也不存在。这个模块唯一会被执行到的形态是 **Companion spawn 的
//! dream_cli.py 子进程** —— 也就是员工点「现在重蒸」那条路。
//!
//! 定时器因此必须在这边。放这里还顺带解决一件事: 不受 tool-bridge 反复重启影响。
//!
//! # 为什么是"每 15 分钟敲门"而不是"睡 24 小时"
//!
//! 睡 24h 的话, Companion 每次重启都清零 —— 那正是老实现"挂在会话结束上"同一类
//! 错误的另一种写法: 把状态放在活不了那么久的东西里。
//!
//! 真正的 24h 判定在 `~/.catfish/memory_distill_state.json`, 由 dream_cli
//! `--no-force` 读。状态在盘上, 重启多少次都不丢。95/96 次敲门就是子进程读个
//! 小 JSON 立刻返回 cooldown。
//!
//! # 模型
//!
//! 用 picker 当前选的模型 —— 鸿波定的原则, 不另设 distill_model。取不到就不跑,
//! 而不是退到某个默认模型偷偷用别的。

use std::time::Duration;
use tauri::AppHandle;
use tokio::time;

use crate::commands::dream;
use crate::services::{catfish_paths, picker_config};

/// 敲门间隔。真正的 24h 判定在 memory_distill_state.json, 不在这里。
const TICK_SECS: u64 = 15 * 60;
/// 启动后先等一会 —— 冷启动阶段在装 hermes / 起 tool-bridge, 别再压一个 LLM 长任务。
const STARTUP_DELAY_SECS: u64 = 5 * 60;

pub fn schedule_distill(app: AppHandle) {
    tauri::async_runtime::spawn(async move {
        time::sleep(Duration::from_secs(STARTUP_DELAY_SECS)).await;
        log::info!(
            "定时蒸馏: 启动 (每 {} 分钟敲一次门; 真正的 24h 判定在 \
             ~/.catfish/memory_distill_state.json, Companion 重启不丢)",
            TICK_SECS / 60
        );
        let mut interval = time::interval(Duration::from_secs(TICK_SECS));
        loop {
            interval.tick().await;
            tick_once(&app).await;
        }
    });
}

async fn tick_once(app: &AppHandle) {
    let Some(model) = picker_config::current_model() else {
        // 不退到默认模型 —— 蒸馏必须用员工选的那个
        log::debug!("定时蒸馏: picker 没有当前模型, 跳过这轮");
        return;
    };
    if model.trim().is_empty() {
        log::debug!("定时蒸馏: picker 模型为空, 跳过这轮");
        return;
    }

    match dream::run_dream(app.clone(), model.clone(), /* force */ false).await {
        // 说人话 —— exit_code=0 分不出"跑了"和"cooldown 跳过了", describe 能
        Ok(out) => log::info!(
            "定时蒸馏: {} (model={model}, exit={:?})",
            dream::describe(&out.result),
            out.exit_code
        ),
        // 失败要出声 —— 静默失败正是这条线上最贵的毛病
        Err(e) => log::warn!("定时蒸馏失败 model={model}: {e}"),
    }

    log_wiki_health();
}

/// 顺带体检一次知识库, 指标写进日志。
///
/// 8/4: 结构指标 (frontmatter 完整率 / 类型覆盖 / 引用有效 / 重复组) 本来只有
/// 员工手动跑 wiki_health.py 才看得到 —— 也就是"想起来查"才知道。而这两天所有
/// 最贵的 bug 都是没人想起来去查的: 19 个文件缺 frontmatter、408 条边 0 条带
/// 类型、21 组重复条目, 全都安静地待了几周。
///
/// 跟蒸馏共用一次 tick。失败只 warn —— 体检挂了不该影响蒸馏。
fn log_wiki_health() {
    let (Some(python), Some(dir)) = (
        catfish_paths::tool_bridge_python(),
        catfish_paths::catfish_memory_plugin_dir(),
    ) else {
        return;
    };
    let script = dir.join("wiki_health.py");
    if !script.exists() {
        return;
    }
    match std::process::Command::new(&python)
        .arg(&script)
        .output()
    {
        Ok(out) if out.status.success() => {
            let txt = String::from_utf8_lossy(&out.stdout);
            // 只捞百分比那几行, 不把整份报告灌进日志
            for line in txt.lines().filter(|l| l.contains('%')) {
                log::info!("知识库体检: {}", line.trim());
            }
        }
        Ok(out) => log::warn!(
            "知识库体检失败 (exit {:?}): {}",
            out.status.code(),
            String::from_utf8_lossy(&out.stderr).chars().take(300).collect::<String>()
        ),
        Err(e) => log::warn!("知识库体检跑不起来: {e}"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn knock_interval_is_far_shorter_than_the_cooldown() {
        // 敲门间隔必须远小于 24h, 否则 Companion 重启一次就错过一个窗口 ——
        // 那就退回成"睡 24 小时"那个错误。
        assert!(TICK_SECS < 24 * 3600 / 4, "敲门太稀, 重启会漏掉窗口");
    }

    #[test]
    fn startup_delay_is_shorter_than_one_tick() {
        // 启动延迟不该长到把第一轮吃掉
        assert!(STARTUP_DELAY_SECS < TICK_SECS);
    }
}
