//! 员工对钓鱼识别的标注 (11/07, 鸿波「这种识别错误, 能不能直接标注」)。
//!
//! 邮件详情的钓鱼红条上「不是钓鱼」、没报的信上「这是钓鱼」—— 按**发件地址**记 (不是按单封),
//! 以后这个地址来的信:
//!
//!   - 标过「不是钓鱼」: 复审结果一律 safe, 不再挂红条 / 钓鱼标签
//!   - 标过「这是钓鱼」: 一律按钓鱼报 (High)
//!
//! 并且最近的几十条标注作为参考一起交给模型复审 (「员工标过: x@y 不是钓鱼」), 同类的来信判得更准。
//!
//! 只存在这台电脑上 (~/.catfish/phishing_marks.json), 邮箱设置里能看、能撤销。
//! 标 / 撤销时, 前端把这个发件人在列表里的那几封 id 一起传来, 这里把它们的旧结果删掉, 前端随后重扫 ——
//! 撤销后按没标过重新判, 不会留着标注时改出来的结论。

use std::collections::BTreeMap;
use std::collections::HashMap;
use std::path::PathBuf;

use serde::{Deserialize, Serialize};

use super::email_state::{persist_phishing_store, phishing_store};
use super::phishing_scan::{Category, PhishingFlag, PhishingScanResult, Severity};

/// 交给模型的参考最多这么多条 (新的在前)
pub const HINTS_MAX: usize = 30;
pub const SAFE: &str = "safe";
pub const PHISHING: &str = "phishing";

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct SenderMark {
    /// "safe" 不是钓鱼 / "phishing" 是钓鱼
    pub verdict: String,
    pub at: String,
    /// 标的时候那封信的主题 (设置里列出来让人认得是哪封)
    #[serde(default)]
    pub subject: String,
    /// 发件人显示名
    #[serde(default)]
    pub name: String,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
pub struct PhishingMarks {
    /// 发件地址 (小写) → 标注
    #[serde(default)]
    pub senders: BTreeMap<String, SenderMark>,
}

/// "信息安全中心 <FF_NIC@chinatelecom.cn>" → ("ff_nic@chinatelecom.cn", "信息安全中心")
pub fn address_of(sender: &str) -> Option<(String, String)> {
    let s = sender.trim();
    let (name, addr) = match (s.rfind('<'), s.rfind('>')) {
        (Some(lt), Some(gt)) if lt < gt => (s[..lt].trim().trim_matches('"').to_string(), s[lt + 1..gt].trim().to_string()),
        _ => (String::new(), s.to_string()),
    };
    let addr = addr.to_lowercase();
    (addr.contains('@') && !addr.contains(' ')).then_some((addr, name))
}

fn path() -> Option<PathBuf> {
    Some(super::catfish_paths::catfish_home()?.join("phishing_marks.json"))
}

pub fn load() -> PhishingMarks {
    path()
        .and_then(|p| std::fs::read_to_string(p).ok())
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default()
}

fn save(m: &PhishingMarks) -> Result<(), String> {
    let p = path().ok_or("找不到 ~/.catfish")?;
    if let Some(dir) = p.parent() {
        std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    }
    let tmp = p.with_extension("json.tmp");
    std::fs::write(&tmp, serde_json::to_string_pretty(m).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
    std::fs::rename(&tmp, &p).map_err(|e| e.to_string())
}

/// 标 / 改 / 撤销 (verdict = None)。
pub fn set_mark(m: &mut PhishingMarks, sender: &str, verdict: Option<&str>, subject: &str, now: &str) -> Result<String, String> {
    let (addr, name) = address_of(sender).ok_or_else(|| format!("认不出发件地址: {sender}"))?;
    match verdict {
        None => {
            m.senders.remove(&addr);
        }
        Some(v) if v == SAFE || v == PHISHING => {
            m.senders.insert(addr.clone(), SenderMark {
                verdict: v.to_string(),
                at: now.to_string(),
                subject: subject.chars().take(80).collect(),
                name: name.chars().take(40).collect(),
            });
        }
        Some(v) => return Err(format!("不认识的标注: {v}")),
    }
    Ok(addr)
}

/// 这封信的发件人标过什么 (没标 = None)。
pub fn verdict_for<'a>(m: &'a PhishingMarks, sender: &str) -> Option<&'a str> {
    let (addr, _) = address_of(sender)?;
    m.senders.get(&addr).map(|x| x.verdict.as_str())
}

/// 按标注改复审结果: 标过「不是钓鱼」→ safe; 标过「这是钓鱼」→ High。规则命中的记录留着 (红条上看得到为什么原来会报)。
pub fn apply(result: &mut PhishingScanResult, verdict: &str) {
    result.user_mark = Some(verdict.to_string());
    if verdict == SAFE {
        result.highest_severity = Severity::None;
        result.llm_verdict = Some("safe".into());
        result.llm_reason = Some("你标过这个发件人「不是钓鱼」".into());
    } else {
        result.highest_severity = Severity::High;
        result.llm_verdict = Some("phishing".into());
        result.llm_reason = Some("你标过这个发件人「是钓鱼」".into());
        if !result.flags.iter().any(|f| f.rule_id == "USER-MARKED-phishing") {
            result.flags.push(PhishingFlag {
                rule_id: "USER-MARKED-phishing".into(),
                severity: Severity::High,
                category: Category::SenderSpoofing,
                reason: "员工标过这个发件人是钓鱼".into(),
                matched_text: None,
            });
        }
    }
}

