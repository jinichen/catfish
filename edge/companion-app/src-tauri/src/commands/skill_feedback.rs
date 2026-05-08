//! BL-MM11 (5/8) — skill 级 👍/👎/改 评分.
//!
//! # 跟 BL-MM6 区别
//!
//! - BL-MM6 feedback: 给**对话消息**评分 (LLM 这次回得好/差) → ~/.catfish/feedback.jsonl
//! - BL-MM11 (本模块): 给**特定 skill 调用**评分 (这次 weekly-report 用得好/差) →
//!   ~/.catfish/skill_quality.jsonl
//!
//! 用途:
//!   1. Dashboard SkillAuditCard 显示每个 skill 的 thumbs_up / thumbs_down 累计
//!   2. BL-MM12 综合质量分数 (success_rate × 50 + freq × 30 + explicit_feedback × 20)
//!      公式里的 explicit_feedback 来源
//!   3. 员工记录"这个 skill 哪里需要改" 给后续 owner review
//!
//! Schema (一行一条 JSONL):
//!   {
//!     "ts": 1714867200.0,
//!     "kind": "thumb_up" | "thumb_down" | "edit",
//!     "skill_path": "department/weekly-report",
//!     "skill_call_ts": 1714867190.0,            // 关联 skill_audit.jsonl 的调用时间
//!     "session_id": "20260508_140000_xxx",
//!     "comment": "结构对了但口吻太正式" | null
//!   }

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs::{self, OpenOptions};
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct SkillFeedbackEvent {
    pub ts: f64,
    pub kind: String, // "thumb_up" | "thumb_down" | "edit"
    pub skill_path: String,
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub skill_call_ts: Option<f64>,
    pub session_id: String,
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub comment: Option<String>,
}

#[derive(Debug, Serialize, Default)]
pub struct SkillFeedbackPerSkill {
    pub skill_path: String,
    pub thumb_up: u32,
    pub thumb_down: u32,
    pub edit: u32,
    pub last_ts: f64,
}

#[derive(Debug, Serialize)]
pub struct SkillFeedbackSummary {
    pub total: u32,
    pub by_skill: Vec<SkillFeedbackPerSkill>, // 按 last_ts 倒序
    pub recent_negative: Vec<SkillFeedbackEvent>, // 最近 5 条 thumb_down + edit
    pub file_size_bytes: u64,
}

fn home_dir() -> Result<PathBuf, String> {
    std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .map(PathBuf::from)
        .map_err(|_| "找不到 HOME 环境变量".to_string())
}

fn skill_quality_path() -> Result<PathBuf, String> {
    Ok(home_dir()?.join(".catfish").join("skill_quality.jsonl"))
}

fn now_ts() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

#[tauri::command]
pub async fn skill_feedback_record(
    kind: String,
    skill_path: String,
    session_id: String,
    skill_call_ts: Option<f64>,
    comment: Option<String>,
) -> Result<(), String> {
    if !["thumb_up", "thumb_down", "edit"].contains(&kind.as_str()) {
        return Err(format!("kind 不合法: {kind}"));
    }
    if skill_path.trim().is_empty() {
        return Err("skill_path 不能为空".to_string());
    }
    let path = skill_quality_path()?;
    fs::create_dir_all(path.parent().unwrap())
        .map_err(|e| format!("建目录失败: {e}"))?;

    let ev = SkillFeedbackEvent {
        ts: now_ts(),
        kind,
        skill_path: skill_path.chars().take(200).collect(),
        skill_call_ts,
        session_id,
        comment: comment.map(|s| s.chars().take(500).collect()),
    };

    let line = serde_json::to_string(&ev)
        .map_err(|e| format!("序列化失败: {e}"))?;

    let mut f = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
        .map_err(|e| format!("打开 skill_quality.jsonl 失败: {e}"))?;
    writeln!(f, "{line}").map_err(|e| format!("写 skill_quality.jsonl 失败: {e}"))?;
    Ok(())
}

