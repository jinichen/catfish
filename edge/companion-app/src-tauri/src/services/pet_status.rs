//! BL-E27.4 (5/8) — 桌宠状态聚合.
//!
//! # 解决什么 (鸿波 5/8 凌晨抱怨)
//!
//! macOS 通知中心一周累积太乱, 不直观. 桌宠头上加颜色 indicator + 单击打开 Companion
//! 看详情, 才是"鲶鱼是同事不是工具"的样子.
//!
//! # 4 状态 (优先级红 > 绿 > 蓝 > 默认)
//!
//! - default (灰白): 无新事件, 桌宠正常摇摆
//! - running (蓝呼吸): 当前后台有任务跑 (P2, 现在 MVP 不做 — 信息源在 Python tool-bridge,
//!   跨进程要再加一个 marker file. 留 hook)
//! - completed (绿点): 任务完成 / 主动闲聊未读 — 员工没看
//! - failed (红点): 任务失败未看 — 优先级最高
//!
//! # 数据源 (MVP)
//!
//! 1. `~/.catfish/pet_pending_bubbles.jsonl` — 任务通知 (task_manager._notify_task_done 写)
//!    - 每行 `{ts, kind, task_id, task_status, text}` task_status ∈ {completed, failed}
//! 2. `~/.catfish/pet_status_seen_ts.json` — 员工上次"点桌宠确认看过" 的时间戳
//!    - 单击桌宠 → pet_clicked 自动写当前时间
//!    - 老的 bubble (ts < seen_ts) 算已看, 不再触红绿
//!
//! # 重置流程
//!
//! 员工单击桌宠 → pet_clicked → pet_status_clear() 写当前 ts 到 seen_ts.json
//! 下次 pet_status_summary() 拉的时候, 老 bubble 全跳过, 状态回 default.
//!
//! # 不做 (P2 推后)
//!
//! - running 蓝色: tool-bridge Python 写 marker file 才能跨进程拿到
//! - 主动闲聊 unread 状态: ProactiveCard 30 分钟自动换, marker 没必要
//! - Dashboard 单独入口手动清: 单击桌宠已经 cover 95% 场景

use anyhow::{anyhow, Context, Result};
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum PetStatusColor {
    Default,
    Running,   // P2 现在 MVP 不会真触发, 留 enum 给未来
    Completed,
    Failed,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PetStatusSummary {
    pub color: PetStatusColor,
    pub unseen_completed: u32,
    pub unseen_failed: u32,
    pub last_event_ts: f64,
    pub seen_ts: f64,
}

fn catfish_dir() -> Result<PathBuf> {
    // 5/8 注: 仅生产代码用 env. 测试**禁用 env**, 直接用 _at(path) 内部版,
    // 防 parallel test stomp env (跟 5/7 BL-CR 同样的 race 问题).
    if let Ok(home) = std::env::var("CATFISH_HOME") {
        return Ok(PathBuf::from(home));
    }
    let home = crate::util::paths::home_env()
        .or_else(|_| std::env::var("USERPROFILE"))
        .map_err(|_| anyhow!("找不到 HOME 环境变量"))?;
    Ok(PathBuf::from(home).join(".catfish"))
}

fn bubble_jsonl_path_in(dir: &Path) -> PathBuf {
    dir.join("pet_pending_bubbles.jsonl")
}

fn seen_ts_path_in(dir: &Path) -> PathBuf {
    dir.join("pet_status_seen_ts.json")
}

fn read_seen_ts_at(dir: &Path) -> f64 {
    let path = seen_ts_path_in(dir);
    if !path.exists() {
        return 0.0;
    }
    let raw = match fs::read_to_string(&path) {
        Ok(s) => s,
        Err(_) => return 0.0,
    };
    let v: serde_json::Value = match serde_json::from_str(&raw) {
        Ok(v) => v,
        Err(_) => return 0.0,
    };
    v.get("seen_ts").and_then(|x| x.as_f64()).unwrap_or(0.0)
}

fn write_seen_ts_at(dir: &Path, ts: f64) -> Result<()> {
    let path = seen_ts_path_in(dir);
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).context("建 ~/.catfish/ 目录失败")?;
    }
    let payload = serde_json::json!({
        "seen_ts": ts,
        "seen_iso": chrono_ish_iso(ts),
    });
    fs::write(&path, payload.to_string()).context("写 seen_ts.json 失败")?;
    Ok(())
}

