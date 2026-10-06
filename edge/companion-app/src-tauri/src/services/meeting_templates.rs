//! 会议纪要的自定义模版 (10/3)。
//!
//! 一个模版 = 一个 JSON 文件 `~/.catfish/meeting-templates/<id>.json` (Windows 上是
//! `%USERPROFILE%\.catfish\meeting-templates`), 内容 {id, name, body, updated_at}。
//! body 是员工写的 Markdown: 标题层级 / 表格 / 括号里写每节要写什么, 可以用占位符
//! {{会议标题}} {{日期}} {{参会人}} {{参会人数}} {{时长}}。
//!
//! 10/3 下午: 也可以是单位定死的 Word / Excel 文件 (kind = docx / xlsx)。原文件存成
//! `<id>.docx|.xlsx` 跟 JSON 放一起, 生成时原样填空另存 (tool-bridge meeting_file_template.py)。
//! 上传时先让 tool-bridge 认一遍空 (认不出来就不收), 认出的空存进 JSON 给界面列出来核对。
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

/// 上传的 Word / Excel 模版上限 (一般几十 KB, 带公章图片的也就一两 MB)
pub const MAX_FILE_BYTES: usize = 10 * 1024 * 1024;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Template {
    pub id: String,
    pub name: String,
    /// markdown 模版的正文; 文件模版为空
    #[serde(default)]
    pub body: String,
    #[serde(default)]
    pub updated_at: String,
    /// "markdown" (缺省) / "docx" / "xlsx"
    #[serde(default = "markdown_kind")]
    pub kind: String,
    /// 文件模版: 员工上传时的文件名 (给界面看); 存盘的是 <id>.<kind>
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub original_name: Option<String>,
    /// 文件模版: tool-bridge 认出的要填的空 [{id, label, kind, columns?}], 给界面列出来
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub slots: Option<serde_json::Value>,
}

fn markdown_kind() -> String {
    "markdown".into()
}

impl Template {
    pub fn is_file(&self) -> bool {
        self.kind == "docx" || self.kind == "xlsx"
    }
}

/// 文件模版的原文件路径
pub fn file_path(home: &Path, t: &Template) -> PathBuf {
    dir(home).join(format!("{}.{}", t.id, t.kind))
}

/// 上传的文件名 → "docx" / "xlsx"; 老格式给人话
pub fn file_kind(filename: &str) -> Result<&'static str, String> {
    let lower = filename.to_lowercase();
    if lower.ends_with(".docx") {
        Ok("docx")
    } else if lower.ends_with(".xlsx") {
        Ok("xlsx")
    } else if lower.ends_with(".doc") || lower.ends_with(".xls") || lower.ends_with(".wps") || lower.ends_with(".et") {
        Err("老格式 (.doc / .xls / WPS) 读不了: 用 Word / Excel / WPS 打开, 另存为 .docx 或 .xlsx 再上传".into())
    } else {
        Err("纪要模版只支持 Word (.docx) 和 Excel (.xlsx)".into())
    }
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
fn check_name(home: &Path, id: Option<&str>, name: &str) -> Result<(), String> {
    if name.is_empty() {
        return Err("模版要起个名字".into());
    }
    if name.chars().count() > MAX_NAME_CHARS {
        return Err(format!("模版名字太长 (上限 {MAX_NAME_CHARS} 字)"));
    }
    if list(home).iter().any(|t| t.name == name && Some(t.id.as_str()) != id) {
        return Err(format!("已经有叫「{name}」的模版了"));
    }
    Ok(())
}

fn new_id() -> String {
    // 10/6: 原来只有毫秒时间戳, 同一毫秒里连存两个模版 (测试里就是) 会拿到同一个
    // id, 后者把前者盖掉 —— cargo test 全套跑时 3 次里红 1 次。加一个进程内递增
    // 序号 (两位, 取模), 同一毫秒内 100 个以内不撞; valid_id 允许纯数字后缀。
    use std::sync::atomic::{AtomicU32, Ordering};
    static SEQ: AtomicU32 = AtomicU32::new(0);
    let seq = SEQ.fetch_add(1, Ordering::Relaxed) % 100;
    format!("tpl_{}{seq:02}", chrono::Local::now().format("%Y%m%d%H%M%S%3f"))
}

fn write_json(home: &Path, tpl: &Template) -> Result<(), String> {
    let p = path_of(home, &tpl.id)?;
    std::fs::create_dir_all(dir(home)).map_err(|e| format!("建模版目录失败: {e}"))?;
    let tmp = p.with_extension("json.tmp");
    std::fs::write(&tmp, serde_json::to_string_pretty(tpl).map_err(|e| e.to_string())?)
        .map_err(|e| format!("保存模版失败: {e}"))?;
    std::fs::rename(&tmp, &p).map_err(|e| format!("保存模版失败: {e}"))
}

