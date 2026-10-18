//! 本机专家 (10/18, docs/EXPERT-PACK-PLAN.md 第一阶段)。
//!
//! 定义和编译在 tool-bridge (`expert_local.py`): 人设、工作流 / 技能、选路、核对、能力组 → SOUL + 工具清单 + skills 目录。
//! 这里负责落到 hermes profile (复用专家 Bot 的建 profile / 同步模型凭据 / 事务回滚):
//!   - profile id = `expert-<id>`, Companion 管理 (有 managed 标记), 注册成 `chat: true` —— 工作台「找谁」里出现, 网关托管
//!   - SOUL.md = 编译出来的人设 + 选路 + 核对
//!   - profile config.yaml: catfish-tools 只露算出来的工具 (`tools.include`)、skills 只看专家目录 (`external_dirs`)、
//!     profile 自己克隆来的 skill 全禁用 (`skills.disabled`)、hermes 自带的终端 / 写文件 / 自带浏览器等工具集关掉
//! 主小鲶的配置一行不动。

use std::path::{Path, PathBuf};
use std::time::Duration;

use serde::Serialize;
use serde_json::{json, Value as Json};
use serde_yaml::{Mapping, Value};

use super::expert_bots::{get_home, with_config_transaction};
use super::expert_bots_config::{atomic_write, companion_yaml_path, load_config, parse_yaml_root, read_optional};
use super::expert_bots_profiles::{
    create_profile, delete_managed_profile, profile_dir, run_hermes, update_profile_metadata, update_profile_soul,
};
use super::expert_bots_types::{BotRegistration, ModelPolicy, ModelPolicyMode};
use crate::services::tool_bridge_rpc;

const RPC_TIMEOUT: Duration = Duration::from_secs(60);
const PASSTHROUGH: &[&str] = &["catalog", "list", "get", "save"];

async fn rpc(method: &str, params: Json) -> Result<Json, String> {
    tool_bridge_rpc::call_with_timeout(method, params, RPC_TIMEOUT).await
}

/// 设置页读写定义 (catalog / list / get / save), 只放行这几个。
#[tauri::command]
pub async fn expert_local_call(action: String, params: Option<Json>) -> Result<Json, String> {
    if !PASSTHROUGH.contains(&action.as_str()) {
        return Err(format!("不支持的专家动作: {action}"));
    }
    rpc(&format!("pim/expert_{action}"), params.unwrap_or_else(|| json!({}))).await
}

fn str_list(v: &Json, key: &str) -> Vec<String> {
    v.get(key)
        .and_then(Json::as_array)
        .map(|a| a.iter().filter_map(|x| x.as_str().map(str::to_string)).collect())
        .unwrap_or_default()
}

fn mapping_at<'a>(root: &'a mut Value, key: &str) -> Result<&'a mut Mapping, String> {
    let m = root.as_mapping_mut().ok_or("profile config.yaml 不是 mapping")?;
    let k = Value::String(key.to_string());
    if !m.get(&k).map(Value::is_mapping).unwrap_or(false) {
        m.insert(k.clone(), Value::Mapping(Mapping::new()));
    }
    m.get_mut(&k).and_then(Value::as_mapping_mut).ok_or_else(|| format!("{key} 不是 mapping"))
}

fn yaml_list(items: &[String]) -> Value {
    Value::Sequence(items.iter().map(|s| Value::String(s.clone())).collect())
}

/// profile 自己 skills/ 下克隆来的 skill 名 (SKILL.md frontmatter 的 name, 没有就用目录名)。hermes-agent 禁不掉, 跳过。
fn local_skill_names(dir: &Path) -> Vec<String> {
    fn walk(d: &Path, out: &mut Vec<String>) {
        let Ok(rd) = std::fs::read_dir(d) else { return };
        for e in rd.flatten() {
            let p = e.path();
            if p.is_dir() {
                let md = p.join("SKILL.md");
                if md.is_file() {
                    let name = std::fs::read_to_string(&md)
                        .ok()
                        .and_then(|t| t.lines().find_map(|l| l.strip_prefix("name:").map(|v| v.trim().trim_matches('"').to_string())))
                        .filter(|n| !n.is_empty())
                        .unwrap_or_else(|| p.file_name().unwrap_or_default().to_string_lossy().into_owned());
                    out.push(name);
                } else {
                    walk(&p, out);
                }
            }
        }
    }
    let mut out = Vec::new();
    walk(&dir.join("skills"), &mut out);
    out.sort();
    out.dedup();
    out.retain(|n| n != "hermes-agent");
    out
}

