//! learning.rs 的测试 (10/25 拆出: 那边加了「没有分类目录的技能」后到了 945 行, 过了 800 行红线)。
//! 用 `#[path]` 挂成 learning 的子模块, `use super::*` 照旧能看到私有函数。
//! 跑法: cargo test --lib commands::learning

use super::*;

#[test]
fn 今天新技能_没有分类目录的也算() {
    let _g = crate::util::test_env::env_lock();
    let (hh, la) = (std::env::var_os("HERMES_HOME"), std::env::var_os("LOCALAPPDATA"));
    let home = tempfile::tempdir().unwrap();
    let skills = home.path().join("hermes-home").join("skills");
    std::env::set_var("HERMES_HOME", home.path().join("hermes-home"));
    std::env::remove_var("LOCALAPPDATA");
    for d in [skills.join("productivity").join("ocr"), skills.join("huashu-motion")] {
        std::fs::create_dir_all(d.join("references")).unwrap();
        std::fs::write(d.join("SKILL.md"), "---\nname: x\ndescription: 新学的\n---\n").unwrap();
    }
    let mut names: Vec<String> = collect_new_skills(home.path()).into_iter().map(|s| s.full_name).collect();
    names.sort();
    for (k, v) in [("HERMES_HOME", hh), ("LOCALAPPDATA", la)] {
        match v {
            Some(v) => std::env::set_var(k, v),
            None => std::env::remove_var(k),
        }
    }
    assert_eq!(names, vec!["huashu-motion", "productivity/ocr"]);
}

// ---------- extract_description ----------

#[test]
fn extract_description_basic() {
    let text = "---\nname: foo\ndescription: hello world\n---\n# Body";
    assert_eq!(extract_description(text), Some("hello world".into()));
}

#[test]
fn extract_description_quoted() {
    let text = "---\ndescription: \"quoted value\"\n---\n";
    assert_eq!(extract_description(text), Some("quoted value".into()));
}

#[test]
fn extract_description_single_quoted() {
    let text = "---\ndescription: 'single'\n---\n";
    assert_eq!(extract_description(text), Some("single".into()));
}

#[test]
fn extract_description_no_frontmatter() {
    let text = "# 直接是 markdown";
    assert_eq!(extract_description(text), None);
}

#[test]
fn extract_description_no_description_field() {
    let text = "---\nname: foo\nversion: 1\n---\n";
    assert_eq!(extract_description(text), None);
}

#[test]
fn extract_description_empty_value_skipped() {
    let text = "---\ndescription:\n---\n";
    assert_eq!(extract_description(text), None);
}

#[test]
fn extract_description_unterminated_frontmatter() {
    let text = "---\ndescription: never closed\n";
    assert_eq!(extract_description(text), None);
}

// ---------- 时间边界 ----------

#[test]
fn today_start_is_in_past() {
    let start = today_start_unix();
    let now = std::time::SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs_f64();
    assert!(start <= now, "today_start should be <= now");
    assert!(now - start < 86_400.0, "today_start should be < 24h ago");
}

#[test]
fn is_today_true_for_recent() {
    let now = std::time::SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs_f64();
    assert!(is_today(now));
}

#[test]
fn is_today_false_for_yesterday() {
    let yesterday = today_start_unix() - 1.0;
    assert!(!is_today(yesterday));
}

// ---------- build_summary ----------

#[test]
fn build_summary_no_activity() {
    let s = build_summary(0, 0, 0, 0, 0, 0);
    assert!(s.contains("还没动静"));
}

#[test]
fn build_summary_mixed_activity() {
    let s = build_summary(2, 1, 5, 12, 0, 0);
    assert!(s.contains("5 次对话"));
    assert!(s.contains("12 次工具"));
    assert!(s.contains("2 条 memory"));
    assert!(s.contains("1 个 skill"));
}

#[test]
fn build_summary_only_sessions() {
    let s = build_summary(0, 0, 3, 0, 0, 0);
    assert!(s.contains("3 次对话"));
    assert!(!s.contains("memory"));
    assert!(!s.contains("skill"));
    assert!(!s.contains("工具"));
}

#[test]
fn build_summary_with_coaching_and_emails() {
    let s = build_summary(0, 0, 2, 0, 1, 3);
    assert!(s.contains("演练 1 次"));
    assert!(s.contains("起草 3 封邮件"));
    // 要包含老的也包含新的
    assert!(s.contains("2 次对话"));
}

#[test]
fn build_summary_only_soft_skills() {
    // 即便没工具调用 / 没 memory, 演练 + 邮件起草也算"今天有动静"
    let s = build_summary(0, 0, 0, 0, 1, 1);
    assert!(!s.contains("还没动静"));
    assert!(s.contains("演练 1 次"));
    assert!(s.contains("起草 1 封邮件"));
}

// ---------- week_start_unix ----------

#[test]
fn week_start_is_in_past_for_zero_weeks_ago() {
    use std::time::SystemTime;
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs_f64();
    let ws = week_start_unix(0);
    assert!(ws <= now);
    // 本周一 00:00 距 now 不超 7 天
    assert!(now - ws < 7.0 * 86_400.0 + 60.0);
}

#[test]
fn prev_week_is_before_this_week() {
    let this_w = week_start_unix(0);
    let prev_w = week_start_unix(1);
    assert!(prev_w < this_w);
    assert_eq!((this_w - prev_w) as i64, 7 * 86_400);
}

// ---------- KNOWN_METHODOLOGIES ----------

#[test]
fn methodology_keywords_are_unique() {
    // 防 KNOWN_METHODOLOGIES 不小心写重复, 同时确认覆盖关键词
    use std::collections::HashSet;
    let set: HashSet<_> = KNOWN_METHODOLOGIES.iter().collect();
    assert_eq!(set.len(), KNOWN_METHODOLOGIES.len());
    assert!(KNOWN_METHODOLOGIES.contains(&"STAR"));
    assert!(KNOWN_METHODOLOGIES.contains(&"金字塔"));
    assert!(KNOWN_METHODOLOGIES.contains(&"非暴力沟通"));
}

// ---------- unix_to_iso ----------

#[test]
fn unix_to_iso_basic() {
    // 2026-04-26 00:00:00 UTC = 1777161600
    let iso = unix_to_iso(1777161600.0);
    assert!(iso.starts_with("2026-04-26"));
}

#[test]
fn unix_to_iso_zero_returns_unix_epoch() {
    let iso = unix_to_iso(0.0);
    assert!(iso.starts_with("1970-01-01"));
}
