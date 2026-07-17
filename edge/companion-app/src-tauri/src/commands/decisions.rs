//! BL-ADVISOR-DECISIONS (5/21 Phase 7 第 2 步): 决策留痕.
//!
//! 设计稿 §6.2: ~/.catfish/decisions.jsonl
//!
//! Append-only 文件, 每行一条 JSON. 不更新不删除, 只 append. 防丢历史.
//!
//! 跟 BL-CENTRAL-EDGE (5/17): decisions 是员工本机数据. 不出端.
//!
//! Tauri commands:
//!   - decision_record: 员工在 ActionCard 选了某口径, append 一条
//!   - decision_list_recent: 列最近 N 条 (debug / UI 复盘)
//!   - decision_search_by_topic: LLM tool recall_decision_history 用

use std::path::PathBuf;
use chrono::Utc;
use serde::{Deserialize, Serialize};

use super::audit_chain::chain_append_impl;

/// 员工最终选 / 操作记录. 每次 ActionCard 触发 → append 一行.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DecisionRecord {
    /// P3.3.52 (6/12): 记录类型 — "decision_choice" (员工选 A/B/C) / "status_change"
    /// (员工"标记完成 / 推迟 / 不做") / 老数据可为 None.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub record_kind: Option<String>,
    /// ISO-8601 timestamp.
    pub ts: String,
    /// 当次主菜 id (本地序号, e.g. 当天主菜 1/2/3).
    pub main_task_id: u32,
    /// 主菜标题快照.
    pub task_title: String,
    /// 给员工的选项 (label + tone 等元数据).
    pub options_offered: Vec<OptionMeta>,
    /// catfish 当时倾向哪个 (label, e.g. "B").
    pub ai_lean: Option<String>,
    /// 员工最后选了哪个 label. null = 没选 (员工自己重写或忽略).
    pub user_choice: Option<String>,
    /// 员工后续行为. "sent" / "drafted_but_held" / "overridden" / "ignored" / null.
    pub user_action: Option<String>,
    /// 合规 flag 快照 (decision 当时).
    pub compliance_flags_at_decision: Vec<String>,
    /// 上下文引用 (catfish-history:// URI 等), 给后续 recall 用.
    pub context_refs: Vec<String>,
    /// 草稿路径 (员工最终选用的那份, null = 没选草稿).
    pub draft_path_chosen: Option<String>,
    /// P3.3.52: status_change 类型用 — 员工切到哪个状态 ("done" / "snoozed" / "ignored")
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub new_status: Option<String>,
    /// P3.3.52: status_change 用 — task 的 stable uid (跨 refresh)
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub task_uid: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OptionMeta {
    pub label: String,   // "A" / "B" / "C"
    pub tone: String,    // "strict" / "balanced" / ...
    pub summary: Option<String>,
}

// ── 路径 + IO helpers ────────────────────────────────────────────────

fn decisions_path() -> Result<PathBuf, String> {
    let home = crate::util::paths::home_env().map_err(|e| format!("HOME 未设: {e}"))?;
    let dir = PathBuf::from(home).join(".catfish");
    std::fs::create_dir_all(&dir)
        .map_err(|e| format!("创建 ~/.catfish/ 失败: {e}"))?;
    Ok(dir.join("decisions.jsonl"))
}

// ── Tauri commands ────────────────────────────────────────────────────

/// Append 一条决策记录. 自动填 ts (Utc::now), caller 不用传.
///
/// P3.3.52 (6/12): 改走 audit_chain::chain_append_impl, 让 decisions.jsonl 自动
/// 加 sha256+prev_sha256, 配套 decisions.jsonl.chain.json 防篡改. 老 caller (P3.3.43
/// 之前的 ActionCard) 签名不变.
#[tauri::command]
pub async fn decision_record(mut record: DecisionRecord) -> Result<(), String> {
    record.ts = Utc::now().to_rfc3339();
    if record.record_kind.is_none() {
        record.record_kind = Some("decision_choice".to_string());
    }

    let path = decisions_path()?;
    let value = serde_json::to_value(&record)
        .map_err(|e| format!("serialize record 失败: {e}"))?;
    chain_append_impl(path.to_string_lossy().to_string(), value).await?;
    Ok(())
}

/// P3.3.52 (6/12): 员工在早安 detail pane 点"标记完成 / 推迟 / 不做" 时调.
/// 不依赖 ActionCard (P3.3.43 已删 options 框), 在 BriefingTwoColumnView
/// handleStatusChange 里 hook. 不打扰员工, 只留档.
///
/// new_status: "done" / "snoozed" / "ignored" / "cleared" (null)
#[tauri::command]
pub async fn decision_record_status_change(
    task_uid: String,
    task_title: String,
    new_status: String,
) -> Result<(), String> {
    let record = DecisionRecord {
        record_kind: Some("status_change".to_string()),
        ts: Utc::now().to_rfc3339(),
        main_task_id: 0,  // status_change 不一定有 mainTaskId, 0 占位
        task_title,
        options_offered: Vec::new(),
        ai_lean: None,
        user_choice: None,
        user_action: None,
        compliance_flags_at_decision: Vec::new(),
        context_refs: Vec::new(),
        draft_path_chosen: None,
        new_status: Some(new_status),
        task_uid: Some(task_uid),
    };

    let path = decisions_path()?;
    let value = serde_json::to_value(&record)
        .map_err(|e| format!("serialize record 失败: {e}"))?;
    chain_append_impl(path.to_string_lossy().to_string(), value).await?;
    Ok(())
}

