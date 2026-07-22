//! BL-WECHAT-CATFISH-BIND v1 (5/26 鸿波): WeChat ↔ catfish-gateway 员工绑定状态.
//!
//! # 背景
//!
//! 当 hermes ClawBot 把一条 WeChat 消息转发给 catfish-gateway 时, run_agent.py
//! 决定 `X-Catfish-User` header 的值（每个 WeChat 用户走自己的员工身份, 防止
//! 100 个 WeChat 用户把 quota / memory / audit 串到同一个员工虚拟号上, P0 隐私
//! 红线). 解析顺序:
//!
//!   1. `gateway/pairing.PairingStore.get_email(platform, openid)` — admin 用
//!      `hermes pairing approve <platform> <code> --email alice@company.com`
//!      显式绑定后, 直接用真员工 email.
//!   2. user_id 已经是 email shape → 直接用.
//!   3. 合成 `<openid>@im.wechat` — platform-only 命名空间, 跟真员工隔离.
//!   4. env `CATFISH_DEFAULT_USER` 兜底 (CLI 等无 platform user 场景).
//!
//! 这个 Tauri 命令负责把第 1 步的"绑定表"读出来给 Companion Dashboard 展示, 让
//! 鲶鱼界面用户可以一眼看见: 哪些 WeChat openid 已经绑定哪个员工 email, 哪些没
//! 绑(走合成).
//!
//! # 数据来源
//!
//! `~/.hermes/platforms/pairing/<platform>-approved.json`. 文件 schema (PairingStore
//! 写入):
//!   ```json
//!   {
//!     "<openid>": {
//!       "user_name": "Alice",
//!       "approved_at": 1716700000.0,
//!       "catfish_email": "alice@company.com",   // 可选 (v1 新加字段)
//!       "email_bound_at": 1716700123.0          // 可选 (v1 新加)
//!     }
//!   }
//!   ```
//!
//! # 边界
//!
//! Companion 跑在员工 mac → 读自己 `~/.hermes/` 的 JSON 文件天然合规
//! (中央 0 字节 红线不涉及, 这里就是员工本机).

use serde::Serialize;
use std::fs;
use std::path::{Path, PathBuf};

/// 一条 (platform, openid) → 员工 email 的绑定记录.
#[derive(Debug, Serialize)]
pub struct WeChatBindingEntry {
    /// 平台名 (wechat, telegram, feishu, ...) — 固定小写.
    pub platform: String,
    /// 平台侧用户 id (微信叫 openid, telegram 叫 user_id, 其他平台叫 user_id).
    pub user_id: String,
    /// 平台侧的昵称, 没有就是空字符串.
    pub user_name: String,
    /// 已经绑定的 catfish 员工 email; 没绑定就是 None (Companion 提示 "未绑定, 走合成身份").
    pub catfish_email: Option<String>,
    /// 通过 pairing approve 进入 approved 表的 unix 时间戳 (秒, float). 0 表示文件缺字段.
    pub approved_at: f64,
    /// 给 email 绑定的 unix 时间戳; catfish_email = None 时这里也是 None.
    pub email_bound_at: Option<f64>,
}

/// Companion 调一次拿全量绑定状态.
#[derive(Debug, Serialize)]
pub struct WeChatBindingStatus {
    /// 所有平台合并后的绑定列表, 按 platform 升序 + approved_at 降序.
    pub entries: Vec<WeChatBindingEntry>,
    /// 总计已批准的用户数 (跨平台).
    pub total_approved: usize,
    /// 总计已绑定真员工 email 的用户数 (= entries.iter().filter(catfish_email.is_some()).count()).
    pub total_bound: usize,
    /// `~/.hermes/platforms/pairing/` 目录是否存在.
    /// false 通常意味着员工还没跑过任何 hermes pairing 流程 — UI 应提示一下.
    pub pairing_dir_exists: bool,
}

fn pairing_dir() -> Option<PathBuf> {
    let home = crate::util::paths::home_env().ok()?;
    Some(PathBuf::from(home).join(".hermes/platforms/pairing"))
}

