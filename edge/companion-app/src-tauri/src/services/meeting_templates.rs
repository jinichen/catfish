//! 会议纪要的自定义模版 (10/3)。
//!
//! 一个模版 = 一个 JSON 文件 `~/.catfish/meeting-templates/<id>.json` (Windows 上是
//! `%USERPROFILE%\.catfish\meeting-templates`), 内容 {id, name, body, updated_at}。
//! body 是员工写的 Markdown: 标题层级 / 表格 / 括号里写每节要写什么, 可以用占位符
//! {{会议标题}} {{日期}} {{参会人}} {{参会人数}} {{时长}}。
//!
//! 缺省模版 (`default`) 不落盘、不能改: 就是原来那份"摘要 / 决议 / 待定问题 / 待办"。
//! 生成时把模版正文随 RPC 传给 tool-bridge (meeting_minutes_template.py), 那边不用知道
//! 文件在哪。

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

pub const DEFAULT_ID: &str = "default";
pub const MAX_NAME_CHARS: usize = 40;
/// 跟 meeting_minutes_template.MAX_TEMPLATE_CHARS 一致
pub const MAX_BODY_CHARS: usize = 8000;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Template {
    pub id: String,
    pub name: String,
    pub body: String,
    #[serde(default)]
    pub updated_at: String,
}

pub fn dir(home: &Path) -> PathBuf {
    home.join(".catfish").join("meeting-templates")
}

/// tpl_ 开头 + 字母数字 / - / _, 进不了路径穿越。
pub fn valid_id(id: &str) -> bool {
    id.len() <= 48
        && id.strip_prefix("tpl_").is_some_and(|rest| {
            !rest.is_empty() && rest.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
        })
}

fn path_of(home: &Path, id: &str) -> Result<PathBuf, String> {
    if !valid_id(id) {
        return Err(format!("模版 id 不合法: {id}"));
    }
    Ok(dir(home).join(format!("{id}.json")))
}

/// 按名字排序; 坏文件跳过 (不因为一个坏文件整个列表出不来)。
pub fn list(home: &Path) -> Vec<Template> {
    let mut out: Vec<Template> = std::fs::read_dir(dir(home))
        .into_iter()
        .flatten()
        .flatten()
        .filter(|e| e.path().extension().is_some_and(|x| x == "json"))
        .filter_map(|e| std::fs::read_to_string(e.path()).ok())
        .filter_map(|t| serde_json::from_str::<Template>(&t).ok())
        .filter(|t| valid_id(&t.id))
        .collect();
    out.sort_by(|a, b| a.name.cmp(&b.name));
    out
}

pub fn load(home: &Path, id: &str) -> Result<Template, String> {
    let p = path_of(home, id)?;
    let text = std::fs::read_to_string(&p).map_err(|_| format!("纪要模版不存在 (可能被删了): {id}"))?;
    serde_json::from_str(&text).map_err(|e| format!("纪要模版文件坏了: {e}"))
}

/// 新建 (id = None) 或覆盖。返回存好的模版。
pub fn save(home: &Path, id: Option<&str>, name: &str, body: &str) -> Result<Template, String> {
    let name = name.trim();
    let body = body.trim();
    if name.is_empty() {
        return Err("模版要起个名字".into());
    }
    if name.chars().count() > MAX_NAME_CHARS {
        return Err(format!("模版名字太长 (上限 {MAX_NAME_CHARS} 字)"));
    }
    if body.is_empty() {
        return Err("模版内容是空的".into());
    }
    if body.chars().count() > MAX_BODY_CHARS {
        return Err(format!("模版太长 ({} 字, 上限 {MAX_BODY_CHARS})", body.chars().count()));
    }
    if list(home).iter().any(|t| t.name == name && Some(t.id.as_str()) != id) {
        return Err(format!("已经有叫「{name}」的模版了"));
    }
    let id = match id {
        Some(id) => id.to_string(),
        None => format!("tpl_{}", chrono::Local::now().format("%Y%m%d%H%M%S%3f")),
    };
    let tpl = Template {
        id: id.clone(),
        name: name.to_string(),
        body: body.to_string(),
        updated_at: chrono::Local::now().to_rfc3339(),
    };
    let p = path_of(home, &id)?;
    std::fs::create_dir_all(dir(home)).map_err(|e| format!("建模版目录失败: {e}"))?;
    let tmp = p.with_extension("json.tmp");
    std::fs::write(&tmp, serde_json::to_string_pretty(&tpl).map_err(|e| e.to_string())?)
        .map_err(|e| format!("保存模版失败: {e}"))?;
    std::fs::rename(&tmp, &p).map_err(|e| format!("保存模版失败: {e}"))?;
    Ok(tpl)
}

pub fn delete(home: &Path, id: &str) -> Result<(), String> {
    let p = path_of(home, id)?;
    match std::fs::remove_file(&p) {
        Ok(()) => Ok(()),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(e) => Err(format!("删模版失败: {e}")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn save_list_load_update_delete() {
        let home = tempfile::tempdir().unwrap();
        let h = home.path();
        assert!(list(h).is_empty());
        let a = save(h, None, " 党委会纪要 ", "# {{会议标题}}\n## 一、议题").unwrap();
        assert!(valid_id(&a.id) && a.name == "党委会纪要");
        let b = save(h, None, "周例会", "## 本周进展").unwrap();
        assert_eq!(list(h).iter().map(|t| t.name.as_str()).collect::<Vec<_>>(), vec!["党委会纪要", "周例会"]); // 按名字 (码点) 排
        let a2 = save(h, Some(&a.id), "党委会纪要", "# 改过了").unwrap();
        assert_eq!(load(h, &a.id).unwrap().body, "# 改过了");
        assert_eq!(a2.id, a.id);
        delete(h, &b.id).unwrap();
        delete(h, &b.id).unwrap(); // 删两次不报错
        assert_eq!(list(h).len(), 1);
    }

    #[test]
    fn rejects_bad_input() {
        let home = tempfile::tempdir().unwrap();
        let h = home.path();
        assert!(save(h, None, "", "x").unwrap_err().contains("名字"));
        assert!(save(h, None, "a", "  ").unwrap_err().contains("空"));
        assert!(save(h, None, "a", &"字".repeat(MAX_BODY_CHARS + 1)).unwrap_err().contains("太长"));
        save(h, None, "重名", "x").unwrap();
        assert!(save(h, None, "重名", "y").unwrap_err().contains("已经有"));
        for bad in ["../x", "tpl_", "tpl_../../etc", "default", "tpl_a/b"] {
            assert!(!valid_id(bad), "{bad}");
            assert!(load(h, bad).is_err() && delete(h, bad).is_err());
        }
    }

    #[test]
    fn broken_file_is_skipped_not_fatal() {
        let home = tempfile::tempdir().unwrap();
        let h = home.path();
        save(h, None, "好的", "x").unwrap();
        std::fs::write(dir(h).join("tpl_bad.json"), "{not json").unwrap();
        assert_eq!(list(h).len(), 1);
    }
}
