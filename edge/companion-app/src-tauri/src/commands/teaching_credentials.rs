//! 教学流程的本地凭据保存。
//!
//! 密码只通过 Tauri IPC 到 Rust，写入操作系统凭据库。这里绝不记录密码，
//! 也不把密码放进 shell 参数、配置文件或聊天消息。
//!
//! # 为什么要单独维护一份标签索引
//!
//! keyring 3.6.3 的 `Entry` 只有 new / set_password / get_password / get_secret /
//! get_attributes / update_attributes / delete_credential / get_credential ——
//! **没有任何枚举 API**。底层也不好绕: macOS 的 `security` 命令不支持按 service
//! 前缀搜索, `dump-keychain` 会把整个钥匙串倒出来还要用户授权, 拿来做个列表
//! 完全不成比例。
//!
//! 所以标签列表另存一份 `~/.catfish/teaching_credentials.json`。里面**只有标签、
//! 引用串和创建时间, 没有密码** —— 密码始终只在系统凭据库里。
//!
//! ## 索引不是真源
//!
//! 系统凭据库才是。索引可能跟它对不上:
//!   - 员工在「钥匙串访问」里手工删了某一条 → 索引留下残项
//!   - 换了台机器同步过来配置 → 索引在但凭据不在
//!
//! 处理原则:
//!   - **list 绝不去验证凭据还在不在**。验证要调 get_password, 而 macOS 每次
//!     都可能弹授权框 —— 打开个列表弹一串授权框是不能接受的。列表如实说明
//!     "这是本机记过的标签"。
//!   - **delete 对「凭据库里已经没有」容错**, 照样把索引清掉。不然残项永远删不掉。

use keyring::Entry;
use serde::{Deserialize, Serialize};
use std::path::PathBuf;

const ACCOUNT: &str = "catfish-teaching";
const SERVICE_PREFIX: &str = "catfish-teaching:";

fn service_name(label: &str) -> Result<String, String> {
    let label = label.trim();
    if label.is_empty() {
        return Err("请填写凭据名称".to_string());
    }
    if label.len() > 80 || label.chars().any(|c| c.is_control()) {
        return Err("凭据名称过长或包含不可用字符".to_string());
    }
    Ok(format!("{SERVICE_PREFIX}{label}"))
}

/// 引用串 —— 教学流程里 `secret_ref` 用的就是这个。
/// scheme 必须跟 tool-bridge 的 secret_resolver 对得上:
///   macOS   keychain:// → `security find-generic-password -s <target> -w`
///   Windows wincred://  → `keyring.get_password("catfish", <target>)`
fn reference_for(target: &str) -> String {
    #[cfg(target_os = "windows")]
    {
        format!("wincred://{target}")
    }
    #[cfg(not(target_os = "windows"))]
    {
        format!("keychain://{target}")
    }
}

/// 打开凭据库条目。service / user 两个位置的用法必须跟 secret_resolver 一致,
/// 写进去读不出来就白存了 (两边都对过: macOS 查 service 不带 -a, Windows 查
/// service="catfish" + user=target)。
fn entry_for(target: &str) -> Result<Entry, String> {
    #[cfg(target_os = "windows")]
    let entry = Entry::new("catfish", target);
    #[cfg(not(target_os = "windows"))]
    let entry = Entry::new(target, ACCOUNT);
    entry.map_err(|e| format!("无法打开本机凭据库: {e}"))
}

// ─── 标签索引 (只有标签, 没有密码) ────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TeachingCredential {
    /// 员工填的名称, 例 "教学网站"
    pub label: String,
    /// 教学流程里粘贴的那个 secret_ref
    pub reference: String,
    /// ISO-8601, 只用于列表排序和"这是什么时候存的"
    pub created_at: String,
}

fn index_path() -> Result<PathBuf, String> {
    let home = std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .ok_or("HOME 未设")?;
    let dir = PathBuf::from(home).join(".catfish");
    std::fs::create_dir_all(&dir).map_err(|e| format!("创建 ~/.catfish/ 失败: {e}"))?;
    Ok(dir.join("teaching_credentials.json"))
}

/// 读索引。读不出来一律当空 —— 索引坏掉不该让员工连保存都做不了,
/// 凭据本体在系统凭据库里, 没受影响。
fn read_index() -> Vec<TeachingCredential> {
    let Ok(path) = index_path() else {
        return Vec::new();
    };
    let Ok(text) = std::fs::read_to_string(&path) else {
        return Vec::new();
    };
    if text.trim().is_empty() {
        return Vec::new();
    }
    serde_json::from_str(&text).unwrap_or_else(|e| {
        log::warn!("teaching_credentials.json 解析失败, 当空处理: {e}");
        Vec::new()
    })
}

fn write_index(items: &[TeachingCredential]) -> Result<(), String> {
    let path = index_path()?;
    let text =
        serde_json::to_string_pretty(items).map_err(|e| format!("serialize 失败: {e}"))?;
    let tmp = path.with_extension("json.tmp");
    std::fs::write(&tmp, text).map_err(|e| format!("写 tmp 失败: {e}"))?;
    // 索引里没有密码, 0600 是保守做法不是必需 —— 但它确实泄露"这台机器上
    // 存过哪些系统的凭据", 没必要让同机其它用户看见。
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&tmp, std::fs::Permissions::from_mode(0o600));
    }
    std::fs::rename(&tmp, &path).map_err(|e| format!("rename atomic 失败: {e}"))?;
    Ok(())
}