#[tauri::command]
pub async fn skill_feedback_summary() -> Result<SkillFeedbackSummary, String> {
    let path = skill_quality_path()?;
    if !path.exists() {
        return Ok(SkillFeedbackSummary {
            total: 0,
            by_skill: vec![],
            recent_negative: vec![],
            file_size_bytes: 0,
        });
    }
    let f = OpenOptions::new()
        .read(true)
        .open(&path)
        .map_err(|e| format!("打开 skill_quality.jsonl 失败: {e}"))?;
    let reader = BufReader::new(f);

    let mut total = 0u32;
    let mut by_skill_map: HashMap<String, SkillFeedbackPerSkill> = HashMap::new();
    let mut all_events: Vec<SkillFeedbackEvent> = Vec::new();

    for line in reader.lines() {
        let line = match line {
            Ok(l) => l,
            Err(_) => continue,
        };
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let ev: SkillFeedbackEvent = match serde_json::from_str(line) {
            Ok(e) => e,
            Err(_) => continue,
        };
        total += 1;
        let entry = by_skill_map
            .entry(ev.skill_path.clone())
            .or_insert_with(|| SkillFeedbackPerSkill {
                skill_path: ev.skill_path.clone(),
                ..Default::default()
            });
        match ev.kind.as_str() {
            "thumb_up" => entry.thumb_up += 1,
            "thumb_down" => entry.thumb_down += 1,
            "edit" => entry.edit += 1,
            _ => {}
        }
        if ev.ts > entry.last_ts {
            entry.last_ts = ev.ts;
        }
        all_events.push(ev);
    }

    // by_skill 按 last_ts 倒序
    let mut by_skill: Vec<SkillFeedbackPerSkill> = by_skill_map.into_values().collect();
    by_skill.sort_by(|a, b| b.last_ts.partial_cmp(&a.last_ts).unwrap_or(std::cmp::Ordering::Equal));

    // recent_negative: 最近 5 条 thumb_down / edit
    let mut neg: Vec<SkillFeedbackEvent> = all_events
        .into_iter()
        .filter(|e| e.kind == "thumb_down" || e.kind == "edit")
        .collect();
    neg.sort_by(|a, b| b.ts.partial_cmp(&a.ts).unwrap_or(std::cmp::Ordering::Equal));
    neg.truncate(5);

    let file_size_bytes = fs::metadata(&path).map(|m| m.len()).unwrap_or(0);

    Ok(SkillFeedbackSummary {
        total,
        by_skill,
        recent_negative: neg,
        file_size_bytes,
    })
}

#[tauri::command]
pub async fn skill_feedback_clear() -> Result<(), String> {
    let path = skill_quality_path()?;
    if path.exists() {
        fs::remove_file(&path).map_err(|e| format!("删 skill_quality.jsonl 失败: {e}"))?;
    }
    Ok(())
}

/// 5/8 BL-MM11: 给 BL-MM12 的综合质量分数公式用 — 拿单个 skill 的
/// (up_count, down_count, edit_count) 直接给打分函数.
pub fn aggregate_for_skill(skill_path: &str) -> (u32, u32, u32) {
    let path = match skill_quality_path() {
        Ok(p) => p,
        Err(_) => return (0, 0, 0),
    };
    if !path.exists() {
        return (0, 0, 0);
    }
    let f = match OpenOptions::new().read(true).open(&path) {
        Ok(f) => f,
        Err(_) => return (0, 0, 0),
    };
    let reader = BufReader::new(f);
    let mut up = 0u32;
    let mut down = 0u32;
    let mut edit = 0u32;
    for line in reader.lines() {
        let line = match line {
            Ok(l) => l,
            Err(_) => continue,
        };
        let ev: SkillFeedbackEvent = match serde_json::from_str(line.trim()) {
            Ok(e) => e,
            Err(_) => continue,
        };
        if ev.skill_path != skill_path {
            continue;
        }
        match ev.kind.as_str() {
            "thumb_up" => up += 1,
            "thumb_down" => down += 1,
            "edit" => edit += 1,
            _ => {}
        }
    }
    (up, down, edit)
}