/// 把专家的范围写进 profile config.yaml。返回改没改。
pub(crate) fn apply_scope(dir: &Path, compiled: &Json) -> Result<bool, String> {
    let path = dir.join("config.yaml");
    let raw = read_optional(&path)?;
    let mut root = parse_yaml_root(&raw, "profile config.yaml")?;
    let before = serde_yaml::to_string(&root).map_err(|e| e.to_string())?;

    let tools = str_list(compiled, "tools_include");
    let servers = mapping_at(&mut root, "mcp_servers")?;
    let key = Value::String("catfish-tools".into());
    let server = servers
        .get_mut(&key)
        .and_then(Value::as_mapping_mut)
        .ok_or("专家 profile 里没有 catfish-tools 工具服务 (克隆自主配置时应该有), 没法收工具")?;
    let mut filt = Mapping::new();
    filt.insert(Value::String("include".into()), yaml_list(&tools));
    server.insert(Value::String("tools".into()), Value::Mapping(filt));

    let skills_dir = compiled.get("skills_dir").and_then(Json::as_str).unwrap_or_default().to_string();
    let disabled_skills = local_skill_names(dir);
    let skills = mapping_at(&mut root, "skills")?;
    skills.insert(Value::String("external_dirs".into()), yaml_list(&[skills_dir]));
    skills.insert(Value::String("disabled".into()), yaml_list(&disabled_skills));

    let toolsets = str_list(compiled, "disabled_toolsets");
    mapping_at(&mut root, "agent")?.insert(Value::String("disabled_toolsets".into()), yaml_list(&toolsets));

    let after = serde_yaml::to_string(&root).map_err(|e| e.to_string())?;
    if after != before {
        atomic_write(&path, &after)?;
    }
    Ok(after != before)
}

fn policy_from(model: &str) -> ModelPolicy {
    if model.is_empty() || model == "inherit" {
        ModelPolicy::default()
    } else {
        ModelPolicy { mode: ModelPolicyMode::Fixed, model_id: Some(model.to_string()) }
    }
}

/// 存好定义之后点「生效」: 建 / 更新专家 profile, 写 SOUL 和范围, 网关托管它。
#[tauri::command]
pub async fn expert_local_apply(id: String) -> Result<Json, String> {
    let compiled = rpc("pim/expert_compile", json!({ "id": id })).await?;
    let pid = compiled.get("profile_id").and_then(Json::as_str).ok_or("编译结果缺 profile_id")?.to_string();
    let enabled = compiled.pointer("/expert/enabled").and_then(Json::as_bool).unwrap_or(true);
    let result = tokio::task::spawn_blocking(move || -> Result<Json, String> {
        let soul = compiled.get("soul").and_then(Json::as_str).unwrap_or_default().to_string();
        let name = compiled.get("display_name").and_then(Json::as_str).unwrap_or(&pid).to_string();
        let desc = compiled.get("description").and_then(Json::as_str).unwrap_or_default().to_string();
        let model = compiled.get("model").and_then(Json::as_str).unwrap_or("inherit").to_string();
        let mut was_served = false;
        with_config_transaction(|_, home, old_cfg| {
            let mut cfg = old_cfg.clone();
            let dir: PathBuf = if cfg.registrations.contains_key(&pid) {
                was_served = cfg.registrations.get(&pid).map(|r| r.enabled && r.chat).unwrap_or(false);
                profile_dir(home, &pid)?
            } else {
                let d = create_profile(home, &pid, "default", &desc)?;
                crate::services::hermes_profile_sync::sync_profile_from_default(home, &d);
                d
            };
            update_profile_metadata(&dir, Some(&name), Some(&desc))?;
            update_profile_soul(&dir, &soul)?;
            apply_scope(&dir, &compiled)?;
            cfg.registrations.insert(
                pid.clone(),
                BotRegistration { enabled, model_policy: policy_from(&model), chat: true },
            );
            Ok((cfg, ()))
        })?;
        // 已经在跑的专家改了 SOUL / 范围, 网关要重启才读到新的 (注册表没变时上面的事务不会重启)
        if was_served && enabled {
            run_hermes(&get_home()?, &["gateway", "restart"])?;
        }
        Ok(json!({ "ok": true, "profile_id": pid }))
    })
    .await
    .map_err(|e| format!("生效专家异常: {e}"))??;
    Ok(result)
}

