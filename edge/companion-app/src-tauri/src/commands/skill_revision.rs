//! BL-MM14 / MM15 (5/8) — Dashboard skill revision proposals + 改进有效性跟踪.
//!
//! # 跟 BL-MM9 / BL-MM11 / BL-MM12 关系
//!
//! - BL-MM9 propose 新 skill (员工反复做 → 提议存) → ~/.catfish/skill_proposals.jsonl
//! - BL-MM11 给 skill 评分 → ~/.catfish/skill_quality.jsonl
//! - BL-MM12 算综合质量分 (Dashboard SkillAuditCard 显示)
//! - **BL-MM13 propose 改老 skill** (本模块的数据源) → ~/.catfish/skill_revisions.jsonl
//! - **BL-MM14 (本模块前半)**: Dashboard 列出 revision proposals + 员工 accept/reject
//! - **BL-MM15 (本模块后半)**: accept 后 14d 监测 quality_score 变化, 没提升建议回退
//!
//! # JSONL Schema (~/.catfish/skill_revisions.jsonl)
//!
//! propose 事件 (LLM 写, 见 catfish_tools.propose_skill_revision):
//!   {
//!     "event_type": "proposed",
//!     "revision_id": "rev_<ts>_<skill_path>_<proposed_v>",
//!     "skill_path": "department/weekly-report",
//!     "current_version": "0.3.2",
//!     "proposed_version": "0.4.0",
//!     "reason": "...",
//!     "diff_summary": "...",
//!     "evidence_summary": "...",
//!     "status": "proposed",
//!     "ts": 1715200000.0,
//!     "ts_iso": "2026-05-08T20:00:00+09:00"
//!   }
//!
//! accept 事件 (员工 click ✅, 本模块写):
//!   {
//!     "event_type": "accepted",
//!     "revision_id": "rev_...",
//!     "ts": ...,
//!     // BL-MM15: 记录采纳时的基线分数, 14d 后看是否提升
//!     "baseline_quality_score": 35.0
//!   }
//!
//! reject 事件 (员工 click ❌):
//!   {"event_type": "rejected", "revision_id": "rev_...", "ts": ..., "comment": "..." }

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs::{self, OpenOptions};
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct RevisionEvent {
    pub event_type: String, // "proposed" | "accepted" | "rejected"
    pub revision_id: String,
    pub ts: f64,
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub skill_path: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub current_version: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub proposed_version: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub reason: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub diff_summary: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub evidence_summary: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub status: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub ts_iso: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub comment: Option<String>,
    /// BL-MM15: accept 时记基线分, 14d 后比对.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub baseline_quality_score: Option<f64>,
}

/// 一条 revision proposal 的"当前状态"视图 (汇总 proposed + 后续 accepted/rejected event).
#[derive(Debug, Serialize, Clone)]
pub struct RevisionView {
    pub revision_id: String,
    pub skill_path: String,
    pub current_version: String,
    pub proposed_version: String,
    pub reason: String,
    pub diff_summary: String,
    pub evidence_summary: String,
    /// "proposed" / "accepted" / "rejected"
    pub status: String,
    pub proposed_ts: f64,
    pub proposed_ts_iso: String,
    /// BL-MM15: accept 时记的基线分, 给 effectiveness 跟踪用
    #[serde(skip_serializing_if = "Option::is_none")]
    pub baseline_quality_score: Option<f64>,
    /// BL-MM15: accepted 之后过了多少天 (用于 UI 显示"采纳 N 天")
    #[serde(skip_serializing_if = "Option::is_none")]
    pub accepted_days_ago: Option<u32>,
}

/// Dashboard 展示的整张表格.
#[derive(Debug, Serialize)]
pub struct RevisionSummary {
    pub total_proposed: u32,
    pub total_accepted: u32,
    pub total_rejected: u32,
    /// 待员工处理的 (status=proposed). UI 顶部置顶.
    pub pending: Vec<RevisionView>,
    /// 已处理的 (accepted/rejected), 按时间倒序最近 20 条.
    pub recent_resolved: Vec<RevisionView>,
    /// BL-MM15: 已采纳 ≥14d 但还没做 effectiveness check 的, UI 提示"该评估了"
    pub effectiveness_due: Vec<RevisionView>,
    pub file_size_bytes: u64,
}