/// 列出 pairing_dir 下所有 `<platform>-approved.json` 的 platform 名.
/// 跳过 `_rate_limits.json` 等下划线开头的内部文件.
fn list_platforms(dir: &Path) -> Vec<String> {
    let mut out = Vec::new();
    let Ok(rd) = fs::read_dir(dir) else {
        return out;
    };
    for ent in rd.flatten() {
        let Some(name) = ent.file_name().to_str().map(String::from) else {
            continue;
        };
        if name.starts_with('_') {
            continue;
        }
        let Some(stripped) = name.strip_suffix("-approved.json") else {
            continue;
        };
        if !stripped.is_empty() {
            out.push(stripped.to_string());
        }
    }
    out.sort();
    out
}

/// 解析单个平台的 approved.json 为 entries.
fn read_platform_approved(dir: &Path, platform: &str) -> Vec<WeChatBindingEntry> {
    let mut out = Vec::new();
    let path = dir.join(format!("{}-approved.json", platform));
    let Ok(text) = fs::read_to_string(&path) else {
        return out;
    };
    let Ok(v) = serde_json::from_str::<serde_json::Value>(&text) else {
        return out;
    };
    let Some(obj) = v.as_object() else {
        return out;
    };
    for (uid, info) in obj.iter() {
        let user_name = info
            .get("user_name")
            .and_then(|x| x.as_str())
            .unwrap_or("")
            .to_string();
        let approved_at = info
            .get("approved_at")
            .and_then(|x| x.as_f64())
            .unwrap_or(0.0);
        let catfish_email = info
            .get("catfish_email")
            .and_then(|x| x.as_str())
            .filter(|s| !s.is_empty())
            .map(String::from);
        let email_bound_at = info.get("email_bound_at").and_then(|x| x.as_f64());
        out.push(WeChatBindingEntry {
            platform: platform.to_string(),
            user_id: uid.clone(),
            user_name,
            catfish_email,
            approved_at,
            email_bound_at,
        });
    }
    out
}

/// Tauri 命令: 返回 hermes pairing 表的当前绑定状态.
///
/// 永远不会 panic — IO 错误一律返回空表 + pairing_dir_exists=false.
#[tauri::command]
pub fn wechat_binding_status() -> WeChatBindingStatus {
    let Some(dir) = pairing_dir() else {
        return WeChatBindingStatus {
            entries: Vec::new(),
            total_approved: 0,
            total_bound: 0,
            pairing_dir_exists: false,
        };
    };
    let exists = dir.exists();
    if !exists {
        return WeChatBindingStatus {
            entries: Vec::new(),
            total_approved: 0,
            total_bound: 0,
            pairing_dir_exists: false,
        };
    }

    let mut entries: Vec<WeChatBindingEntry> = list_platforms(&dir)
        .iter()
        .flat_map(|p| read_platform_approved(&dir, p))
        .collect();

    // platform 升序 + approved_at 降序 — UI 展示稳定排序.
    entries.sort_by(|a, b| {
        a.platform.cmp(&b.platform).then_with(|| {
            b.approved_at
                .partial_cmp(&a.approved_at)
                .unwrap_or(std::cmp::Ordering::Equal)
        })
    });

    let total_approved = entries.len();
    let total_bound = entries.iter().filter(|e| e.catfish_email.is_some()).count();
    WeChatBindingStatus {
        entries,
        total_approved,
        total_bound,
        pairing_dir_exists: true,
    }
}

// =============================================================================
// BL-WECHAT-CATFISH-BIND v2 (5/26 鸿波): 写命令 — 给 Dashboard UI 用
// =============================================================================
//
// v1 (read-only) 用户反馈 "一般人怎么会用" — 让员工自己跑 hermes CLI 等于没做 UI.
// v2 加 4 个写命令, 让 Companion UI 可以一键审批 / 改绑 / 解绑, 不用敲命令.
//
// 安全模型: Companion 跑在员工本机 → 谁能打开 Companion 谁也能直接 rm/edit
// ~/.hermes/platforms/pairing/*.json. 所以 UI 能改 = 现实安全级别没降. 写操作
// 跟 PairingStore Python 实现 schema 对齐, 但**不复制** lockout/rate-limit
// 逻辑 — 那俩是防 IM 远端用户 brute force 的, 员工自己点界面用不上.
//
// 并发: 不加跨进程锁. atomic_replace (tmp + rename) 防 partial write 就够 —
// 最坏情况是员工同时在 Companion 点审批 + 终端跑 hermes pairing approve,
// 后写盖前写; 数据本身不会坏. 实际触发概率 ~0.
//
// (fs / PathBuf 已在文件顶部 use 过, 这里只加新依赖.)

