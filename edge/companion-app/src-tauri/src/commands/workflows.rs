//! 工作流 skill (10/17, docs/WORKFLOW-SKILL-PLAN.md): 技能页的编辑 / 试跑 / 运行记录, 以及全局的「对外步骤确认」。
//!
//! 数据和规则都在 tool-bridge (skill_workflow.py / workflow_tool.py), 这里只转 RPC。
//! 只放行 `pim/workflow_*` 这一组。确认对外步骤 (`confirm`) 和「不用每次确认」开关 (`set_auto_confirm`)
//! 只有界面走这里能做 —— 模型的工具里没有这两个动作。

use std::time::Duration;

use serde_json::{json, Value};

use crate::services::tool_bridge_rpc;

/// 跑工作流 / 确认后接着跑可能要几分钟 (逐步调 skill、小鲶判断)。
const RPC_TIMEOUT: Duration = Duration::from_secs(600);

const ALLOWED: &[&str] = &[
    "catalog", "list", "get", "update", "draft_get", "draft_save", "draft_delete", "trial", "save", "run", "runs",
    "run_get", "run_delete", "pending", "confirm", "resume", "set_auto_confirm",
];

fn method_for(action: &str) -> Result<String, String> {
    if ALLOWED.contains(&action) {
        Ok(format!("pim/workflow_{action}"))
    } else {
        Err(format!("不支持的工作流动作: {action}"))
    }
}

#[tauri::command]
pub async fn workflow_call(action: String, params: Option<Value>) -> Result<Value, String> {
    let method = method_for(&action)?;
    tool_bridge_rpc::call_with_timeout(&method, params.unwrap_or_else(|| json!({})), RPC_TIMEOUT).await
}

/// 10/19: 定时跑一个工作流 = 建一个 hermes 定时任务, 让小鲶到点调 catfish_run_skill。
/// 对外步骤照样停下等员工在鲶鱼里确认 (除非他给这个工作流开了「不用每次确认」), 不绕开。
/// 任务名统一 `工作流·<中文名>`, 提示里写明 skill_path —— 界面按 skill_path 认出是哪个工作流的定时。
pub(crate) fn schedule_prompt(skill_path: &str, title: &str) -> String {
    format!(
        "定时运行工作流「{title}」: 调用 catfish_run_skill(skill_path=\"{skill_path}\") 跑一次, 用默认参数。\
         跑完用一两句话说明结果; 停在对外步骤 (waiting_confirm) 就说「等你在鲶鱼里确认」, 不要绕开确认。"
    )
}

/// schedule: hermes cron 认的写法 (例 `0 9 * * 1` = 每周一 9 点)。只认 5 段 cron, 防止乱传。
fn valid_schedule(s: &str) -> bool {
    let parts: Vec<&str> = s.split_whitespace().collect();
    parts.len() == 5 && parts.iter().all(|p| p.chars().all(|c| c.is_ascii_digit() || ",-*/".contains(c)))
}

#[tauri::command]
pub async fn workflow_schedule_set(skill_path: String, title: String, schedule: String) -> Result<(), String> {
    if !valid_schedule(&schedule) {
        return Err(format!("时间写法不对: {schedule}"));
    }
    if skill_path.contains('"') || skill_path.contains('\n') {
        return Err("工作流路径不对".into());
    }
    let prompt = schedule_prompt(&skill_path, &title);
    let name = format!("工作流·{}", title.chars().take(30).collect::<String>());
    tokio::task::spawn_blocking(move || {
        let home = super::expert_bots::get_home()?;
        super::expert_bots_profiles::run_hermes(&home, &["cron", "create", &schedule, &prompt, "--name", &name])
    })
    .await
    .map_err(|e| format!("建定时任务异常: {e}"))?
}

#[cfg(test)]
mod tests {
    use super::method_for;

    #[test]
    fn 定时只认5段cron_提示里写明skill_path() {
        assert!(super::valid_schedule("0 9 * * 1"));
        assert!(super::valid_schedule("30 8 * * 1-5"));
        assert!(!super::valid_schedule("every monday"));
        assert!(!super::valid_schedule("0 9 * * 1; rm -rf ~"));
        assert!(super::schedule_prompt("personal/x", "每周资质").contains("skill_path=\"personal/x\""));
    }

    #[test]
    fn 只放行工作流这一组() {
        assert_eq!(method_for("confirm").unwrap(), "pim/workflow_confirm");
        assert_eq!(method_for("run_delete").unwrap(), "pim/workflow_run_delete");
        assert!(method_for("form_send").is_err());
        assert!(method_for("../tools/dispatch").is_err());
    }
}