/// 列最近 N 条决策. 倒序 (最新在前). N 默认 50, 上限 500.
#[tauri::command]
pub async fn decision_list_recent(limit: Option<u32>) -> Result<Vec<DecisionRecord>, String> {
    let n = limit.unwrap_or(50).min(500) as usize;
    let path = decisions_path()?;
    if !path.exists() {
        return Ok(Vec::new());
    }

    let content = std::fs::read_to_string(&path)
        .map_err(|e| format!("读 decisions.jsonl 失败: {e}"))?;
    let mut records: Vec<DecisionRecord> = Vec::new();
    for line in content.lines() {
        if line.trim().is_empty() {
            continue;
        }
        match serde_json::from_str::<DecisionRecord>(line) {
            Ok(r) => records.push(r),
            Err(_) => continue,  // 坏行跳过, 不阻塞
        }
    }
    records.sort_by(|a, b| b.ts.cmp(&a.ts));
    records.truncate(n);
    Ok(records)
}

/// LLM tool `catfish_recall_decision_history` 后端实现.
/// 按 topic 关键词 + 可选 person/project 过滤. 返最近 limit 条 (默认 5).
///
/// 匹配规则 (第一版简单, 用 substring):
///   - topic 词出现在 task_title 中
///   - person (if given) 出现在 task_title 中
///   - project (if given) 出现在 task_title 中
/// 后续可加更聪明的检索 (向量 / LLM 语义), 现在先简单跑通.
#[tauri::command]
pub async fn decision_search(
    topic: String,
    person: Option<String>,
    project: Option<String>,
    limit: Option<u32>,
) -> Result<Vec<DecisionRecord>, String> {
    let n = limit.unwrap_or(5).min(50) as usize;
    let all = decision_list_recent(Some(500)).await?;

    let topic_lower = topic.to_lowercase();
    let person_lower = person.as_ref().map(|p| p.to_lowercase());
    let project_lower = project.as_ref().map(|p| p.to_lowercase());

    let matched: Vec<DecisionRecord> = all
        .into_iter()
        .filter(|r| {
            let title_lower = r.task_title.to_lowercase();
            if !topic_lower.is_empty() && !title_lower.contains(&topic_lower) {
                return false;
            }
            if let Some(p) = &person_lower {
                if !title_lower.contains(p) {
                    return false;
                }
            }
            if let Some(pj) = &project_lower {
                if !title_lower.contains(pj) {
                    return false;
                }
            }
            true
        })
        .take(n)
        .collect();
    Ok(matched)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_record() -> DecisionRecord {
        DecisionRecord {
            record_kind: Some("decision_choice".to_string()),  // P3.3.52
            ts: String::new(),  // 会被 decision_record 覆盖
            main_task_id: 1,
            task_title: "老李催资质方案范围".to_string(),
            options_offered: vec![
                OptionMeta {
                    label: "A".to_string(),
                    tone: "strict".to_string(),
                    summary: Some("紧扣班子会".to_string()),
                },
                OptionMeta {
                    label: "B".to_string(),
                    tone: "balanced".to_string(),
                    summary: Some("微调保留余地".to_string()),
                },
            ],
            ai_lean: Some("B".to_string()),
            user_choice: Some("B".to_string()),
            user_action: Some("sent".to_string()),
            compliance_flags_at_decision: vec!["iso_audit_relevant".to_string()],
            context_refs: vec!["catfish-history://session/abc".to_string()],
            draft_path_chosen: Some("outputs/2026-05-22/reply-laoli-balanced.md".to_string()),
            new_status: None,  // P3.3.52: status_change 用
            task_uid: None,    // P3.3.52: status_change 用
        }
    }

    #[test]
    fn record_roundtrip_json() {
        let r = sample_record();
        let json = serde_json::to_string(&r).expect("serialize");
        let back: DecisionRecord = serde_json::from_str(&json).expect("parse");
        assert_eq!(back.main_task_id, 1);
        assert_eq!(back.user_choice.as_deref(), Some("B"));
        assert_eq!(back.options_offered.len(), 2);
    }

    #[test]
    fn options_meta_serializes() {
        let r = sample_record();
        let json = serde_json::to_value(&r).expect("serialize");
        assert_eq!(json["optionsOffered"][0]["label"], "A");
        assert_eq!(json["aiLean"], "B");
        assert_eq!(json["draftPathChosen"], "outputs/2026-05-22/reply-laoli-balanced.md");
    }
}