/// 交给模型的参考: 「x@y.cn (显示名) → 不是钓鱼」, 新的在前, 最多 HINTS_MAX 条。
pub fn llm_hints(m: &PhishingMarks) -> Vec<String> {
    let mut v: Vec<(&String, &SenderMark)> = m.senders.iter().collect();
    v.sort_by(|a, b| b.1.at.cmp(&a.1.at));
    v.into_iter()
        .take(HINTS_MAX)
        .map(|(addr, x)| {
            let who = if x.name.is_empty() { addr.clone() } else { format!("{} <{}>", x.name, addr) };
            format!("{who} → {}", if x.verdict == SAFE { "不是钓鱼 (正常来信)" } else { "是钓鱼" })
        })
        .collect()
}

#[tauri::command]
pub fn email_phishing_marks() -> PhishingMarks {
    load()
}

/// 员工在红条上点了「不是钓鱼」/「这是钓鱼」/「撤销」。ids: 这个发件人在列表里的那几封 ——
/// 旧的复审结果删掉, 前端随后重扫 (按新的标注判)。返回删掉旧结果后的整张结果表 + 标注。
#[tauri::command]
pub fn email_phishing_mark(
    sender: String,
    verdict: Option<String>,
    subject: Option<String>,
    ids: Vec<String>,
) -> Result<serde_json::Value, String> {
    let mut m = load();
    let now = chrono::Utc::now().to_rfc3339();
    let addr = set_mark(&mut m, &sender, verdict.as_deref(), subject.as_deref().unwrap_or(""), &now)?;
    save(&m)?;
    let snapshot: HashMap<String, PhishingScanResult> = {
        let mut store = phishing_store().lock().map_err(|e| e.to_string())?;
        for id in &ids {
            store.remove(id);
        }
        store.clone()
    };
    persist_phishing_store();
    log::info!("[phishing] 员工标注 {addr} = {:?} ({} 封待重扫)", verdict, ids.len());
    Ok(serde_json::json!({ "marks": m, "store": snapshot }))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn 认发件地址() {
        assert_eq!(address_of("信息安全中心 <FF_NIC@chinatelecom.cn>"),
                   Some(("ff_nic@chinatelecom.cn".into(), "信息安全中心".into())));
        assert_eq!(address_of("a@b.cn"), Some(("a@b.cn".into(), String::new())));
        assert_eq!(address_of("没有地址"), None);
    }

    #[test]
    fn 标_改_撤销() {
        let mut m = PhishingMarks::default();
        set_mark(&mut m, "信息安全中心 <ff_nic@chinatelecom.cn>", Some(SAFE), "安全预警", "2026-11-07T15:00:00Z").unwrap();
        assert_eq!(verdict_for(&m, "Ff_Nic@ChinaTelecom.cn"), Some(SAFE));
        set_mark(&mut m, "ff_nic@chinatelecom.cn", Some(PHISHING), "", "2026-11-07T16:00:00Z").unwrap();
        assert_eq!(verdict_for(&m, "x <ff_nic@chinatelecom.cn>"), Some(PHISHING));
        set_mark(&mut m, "ff_nic@chinatelecom.cn", None, "", "").unwrap();
        assert!(m.senders.is_empty());
        assert!(set_mark(&mut m, "a@b.cn", Some("maybe"), "", "").is_err());
    }

    #[test]
    fn 按标注改结果_规则记录留着() {
        let mut r = PhishingScanResult {
            highest_severity: Severity::High,
            llm_verdict: Some("suspicious".into()),
            flags: vec![PhishingFlag { rule_id: "PHISH-001-display-name-mismatch".into(), severity: Severity::High,
                                       category: Category::SenderSpoofing, reason: String::new(), matched_text: None }],
            ..Default::default()
        };
        apply(&mut r, SAFE);
        assert_eq!(r.highest_severity, Severity::None);
        assert_eq!(r.user_mark.as_deref(), Some(SAFE));
        assert_eq!(r.flags.len(), 1);
        let mut r2 = PhishingScanResult::default();
        apply(&mut r2, PHISHING);
        apply(&mut r2, PHISHING);
        assert_eq!(r2.highest_severity, Severity::High);
        assert_eq!(r2.flags.len(), 1);
    }

    #[test]
    fn 交给模型的参考_新的在前() {
        let mut m = PhishingMarks::default();
        set_mark(&mut m, "旧 <a@x.cn>", Some(SAFE), "", "2026-11-01").unwrap();
        set_mark(&mut m, "b@evil.tk", Some(PHISHING), "", "2026-11-07").unwrap();
        assert_eq!(llm_hints(&m), vec!["b@evil.tk → 是钓鱼".to_string(), "旧 <a@x.cn> → 不是钓鱼 (正常来信)".to_string()]);
    }
}
