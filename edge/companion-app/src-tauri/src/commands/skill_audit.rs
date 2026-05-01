//! Skill 审计聚合 — 五一 sprint Day 2.
//!
//! 读 ~/.catfish/skill_audit.jsonl, 聚合成 SkillAuditSummary 给 Companion 仪表盘.
//!
//! audit jsonl 格式 (tool-bridge run_skill / skill_delete 写, 一行一个 event):
//!   {ts, skill_path, skill_version, deprecated, param_keys, ok, duration_ms,
//!    file_count, files, error_type?, error_msg?, event_type?}
//!
//! event_type 缺省 = "run", 也可以是 "delete" (catfish_skill_delete 写的).

use std::collections::HashMap;
use std::path::PathBuf;

use serde::{Deserialize, Serialize};

#[derive(Debug, Serialize)]
pub struct SkillAuditSummary {
    pub today_count: usize,
    pub today_ok_count: usize,
    pub today_error_count: usize,
    pub total_count: usize,
    pub top_skills: Vec<TopSkill>,
    pub failed_skills: Vec<FailedSkill>,
    /// 🆕 最近 7 天 ship 的 skill (SKILL.md mtime < 7 天). 没 audit 也正常 — 鼓励员工试用
    pub recently_shipped: Vec<String>,
    /// ⚠ 老 skill 从没用过 (SKILL.md ≥ 7 天 + audit 无记录). 真没人用, 评估删/留
    pub never_called_old: Vec<String>,
    /// 💤 调用过但最近 30 天没调用 — 老 skill 提示员工评估删/留
    pub stale_30d: Vec<String>,
    pub avg_duration_ms: u64,
}

#[derive(Debug, Serialize)]
pub struct TopSkill {
    pub skill_path: String,
    pub count: usize,
}

#[derive(Debug, Serialize)]
pub struct FailedSkill {
    pub skill_path: String,
    pub error_msg: String,
    pub ts: String,
}

/// audit jsonl 一行的反序列化形状. 字段都 optional, 兼容旧/新格式.
#[derive(Debug, Deserialize)]
struct AuditEvent {
    #[serde(default)]
    ts: String,
    #[serde(default)]
    event_type: Option<String>,
    #[serde(default)]
    skill_path: String,
    #[serde(default)]
    ok: Option<bool>,
    #[serde(default)]
    duration_ms: Option<u64>,
    #[serde(default)]
    error_msg: Option<String>,
}

fn audit_path() -> Option<PathBuf> {
    let home = std::env::var("HOME").ok()?;
    Some(PathBuf::from(home).join(".catfish").join("skill_audit.jsonl"))
}

#[tauri::command]
pub async fn skill_audit_summary() -> Result<SkillAuditSummary, String> {
    let path = audit_path().ok_or("HOME env 未设")?;
    if !path.exists() {
        // 没 audit 文件. 按 mtime 区分新老
        let (recently_shipped, never_called_old) = classify_known_by_mtime()?;
        return Ok(SkillAuditSummary {
            today_count: 0,
            today_ok_count: 0,
            today_error_count: 0,
            total_count: 0,
            top_skills: vec![],
            failed_skills: vec![],
            recently_shipped,
            never_called_old,
            stale_30d: vec![],
            avg_duration_ms: 0,
        });
    }

    let content = std::fs::read_to_string(&path)
        .map_err(|e| format!("读 {}: {e}", path.display()))?;

    let mut events: Vec<AuditEvent> = Vec::new();
    for line in content.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        match serde_json::from_str::<AuditEvent>(line) {
            Ok(ev) => events.push(ev),
            Err(_) => continue, // 旧格式 / 损坏行跳过
        }
    }

    // 时间过滤
    let now = chrono::Utc::now();
    let today_start = chrono::Utc::now()
        .date_naive()
        .and_hms_opt(0, 0, 0)
        .unwrap()
        .and_utc();
    let cutoff_30d = now - chrono::Duration::days(30);

    fn parse_ts(s: &str) -> Option<chrono::DateTime<chrono::Utc>> {
        chrono::DateTime::parse_from_rfc3339(s)
            .ok()
            .map(|dt| dt.with_timezone(&chrono::Utc))
    }

    // 只看 run 事件 (delete 不算 skill 调用)
    let run_events: Vec<&AuditEvent> = events
        .iter()
        .filter(|e| e.event_type.as_deref().unwrap_or("run") == "run")
        .collect();

    let total_count = run_events.len();
    let today_run: Vec<&&AuditEvent> = run_events
        .iter()
        .filter(|e| {
            parse_ts(&e.ts)
                .map(|t| t >= today_start)
                .unwrap_or(false)
        })
        .collect();
    let today_count = today_run.len();
    let today_ok_count = today_run
        .iter()
        .filter(|e| e.ok.unwrap_or(false))
        .count();
    let today_error_count = today_count - today_ok_count;

    // top skills (按 30 天内调用次数)
    let mut counts: HashMap<String, usize> = HashMap::new();
    for ev in &run_events {
        if let Some(t) = parse_ts(&ev.ts) {
            if t >= cutoff_30d && !ev.skill_path.is_empty() {
                *counts.entry(ev.skill_path.clone()).or_insert(0) += 1;
            }
        }
    }
    let mut top_skills: Vec<TopSkill> = counts
        .iter()
        .map(|(k, v)| TopSkill {
            skill_path: k.clone(),
            count: *v,
        })
        .collect();
    top_skills.sort_by(|a, b| b.count.cmp(&a.count));
    top_skills.truncate(5);

    // 最近失败 (近 24h)
    let cutoff_24h = now - chrono::Duration::hours(24);
    let mut failed_skills: Vec<FailedSkill> = run_events
        .iter()
        .filter(|e| !e.ok.unwrap_or(true))
        .filter(|e| {
            parse_ts(&e.ts)
                .map(|t| t >= cutoff_24h)
                .unwrap_or(false)
        })
        .map(|e| FailedSkill {
            skill_path: e.skill_path.clone(),
            error_msg: e.error_msg.clone().unwrap_or_default(),
            ts: e.ts.clone(),
        })
        .collect();
    failed_skills.reverse(); // 最新在前
    failed_skills.truncate(10);

    // 区分 3 类 skill 状态:
    //   🆕 recently_shipped:  SKILL.md mtime < 7 天, 不论 audit
    //   ⚠ never_called_old:  ≥ 7 天 + audit 无记录 (真没人用, 评估删)
    //   💤 stale_30d:         调过, 但 30 天没调
    let known_with_age = skills_known_with_age()?;
    // 所有 audit 里出现过的 skill_path
    let ever_called: std::collections::HashSet<String> = run_events
        .iter()
        .filter(|e| !e.skill_path.is_empty())
        .map(|e| e.skill_path.clone())
        .collect();
    // 30 天内被调用过的
    let used_30d: std::collections::HashSet<String> = counts.keys().cloned().collect();

    let mut recently_shipped: Vec<String> = vec![];
    let mut never_called_old: Vec<String> = vec![];
    let mut stale_30d: Vec<String> = vec![];
    let now = chrono::Utc::now();
    let seven_days_ago = now - chrono::Duration::days(7);
    for (s, mtime_iso) in known_with_age {
        let is_new = parse_ts(&mtime_iso).map(|t| t >= seven_days_ago).unwrap_or(false);
        if !ever_called.contains(&s) {
            if is_new {
                recently_shipped.push(s);
            } else {
                never_called_old.push(s);
            }
        } else if !used_30d.contains(&s) {
            stale_30d.push(s);
        }
    }

    // 平均耗时 (近 30 天 ok 的)
    let durations: Vec<u64> = run_events
        .iter()
        .filter(|e| e.ok.unwrap_or(false))
        .filter_map(|e| e.duration_ms)
        .collect();
    let avg_duration_ms = if durations.is_empty() {
        0
    } else {
        durations.iter().sum::<u64>() / durations.len() as u64
    };

    Ok(SkillAuditSummary {
        today_count,
        today_ok_count,
        today_error_count,
        total_count,
        top_skills,
        failed_skills,
        recently_shipped,
        never_called_old,
        stale_30d,
        avg_duration_ms,
    })
}

