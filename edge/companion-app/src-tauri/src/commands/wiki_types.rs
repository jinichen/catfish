//! 11/07 知识库「类型定义」(属性 / 状态 / 类型上的行动 / 整理属性)。定义和读写都在 tool-bridge
//! (wiki_types.py, 定义文件 edge/contracts/wiki_type_schema.json), 这里只转发, 再给行动标上本机技能装没装。

use serde_json::{json, Value};
use std::time::Duration;

use crate::services::tool_bridge_rpc::call_with_timeout;

fn ok_or_err(v: Value, fallback: &str) -> Result<Value, String> {
    if v.get("ok").and_then(Value::as_bool) == Some(false) {
        return Err(v.get("error").and_then(Value::as_str).unwrap_or(fallback).to_string());
    }
    Ok(v)
}

/// 行动要的技能本机装没装 / 能不能直接调 —— 跟单页关联的行动 (wiki_actions::binding) 同一套判断。
/// 不要技能的行动 (例: 到期提醒, 小鲶用自带工具做) 总是可用。
fn annotate_actions(info: &mut Value) {
    let roots = super::wiki_actions::skill_roots();
    let Some(actions) = info.get_mut("actions").and_then(Value::as_array_mut) else { return };
    for action in actions {
        let skill = action.get("skill_path").and_then(Value::as_str).unwrap_or("").trim().to_string();
        let (available, reason, dir, runnable) = if skill.is_empty() {
            (true, Value::Null, Value::Null, false)
        } else {
            match super::wiki_actions::find_skill_dir(&roots, &skill) {
                Some(d) => {
                    let runnable = d.join("script.py").is_file() || d.join("workflow.yaml").is_file();
                    (true, Value::Null, json!(d.to_string_lossy()), runnable)
                }
                None => (false, json!("本机没装这个技能"), Value::Null, false),
            }
        };
        if let Some(obj) = action.as_object_mut() {
            obj.insert("available".into(), json!(available));
            obj.insert("reason".into(), reason);
            obj.insert("skill_dir".into(), dir);
            obj.insert("runnable".into(), json!(runnable));
        }
    }
}

/// 类型定义页: 每一类的属性 / 行动 (标内置还是自己加的、藏没藏) + 知识库里还没定义的细分类型。
#[tauri::command(rename_all = "camelCase")]
pub async fn wiki_type_defs() -> Result<Value, String> {
    ok_or_err(call_with_timeout("wiki/type_defs", json!({}), Duration::from_secs(30)).await?, "读类型定义失败")
}

/// 类型定义页改一处 (op + 参数, 见 tool-bridge wiki_type_defs.apply), 只写本机 ~/.catfish/wiki-type-schema.json。
#[tauri::command(rename_all = "camelCase")]
pub async fn wiki_type_defs_apply(change: Value) -> Result<Value, String> {
    ok_or_err(call_with_timeout("wiki/type_defs_apply", change, Duration::from_secs(30)).await?, "没改成")
}

/// 定义了哪些类型 ([{key, label}]) —— 「整理属性」按类型列入口。
#[tauri::command(rename_all = "camelCase")]
pub async fn wiki_types_list() -> Result<Value, String> {
    ok_or_err(call_with_timeout("wiki/types", json!({}), Duration::from_secs(20)).await?, "读类型定义失败")
}

/// 一条知识的类型信息: 属性 (含没填的)、自动算的状态、这一类都有的行动。没有类型定义时 typed=false。
#[tauri::command(rename_all = "camelCase")]
pub async fn wiki_type_entry(rel_path: String) -> Result<Value, String> {
    let mut v = ok_or_err(
        call_with_timeout("wiki/type_entry", json!({ "rel_path": rel_path }), Duration::from_secs(20)).await?,
        "读类型信息失败",
    )?;
    annotate_actions(&mut v);
    Ok(v)
}

/// 「整理属性」: 列出这一类能补 / 能改的属性建议 (不写)。要请小鲶读的条目多时要一两分钟。
#[tauri::command(rename_all = "camelCase")]
pub async fn wiki_props_propose(type_key: String, use_model: Option<bool>) -> Result<Value, String> {
    let params = json!({ "type": type_key, "use_model": use_model.unwrap_or(true) });
    ok_or_err(call_with_timeout("wiki/props_propose", params, Duration::from_secs(600)).await?, "整理属性失败")
}

/// 11/08 界面建页 / 改关系前的校验 (tool-bridge wiki_guard.check): 相似名 / 关系类型不对 / 引用不存在的页。
/// tool-bridge 没起来时返 Err, 前端照常保存 (校验是帮忙, 不是闸)。
#[tauri::command(rename_all = "camelCase")]
pub async fn wiki_guard_check(params: Value) -> Result<Value, String> {
    call_with_timeout("wiki/guard_check", params, Duration::from_secs(20)).await
}

/// 员工过目改好的属性写进条目 (每个文件先整份备份到 ~/.catfish/wiki/.props-backups/)。
#[tauri::command(rename_all = "camelCase")]
pub async fn wiki_props_apply(items: Value) -> Result<Value, String> {
    call_with_timeout("wiki/props_apply", json!({ "items": items }), Duration::from_secs(60)).await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn actions_without_skill_are_available_and_missing_skill_is_grayed() {
        let mut info = json!({ "actions": [
            { "id": "type.cert.expiry-reminder", "skill_path": "" },
            { "id": "type.cert.x", "skill_path": "nobody/never-installed-skill-zz" },
        ]});
        annotate_actions(&mut info);
        let a = &info["actions"];
        assert_eq!(a[0]["available"], json!(true));
        assert_eq!(a[0]["runnable"], json!(false));
        assert_eq!(a[1]["available"], json!(false));
        assert_eq!(a[1]["reason"], json!("本机没装这个技能"));
    }
}
