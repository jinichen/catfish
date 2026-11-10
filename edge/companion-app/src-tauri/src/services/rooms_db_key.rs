//! 同事群聊本机库 (`~/.catfish/collab_rooms/rooms.db`) 的加密密钥 (11/6, docs/GROUP-CHAT-PLAN.md §4.3)。
//!
//! 库在 tool-bridge 那边用 SQLCipher 整库加密 (colleague_rooms_crypt.py)。密钥是 32 字节随机数, 存**系统钥匙串**
//! (macOS 钥匙串 / Windows 凭据管理器), 第一次用时由 Companion 生成。只有 Companion 读: tool-bridge 读钥匙串在 macOS 上
//! 会弹看不见的授权框 (teaching_credentials/socket.rs 文件头), 所以反过来 —— Companion 调群聊 / 派任务的接口时
//! 把密钥带在参数 `_rooms_key` 里 ([`with_key`]), tool-bridge 进门就摘掉记在内存。tool-bridge 重启了, 下一次调用又带上。
//!
//! 取不到密钥 (钥匙串出错 / Linux 没有后端) 就不带: 库还是明文时 tool-bridge 照常用, 已经加密的会明说打不开。
//!
//! 换电脑: 迁移包 (age 口令加密) 里带上这把密钥 ([`existing`]), 新电脑导入时 [`replace`] 写进它的钥匙串;
//! 新电脑原来那把另存一条 (覆盖导入时原数据挪去备份目录, 备份里的库还要用原来那把才打得开)。
//!
//! ⚠ 密钥不进日志、不进错误消息、不给前端 (不是 Tauri command)。

use std::sync::Mutex;

use serde_json::Value;

/// 钥匙串服务名。只有 macOS / Windows 有系统凭据库后端用得到 (见下面 `entry`); Linux 上不编译,
/// 免得 CI (Linux) 报 dead_code。
#[cfg(any(target_os = "macos", target_os = "windows"))]
const SERVICE: &str = "catfish-collab-rooms";
const ACCOUNT: &str = "rooms-db-key";
/// 带密钥的方法: 群聊 / 群里的小鲶 / 派任务 (群里派的任务会往群里发任务卡和状态)。
const PREFIXES: &[&str] = &["pim/croom_", "room/", "pim/ctask_"];
pub const PARAM: &str = "_rooms_key";

static CACHE: Mutex<Option<String>> = Mutex::new(None);
static WARNED: Mutex<bool> = Mutex::new(false);

pub fn wants_key(method: &str) -> bool {
    PREFIXES.iter().any(|p| method.starts_with(p))
}

