//! 扫码直传 (11/21, docs/DEVICE-MIGRATION-DESIGN.md §12): 旧电脑 → 新电脑, 不用 U 盘、不用设口令。
//!
//! - 新电脑现场生成一把一次性密钥 (age x25519), 在中央开一次直传, 屏幕上出二维码 (直传编号 + 公钥) 和 8 位配对码。
//! - 旧电脑扫码 (或手输配对码) 拿到公钥, 用它加密迁移包, 1MB 一块经中央传过去。中央只见密文, 私钥只在新电脑内存里;
//!   中央同时只留一个窗口 (默认 64MB) 的块, 新电脑取走一块删一块 (central device_transfer_db.py)。
//! - 两边屏幕都显示一组 6 位「核对数字」(由公钥算出): 手输配对码时公钥是从中央拿的, 两边数字一样才说明中间没被换过。
//! - 新电脑收齐 → 解密、逐文件校验 → 跟导入迁移包一样的检查 (本人? 版本? 已有数据?) → 员工点导入。
//! - 旧电脑那边: 外发记录链上记「导出迁移包」, 另记一条外发 (目的地中央, 已加密, 只记字节数)。

use std::fs;
use std::path::PathBuf;
use std::str::FromStr;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use super::direct_net::{download, s, upload, Conn, Got, Retry, Sent};
use sha2::{Digest, Sha256};

use super::package::{Lock, Manifest, Unlock};
use super::snapshot::Roots;
use super::{Me, TmpDir};

pub const QR_PREFIX: &str = "catfishmove:1:";
const EGRESS_PURPOSE: &str = "换机直传 (迁移包, 已加密)";

// ── 二维码 / 核对数字 ─────────────────────────────────────

/// 6 位核对数字 (「123 456」): 两边各自从公钥算, 一样就说明公钥没被换。
pub fn safety_number(recipient: &str) -> String {
    let h = Sha256::digest(recipient.trim().as_bytes());
    let n = u64::from_be_bytes(h[..8].try_into().unwrap_or([0; 8])) % 1_000_000;
    format!("{:03} {:03}", n / 1000, n % 1000)
}

pub fn qr_text(id: &str, recipient: &str) -> String {
    format!("{QR_PREFIX}{id}:{recipient}")
}

fn valid_id(id: &str) -> bool {
    id.len() == 34 && id.starts_with("t_") && id[2..].bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

fn valid_recipient(r: &str) -> bool {
    age::x25519::Recipient::from_str(r).is_ok()
}

/// 扫到的文字 → (直传编号, 公钥)。不是鲶鱼直传的码 → None。
pub fn parse_qr(text: &str) -> Option<(String, String)> {
    let rest = text.trim().strip_prefix(QR_PREFIX)?;
    let (id, recipient) = rest.split_once(':')?;
    (valid_id(id) && valid_recipient(recipient)).then(|| (id.to_string(), recipient.to_string()))
}

// ── 新电脑: 收 ─────────────────────────────────────────────

#[derive(Debug, Clone, Default, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RecvStatus {
    /// waiting (等旧电脑扫码) / receiving / verifying / ready (可以导入) / failed / cancelled
    pub state: String,
    pub id: String,
    pub code: String,
    pub qr: String,
    pub safety: String,
    pub to_device: String,
    pub from_device: String,
    pub got: u64,
    pub n: u64,
    pub size: u64,
    pub error: Option<String>,
    /// 收齐解开后: 跟「查看包里有什么」同一份 (InspectReport)
    pub info: Option<Value>,
}

struct Recv {
    status: RecvStatus,
    stop: Arc<AtomicBool>,
    opened: Option<(TmpDir, PathBuf, Manifest)>,
}

static RECV: Mutex<Option<Recv>> = Mutex::new(None);

fn recv_update(f: impl FnOnce(&mut RecvStatus)) {
    if let Ok(mut g) = RECV.lock() {
        if let Some(r) = g.as_mut() {
            f(&mut r.status);
        }
    }
}

fn recv_fail(e: String) {
    recv_update(|st| {
        st.state = "failed".into();
        st.error = Some(e);
    });
}

fn part_path(roots: &Roots, id: &str) -> PathBuf {
    roots.catfish.join(format!(".migration-direct-{id}.part"))
}