fn home_dir() -> Result<PathBuf, String> {
    if let Ok(h) = crate::util::paths::home_env() {
        return Ok(PathBuf::from(h));
    }
    if let Ok(h) = std::env::var("USERPROFILE") {
        return Ok(PathBuf::from(h));
    }
    Err("找不到 HOME / USERPROFILE".to_string())
}

fn revisions_path() -> Result<PathBuf, String> {
    Ok(home_dir()?.join(".catfish").join("skill_revisions.jsonl"))
}

fn read_all_events() -> Result<Vec<RevisionEvent>, String> {
    let path = revisions_path()?;
    if !path.exists() {
        return Ok(Vec::new());
    }
    let f = fs::File::open(&path).map_err(|e| format!("打开 {} 失败: {}", path.display(), e))?;
    let reader = BufReader::new(f);
    let mut out = Vec::new();
    for line in reader.lines() {
        let Ok(line) = line else { continue };
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        if let Ok(ev) = serde_json::from_str::<RevisionEvent>(line) {
            out.push(ev);
        }
    }
    Ok(out)
}

fn append_event(ev: &RevisionEvent) -> Result<(), String> {
    let path = revisions_path()?;
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| format!("建 .catfish 目录失败: {}", e))?;
    }
    let mut f = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
        .map_err(|e| format!("打开 {} append 失败: {}", path.display(), e))?;
    let line = serde_json::to_string(ev).map_err(|e| format!("序列化 event 失败: {}", e))?;
    writeln!(f, "{}", line).map_err(|e| format!("写 {} 失败: {}", path.display(), e))?;
    Ok(())
}