use std::io::Write;
use std::time::{SystemTime, UNIX_EPOCH};

/// 待审批的 pairing 请求.
#[derive(Debug, serde::Serialize)]
pub struct WeChatPendingEntry {
    pub platform: String,
    /// 8-char pairing code (PairingStore 生成).
    pub code: String,
    pub user_id: String,
    pub user_name: String,
    pub created_at: f64,
    pub age_minutes: u32,
}

/// 跟 Python 端 `_EMAIL_SHAPE_RE` 对齐, 用 regex crate 跑.
/// 任何路径只要要触达 catfish-gateway X-Catfish-User 都先过这个检查.
fn email_shape_ok(s: &str) -> bool {
    // ^[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,253}\.[A-Za-z]{2,}$
    let (local, domain) = match s.split_once('@') {
        Some(parts) => parts,
        None => return false,
    };
    if local.is_empty() || local.len() > 64 {
        return false;
    }
    if !local.chars().all(|c| {
        c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '%' | '+' | '-')
    }) {
        return false;
    }
    // domain 必须有至少一个 dot, 末尾 tld 全字母 + ≥2 字符
    if domain.is_empty() || domain.len() > 253 {
        return false;
    }
    let Some(last_dot) = domain.rfind('.') else {
        return false;
    };
    let (subdom, tld) = domain.split_at(last_dot);
    let tld = &tld[1..]; // skip the dot
    if tld.len() < 2 || !tld.chars().all(|c| c.is_ascii_alphabetic()) {
        return false;
    }
    // sub-domain 允许 alnum + . + -, 不能空
    if subdom.is_empty() || !subdom.chars().all(|c| {
        c.is_ascii_alphanumeric() || c == '.' || c == '-'
    }) {
        return false;
    }
    true
}

fn normalize_email(s: &str) -> Option<String> {
    let cleaned = s.trim().to_lowercase();
    if email_shape_ok(&cleaned) {
        Some(cleaned)
    } else {
        None
    }
}

fn now_secs() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

/// 原子写: tmp + rename, chmod 0600. 跟 Python PairingStore._secure_write 对齐.
fn secure_write_json(path: &Path, value: &serde_json::Value) -> Result<(), String> {
    let parent = path.parent().ok_or_else(|| "no parent dir".to_string())?;
    fs::create_dir_all(parent).map_err(|e| format!("mkdir failed: {e}"))?;
    let pretty =
        serde_json::to_string_pretty(value).map_err(|e| format!("json encode: {e}"))?;
    let tmp = parent.join(format!(
        ".tmp.{}.{}",
        path.file_name()
            .and_then(|n| n.to_str())
            .unwrap_or("data"),
        now_secs() as u64
    ));
    {
        let mut f = fs::File::create(&tmp).map_err(|e| format!("create tmp: {e}"))?;
        f.write_all(pretty.as_bytes())
            .map_err(|e| format!("write tmp: {e}"))?;
        f.sync_all().ok();
    }
    fs::rename(&tmp, path).map_err(|e| format!("rename: {e}"))?;
    // 不致命 — Windows / 某些 FS 没 chmod 概念.
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = fs::set_permissions(path, fs::Permissions::from_mode(0o600));
    }
    Ok(())
}

fn read_json_object(path: &Path) -> serde_json::Map<String, serde_json::Value> {
    fs::read_to_string(path)
        .ok()
        .and_then(|s| serde_json::from_str::<serde_json::Value>(&s).ok())
        .and_then(|v| v.as_object().cloned())
        .unwrap_or_default()
}

const CODE_TTL_SECONDS: f64 = 3600.0;

