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
    /// BL-MM12 (5/8): 综合质量分数 0-100, 按分降序. 公式见 compute_quality_score().
    pub quality_scores: Vec<SkillQualityScore>,
}

/// BL-MM12 综合质量分数 — 0-100 整数, 越高越好.
///
/// 公式 (合计 100 分, 再乘 Compactness 乘数):
///   - 50 × success_rate            (调用成功率, 来自 skill_audit.jsonl ok 字段)
///   - 30 × normalized_freq         (调用频率归一化, log 缓增防"用 100 次 = 用 5 次×20 倍")
///   - 20 × explicit_feedback_ratio (员工显式 thumbs_up / (up+down), 来自 BL-MM11
///                                    skill_quality.jsonl. 没 feedback 时给 50 分位中性)
///   - **× Compactness 乘数 (P3.5.128, 借鉴 Skill-DisCo 2606.26669 Compactness 性质)**:
///     - <3 calls → 0.7 (特化, Coverage 不足)
///     - >50 calls 且 success<0.5 → 0.8 (泛而弱, scope creep)
///     - 其它 → 1.0
///     Compactness 用乘数而非加权重, 不破坏老分数对比性 (健康 skill 数字稳, 异常才扣).
///
/// 边界:
///   - 0 调用 → 不返 (不在 quality_scores 里)
///   - 全失败 + 0 反馈 → 接近 0 (合理)
///   - 调用多 + 全成功 + 全 thumbs_up → 接近 100
#[derive(Debug, Serialize)]
pub struct SkillQualityScore {
    pub skill_path: String,
    pub score: u32,           // 0-100
    pub call_count: usize,
    pub success_rate: f64,    // 0-1
    pub thumbs_up: u32,
    pub thumbs_down: u32,
    pub edits: u32,
    /// P3.5.128: Compactness 乘数 (1.0=健康 / 0.7=特化 / 0.8=泛而弱). 0-1 float.
    pub compactness: f64,
    /// 给员工看 "为啥这分"
    pub breakdown: String,
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
            quality_scores: vec![],
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

    // BL-MM12 (5/8): 综合质量分数 — 按 skill 算分, 降序
    let quality_scores = compute_quality_scores(&run_events);

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
        quality_scores,
    })
}


// ============================================================
// BL-MM12 (5/8) — 综合质量分数 0-100
// ============================================================

