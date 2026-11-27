//! 11/27 技能验证题 —— 仪表盘「技能 → 验证题」分页的后端。
//!
//! 题放在 `~/.catfish/skill_evals/<技能名>.json` (hermes 插件 skill_eval_gate.py / skill_eval_learn.py 写):
//!   {"skill", "questions": [{"id", "q", "gold", "status": "confirmed"|"pending", "origin", "source", ...}], "baseline"}
//! 小鲶照员工原话自动出的题是「待确认」, 员工在这里点「确认」才拿来拦技能改动; 也能改答案、删题。
//! 「从历史对话出题」= 跑插件里的 `skill_eval_gate.py mine` (老员工冷启动, 出的也都是待确认)。
//! 拦下 / 撤回的记录在同目录 gate_log.jsonl, 这里列最近的给员工看。

use serde::Serialize;
use serde_json::Value;
use std::fs;
use std::path::PathBuf;

#[derive(Debug, Serialize)]
pub struct EvalQuestion {
    pub skill: String,
    pub id: String,
    pub q: String,
    pub gold: String,
    pub status: String,
    pub origin: String,
    pub source: String,
    pub at: String,
    pub changed: bool,
}

#[derive(Debug, Serialize)]
pub struct EvalSummary {
    pub pending: Vec<EvalQuestion>,
    pub confirmed: Vec<EvalQuestion>,
    pub skills: usize,
    pub log: Vec<Value>,
}

fn dir() -> Result<PathBuf, String> {
    crate::services::catfish_paths::catfish_home()
        .map(|h| h.join("skill_evals"))
        .ok_or_else(|| "找不到 ~/.catfish".to_string())
}

fn safe(name: &str) -> String {
    name.rsplit('/')
        .next()
        .unwrap_or(name)
        .chars()
        .map(|c| if c.is_alphanumeric() || "._-".contains(c) { c } else { '_' })
        .collect()
}

fn read(path: &PathBuf) -> Option<Value> {
    serde_json::from_str(&fs::read_to_string(path).ok()?).ok()
}

fn write(path: &PathBuf, v: &Value) -> Result<(), String> {
    let tmp = path.with_extension("tmp");
    let text = serde_json::to_string_pretty(v).map_err(|e| e.to_string())?;
    fs::write(&tmp, text).map_err(|e| format!("写 {} 失败: {e}", tmp.display()))?;
    fs::rename(&tmp, path).map_err(|e| format!("写 {} 失败: {e}", path.display()))
}

fn s(v: &Value, k: &str) -> String {
    v.get(k).and_then(Value::as_str).unwrap_or_default().to_string()
}

#[tauri::command]
pub fn skill_evals_summary() -> Result<EvalSummary, String> {
    let d = dir()?;
    let mut out = EvalSummary { pending: vec![], confirmed: vec![], skills: 0, log: vec![] };
    let Ok(entries) = fs::read_dir(&d) else { return Ok(out) };
    for e in entries.flatten() {
        let p = e.path();
        if p.extension().and_then(|x| x.to_str()) != Some("json") {
            continue;
        }
        let Some(v) = read(&p) else { continue };
        let skill = s(&v, "skill");
        let qs = v.get("questions").and_then(Value::as_array).cloned().unwrap_or_default();
        if !qs.is_empty() {
            out.skills += 1;
        }
        for q in qs {
            let status = q.get("status").and_then(Value::as_str).unwrap_or("confirmed").to_string();
            let at = [s(&q, "updated_at"), s(&q, "created_at")].into_iter().find(|x| !x.is_empty()).unwrap_or_default();
            let item = EvalQuestion {
                skill: skill.clone(),
                id: s(&q, "id"),
                q: s(&q, "q"),
                gold: s(&q, "gold"),
                origin: q.get("origin").and_then(Value::as_str).unwrap_or("manual").to_string(),
                source: s(&q, "source"),
                changed: q.get("history").and_then(Value::as_array).is_some_and(|h| !h.is_empty()),
                status: status.clone(),
                at,
            };
            if status == "pending" { out.pending.push(item) } else { out.confirmed.push(item) }
        }
    }
    out.pending.sort_by(|a, b| b.at.cmp(&a.at));
    out.confirmed.sort_by(|a, b| (a.skill.as_str(), a.id.as_str()).cmp(&(b.skill.as_str(), b.id.as_str())));
    if let Ok(text) = fs::read_to_string(d.join("gate_log.jsonl")) {
        out.log = text.lines().rev().filter_map(|l| serde_json::from_str::<Value>(l).ok())
            .filter(|v| v.get("reverted").and_then(Value::as_bool) == Some(true) || v.get("blocked").is_some())
            .take(20).collect();
    }
    Ok(out)
}