/// 列出所有平台的 pending 审批请求 (清掉过期的).
///
/// 跟 Python PairingStore._cleanup_expired 一致: created_at 距今 > 1h → 删.
#[tauri::command]
pub fn wechat_binding_pending_list() -> Vec<WeChatPendingEntry> {
    let Some(dir) = pairing_dir() else {
        return Vec::new();
    };
    if !dir.exists() {
        return Vec::new();
    }
    let mut out = Vec::new();
    let now = now_secs();

    // 找所有 *-pending.json
    let Ok(rd) = fs::read_dir(&dir) else {
        return out;
    };
    for ent in rd.flatten() {
        let Some(name) = ent.file_name().to_str().map(String::from) else {
            continue;
        };
        if name.starts_with('_') {
            continue;
        }
        let Some(platform) = name.strip_suffix("-pending.json") else {
            continue;
        };
        if platform.is_empty() {
            continue;
        }
        let path = dir.join(&name);
        let mut pending = read_json_object(&path);

        // cleanup expired (写回)
        let expired: Vec<String> = pending
            .iter()
            .filter_map(|(code, info)| {
                let created_at = info.get("created_at").and_then(|x| x.as_f64()).unwrap_or(0.0);
                if (now - created_at) > CODE_TTL_SECONDS {
                    Some(code.clone())
                } else {
                    None
                }
            })
            .collect();
        let cleaned_count = expired.len();
        for code in &expired {
            pending.remove(code);
        }
        if cleaned_count > 0 {
            let _ = secure_write_json(&path, &serde_json::Value::Object(pending.clone()));
        }

        for (code, info) in pending.iter() {
            let user_id = info
                .get("user_id")
                .and_then(|x| x.as_str())
                .unwrap_or("")
                .to_string();
            let user_name = info
                .get("user_name")
                .and_then(|x| x.as_str())
                .unwrap_or("")
                .to_string();
            let created_at = info.get("created_at").and_then(|x| x.as_f64()).unwrap_or(0.0);
            let age_minutes = ((now - created_at) / 60.0).max(0.0) as u32;
            out.push(WeChatPendingEntry {
                platform: platform.to_string(),
                code: code.clone(),
                user_id,
                user_name,
                created_at,
                age_minutes,
            });
        }
    }

    // 老的先看, 防员工漏审
    out.sort_by(|a, b| {
        a.created_at
            .partial_cmp(&b.created_at)
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    out
}

/// 审批一个 pairing code, 可选同时绑定 catfish 员工 email.
///
/// 操作:
///   1. 从 <platform>-pending.json 删 code
///   2. 加到 <platform>-approved.json: {user_name, approved_at, [catfish_email, email_bound_at]}
///   3. 如果 catfish_email 给了但不是合法 email shape → 返错, 不执行 (UI 应防御)
///
/// 返回审批后的 entry (user_id / user_name / catfish_email).
#[tauri::command]
pub fn wechat_binding_approve(
    platform: String,
    code: String,
    catfish_email: Option<String>,
) -> Result<WeChatBindingEntry, String> {
    let dir = pairing_dir().ok_or_else(|| "no HOME".to_string())?;
    let platform = platform.trim().to_lowercase();
    let code = code.trim().to_uppercase();
    if platform.is_empty() || code.is_empty() {
        return Err("platform / code 不能为空".into());
    }

    // email 在写之前校验, 失败立即返
    let normalized_email = match &catfish_email {
        Some(e) if !e.trim().is_empty() => match normalize_email(e) {
            Some(n) => Some(n),
            None => return Err(format!("email 格式不对: {e}")),
        },
        _ => None,
    };

    let pending_path = dir.join(format!("{}-pending.json", platform));
    let approved_path = dir.join(format!("{}-approved.json", platform));

    let mut pending = read_json_object(&pending_path);
    // BL-WECHAT-APPROVE-CASE (7/18 鸿波 catch "code 找不到"): Rust line 422
    // code.to_uppercase(), 但 hermes Python 侧 pending 表 key 可能保原始 case (混合 /
    // 小写). 直接 remove(&code_upper) 会 miss. 修: case-insensitive 找 key.
    let matched_key = pending
        .keys()
        .find(|k| k.eq_ignore_ascii_case(&code))
        .cloned();
    let entry = matched_key
        .and_then(|k| pending.remove(&k))
        .ok_or_else(|| format!("code '{}' 在 {} 的 pending 表里找不到 (可能过期了)", code, platform))?;

    // 写回 pending (少了一项)
    secure_write_json(&pending_path, &serde_json::Value::Object(pending))?;

    let user_id = entry
        .get("user_id")
        .and_then(|x| x.as_str())
        .ok_or_else(|| "pending entry 缺 user_id".to_string())?
        .to_string();
    let user_name = entry
        .get("user_name")
        .and_then(|x| x.as_str())
        .unwrap_or("")
        .to_string();

    let mut approved = read_json_object(&approved_path);
    let approved_at = now_secs();
    let mut new_entry = serde_json::Map::new();
    new_entry.insert("user_name".into(), serde_json::Value::String(user_name.clone()));
    new_entry.insert(
        "approved_at".into(),
        serde_json::json!(approved_at),
    );
    if let Some(em) = &normalized_email {
        new_entry.insert("catfish_email".into(), serde_json::Value::String(em.clone()));
        new_entry.insert("email_bound_at".into(), serde_json::json!(approved_at));
    }
    approved.insert(user_id.clone(), serde_json::Value::Object(new_entry));
    secure_write_json(&approved_path, &serde_json::Value::Object(approved))?;

    Ok(WeChatBindingEntry {
        platform,
        user_id,
        user_name,
        catfish_email: normalized_email,
        approved_at,
        email_bound_at: catfish_email.as_ref().and(Some(approved_at)),
    })
}

/// 给一个已审批的 (platform, user_id) 改绑 / 后补绑 catfish 员工 email.
#[tauri::command]
pub fn wechat_binding_set_email(
    platform: String,
    user_id: String,
    catfish_email: String,
) -> Result<(), String> {
    let dir = pairing_dir().ok_or_else(|| "no HOME".to_string())?;
    let platform = platform.trim().to_lowercase();
    if platform.is_empty() || user_id.trim().is_empty() {
        return Err("platform / user_id 不能为空".into());
    }
    let normalized = normalize_email(&catfish_email)
        .ok_or_else(|| format!("email 格式不对: {catfish_email}"))?;

    let approved_path = dir.join(format!("{}-approved.json", platform));
    let mut approved = read_json_object(&approved_path);

    let entry_v = approved
        .get_mut(&user_id)
        .ok_or_else(|| format!("{} 在 {} approved 表里找不到", user_id, platform))?;
    let entry = entry_v
        .as_object_mut()
        .ok_or_else(|| "approved entry 不是 object".to_string())?;
    entry.insert(
        "catfish_email".into(),
        serde_json::Value::String(normalized),
    );
    entry.insert("email_bound_at".into(), serde_json::json!(now_secs()));

    secure_write_json(&approved_path, &serde_json::Value::Object(approved))?;
    Ok(())
}

/// 解绑: 整个移出 approved 表. 下次该 IM 用户再发消息 → 走 pending 流程重新审批.
#[tauri::command]
pub fn wechat_binding_revoke(platform: String, user_id: String) -> Result<(), String> {
    let dir = pairing_dir().ok_or_else(|| "no HOME".to_string())?;
    let platform = platform.trim().to_lowercase();
    if platform.is_empty() || user_id.trim().is_empty() {
        return Err("platform / user_id 不能为空".into());
    }
    let approved_path = dir.join(format!("{}-approved.json", platform));
    let mut approved = read_json_object(&approved_path);
    if approved.remove(&user_id).is_none() {
        return Err(format!("{} 在 {} approved 表里找不到", user_id, platform));
    }
    secure_write_json(&approved_path, &serde_json::Value::Object(approved))?;
    Ok(())
}

/// 拒绝 pending: 从 <platform>-pending.json 删 code, 不动 approved.
/// (跟 revoke 区别: revoke 是已审批用户清掉; reject 是审批前直接拒.)
#[tauri::command]
pub fn wechat_binding_reject(platform: String, code: String) -> Result<(), String> {
    let dir = pairing_dir().ok_or_else(|| "no HOME".to_string())?;
    let platform = platform.trim().to_lowercase();
    let code = code.trim().to_uppercase();
    if platform.is_empty() || code.is_empty() {
        return Err("platform / code 不能为空".into());
    }
    let pending_path = dir.join(format!("{}-pending.json", platform));
    let mut pending = read_json_object(&pending_path);
    if pending.remove(&code).is_none() {
        return Err(format!("code '{}' 在 {} pending 表里找不到 (可能过期了)", code, platform));
    }
    secure_write_json(&pending_path, &serde_json::Value::Object(pending))?;
    Ok(())
}