#[tauri::command]
pub async fn migration_direct_receive_start() -> Result<RecvStatus, String> {
    let _ = migration_direct_receive_cancel().await;
    Me::current()?; // 先登录 (导入要核对是不是本人)
    let identity = age::x25519::Identity::generate();
    let recipient = identity.to_public().to_string();
    let to_device = crate::commands::mobile_bridge::device_name();
    let v = Conn::live()
        .api(reqwest::Method::POST, "/api/device-transfer", Some(json!({ "recipient": recipient, "to_device": to_device })))
        .await?;
    let id = s(&v, "id");
    if !valid_id(&id) {
        return Err("公司服务器回的直传编号不对".into());
    }
    let status = RecvStatus {
        state: "waiting".into(),
        id: id.clone(),
        code: s(&v, "code"),
        qr: qr_text(&id, &recipient),
        safety: safety_number(&recipient),
        to_device,
        ..Default::default()
    };
    let stop = Arc::new(AtomicBool::new(false));
    *RECV.lock().map_err(|e| e.to_string())? = Some(Recv { status: status.clone(), stop: stop.clone(), opened: None });
    tauri::async_runtime::spawn(async move {
        if let Err(e) = receive(id, identity, stop.clone()).await {
            if !stop.load(Ordering::SeqCst) {
                recv_fail(e);
            }
        }
    });
    Ok(status)
}

async fn receive(id: String, identity: age::x25519::Identity, stop: Arc<AtomicBool>) -> Result<(), String> {
    let roots = Roots::current()?;
    let part = part_path(&roots, &id);
    // 1. 等旧电脑开始传, 一块块收 (收一块删一块), 收齐报 done
    let got = download(&Conn::live(), &id, &part, &stop, |g| match g {
        Got::Started { size, n, from } => recv_update(|st| {
            st.state = "receiving".into();
            st.from_device = from;
            st.size = size;
            st.n = n;
        }),
        Got::Chunk(k) => recv_update(|st| st.got = k),
    })
    .await?;
    if got.is_none() {
        return Ok(()); // 取消了
    }
    // 2. 解密 + 逐文件校验 + 跟导入同样的检查
    recv_update(|st| st.state = "verifying".into());
    let p2 = part.clone();
    let opened = super::blocking(move || {
        let r = super::open_package(&roots, &p2, &Unlock::Identity(identity)).and_then(|(tmp, zip, m)| {
            let info = super::inspect_report(&roots, &Me::current()?, &m);
            Ok((tmp, zip, m, serde_json::to_value(info).map_err(|e| e.to_string())?))
        });
        let _ = fs::remove_file(&p2);
        r
    })
    .await;
    let (tmp, zip, m, info) = opened?;
    if let Ok(mut g) = RECV.lock() {
        if let Some(r) = g.as_mut().filter(|r| r.status.id == id) {
            r.status.state = "ready".into();
            r.status.info = Some(info);
            r.opened = Some((tmp, zip, m));
        }
    }
    Ok(())
}

#[tauri::command]
pub fn migration_direct_receive_status() -> Option<RecvStatus> {
    RECV.lock().ok()?.as_ref().map(|r| r.status.clone())
}

#[tauri::command]
pub async fn migration_direct_receive_cancel() -> Result<(), String> {
    let old = RECV.lock().map_err(|e| e.to_string())?.take();
    if let Some(r) = old {
        r.stop.store(true, Ordering::SeqCst);
        if matches!(r.status.state.as_str(), "waiting" | "receiving") {
            Conn::live().cancel(&r.status.id).await;
        }
        if let Ok(roots) = Roots::current() {
            let _ = fs::remove_file(part_path(&roots, &r.status.id));
        }
    }
    Ok(())
}

#[tauri::command]
pub async fn migration_direct_import(overwrite: bool) -> Result<super::apply::ImportReport, String> {
    let opened = {
        let mut g = RECV.lock().map_err(|e| e.to_string())?;
        let r = g.as_mut().filter(|r| r.status.state == "ready").ok_or("还没收齐, 不能导入")?;
        r.opened.take().ok_or("已经导入过了")?
    };
    let rep = super::exclusive(move || {
        let (tmp, zip, m) = opened;
        let o = super::ImportOpts { overwrite, only: None, lineage: true };
        super::import_opened(&Roots::current()?, &Me::current()?, &tmp, &zip, &m, &o)
    })
    .await;
    let rep = match rep {
        Ok(r) => r,
        Err(e) => {
            // 收到的包已经清掉了 (临时目录随失败释放): 要重来就重新扫一次
            recv_fail(format!("{e} (要重来请重新开始直传)"));
            return Err(e);
        }
    };
    *RECV.lock().map_err(|e| e.to_string())? = None;
    Ok(super::after_import(rep).await)
}

