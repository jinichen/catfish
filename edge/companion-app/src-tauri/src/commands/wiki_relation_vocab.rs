//! 关系类型: 内置词表 + 员工自定义 (10/23)。
//!
//! 内置词表是 edge/contracts/wiki_relation_vocab.json (编译期嵌进来, 见 wiki_frontmatter)。
//! 9/17 起关系词表闭合 —— 表外词归「关联」, 因为模型会自造 (持有主体/采用口径/不同条目…)。
//! 但员工自己的业务里确实会有词表外、又常用的关系, 原来只能等改代码发版。
//!
//! 现在: 员工在知识体系「关系类型」里**先登记** (词 + 一句话意思), 登记过的词:
//!   · 写入归一 (wiki_frontmatter::canon_relation) 认它, 不再归「关联」
//!   · 前端下拉 / 小鲶建议 / 蒸馏插件 / tool-bridge / 主聊天都读同一个文件
//! 没登记的词照旧归「关联」—— 自造词不会回来。
//!
//! 文件: ~/.catfish/wiki_relation_vocab.custom.json  `{"relations": {"词": "一句话意思"}}`

use serde::Serialize;
use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};

use super::wiki_read::catfish_home;

pub(crate) const CUSTOM_FILE: &str = "wiki_relation_vocab.custom.json";
const MAX_NAME_CHARS: usize = 6;
const MAX_MEANING_CHARS: usize = 60;

fn custom_path(home: &Path) -> PathBuf {
    home.join(CUSTOM_FILE)
}

/// 员工登记过的自定义关系 (词 → 意思)。文件没有 / 坏了 → 空表 (不影响内置词)。
pub(crate) fn load_custom_at(home: &Path) -> BTreeMap<String, String> {
    let Ok(text) = fs::read_to_string(custom_path(home)) else {
        return BTreeMap::new();
    };
    serde_json::from_str::<serde_json::Value>(&text)
        .ok()
        .and_then(|v| v.get("relations").cloned())
        .and_then(|v| serde_json::from_value::<BTreeMap<String, String>>(v).ok())
        .unwrap_or_default()
}

pub(crate) fn load_custom() -> BTreeMap<String, String> {
    catfish_home().map(|home| load_custom_at(&home)).unwrap_or_default()
}

fn save_custom_at(home: &Path, map: &BTreeMap<String, String>) -> Result<(), String> {
    fs::create_dir_all(home).map_err(|e| e.to_string())?;
    let text = serde_json::to_string_pretty(&serde_json::json!({ "relations": map })).map_err(|e| e.to_string())?;
    let tmp = custom_path(home).with_extension("json.tmp");
    fs::write(&tmp, text).map_err(|e| e.to_string())?;
    fs::rename(&tmp, custom_path(home)).map_err(|e| e.to_string())
}

fn builtin() -> (BTreeMap<String, String>, BTreeMap<String, String>) {
    let v: serde_json::Value =
        serde_json::from_str(super::wiki_frontmatter::RELATION_VOCAB_JSON).unwrap_or_default();
    let rel = serde_json::from_value(v["relations"].clone()).unwrap_or_default();
    let aliases = serde_json::from_value(v["aliases"].clone()).unwrap_or_default();
    (rel, aliases)
}

/// 新词能不能登记: 1–6 个字, 不含空白和标点, 不跟内置词 / 内置别名 / 兜底词 / 已登记的重复。
pub(crate) fn validate_new(name: &str, meaning: &str, custom: &BTreeMap<String, String>) -> Result<(), String> {
    let n = name.trim();
    let count = n.chars().count();
    if count == 0 || count > MAX_NAME_CHARS {
        return Err(format!("关系词要 1–{MAX_NAME_CHARS} 个字"));
    }
    if n.chars().any(|c| c.is_whitespace() || c.is_ascii_punctuation() || "，。、；：「」『』（）".contains(c)) {
        return Err("关系词里不要有空格或标点".into());
    }
    let (rel, aliases) = builtin();
    if rel.contains_key(n) || n == super::wiki_frontmatter::RELATION_FALLBACK {
        return Err(format!("「{n}」已经是内置关系"));
    }
    if let Some(canon) = aliases.get(n) {
        return Err(format!("「{n}」会被当成内置关系「{canon}」, 直接用「{canon}」"));
    }
    if custom.contains_key(n) {
        return Err(format!("「{n}」已经登记过了"));
    }
    let m = meaning.trim();
    if m.is_empty() || m.chars().count() > MAX_MEANING_CHARS {
        return Err(format!("意思写一句话 (1–{MAX_MEANING_CHARS} 字), 例: 本条目正在申请对方 (公司→证书)"));
    }
    Ok(())
}

