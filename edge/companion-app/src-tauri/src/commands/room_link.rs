//! P50 (9/10): 横向协同 —— 本机在 RoomLink 握手里要报出去的身份.
//!
//! A 请 B 帮忙时, 请求里要带 A 的 `authority_gateway_id` (hermes 用
//! `install:<install_id>` 形式, 见 hermes `hosted_rooms.local_authority_gateway_id`);
//! B 同意后要把自己 8642 的内网地址回给 A。两样都只有 Rust 侧拿得到:
//! install_id 在 hermes home 下的 `install_id` 文件 (32 位 hex), 内网地址靠
//! 一个不发包的 UDP connect 问内核走哪块网卡。
//!
//! hermes 自己没有匿名端点能查这两样 (`/v1/room-members/capabilities` 要先有
//! grant), 所以 Companion 直接读文件, 跟 `hermes_jwt_sync` 读 `.env` 同一路数。

use anyhow::{Context, Result};
use serde::Serialize;
use std::net::UdpSocket;

#[derive(Debug, Serialize, Clone, PartialEq)]
pub struct RoomLinkLocalIdentity {
    /// hermes 侧的 `install:<hex32>`; 文件缺 / 格式错时 None (hermes 未起过).
    pub authority_gateway_id: Option<String>,
    /// 本机对外网卡 IPv4, 拿不到 None (离线 / 只有 loopback).
    pub lan_ip: Option<String>,
}

/// `install_id` 文件内容 → `install:<hex32>`. 判据跟 hermes `_INSTALL_ID_RE` 一致.
pub fn authority_gateway_id_from_file(text: &str) -> Option<String> {
    let id = text.trim();
    if id.len() == 32 && id.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f')) {
        Some(format!("install:{id}"))
    } else {
        None
    }
}

fn read_authority_gateway_id() -> Result<Option<String>> {
    let path = crate::services::catfish_paths::hermes_home()
        .context("无法定位 Hermes home")?
        .join("install_id");
    if !path.exists() {
        return Ok(None);
    }
    let text = std::fs::read_to_string(&path).with_context(|| format!("读 {}", path.display()))?;
    Ok(authority_gateway_id_from_file(&text))
}

/// UDP connect 不发任何包, 只让内核选路由; 目标地址随便一个非本机公网段即可.
fn detect_lan_ip() -> Option<String> {
    let sock = UdpSocket::bind("0.0.0.0:0").ok()?;
    sock.connect("10.255.255.255:1").ok()?;
    let ip = sock.local_addr().ok()?.ip();
    if ip.is_loopback() || ip.is_unspecified() {
        return None;
    }
    Some(ip.to_string())
}

#[tauri::command]
pub fn room_link_local_identity() -> Result<RoomLinkLocalIdentity, String> {
    let authority_gateway_id = read_authority_gateway_id().map_err(|e| e.to_string())?;
    Ok(RoomLinkLocalIdentity {
        authority_gateway_id,
        lan_ip: detect_lan_ip(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn install_id_合法_带前缀() {
        let id = "0123456789abcdef0123456789abcdef";
        assert_eq!(
            authority_gateway_id_from_file(&format!("{id}\n")),
            Some(format!("install:{id}"))
        );
    }

    #[test]
    fn install_id_长度或字符不对_none() {
        assert_eq!(authority_gateway_id_from_file(""), None);
        assert_eq!(authority_gateway_id_from_file("0123456789abcdef"), None);
        assert_eq!(
            authority_gateway_id_from_file("0123456789ABCDEF0123456789ABCDEF"),
            None,
            "hermes 正则只认小写"
        );
        assert_eq!(
            authority_gateway_id_from_file("0123456789abcdef0123456789abcdeg"),
            None
        );
    }
}