// ── 旧电脑: 发 ─────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Target {
    pub id: String,
    pub recipient: String,
    pub to_device: String,
    pub safety: String,
}

/// 手输配对码 → 新电脑那次直传 (公钥是从中央拿的, 所以界面要让员工核对两边的数字)。
#[tauri::command]
pub async fn migration_direct_lookup(code: String) -> Result<Target, String> {
    let v = Conn::live()
        .api(reqwest::Method::GET, &format!("/api/device-transfer/by-code/{}", urlencoding::encode(code.trim())), None)
        .await?;
    let recipient = s(&v, "recipient");
    if !valid_recipient(&recipient) || !valid_id(&s(&v, "id")) {
        return Err("公司服务器回的信息不对".into());
    }
    Ok(Target { id: s(&v, "id"), safety: safety_number(&recipient), recipient, to_device: s(&v, "to_device") })
}

/// 扫到的二维码 → 新电脑那次直传。公钥以二维码为准, 再跟中央记的核一遍 (对不上 = 有人在中间换过, 不传)。
#[tauri::command]
pub async fn migration_direct_from_qr(text: String) -> Result<Target, String> {
    let (id, recipient) = parse_qr(&text).ok_or("这不是鲶鱼换机直传的二维码")?;
    let v = Conn::live().info(&id).await?;
    if s(&v, "recipient") != recipient {
        return Err("二维码跟公司服务器上记的对不上, 为安全起见不传。请在新电脑上重新开始".into());
    }
    if s(&v, "state") != "waiting" {
        return Err("新电脑那边这次直传已经开始或结束了, 请在新电脑上重新开始".into());
    }
    Ok(Target { id, safety: safety_number(&recipient), recipient, to_device: s(&v, "to_device") })
}

#[derive(Debug, Clone, Default, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SendStatus {
    /// packing (打包加密) / sending / finishing (等新电脑收完) / done / failed / cancelled
    pub state: String,
    pub id: String,
    pub to_device: String,
    pub size: u64,
    pub n: u64,
    pub sent: u64,
    /// 新电脑已经收好的块数
    pub acked: u64,
    pub files: u64,
    pub error: Option<String>,
}

static SEND: Mutex<Option<(SendStatus, Arc<AtomicBool>)>> = Mutex::new(None);

fn send_update(f: impl FnOnce(&mut SendStatus)) {
    if let Ok(mut g) = SEND.lock() {
        if let Some((st, _)) = g.as_mut() {
            f(st);
        }
    }
}

#[tauri::command]
pub async fn migration_direct_send(target: Target, optional: Vec<String>) -> Result<SendStatus, String> {
    if !valid_id(&target.id) {
        return Err("直传编号不对".into());
    }
    let recipient = age::x25519::Recipient::from_str(&target.recipient).map_err(|_| "公钥格式不对".to_string())?;
    {
        let g = SEND.lock().map_err(|e| e.to_string())?;
        if g.as_ref().is_some_and(|(st, _)| matches!(st.state.as_str(), "packing" | "sending" | "finishing")) {
            return Err("正在传另一次".into());
        }
    }
    let stop = Arc::new(AtomicBool::new(false));
    let st = SendStatus { state: "packing".into(), id: target.id.clone(), to_device: target.to_device.clone(), ..Default::default() };
    *SEND.lock().map_err(|e| e.to_string())? = Some((st.clone(), stop.clone()));
    tauri::async_runtime::spawn(async move {
        let id = target.id.clone();
        if let Err(e) = send(target, recipient, optional, stop.clone()).await {
            if !stop.load(Ordering::SeqCst) {
                Conn::live().cancel(&id).await;
                send_update(|st| {
                    st.state = "failed".into();
                    st.error = Some(e);
                });
            }
        }
    });
    Ok(st)
}