/// action: "confirm" | "delete" | "edit" (edit 带新的 gold) | "confirm_all" (这个技能待确认的全确认, id 不用)
#[tauri::command]
pub fn skill_evals_update(skill: String, id: String, action: String, gold: Option<String>) -> Result<(), String> {
    let p = dir()?.join(format!("{}.json", safe(&skill)));
    let mut v = read(&p).ok_or_else(|| format!("没有「{skill}」的验证题"))?;
    let qs = v.get_mut("questions").and_then(Value::as_array_mut).ok_or("题目格式不对")?;
    let now = chrono::Local::now().format("%Y-%m-%dT%H:%M:%S").to_string();
    if action == "confirm_all" {
        // 这个技能里待确认的题一次全确认 (老员工从历史出题后, 一个技能常有十几道)
        for q in qs.iter_mut().filter(|q| q.get("status").and_then(Value::as_str) == Some("pending")) {
            q["status"] = Value::from("confirmed");
            q["confirmed_at"] = Value::from(now.clone());
        }
        return write(&p, &v);
    }
    let idx = qs.iter().position(|q| q.get("id").and_then(Value::as_str) == Some(id.as_str()))
        .ok_or_else(|| format!("找不到这道题 ({id})"))?;
    match action.as_str() {
        "confirm" => {
            qs[idx]["status"] = Value::from("confirmed");
            qs[idx]["confirmed_at"] = Value::from(now);
        }
        "delete" => {
            qs.remove(idx);
        }
        "edit" => {
            let g = gold.map(|g| g.trim().to_string()).filter(|g| !g.is_empty()).ok_or("答案不能为空")?;
            let old = s(&qs[idx], "gold");
            if old != g {
                let hist = qs[idx].as_object_mut().ok_or("题目格式不对")?
                    .entry("history").or_insert_with(|| Value::Array(vec![]));
                if let Some(h) = hist.as_array_mut() {
                    h.push(serde_json::json!({"gold": old, "until": now, "by": "员工在首页改"}));
                }
                let o = qs[idx].as_object_mut().ok_or("题目格式不对")?;
                o.insert("gold".into(), Value::from(g));
                o.insert("updated_at".into(), Value::from(now));
                // 要点是照旧答案写的, 员工改了答案就只靠模型对着新答案判
                o.remove("must");
                o.remove("must_not");
            }
        }
        _ => return Err(format!("不认识的操作 {action}")),
    }
    write(&p, &v)
}

/// 老员工冷启动: 从历史对话里出待确认的题。几分钟, 费用走员工自己选的模型。
#[tauri::command]
pub async fn skill_evals_mine() -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(|| {
        let hermes = crate::services::catfish_paths::hermes_home().ok_or("找不到 hermes 目录")?;
        let venv = hermes.join("hermes-agent").join("venv");
        let py = if cfg!(windows) { venv.join("Scripts").join("python.exe") } else { venv.join("bin").join("python") };
        let script = hermes.join("plugins").join("catfish-xcatfish-user").join("skill_eval_gate.py");
        if !script.exists() {
            return Err("插件里没有 skill_eval_gate.py, 先重启一次小鲶".to_string());
        }
        let out = crate::services::process::background_command(py)
            .arg(&script)
            .arg("mine")
            .output()
            .map_err(|e| format!("启动失败: {e}"))?;
        if !out.status.success() {
            let err = String::from_utf8_lossy(&out.stderr);
            return Err(format!("出题失败: {}", err.lines().last().unwrap_or("").chars().take(200).collect::<String>()));
        }
        let text = String::from_utf8_lossy(&out.stdout);
        serde_json::from_str(text.lines().last().unwrap_or("{}")).map_err(|e| format!("结果看不懂: {e}"))
    })
    .await
    .map_err(|e| e.to_string())?
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn safe_keeps_skill_name() {
        assert_eq!(safe("productivity/zizhi-tongbao"), "zizhi-tongbao");
        assert_eq!(safe("a b"), "a_b");
    }
}