/// 文件模版第一步: 把上传的字节放到 <id>.<kind>.new (还没生效), 返回 (id, kind, 路径)。
/// 认空通过再 commit_file; 认不出就 discard_file, 旧模版 (替换时) 完全不受影响。
pub fn stage_file(home: &Path, id: Option<&str>, filename: &str, bytes: &[u8]) -> Result<(String, String, PathBuf), String> {
    let kind = file_kind(filename)?;
    if bytes.is_empty() || bytes.len() > MAX_FILE_BYTES {
        return Err(format!("模版文件大小不对 ({} 字节, 上限 10MB)", bytes.len()));
    }
    let id = match id {
        Some(id) if valid_id(id) => id.to_string(),
        Some(id) => return Err(format!("模版 id 不合法: {id}")),
        None => new_id(),
    };
    std::fs::create_dir_all(dir(home)).map_err(|e| format!("建模版目录失败: {e}"))?;
    let staged = dir(home).join(format!("{id}.new.{kind}"));
    std::fs::write(&staged, bytes).map_err(|e| format!("保存模版文件失败: {e}"))?;
    Ok((id, kind.to_string(), staged))
}

pub fn discard_file(staged: &Path) {
    let _ = std::fs::remove_file(staged);
}

/// 认空通过: 文件转正, 写 JSON。替换时换了格式 (docx → xlsx) 就删掉旧文件。
pub fn commit_file(
    home: &Path,
    id: &str,
    kind: &str,
    staged: &Path,
    name: &str,
    original_name: &str,
    slots: serde_json::Value,
) -> Result<Template, String> {
    let name = name.trim();
    if let Err(e) = check_name(home, Some(id), name) {
        discard_file(staged);
        return Err(e);
    }
    let old = load(home, id).ok();
    let tpl = Template {
        id: id.to_string(),
        name: name.to_string(),
        body: String::new(),
        updated_at: chrono::Local::now().to_rfc3339(),
        kind: kind.to_string(),
        original_name: Some(original_name.to_string()),
        slots: Some(slots),
    };
    let dst = file_path(home, &tpl);
    std::fs::rename(staged, &dst).map_err(|e| format!("保存模版文件失败: {e}"))?;
    if let Some(old) = old.filter(|o| o.is_file() && o.kind != tpl.kind) {
        let _ = std::fs::remove_file(file_path(home, &old));
    }
    write_json(home, &tpl)?;
    Ok(tpl)
}

pub fn save(home: &Path, id: Option<&str>, name: &str, body: &str) -> Result<Template, String> {
    let name = name.trim();
    let body = body.trim();
    check_name(home, id, name)?;
    if id.is_some_and(|i| load(home, i).is_ok_and(|t| t.is_file())) {
        return Err("Word / Excel 模版改内容请直接改文件再「替换文件」".into());
    }
    if body.is_empty() {
        return Err("模版内容是空的".into());
    }
    if body.chars().count() > MAX_BODY_CHARS {
        return Err(format!("模版太长 ({} 字, 上限 {MAX_BODY_CHARS})", body.chars().count()));
    }
    let id = match id {
        Some(id) => id.to_string(),
        None => new_id(),
    };
    path_of(home, &id)?;
    let tpl = Template {
        id,
        name: name.to_string(),
        body: body.to_string(),
        updated_at: chrono::Local::now().to_rfc3339(),
        kind: markdown_kind(),
        original_name: None,
        slots: None,
    };
    write_json(home, &tpl)?;
    Ok(tpl)
}

pub fn delete(home: &Path, id: &str) -> Result<(), String> {
    let p = path_of(home, id)?;
    if let Ok(t) = load(home, id) {
        if t.is_file() {
            let _ = std::fs::remove_file(file_path(home, &t));
        }
    }
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
    fn file_template_stage_commit_replace_delete() {
        let home = tempfile::tempdir().unwrap();
        let h = home.path();
        let slots = serde_json::json!([{"id": "s1", "label": "会议名称", "kind": "text"}]);
        let (id, kind, staged) = stage_file(h, None, "公司纪要.DOCX", b"PK-docx").unwrap();
        assert_eq!(kind, "docx");
        let t = commit_file(h, &id, &kind, &staged, "公司纪要表", "公司纪要.DOCX", slots.clone()).unwrap();
        assert!(t.is_file() && file_path(h, &t).is_file() && !staged.exists());
        assert_eq!(list(h)[0].slots, Some(slots.clone()));
        // 不能当文字模版改
        assert!(save(h, Some(&id), "公司纪要表", "# x").unwrap_err().contains("替换文件"));
        // 替换成 Excel: 旧 docx 删掉
        let (_, kind2, staged2) = stage_file(h, Some(&id), "新表.xlsx", b"PK-xlsx").unwrap();
        let t2 = commit_file(h, &id, &kind2, &staged2, "公司纪要表", "新表.xlsx", slots).unwrap();
        assert!(file_path(h, &t2).is_file() && !file_path(h, &t).exists());
        delete(h, &id).unwrap();
        assert!(!file_path(h, &t2).exists() && list(h).is_empty());
    }

    #[test]
    fn file_template_rejects_old_formats_and_sizes() {
        let home = tempfile::tempdir().unwrap();
        let h = home.path();
        assert!(stage_file(h, None, "纪要.doc", b"x").unwrap_err().contains("另存为"));
        assert!(stage_file(h, None, "纪要.pdf", b"x").unwrap_err().contains("只支持"));
        assert!(stage_file(h, None, "纪要.docx", b"").is_err());
        assert!(stage_file(h, Some("../x"), "纪要.docx", b"x").is_err());
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