/// 给定全部 run events, 算每个 skill 的 0-100 分, 按分降序返.
///
/// 公式拆三块:
///   success_rate (50 分权重): ok / (ok + fail)
///   normalized_freq (30 分权重): log(1 + n) / log(1 + max_n) — 防"用过最多的拿满 30, 别人 0"
///   explicit_feedback (20 分权重): up / (up + down). 0 反馈 → 中性 0.5 (10/20 分).
///
/// 0 调用的 skill 不进结果 (没数据无意义).
///
/// visibility: module-private (默认) — `AuditEvent` 也是 module-private,
/// 函数签名暴露 visibility 必须 ≤ 类型. 只在同 module 内被 skill_audit_summary
/// + tests 调用, 没必要 pub.
fn compute_quality_scores(run_events: &[&AuditEvent]) -> Vec<SkillQualityScore> {
    use std::collections::HashMap;

    // 聚合每个 skill 的 (call_count, ok_count)
    #[derive(Default)]
    struct Agg {
        call_count: usize,
        ok_count: usize,
    }
    let mut agg: HashMap<String, Agg> = HashMap::new();
    for ev in run_events {
        if ev.skill_path.is_empty() {
            continue;
        }
        let entry = agg.entry(ev.skill_path.clone()).or_default();
        entry.call_count += 1;
        if ev.ok.unwrap_or(false) {
            entry.ok_count += 1;
        }
    }
    if agg.is_empty() {
        return vec![];
    }

    let max_calls = agg.values().map(|a| a.call_count).max().unwrap_or(1);
    // log(1 + max_calls) 用作归一化分母, +1 防 log(0)
    let log_max = ((max_calls + 1) as f64).ln().max(1e-9);

    let mut scores: Vec<SkillQualityScore> = agg
        .into_iter()
        .map(|(skill_path, a)| {
            let success_rate = if a.call_count == 0 {
                0.0
            } else {
                a.ok_count as f64 / a.call_count as f64
            };
            let log_n = ((a.call_count + 1) as f64).ln();
            let normalized_freq = (log_n / log_max).clamp(0.0, 1.0);

            // 拉 BL-MM11 显式反馈
            let (up, down, edits) =
                crate::commands::skill_feedback::aggregate_for_skill(&skill_path);
            let total_explicit = up + down;
            let explicit_ratio = if total_explicit == 0 {
                // 没显式反馈 → 中性 0.5 (10/20 分), 不偏好/不惩罚
                0.5
            } else {
                up as f64 / total_explicit as f64
            };

            // 三块加权
            let part_success = 50.0 * success_rate;
            let part_freq = 30.0 * normalized_freq;
            let part_feedback = 20.0 * explicit_ratio;
            let raw = part_success + part_freq + part_feedback;

            // P3.5.128 (借鉴 Skill-DisCo arxiv 2606.26669 Compactness 性质):
            // 特化 (call 太少 → Coverage 不足) 或 泛而弱 (call 多但成功率薄 →
            // scope creep) 都扣分. 用乘数不动权重, 老对比性稳.
            let (compactness, compact_reason): (f64, &str) =
                if a.call_count < 3 {
                    (0.7, "特化(<3次)")
                } else if a.call_count > 50 && success_rate < 0.5 {
                    (0.8, "泛而弱(>50次但<50%成功)")
                } else {
                    (1.0, "健康")
                };

            let score = (raw * compactness).round().clamp(0.0, 100.0) as u32;

            let breakdown = format!(
                "成功率 {:.0}/50 + 频次 {:.0}/30 + 显式反馈 {:.0}/20 × Compactness {:.1} ({}) = {} 分",
                part_success, part_freq, part_feedback, compactness, compact_reason, score,
            );

            SkillQualityScore {
                skill_path,
                score,
                call_count: a.call_count,
                success_rate,
                thumbs_up: up,
                thumbs_down: down,
                edits,
                compactness,
                breakdown,
            }
        })
        .collect();

    // 按分降序, 同分按 call_count 降序
    scores.sort_by(|a, b| {
        b.score
            .cmp(&a.score)
            .then(b.call_count.cmp(&a.call_count))
    });
    scores
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ev(path: &str, ok: bool) -> AuditEvent {
        AuditEvent {
            ts: "2026-05-08T10:00:00Z".to_string(),
            event_type: Some("run".to_string()),
            skill_path: path.to_string(),
            ok: Some(ok),
            duration_ms: Some(100),
            error_msg: None,
        }
    }

    #[test]
    fn empty_returns_empty() {
        let scores = compute_quality_scores(&[]);
        assert!(scores.is_empty());
    }

    #[test]
    fn single_skill_all_success_no_feedback() {
        // 1 个 skill, 全成功, 无反馈
        // success_rate=1.0, normalized_freq=1.0 (它就是 max), feedback=0.5 中性
        // 分 = 50 + 30 + 10 = 90
        let events = vec![ev("a", true), ev("a", true), ev("a", true)];
        let refs: Vec<&AuditEvent> = events.iter().collect();
        let scores = compute_quality_scores(&refs);
        assert_eq!(scores.len(), 1);
        assert_eq!(scores[0].skill_path, "a");
        // ~90 (允许 ±1 因 round)
        assert!((scores[0].score as i32 - 90).abs() <= 1);
    }

    #[test]
    fn all_failures_low_score() {
        let events = vec![ev("bad", false), ev("bad", false)];
        let refs: Vec<&AuditEvent> = events.iter().collect();
        let scores = compute_quality_scores(&refs);
        // P3.5.128 更新: success_rate=0 → 0/50, freq=1.0 → 30, feedback=0.5 中性 → 10
        // raw=40, 但 call_count=2 (<3) → Compactness 0.7 → 40 × 0.7 = 28
        assert_eq!(scores[0].score, 28);
        assert!((scores[0].compactness - 0.7).abs() < 1e-6);
        assert!(scores[0].breakdown.contains("特化"));
    }

    #[test]
    fn p3_5_128_specialized_low_calls_get_0_7_multiplier() {
        // 2 calls (<3) 即使全成功也扣到 0.7
        let events = vec![ev("rare", true), ev("rare", true)];
        let refs: Vec<&AuditEvent> = events.iter().collect();
        let scores = compute_quality_scores(&refs);
        // raw = 50 + 30 + 10 = 90, × 0.7 = 63
        assert_eq!(scores[0].score, 63);
        assert!((scores[0].compactness - 0.7).abs() < 1e-6);
    }

    #[test]
    fn p3_5_128_generalized_weak_gets_0_8_multiplier() {
        // >50 calls 但 success<0.5 触发 0.8 (泛而弱)
        // 51 calls, 25 成功 / 26 失败 → success ≈ 0.49
        let mut events: Vec<AuditEvent> = (0..25).map(|_| ev("creep", true)).collect();
        events.extend((0..26).map(|_| ev("creep", false)));
        let refs: Vec<&AuditEvent> = events.iter().collect();
        let scores = compute_quality_scores(&refs);
        // call_count=51 (>50) && success_rate=0.490 (<0.5) → compactness 0.8
        assert!((scores[0].compactness - 0.8).abs() < 1e-6);
        assert!(scores[0].breakdown.contains("泛而弱"));
    }

    #[test]
    fn p3_5_128_healthy_stays_at_1_0_multiplier() {
        // 5 calls 全成功 → 健康, compactness 1.0, score 不被乘数扣
        let events: Vec<AuditEvent> = (0..5).map(|_| ev("healthy", true)).collect();
        let refs: Vec<&AuditEvent> = events.iter().collect();
        let scores = compute_quality_scores(&refs);
        assert!((scores[0].compactness - 1.0).abs() < 1e-6);
        assert!(scores[0].breakdown.contains("健康"));
        // raw 0~90, × 1.0 = raw — 不被扣
        assert!(scores[0].score >= 89);
    }

    #[test]
    fn descending_score_order() {
        // 3 个 skill: good 全成功 / mid 一半成功 / bad 全失败
        let events: Vec<AuditEvent> = vec![
            ev("good", true), ev("good", true), ev("good", true),
            ev("mid", true), ev("mid", false),
            ev("bad", false), ev("bad", false),
        ];
        let refs: Vec<&AuditEvent> = events.iter().collect();
        let scores = compute_quality_scores(&refs);
        assert_eq!(scores.len(), 3);
        assert_eq!(scores[0].skill_path, "good");
        assert_eq!(scores[2].skill_path, "bad");
        assert!(scores[0].score > scores[1].score);
        assert!(scores[1].score > scores[2].score);
    }

    #[test]
    fn breakdown_contains_three_parts() {
        let events = vec![ev("x", true)];
        let refs: Vec<&AuditEvent> = events.iter().collect();
        let scores = compute_quality_scores(&refs);
        assert!(scores[0].breakdown.contains("成功率"));
        assert!(scores[0].breakdown.contains("频次"));
        assert!(scores[0].breakdown.contains("显式反馈"));
    }

    #[test]
    fn skips_skills_with_empty_path() {
        let events = vec![ev("", true), ev("real", true)];
        let refs: Vec<&AuditEvent> = events.iter().collect();
        let scores = compute_quality_scores(&refs);
        assert_eq!(scores.len(), 1);
        assert_eq!(scores[0].skill_path, "real");
    }
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
