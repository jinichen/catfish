//! 本机设备号: `~/.catfish/device_id`, 第一次用时随机生成 (UUID v4), 之后不变。
//!
//! 10/31 换机迁移 (docs/DEVICE-MIGRATION-DESIGN.md §6.2): 外发记录的每日锚点带上它, 中央就能分清
//! 「迁到新机后接着写的那条链」和「旧机导出后还在写的那条」。只是随机数, 不含任何信息;
//! 迁移包**不带**它 (catalog: Secret) —— 每台电脑自己一个。

use std::path::Path;

/// 读; 没有就生成并写盘。写不了盘 (目录只读等) 时返回本次生成的, 不报错 —— 锚点照报, 只是下次换号。
pub fn get_or_create(catfish_home: &Path) -> String {
    let path = catfish_home.join("device_id");
    if let Ok(s) = std::fs::read_to_string(&path) {
        let s = s.trim();
        if is_valid(s) {
            return s.to_string();
        }
    }
    let id = new_uuid();
    let _ = std::fs::create_dir_all(catfish_home);
    let _ = std::fs::write(&path, &id);
    id
}

/// 只读, 不生成 (校验时用: 没有就当还没报过带设备号的锚点)。
pub fn read(catfish_home: &Path) -> Option<String> {
    let s = std::fs::read_to_string(catfish_home.join("device_id")).ok()?;
    let s = s.trim();
    is_valid(s).then(|| s.to_string())
}

pub fn current() -> Option<String> {
    crate::services::catfish_paths::catfish_home().map(|h| get_or_create(&h))
}

/// 跟中央 `AnchorReq.device_id` 的格式一致: 小写十六进制和连字符, 8~64 位。
pub fn is_valid(s: &str) -> bool {
    (8..=64).contains(&s.len()) && s.chars().all(|c| c.is_ascii_digit() || ('a'..='f').contains(&c) || c == '-')
}

fn new_uuid() -> String {
    let mut b: [u8; 16] = rand::random();
    b[6] = (b[6] & 0x0f) | 0x40;
    b[8] = (b[8] & 0x3f) | 0x80;
    let h = hex::encode(b);
    format!("{}-{}-{}-{}-{}", &h[0..8], &h[8..12], &h[12..16], &h[16..20], &h[20..32])
}

#[tauri::command]
pub fn device_id_get() -> Result<String, String> {
    current().ok_or_else(|| "找不到用户目录".to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn 第一次生成_之后不变_格式跟中央一致() {
        let t = tempfile::tempdir().unwrap();
        let a = get_or_create(t.path());
        assert!(is_valid(&a) && a.len() == 36, "{a}");
        assert_eq!(get_or_create(t.path()), a);
        assert_eq!(read(t.path()), Some(a));
        assert!(!is_valid("员工的内容") && !is_valid("ABCDEF12"));
    }
}