/// 删专家: 注销 + 删它的 profile (只删 Companion 管的) + 删定义。
#[tauri::command]
pub async fn expert_local_remove(id: String) -> Result<(), String> {
    let pid = format!("expert-{id}");
    let pid2 = pid.clone();
    tokio::task::spawn_blocking(move || {
        with_config_transaction(|_, home, old_cfg| {
            let mut cfg = old_cfg.clone();
            cfg.registrations.remove(&pid2);
            cfg.bindings.retain(|_, v| v != &pid2);
            if profile_dir(home, &pid2).map(|d| d.exists()).unwrap_or(false) {
                delete_managed_profile(home, &pid2)?;
            }
            Ok((cfg, ()))
        })
    })
    .await
    .map_err(|e| format!("删除专家异常: {e}"))??;
    rpc("pim/expert_delete", json!({ "id": id })).await?;
    Ok(())
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ChatExpert {
    pub profile_id: String,
    pub expert_id: String,
    pub name: String,
    pub description: String,
    pub quick_prompts: Vec<String>,
    /// 固定模型的专家才有; None = 跟着员工当前选的模型 (发送时再取, 不在这里算死)
    pub fixed_model: Option<String>,
    pub ready: bool,
}

/// 工作台「找谁」: 已生效、启用的本机专家, 带解析好的模型。
#[tauri::command]
pub async fn expert_chat_list() -> Result<Vec<ChatExpert>, String> {
    let defs = rpc("pim/expert_list", json!({})).await.unwrap_or_else(|_| json!({ "experts": [] }));
    tokio::task::spawn_blocking(move || {
        let cfg = load_config(&companion_yaml_path()?)?;
        let home = get_home()?;
        let empty = vec![];
        let list = defs.get("experts").and_then(Json::as_array).unwrap_or(&empty);
        let mut out = Vec::new();
        for (pid, reg) in cfg.registrations.iter().filter(|(_, r)| r.chat && r.enabled) {
            let Some(d) = list.iter().find(|d| d.get("profile_id").and_then(Json::as_str) == Some(pid.as_str())) else {
                continue;
            };
            let dir = profile_dir(&home, pid)?;
            out.push(ChatExpert {
                profile_id: pid.clone(),
                expert_id: d.get("id").and_then(Json::as_str).unwrap_or_default().to_string(),
                name: d.get("name").and_then(Json::as_str).unwrap_or(pid).to_string(),
                description: d.get("description").and_then(Json::as_str).unwrap_or_default().to_string(),
                quick_prompts: str_list(d, "quick_prompts"),
                fixed_model: match reg.model_policy.mode {
                    ModelPolicyMode::Fixed => reg.model_policy.model_id.clone(),
                    ModelPolicyMode::InheritPicker => None,
                },
                ready: dir.join("SOUL.md").is_file() && dir.join("config.yaml").is_file(),
            });
        }
        Ok(out)
    })
    .await
    .map_err(|e| format!("读专家列表异常: {e}"))?
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn 范围写进profile配置_主配置别的不动() {
        let d = tempdir().unwrap();
        std::fs::create_dir_all(d.path().join("skills/productivity/notes")).unwrap();
        std::fs::write(d.path().join("skills/productivity/notes/SKILL.md"), "---\nname: notes\n---\n").unwrap();
        std::fs::create_dir_all(d.path().join("skills/hermes-agent")).unwrap();
        std::fs::write(d.path().join("skills/hermes-agent/SKILL.md"), "---\nname: hermes-agent\n---\n").unwrap();
        std::fs::write(
            d.path().join("config.yaml"),
            "model:\n  default: m\nmcp_servers:\n  catfish-tools:\n    command: py\nskills:\n  external_dirs:\n  - /all\n",
        )
        .unwrap();
        let compiled = json!({
            "tools_include": ["catfish_run_skill", "catfish_workflow"],
            "skills_dir": "/x/experts/demo/skills",
            "disabled_toolsets": ["terminal", "file"],
        });
        assert!(apply_scope(d.path(), &compiled).unwrap());
        let v: Value = serde_yaml::from_str(&std::fs::read_to_string(d.path().join("config.yaml")).unwrap()).unwrap();
        assert_eq!(v["mcp_servers"]["catfish-tools"]["command"], Value::String("py".into()));
        assert_eq!(v["mcp_servers"]["catfish-tools"]["tools"]["include"][1], Value::String("catfish_workflow".into()));
        assert_eq!(v["skills"]["external_dirs"][0], Value::String("/x/experts/demo/skills".into()));
        assert_eq!(v["skills"]["disabled"], yaml_list(&["notes".to_string()]));
        assert_eq!(v["agent"]["disabled_toolsets"][0], Value::String("terminal".into()));
        assert_eq!(v["model"]["default"], Value::String("m".into()));
        assert!(!apply_scope(d.path(), &compiled).unwrap(), "再写一遍没有变化");
    }

    #[test]
    fn 没有catfish工具服务就报错() {
        let d = tempdir().unwrap();
        std::fs::write(d.path().join("config.yaml"), "model:\n  default: m\n").unwrap();
        assert!(apply_scope(d.path(), &json!({"tools_include": []})).is_err());
    }

    #[test]
    fn 只放行设置页那几个动作() {
        assert!(PASSTHROUGH.contains(&"save"));
        assert!(!PASSTHROUGH.contains(&"compile"));
    }
}