/// 把所有 events 折叠成"每个 revision_id 当前状态" map.
/// 规则: 取 ts 最大的 event 的 status (后写覆盖前写, 跟 jsonl append-only 语义一致).
fn fold_revisions(events: &[RevisionEvent]) -> Vec<RevisionView> {
    // group by revision_id
    let mut groups: HashMap<String, Vec<&RevisionEvent>> = HashMap::new();
    for ev in events {
        groups.entry(ev.revision_id.clone()).or_default().push(ev);
    }

    let mut out = Vec::with_capacity(groups.len());
    for (rev_id, mut evs) in groups {
        // 按 ts 升序排, 最早 (proposed) 在前
        evs.sort_by(|a, b| a.ts.partial_cmp(&b.ts).unwrap_or(std::cmp::Ordering::Equal));

        let proposed = evs.iter().find(|e| e.event_type == "proposed");
        let Some(prop) = proposed else {
            // 跳: 没 proposed 事件的 revision_id 是脏数据, 忽略
            continue;
        };

        // 当前状态 = 最后一个 event 的 event_type
        let last = evs.last().unwrap();
        let status = last.event_type.clone();

        let baseline = evs
            .iter()
            .find(|e| e.event_type == "accepted")
            .and_then(|e| e.baseline_quality_score);
        let accepted_days_ago = evs
            .iter()
            .find(|e| e.event_type == "accepted")
            .map(|e| {
                let now = std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .map(|d| d.as_secs_f64())
                    .unwrap_or(0.0);
                let elapsed_days = ((now - e.ts) / 86400.0).max(0.0);
                elapsed_days as u32
            });

        out.push(RevisionView {
            revision_id: rev_id,
            skill_path: prop.skill_path.clone().unwrap_or_default(),
            current_version: prop.current_version.clone().unwrap_or_default(),
            proposed_version: prop.proposed_version.clone().unwrap_or_default(),
            reason: prop.reason.clone().unwrap_or_default(),
            diff_summary: prop.diff_summary.clone().unwrap_or_default(),
            evidence_summary: prop.evidence_summary.clone().unwrap_or_default(),
            status,
            proposed_ts: prop.ts,
            proposed_ts_iso: prop.ts_iso.clone().unwrap_or_default(),
            baseline_quality_score: baseline,
            accepted_days_ago,
        });
    }

    // 按 proposed_ts 倒序
    out.sort_by(|a, b| {
        b.proposed_ts
            .partial_cmp(&a.proposed_ts)
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    out
}

/// BL-MM14: 给 Dashboard SkillRevisionCard 拉数据.
#[tauri::command]
pub fn skill_revision_summary() -> Result<RevisionSummary, String> {
    let events = read_all_events()?;
    let views = fold_revisions(&events);

    let total_proposed = views.len() as u32;
    let total_accepted = views.iter().filter(|v| v.status == "accepted").count() as u32;
    let total_rejected = views.iter().filter(|v| v.status == "rejected").count() as u32;

    let mut pending: Vec<RevisionView> = views.iter().filter(|v| v.status == "proposed").cloned().collect();
    pending.sort_by(|a, b| {
        b.proposed_ts
            .partial_cmp(&a.proposed_ts)
            .unwrap_or(std::cmp::Ordering::Equal)
    });

    let mut recent_resolved: Vec<RevisionView> = views
        .iter()
        .filter(|v| v.status != "proposed")
        .cloned()
        .collect();
    recent_resolved.sort_by(|a, b| {
        b.proposed_ts
            .partial_cmp(&a.proposed_ts)
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    recent_resolved.truncate(20);

    // BL-MM15: 已采纳 ≥14d 的, 提示"该评估了"
    let effectiveness_due: Vec<RevisionView> = views
        .iter()
        .filter(|v| v.status == "accepted" && v.accepted_days_ago.unwrap_or(0) >= 14)
        .cloned()
        .collect();

    let file_size_bytes = revisions_path()
        .ok()
        .and_then(|p| fs::metadata(&p).ok())
        .map(|m| m.len())
        .unwrap_or(0);

    Ok(RevisionSummary {
        total_proposed,
        total_accepted,
        total_rejected,
        pending,
        recent_resolved,
        effectiveness_due,
        file_size_bytes,
    })
}

#[derive(Debug, Deserialize)]
pub struct AcceptArgs {
    pub revision_id: String,
    /// BL-MM15: 当前 quality_score (前端从 BL-MM12 SkillAuditCard 拉, 调用本命令时传)
    pub baseline_quality_score: Option<f64>,
}

/// BL-MM14: 员工 click ✅ 采纳一个 revision proposal.
///
/// 注: **本命令只标记 accepted**, 不真改 skill 文件 (那需要 LLM 重新生成
/// SKILL.md + script.py 内容, 单走一条 user-LLM 对话, 不在这一步做).
/// 真改 skill 走 catfish_skill_install + BL-MM3 自动备份老版.
///
/// BL-MM15: 同时记 baseline_quality_score, 14d 后跟踪是否提升.
#[tauri::command]
pub fn skill_revision_accept(args: AcceptArgs) -> Result<(), String> {
    if args.revision_id.is_empty() {
        return Err("revision_id 必填".to_string());
    }
    let now_ts = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_err(|e| format!("时钟错: {}", e))?
        .as_secs_f64();
    let event = RevisionEvent {
        event_type: "accepted".to_string(),
        revision_id: args.revision_id,
        ts: now_ts,
        skill_path: None,
        current_version: None,
        proposed_version: None,
        reason: None,
        diff_summary: None,
        evidence_summary: None,
        status: Some("accepted".to_string()),
        ts_iso: None,
        comment: None,
        baseline_quality_score: args.baseline_quality_score,
    };
    append_event(&event)
}

#[derive(Debug, Deserialize)]
pub struct RejectArgs {
    pub revision_id: String,
    /// 可选: 员工拒绝理由 (例 '改了反而更差')
    pub comment: Option<String>,
}

/// BL-MM14: 员工 click ❌ 拒绝一个 revision proposal.
#[tauri::command]
pub fn skill_revision_reject(args: RejectArgs) -> Result<(), String> {
    if args.revision_id.is_empty() {
        return Err("revision_id 必填".to_string());
    }
    let now_ts = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_err(|e| format!("时钟错: {}", e))?
        .as_secs_f64();
    let event = RevisionEvent {
        event_type: "rejected".to_string(),
        revision_id: args.revision_id,
        ts: now_ts,
        skill_path: None,
        current_version: None,
        proposed_version: None,
        reason: None,
        diff_summary: None,
        evidence_summary: None,
        status: Some("rejected".to_string()),
        ts_iso: None,
        comment: args.comment,
        baseline_quality_score: None,
    };
    append_event(&event)
}

#[derive(Debug, Serialize)]
pub struct EffectivenessReport {
    pub revision_id: String,
    pub skill_path: String,
    pub baseline: f64,
    pub current: f64,
    pub delta: f64,
    /// "improved" (delta ≥ +10) / "neutral" (-10 < delta < +10) / "regressed" (delta ≤ -10)
    pub verdict: String,
    pub recommendation: String,
}

#[derive(Debug, Deserialize)]
pub struct EffectivenessArgs {
    pub revision_id: String,
    /// 当前 BL-MM12 综合分 (前端从 SkillAuditCard 当前 score 传过来)
    pub current_quality_score: f64,
}

/// BL-MM15: 评估一个已采纳 revision 的有效性 (采纳 N 天后跟当时基线对比).
///
/// 不自动调度执行 — 前端在 SkillRevisionCard "评估有效性" 按钮触发.
#[tauri::command]
pub fn skill_revision_check_effectiveness(
    args: EffectivenessArgs,
) -> Result<EffectivenessReport, String> {
    let events = read_all_events()?;
    let mut accept_event: Option<RevisionEvent> = None;
    let mut proposed_event: Option<RevisionEvent> = None;
    for ev in &events {
        if ev.revision_id != args.revision_id {
            continue;
        }
        if ev.event_type == "proposed" {
            proposed_event = Some(ev.clone());
        }
        if ev.event_type == "accepted" {
            accept_event = Some(ev.clone());
        }
    }
    let prop = proposed_event.ok_or_else(|| {
        format!("找不到 proposed 事件 (revision_id={})", args.revision_id)
    })?;
    let acc = accept_event.ok_or_else(|| {
        format!(
            "revision_id={} 还没采纳, 不能评估有效性",
            args.revision_id
        )
    })?;
    let baseline = acc.baseline_quality_score.ok_or_else(|| {
        "采纳事件没记录 baseline_quality_score (BL-MM15 要求 accept 时传基线分)".to_string()
    })?;
    let current = args.current_quality_score;
    let delta = current - baseline;

    let (verdict, recommendation) = if delta >= 10.0 {
        (
            "improved".to_string(),
            format!(
                "✅ 改进有效 — 分数从 {:.0} 提到 {:.0} (+{:.0}). 保持新版.",
                baseline, current, delta
            ),
        )
    } else if delta <= -10.0 {
        (
            "regressed".to_string(),
            format!(
                "⚠️ 改进反而变差 — 分数从 {:.0} 降到 {:.0} ({:.0}). 建议回退老版 \
                 (走 catfish_skill_install 装老版本号 — BL-MM3 .versions/ 还存着).",
                baseline, current, delta
            ),
        )
    } else {
        (
            "neutral".to_string(),
            format!(
                "🟡 改进效果不明显 — 分数 {:.0} → {:.0} (Δ{:+.0}). \
                 再观察 1 周或者再 propose 一轮改进.",
                baseline, current, delta
            ),
        )
    };

    Ok(EffectivenessReport {
        revision_id: args.revision_id.clone(),
        skill_path: prop.skill_path.unwrap_or_default(),
        baseline,
        current,
        delta,
        verdict,
        recommendation,
    })
}