fn chrono_ish_iso(unix_secs: f64) -> String {
    // 不引 chrono crate, 简化 ISO 时间戳 — 给员工 inspect 时人看着方便
    let secs = unix_secs as i64;
    let utc_min = (secs / 60) % 60;
    let utc_hr = (secs / 3600) % 24;
    format!("unix={secs} (~{utc_hr:02}:{utc_min:02} UTC)")
}

#[derive(Debug, Deserialize)]
struct BubbleEvent {
    ts: f64,
    #[serde(default)]
    task_status: Option<String>,
}

/// 读 pet_pending_bubbles.jsonl + seen_ts → 返 PetStatusSummary (内部版, 测试用)
pub fn read_status_summary_at(dir: &Path) -> Result<PetStatusSummary> {
    let seen_ts = read_seen_ts_at(dir);
    let bubbles_path = bubble_jsonl_path_in(dir);
    if !bubbles_path.exists() {
        return Ok(PetStatusSummary {
            color: PetStatusColor::Default,
            unseen_completed: 0,
            unseen_failed: 0,
            last_event_ts: 0.0,
            seen_ts,
        });
    }
    let raw = fs::read_to_string(&bubbles_path).unwrap_or_default();

    let mut unseen_completed: u32 = 0;
    let mut unseen_failed: u32 = 0;
    let mut last_event_ts: f64 = 0.0;

    for line in raw.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let evt: BubbleEvent = match serde_json::from_str(line) {
            Ok(e) => e,
            Err(_) => continue,
        };
        if evt.ts <= seen_ts {
            continue; // 已看过
        }
        last_event_ts = last_event_ts.max(evt.ts);
        match evt.task_status.as_deref() {
            Some("failed") => unseen_failed += 1,
            Some("completed") => unseen_completed += 1,
            _ => {}
        }
    }

    let color = if unseen_failed > 0 {
        PetStatusColor::Failed
    } else if unseen_completed > 0 {
        PetStatusColor::Completed
    } else {
        PetStatusColor::Default
    };

    Ok(PetStatusSummary {
        color,
        unseen_completed,
        unseen_failed,
        last_event_ts,
        seen_ts,
    })
}

/// 内部版: 单击桌宠后调, 把当前时间写到指定目录 seen_ts.json.
pub fn mark_all_seen_at(dir: &Path) -> Result<f64> {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0);
    write_seen_ts_at(dir, now)?;
    Ok(now)
}

/// 公开版: 用默认 catfish_dir() (env 解析). 生产代码 (commands/pet.rs) 用这个.
pub fn read_status_summary() -> Result<PetStatusSummary> {
    read_status_summary_at(&catfish_dir()?)
}

/// 公开版: 用默认 catfish_dir(). pet_clicked 调.
pub fn mark_all_seen() -> Result<f64> {
    mark_all_seen_at(&catfish_dir()?)
}

#[cfg(test)]
mod tests {
    // 测试用 _at(path) 内部版直接传 TempDir, 不动 env, parallel-safe.
    // (5/8 跟 5/7 BL-CR 同问题: env::set_var 是 process-global, 多测试 stomp)
    use super::*;
    use tempfile::TempDir;

    fn write_bubble(tmp: &TempDir, ts: f64, status: &str) {
        let path = tmp.path().join("pet_pending_bubbles.jsonl");
        if let Some(p) = path.parent() {
            let _ = fs::create_dir_all(p);
        }
        let line = serde_json::json!({
            "ts": ts,
            "kind": "task_done",
            "task_id": format!("t-{ts}"),
            "task_status": status,
            "text": format!("{status} test"),
        });
        let mut content = fs::read_to_string(&path).unwrap_or_default();
        content.push_str(&line.to_string());
        content.push('\n');
        fs::write(&path, content).unwrap();
    }

