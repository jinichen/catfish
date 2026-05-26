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
use std::path::PathBuf;

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
    let home = std::env::var("HOME").ok()?;
    Some(PathBuf::from(home).join(".hermes/platforms/pairing"))
}

/// 列出 pairing_dir 下所有 `<platform>-approved.json` 的 platform 名.
/// 跳过 `_rate_limits.json` 等下划线开头的内部文件.
fn list_platforms(dir: &PathBuf) -> Vec<String> {
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
fn read_platform_approved(dir: &PathBuf, platform: &str) -> Vec<WeChatBindingEntry> {
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