/// 扫 catfish/skills/ 拿 (skill_path, SKILL.md mtime ISO) 对.
/// 用于 SkillAuditCard 区分 "🆕 最近 ship" vs "⚠ 老 skill 没用过".
fn skills_known_with_age() -> Result<Vec<(String, String)>, String> {
    if let Ok(custom) = std::env::var("CATFISH_SKILLS_DIR") {
        return scan_skills_with_age(&PathBuf::from(custom));
    }
    let home = std::env::var("HOME").map_err(|_| "HOME env 未设".to_string())?;
    let candidates = [
        format!("{home}/person_task/catfish/skills"),
        format!("{home}/catfish/skills"),
    ];
    for c in &candidates {
        let p = PathBuf::from(c);
        if p.is_dir() {
            return scan_skills_with_age(&p);
        }
    }
    Ok(vec![])
}

fn scan_skills_with_age(root: &PathBuf) -> Result<Vec<(String, String)>, String> {
    let mut paths_with_age: Vec<(String, String)> = vec![];
    fn walk(
        dir: &PathBuf,
        root: &PathBuf,
        out: &mut Vec<(String, String)>,
    ) -> std::io::Result<()> {
        for entry in std::fs::read_dir(dir)? {
            let entry = entry?;
            let p = entry.path();
            if p.is_dir() {
                let skill_md = p.join("SKILL.md");
                if skill_md.exists() {
                    if let Ok(rel) = p.strip_prefix(root) {
                        let mtime_iso = std::fs::metadata(&skill_md)
                            .ok()
                            .and_then(|m| m.modified().ok())
                            .map(|sys| chrono::DateTime::<chrono::Utc>::from(sys).to_rfc3339())
                            .unwrap_or_default();
                        out.push((rel.to_string_lossy().to_string(), mtime_iso));
                    }
                } else {
                    walk(&p, root, out)?;
                }
            }
        }
        Ok(())
    }
    let _ = walk(root, root, &mut paths_with_age);
    paths_with_age.sort();
    Ok(paths_with_age)
}

/// audit 文件完全不存在时的 fallback — 按 mtime 直接分两类.
fn classify_known_by_mtime() -> Result<(Vec<String>, Vec<String>), String> {
    let with_age = skills_known_with_age()?;
    let now = chrono::Utc::now();
    let seven_days_ago = now - chrono::Duration::days(7);

    let mut recently_shipped: Vec<String> = vec![];
    let mut never_called_old: Vec<String> = vec![];

    fn parse_ts(s: &str) -> Option<chrono::DateTime<chrono::Utc>> {
        chrono::DateTime::parse_from_rfc3339(s)
            .ok()
            .map(|dt| dt.with_timezone(&chrono::Utc))
    }

    for (s, mtime_iso) in with_age {
        let is_new = parse_ts(&mtime_iso)
            .map(|t| t >= seven_days_ago)
            .unwrap_or(false);
        if is_new {
            recently_shipped.push(s);
        } else {
            never_called_old.push(s);
        }
    }
    Ok((recently_shipped, never_called_old))
}

// (老版 skills_known / scan_skills 已被 skills_known_with_age + scan_skills_with_age 替代)