#[derive(Serialize)]
pub struct RelationTypeInfo {
    pub name: String,
    pub meaning: String,
}

#[derive(Serialize)]
pub struct RelationVocab {
    pub builtin: Vec<RelationTypeInfo>,
    pub custom: Vec<RelationTypeInfo>,
    pub fallback: String,
}

#[tauri::command]
pub fn wiki_relation_vocab_list() -> RelationVocab {
    let (rel, _) = builtin();
    let order: Vec<String> = serde_json::from_str::<serde_json::Value>(super::wiki_frontmatter::RELATION_VOCAB_JSON)
        .ok()
        .and_then(|v| v["relations"].as_object().map(|o| o.keys().cloned().collect()))
        .unwrap_or_default();
    RelationVocab {
        // 按词表文件里的顺序 (BTreeMap 会按字典序打乱)
        builtin: order
            .into_iter()
            .map(|name| RelationTypeInfo { meaning: rel.get(&name).cloned().unwrap_or_default(), name })
            .collect(),
        custom: load_custom().into_iter().map(|(name, meaning)| RelationTypeInfo { name, meaning }).collect(),
        fallback: super::wiki_frontmatter::RELATION_FALLBACK.to_string(),
    }
}

#[tauri::command]
pub fn wiki_relation_vocab_add(name: String, meaning: String) -> Result<RelationVocab, String> {
    let home = catfish_home()?;
    let mut custom = load_custom_at(&home);
    validate_new(&name, &meaning, &custom)?;
    custom.insert(name.trim().to_string(), meaning.trim().to_string());
    save_custom_at(&home, &custom)?;
    Ok(wiki_relation_vocab_list())
}

/// 删掉一个自定义词。已经写着这个词的关系留在文件里, 但下次保存那个条目时会归成「关联」(前端删除前会提示)。
#[tauri::command]
pub fn wiki_relation_vocab_remove(name: String) -> Result<RelationVocab, String> {
    let home = catfish_home()?;
    let mut custom = load_custom_at(&home);
    if custom.remove(name.trim()).is_none() {
        return Err(format!("「{name}」不是自定义关系"));
    }
    save_custom_at(&home, &custom)?;
    Ok(wiki_relation_vocab_list())
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    #[test]
    fn 登记校验() {
        let custom = BTreeMap::from([("对口".to_string(), "x".to_string())]);
        assert!(validate_new("在办", "x", &custom).unwrap_err().contains("内置"));
        assert!(validate_new("办理中", "x", &custom).unwrap_err().contains("在办"));
        assert!(validate_new("关联", "x", &custom).is_err());
        assert!(validate_new("对口", "x", &custom).unwrap_err().contains("登记过"));
        assert!(validate_new("太长的关系词啊", "x", &custom).is_err());
        assert!(validate_new("挂 靠", "x", &custom).is_err());
        assert!(validate_new("挂靠", "", &custom).is_err());
        assert!(validate_new("挂靠", "本条目挂靠在对方名下 (人→公司)", &custom).is_ok());
    }

    #[test]
    fn 存取往返_坏文件当空表() {
        let home = TempDir::new().unwrap();
        assert!(load_custom_at(home.path()).is_empty());
        let map = BTreeMap::from([("挂靠".to_string(), "本条目挂靠在对方名下".to_string())]);
        save_custom_at(home.path(), &map).unwrap();
        assert_eq!(load_custom_at(home.path()), map);
        fs::write(custom_path(home.path()), "{not json").unwrap();
        assert!(load_custom_at(home.path()).is_empty());
    }
}