/// 64 位小写十六进制。
pub fn valid_key(k: &str) -> bool {
    k.len() == 64 && k.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

pub fn new_key() -> String {
    use rand::RngCore;
    let mut buf = [0u8; 32];
    rand::rngs::OsRng.fill_bytes(&mut buf);
    hex::encode(buf)
}

/// 参数里塞上密钥 (只对 [`PREFIXES`] 里的方法, 只往对象里塞)。
pub fn put(params: &mut Value, key: &str) {
    if let Some(obj) = params.as_object_mut() {
        obj.insert(PARAM.to_string(), Value::String(key.to_string()));
    }
}

#[cfg(any(target_os = "macos", target_os = "windows"))]
fn entry(account: &str) -> Result<keyring::Entry, String> {
    keyring::Entry::new(SERVICE, account).map_err(|e| format!("无法打开本机凭据库: {e}"))
}

#[cfg(not(any(target_os = "macos", target_os = "windows")))]
fn entry(_account: &str) -> Result<keyring::Entry, String> {
    Err("本平台没有配置系统凭据库后端".to_string())
}

fn read(account: &str) -> Result<Option<String>, String> {
    match entry(account)?.get_password() {
        Ok(k) if valid_key(&k) => Ok(Some(k)),
        Ok(_) => Err("钥匙串里的群聊库密钥格式不对".to_string()),
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(e) => Err(e.to_string()),
    }
}

/// 钥匙串里已有的那把 (没有不新建)。迁移导出用。
pub fn existing() -> Result<Option<String>, String> {
    if let Some(k) = CACHE.lock().map_err(|e| e.to_string())?.clone() {
        return Ok(Some(k));
    }
    read(ACCOUNT)
}

/// 取密钥, 没有就生成一把存进去 (存完再读一遍核对)。
pub fn get_or_create() -> Result<String, String> {
    let mut cache = CACHE.lock().map_err(|e| e.to_string())?;
    if let Some(k) = cache.clone() {
        return Ok(k);
    }
    let k = match read(ACCOUNT)? {
        Some(k) => k,
        None => {
            let k = new_key();
            entry(ACCOUNT)?.set_password(&k).map_err(|e| format!("群聊库密钥存不进钥匙串: {e}"))?;
            if read(ACCOUNT)?.as_deref() != Some(k.as_str()) {
                return Err("群聊库密钥存进钥匙串后读回来对不上".to_string());
            }
            log::info!("[rooms-db-key] 生成了群聊本机库的加密密钥 (存在系统钥匙串)");
            k
        }
    };
    *cache = Some(k.clone());
    Ok(k)
}

/// 换电脑导入: 用迁移包里的那把; 本机原来那把另存为 `rooms-db-key.before-<stamp>`。
pub fn replace(key: &str, stamp: &str) -> Result<(), String> {
    if !valid_key(key) {
        return Err("迁移包里的群聊库密钥格式不对".to_string());
    }
    let mut cache = CACHE.lock().map_err(|e| e.to_string())?;
    if let Some(old) = read(ACCOUNT)? {
        if old == key {
            *cache = Some(old);
            return Ok(());
        }
        entry(&format!("{ACCOUNT}.before-{stamp}"))?
            .set_password(&old)
            .map_err(|e| format!("本机原来的群聊库密钥没能另存: {e}"))?;
    }
    entry(ACCOUNT)?.set_password(key).map_err(|e| format!("群聊库密钥存不进钥匙串: {e}"))?;
    *cache = Some(key.to_string());
    Ok(())
}

/// tool_bridge_rpc 调用前: 该带密钥的方法塞上。取不到只记一次日志, 照常调 (见文件头)。
pub async fn with_key(method: &str, mut params: Value) -> Value {
    if !wants_key(method) || !params.is_object() {
        return params;
    }
    let cached = CACHE.lock().ok().and_then(|c| c.clone());
    let key = match cached {
        Some(k) => Ok(k),
        // 读钥匙串可能卡在授权框上等人点 (重装 / 重签名后第一次): 放到阻塞线程
        None => tokio::task::spawn_blocking(get_or_create).await.unwrap_or_else(|e| Err(e.to_string())),
    };
    match key {
        Ok(k) => put(&mut params, &k),
        Err(e) => {
            if let Ok(mut w) = WARNED.lock() {
                if !*w {
                    *w = true;
                    log::warn!("[rooms-db-key] 取不到群聊库密钥 ({e}) —— 本机群聊记录先不加密");
                }
            }
        }
    }
    params
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn 只给群聊和派任务带密钥() {
        for m in ["pim/croom_list", "pim/croom_snapshot", "room/assist", "pim/ctask_send"] {
            assert!(wants_key(m), "{m}");
        }
        for m in ["pim/form_list", "tools/dispatch", "meeting/summarize", "pim/croomx", "health"] {
            assert!(!wants_key(m), "{m}");
        }
    }

    #[test]
    fn 新密钥是64位小写十六进制_每次不一样() {
        let (a, b) = (new_key(), new_key());
        assert!(valid_key(&a) && valid_key(&b) && a != b);
        assert!(!valid_key(&a.to_uppercase()) || a.chars().all(|c| c.is_ascii_digit()));
        assert!(!valid_key("abc") && !valid_key(&"g".repeat(64)) && !valid_key(&"a".repeat(63)));
    }

    #[test]
    fn 只往对象里塞() {
        let mut p = json!({"room_id": "r_1"});
        put(&mut p, &"a".repeat(64));
        assert_eq!(p[PARAM], json!("a".repeat(64)));
        assert_eq!(p["room_id"], json!("r_1"));
        let mut arr = json!([1, 2]);
        put(&mut arr, "k");
        assert_eq!(arr, json!([1, 2]));
    }
}