async fn send(target: Target, recipient: age::x25519::Recipient, optional: Vec<String>, stop: Arc<AtomicBool>) -> Result<(), String> {
    let roots = Roots::current()?;
    let conn = Conn::live();
    let id = target.id.clone();
    // 1. 打包, 用新电脑的公钥加密 (跟导出迁移包同一套; 外发记录链上记「导出迁移包」)
    let rooms = super::rooms_for_export(&roots).await?;
    let snap = rooms.snapshot.clone();
    let out = roots.catfish.join(format!(".migration-direct-out-{}.catfishmove", super::stamp()));
    let (r2, o2) = (roots.clone(), out.clone());
    let packed = super::exclusive(move || super::export_with(&r2, &Me::current()?, &o2, &Lock::Recipient(recipient), &optional, &rooms, true)).await;
    if let Some(p) = snap {
        let _ = fs::remove_file(p);
    }
    let rep = packed?;
    let _guard = OutFile(out.clone());
    if stop.load(Ordering::SeqCst) {
        return Ok(());
    }
    send_update(|st| st.files = rep.files);
    // 2. 一块块传
    let from = crate::commands::mobile_bridge::device_name();
    let sent = upload(&conn, &id, &out, &from, &stop, |p| match p {
        Sent::Started { size, n } => send_update(|st| {
            st.state = "sending".into();
            st.size = size;
            st.n = n;
        }),
        Sent::Chunk(k) => send_update(|st| st.sent = k),
        Sent::Acked(k) => send_update(|st| st.acked = k),
    })
    .await?;
    let Some(size) = sent else { return Ok(()) };
    record_egress(conn.base(), &id, size);
    // 3. 等新电脑收完 (它收齐后报 done)
    send_update(|st| st.state = "finishing".into());
    let mut retry = Retry::new();
    loop {
        if stop.load(Ordering::SeqCst) {
            return Ok(());
        }
        match conn.info(&id).await {
            Ok(v) => {
                send_update(|st| st.acked = super::direct_net::u(&v, "acked"));
                match s(&v, "state").as_str() {
                    "done" => break,
                    "sending" => {}
                    "expired" => return Err("新电脑半小时没动静, 这次作废了".into()),
                    other => return Err(format!("新电脑那边结束了 ({other})")),
                }
            }
            Err(e) => retry.again(e).await?,
        }
        tokio::time::sleep(std::time::Duration::from_secs(2)).await;
    }
    send_update(|st| st.state = "done".into());
    Ok(())
}

/// 打好的包用完就删 (成功、失败、取消都删)。
struct OutFile(PathBuf);

impl Drop for OutFile {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.0);
    }
}

/// 外发记录: 目的地中央, 内容已加密 (只记字节数)。
fn record_egress(base: &str, id: &str, bytes: u64) {
    let host = crate::services::endpoints::endpoints().gateway_host.clone();
    let req = crate::commands::transparent_log::RecordReq {
        method: "PUT".into(),
        url: format!("{base}/api/device-transfer/{id}/chunks"),
        request_body: None,
        status: Some(200),
        response_bytes: Some(0),
        response_summary: None,
        error: None,
        category: Some("content".into()),
        origin: Some("companion-bg".into()),
        dest_kind: Some("central".into()),
        dest_host: Some(host),
        purpose: Some(EGRESS_PURPOSE.into()),
        request_bytes: Some(bytes),
        ts_request: None,
        pending_id: None,
    };
    std::thread::spawn(move || {
        if let Err(e) = crate::commands::transparent_log::record_blocking(req) {
            log::warn!("[migration-direct] 外发记录失败: {e}");
        }
    });
}

#[tauri::command]
pub fn migration_direct_send_status() -> Option<SendStatus> {
    SEND.lock().ok()?.as_ref().map(|(st, _)| st.clone())
}

#[tauri::command]
pub async fn migration_direct_send_cancel() -> Result<(), String> {
    let cur = SEND.lock().map_err(|e| e.to_string())?.take();
    if let Some((st, stop)) = cur {
        stop.store(true, Ordering::SeqCst);
        if matches!(st.state.as_str(), "packing" | "sending" | "finishing") {
            Conn::live().cancel(&st.id).await;
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn 二维码来回_核对数字稳定() {
        let id = format!("t_{}", "ab".repeat(16));
        let r = age::x25519::Identity::generate().to_public().to_string();
        assert_eq!(parse_qr(&qr_text(&id, &r)), Some((id.clone(), r.clone())));
        assert_eq!(parse_qr(&format!("  {}\n", qr_text(&id, &r))), Some((id.clone(), r.clone())));
        assert!(parse_qr("https://example.com").is_none());
        assert!(parse_qr(&qr_text("t_../../x", &r)).is_none());
        assert!(parse_qr(&qr_text(&id, "age1notakey")).is_none());
        let n = safety_number(&r);
        assert_eq!(n, safety_number(&format!(" {r} ")));
        assert!(n.len() == 7 && n.as_bytes()[3] == b' ' && n.replace(' ', "").bytes().all(|b| b.is_ascii_digit()));
        let other = age::x25519::Identity::generate().to_public().to_string();
        assert_ne!(safety_number(&other), n);
    }
}
