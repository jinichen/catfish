//! 手机通道在本机存的东西: `~/.catfish/mobile/`
//!
//!   settings.json  {"enabled": bool}             员工在「设置 → 手机」里开没开 (默认关)
//!   keys.json      {"secretKey", "publicKey"}     这台电脑的长期密钥 (0600)
//!   phones.json    [{device, name, publicKey, pairedAt}]  配过的手机
//!
//! 换机迁移不带这个目录 (device_migration catalog: Secret) —— 密钥跟着这台电脑, 新电脑重新配对。

use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

use super::crypto;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct Phone {
    pub device: String,
    pub name: String,
    pub public_key: String,
    pub paired_at: String,
    /// 最后一次连上来的时间 (11/01; 老数据没有)。30 天没用的在界面上标出来, 替换时排第一个
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_seen: Option<String>,
}

#[derive(Debug, Default, Serialize, Deserialize)]
struct Settings {
    #[serde(default)]
    enabled: bool,
}

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct Keys {
    secret_key: String,
    public_key: String,
}

pub fn dir() -> Option<PathBuf> {
    crate::services::catfish_paths::catfish_home().map(|h| h.join("mobile"))
}

fn write_private(path: &Path, text: &str) -> Result<(), String> {
    if let Some(p) = path.parent() {
        std::fs::create_dir_all(p).map_err(|e| format!("建 {} 失败: {e}", p.display()))?;
    }
    let tmp = path.with_extension("tmp");
    std::fs::write(&tmp, text).map_err(|e| format!("写 {} 失败: {e}", tmp.display()))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&tmp, std::fs::Permissions::from_mode(0o600));
    }
    std::fs::rename(&tmp, path).map_err(|e| format!("写 {} 失败: {e}", path.display()))
}

pub fn enabled_in(dir: &Path) -> bool {
    std::fs::read_to_string(dir.join("settings.json"))
        .ok()
        .and_then(|t| serde_json::from_str::<Settings>(&t).ok())
        .map(|s| s.enabled)
        .unwrap_or(false)
}

pub fn set_enabled_in(dir: &Path, enabled: bool) -> Result<(), String> {
    let text = serde_json::to_string(&Settings { enabled }).map_err(|e| e.to_string())?;
    write_private(&dir.join("settings.json"), &text)
}

/// 这台电脑的长期密钥; 没有就生成。
pub fn keys_in(dir: &Path) -> Result<crypto_box::SecretKey, String> {
    let path = dir.join("keys.json");
    if let Ok(t) = std::fs::read_to_string(&path) {
        if let Ok(k) = serde_json::from_str::<Keys>(&t) {
            if let Some(sk) = crypto::key32(&k.secret_key) {
                return Ok(crypto_box::SecretKey::from(sk));
            }
        }
    }
    let sk = crypto::new_secret();
    let keys = Keys { secret_key: crypto::b64(&sk.to_bytes()), public_key: crypto::b64(&crypto::public_of(&sk)) };
    write_private(&path, &serde_json::to_string(&keys).map_err(|e| e.to_string())?)?;
    Ok(sk)
}

pub fn phones_in(dir: &Path) -> Vec<Phone> {
    std::fs::read_to_string(dir.join("phones.json"))
        .ok()
        .and_then(|t| serde_json::from_str(&t).ok())
        .unwrap_or_default()
}

pub fn save_phones_in(dir: &Path, phones: &[Phone]) -> Result<(), String> {
    let text = serde_json::to_string_pretty(phones).map_err(|e| e.to_string())?;
    write_private(&dir.join("phones.json"), &text)
}

pub fn add_phone_in(dir: &Path, phone: Phone) -> Result<(), String> {
    let mut all = phones_in(dir);
    all.retain(|p| p.device != phone.device);
    all.push(phone);
    save_phones_in(dir, &all)
}

/// 记一下这台手机刚连上来过
pub fn touch_phone_in(dir: &Path, device: &str, when: &str) -> Result<(), String> {
    let mut all = phones_in(dir);
    let Some(p) = all.iter_mut().find(|p| p.device == device) else { return Ok(()) };
    p.last_seen = Some(when.to_string());
    save_phones_in(dir, &all)
}

pub fn remove_phone_in(dir: &Path, device: &str) -> Result<bool, String> {
    let mut all = phones_in(dir);
    let before = all.len();
    all.retain(|p| p.device != device);
    if all.len() == before {
        return Ok(false);
    }
    save_phones_in(dir, &all)?;
    Ok(true)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn 默认关_开关落盘() {
        let t = tempfile::tempdir().unwrap();
        assert!(!enabled_in(t.path()));
        set_enabled_in(t.path(), true).unwrap();
        assert!(enabled_in(t.path()));
    }

    #[test]
    fn 密钥生成一次之后不变() {
        let t = tempfile::tempdir().unwrap();
        let a = keys_in(t.path()).unwrap();
        let b = keys_in(t.path()).unwrap();
        assert_eq!(a.to_bytes(), b.to_bytes());
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = std::fs::metadata(t.path().join("keys.json")).unwrap().permissions().mode();
            assert_eq!(mode & 0o777, 0o600);
        }
    }

    #[test]
    fn 手机增删_同一台重配只留一条() {
        let t = tempfile::tempdir().unwrap();
        let p = |n: &str| Phone { device: "phone-1".into(), name: n.into(), public_key: "k".into(), paired_at: "t".into(), last_seen: None };
        add_phone_in(t.path(), p("A")).unwrap();
        add_phone_in(t.path(), p("B")).unwrap();
        assert_eq!(phones_in(t.path()), vec![p("B")]);
        touch_phone_in(t.path(), "phone-1", "2026-11-01T10:00:00+08:00").unwrap();
        assert_eq!(phones_in(t.path())[0].last_seen.as_deref(), Some("2026-11-01T10:00:00+08:00"));
        assert!(remove_phone_in(t.path(), "phone-1").unwrap());
        assert!(!remove_phone_in(t.path(), "phone-1").unwrap());
        assert!(phones_in(t.path()).is_empty());
    }
}