    #[test]
    fn no_bubbles_returns_default() {
        let tmp = TempDir::new().unwrap();
        let s = read_status_summary_at(tmp.path()).unwrap();
        assert_eq!(s.color, PetStatusColor::Default);
        assert_eq!(s.unseen_completed, 0);
        assert_eq!(s.unseen_failed, 0);
    }

    #[test]
    fn unseen_completed_returns_completed() {
        let tmp = TempDir::new().unwrap();
        write_bubble(&tmp, 1000.0, "completed");
        write_bubble(&tmp, 2000.0, "completed");
        let s = read_status_summary_at(tmp.path()).unwrap();
        assert_eq!(s.color, PetStatusColor::Completed);
        assert_eq!(s.unseen_completed, 2);
        assert_eq!(s.unseen_failed, 0);
        assert_eq!(s.last_event_ts, 2000.0);
    }

    #[test]
    fn unseen_failed_takes_priority_over_completed() {
        let tmp = TempDir::new().unwrap();
        write_bubble(&tmp, 1000.0, "completed");
        write_bubble(&tmp, 1500.0, "failed");
        write_bubble(&tmp, 2000.0, "completed");
        let s = read_status_summary_at(tmp.path()).unwrap();
        assert_eq!(s.color, PetStatusColor::Failed);
        assert_eq!(s.unseen_completed, 2);
        assert_eq!(s.unseen_failed, 1);
    }

    #[test]
    fn mark_all_seen_clears_old_events() {
        let tmp = TempDir::new().unwrap();
        write_bubble(&tmp, 1000.0, "completed");
        write_bubble(&tmp, 1500.0, "failed");
        std::thread::sleep(std::time::Duration::from_millis(10));
        let seen_ts = mark_all_seen_at(tmp.path()).unwrap();
        assert!(seen_ts > 1500.0);

        let s = read_status_summary_at(tmp.path()).unwrap();
        assert_eq!(s.color, PetStatusColor::Default, "老 bubble 都该已看");
        assert_eq!(s.unseen_completed, 0);
        assert_eq!(s.unseen_failed, 0);
    }

    #[test]
    fn mark_seen_then_new_event_shows_color() {
        let tmp = TempDir::new().unwrap();
        write_bubble(&tmp, 1000.0, "completed");
        mark_all_seen_at(tmp.path()).unwrap();
        std::thread::sleep(std::time::Duration::from_millis(10));
        let now_plus = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs_f64()
            + 100.0;
        write_bubble(&tmp, now_plus, "failed");
        let s = read_status_summary_at(tmp.path()).unwrap();
        assert_eq!(s.color, PetStatusColor::Failed);
        assert_eq!(s.unseen_failed, 1);
    }

    #[test]
    fn ignores_malformed_lines() {
        let tmp = TempDir::new().unwrap();
        let path = tmp.path().join("pet_pending_bubbles.jsonl");
        fs::write(
            &path,
            "{\"ts\":1000,\"task_status\":\"completed\"}\nNOT JSON\n{\"ts\":2000,\"task_status\":\"failed\"}\n",
        )
        .unwrap();
        let s = read_status_summary_at(tmp.path()).unwrap();
        assert_eq!(s.color, PetStatusColor::Failed);
        assert_eq!(s.unseen_completed, 1);
        assert_eq!(s.unseen_failed, 1);
    }

    #[test]
    fn unknown_task_status_counts_as_neither() {
        let tmp = TempDir::new().unwrap();
        write_bubble(&tmp, 1000.0, "weird_state");
        let s = read_status_summary_at(tmp.path()).unwrap();
        assert_eq!(s.color, PetStatusColor::Default);
    }
}