/// 同名的换掉, 没有的追加。返回更新后的列表。
fn upsert(mut items: Vec<TeachingCredential>, item: TeachingCredential) -> Vec<TeachingCredential> {
    // 用 position() 先拿下标, 不用 iter_mut().find() ——
    // `if let Some(x) = v.iter_mut().find(..) { } else { v.push(..) }` 是借用
    // 检查过不去的经典写法: 可变借用被认为在 else 分支里仍然存活。
    match items.iter().position(|i| i.label == item.label) {
        Some(idx) => {
            // created_at 保留最早那次 —— 覆盖密码不算"新建"
            let created = items[idx].created_at.clone();
            items[idx] = TeachingCredential { created_at: created, ..item };
        }
        None => items.push(item),
    }
    items
}

// ─── Tauri 命令 ──────────────────────────────────────────────────

#[tauri::command]
pub fn teaching_credential_save(label: String, password: String) -> Result<String, String> {
    let target = service_name(&label)?;
    if password.is_empty() {
        return Err("请填写密码".to_string());
    }
    entry_for(&target)?
        .set_password(&password)
        .map_err(|e| format!("保存到本机凭据库失败: {e}"))?;

    let reference = reference_for(&target);
    // 索引写失败不能让"密码已经存进去了"这件事变成报错 —— 密码是主线,
    // 索引只是为了后面能列出来。写不进去就记一条日志, 员工仍然拿得到引用串。
    if let Err(e) = write_index(&upsert(
        read_index(),
        TeachingCredential {
            label: label.trim().to_string(),
            reference: reference.clone(),
            created_at: chrono::Utc::now().to_rfc3339(),
        },
    )) {
        log::warn!("凭据已存入系统凭据库, 但标签索引没写成: {e}");
    }
    Ok(reference)
}

/// 列出本机记过的标签。**不校验凭据库里是否还在** —— 见文件头。
#[tauri::command]
pub fn teaching_credential_list() -> Result<Vec<TeachingCredential>, String> {
    let mut items = read_index();
    items.sort_by(|a, b| b.created_at.cmp(&a.created_at)); // 新的在前
    Ok(items)
}

/// 删一条。凭据库里已经没有也算成功 —— 否则手工删过的残项永远清不掉。
#[tauri::command]
pub fn teaching_credential_delete(label: String) -> Result<(), String> {
    let target = service_name(&label)?;
    match entry_for(&target)?.delete_credential() {
        Ok(()) => {}
        Err(keyring::Error::NoEntry) => {
            log::info!("凭据库里已无此条 ({target}), 只清索引");
        }
        Err(e) => return Err(format!("从本机凭据库删除失败: {e}")),
    }
    let want = label.trim();
    let kept: Vec<TeachingCredential> =
        read_index().into_iter().filter(|i| i.label != want).collect();
    write_index(&kept)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn builds_namespaced_service_name() {
        assert_eq!(service_name("教学登录").unwrap(), "catfish-teaching:教学登录");
    }

    #[test]
    fn rejects_empty_name() {
        assert!(service_name("  ").is_err());
    }

    #[test]
    fn reference_scheme_matches_secret_resolver() {
        // tool-bridge/secret_resolver.py 只认这两个 scheme。这里拼错了,
        // 员工拿到的引用串在教学时会 resolve 失败, 而失败信息还会把人
        // 引导去命令行 —— 正好绕开这个界面存在的理由。
        let r = reference_for("catfish-teaching:教学登录");
        assert!(
            r.starts_with("keychain://") || r.starts_with("wincred://"),
            "引用串 scheme 不对: {r}"
        );
        assert!(r.ends_with("catfish-teaching:教学登录"));
    }

    fn cred(label: &str, created: &str) -> TeachingCredential {
        TeachingCredential {
            label: label.to_string(),
            reference: format!("keychain://catfish-teaching:{label}"),
            created_at: created.to_string(),
        }
    }

    #[test]
    fn upsert_replaces_same_label_and_keeps_created_at() {
        // 同名再存一次 = 覆盖密码, 不是新建。created_at 要留最早那次,
        // 不然列表里"最近存的"排序会骗人。
        let items = vec![cred("EIS", "2026-08-01T00:00:00Z")];
        let out = upsert(items, cred("EIS", "2026-08-10T00:00:00Z"));
        assert_eq!(out.len(), 1, "同名不该出现两条");
        assert_eq!(out[0].created_at, "2026-08-01T00:00:00Z");
    }

    #[test]
    fn upsert_appends_new_label() {
        let items = vec![cred("EIS", "2026-08-01T00:00:00Z")];
        let out = upsert(items, cred("OA", "2026-08-10T00:00:00Z"));
        assert_eq!(out.len(), 2);
        assert!(out.iter().any(|i| i.label == "OA"));
    }

    #[test]
    fn index_never_carries_a_password_field() {
        // 这条是红线的结构化表达: 索引文件是明文 JSON, 里面**只能**有
        // label / reference / createdAt 三个字段。哪天有人往
        // TeachingCredential 上加个 password, 这里会红。
        let json = serde_json::to_string(&cred("EIS", "2026-08-01T00:00:00Z")).unwrap();
        let v: serde_json::Value = serde_json::from_str(&json).unwrap();
        // 排序后再比 —— serde_json 默认用 BTreeMap, 键序是字典序不是声明序,
        // 按声明序断言会挂在一个跟本意无关的地方。
        let mut keys: Vec<&str> = v.as_object().unwrap().keys().map(|s| s.as_str()).collect();
        keys.sort_unstable();
        assert_eq!(
            keys,
            vec!["createdAt", "label", "reference"],
            "索引结构变了 —— 密码只能待在系统凭据库里, 绝不能进这个文件"
        );
    }
}
